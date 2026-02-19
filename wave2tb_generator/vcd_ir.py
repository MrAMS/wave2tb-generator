from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re

from vcdvcd import VCDVCD

from .models import CycleVector, PortSpec, WaveIR

_SIGNAL_LEAF_RE = re.compile(r"^(?P<base>[^\[]+)(\[(?P<range>[^\]]+)\])?$")


def _split_reference(reference: str) -> tuple[str, str]:
    if "." in reference:
        scope, leaf = reference.rsplit(".", 1)
    else:
        scope, leaf = "", reference
    match = _SIGNAL_LEAF_RE.match(leaf)
    if not match:
        return scope, leaf
    return scope, match.group("base")


def _normalize_bits(raw_value: str, width: int) -> str:
    value = raw_value.strip().lower()
    if not value:
        return "0" * width
    if value.startswith("b"):
        value = value[1:]

    if width == 1:
        if value[-1] in {"0", "1", "x", "z"}:
            return value[-1]
        return "x"

    if len(value) == 1 and value in {"x", "z"}:
        return value * width

    if len(value) < width:
        if set(value) <= {"0", "1", "x", "z"}:
            pad = value[0] if len(set(value)) == 1 and value[0] in {"x", "z"} else "0"
            value = (pad * (width - len(value))) + value
        else:
            value = ("0" * (width - len(value))) + value
    elif len(value) > width:
        value = value[-width:]
    return "".join(ch if ch in {"0", "1", "x", "z"} else "x" for ch in value)


@dataclass(frozen=True)
class _TVSampler:
    times: list[int]
    values: list[str]

    @classmethod
    def from_tv(cls, tv: list[tuple[int, str]], width: int) -> "_TVSampler":
        if not tv:
            return cls(times=[0], values=["0" * width if width > 1 else "0"])
        times = [int(t) for t, _ in tv]
        values = [_normalize_bits(v, width) for _, v in tv]
        return cls(times=times, values=values)

    def at(self, time_point: int) -> str:
        idx = bisect_right(self.times, time_point) - 1
        if idx < 0:
            return self.values[0]
        return self.values[idx]


def _detect_scope(
    vcd: VCDVCD,
    ports: list[PortSpec],
    requested_scope: str | None,
) -> tuple[str, dict[str, str]]:
    port_names = {port.name for port in ports}
    per_port_candidates: dict[str, list[tuple[str, str]]] = {name: [] for name in port_names}
    scope_coverage: dict[str, set[str]] = {}

    for signal_data in vcd.data.values():
        for ref in signal_data.references:
            scope, base = _split_reference(ref)
            if base not in per_port_candidates:
                continue
            per_port_candidates[base].append((scope, ref))
            scope_coverage.setdefault(scope, set()).add(base)

    if requested_scope is not None:
        if requested_scope not in scope_coverage:
            raise ValueError(f"Requested scope '{requested_scope}' not found in VCD")
        if scope_coverage[requested_scope] != port_names:
            missing = sorted(port_names - scope_coverage[requested_scope])
            raise ValueError(
                f"Requested scope '{requested_scope}' missing ports: {', '.join(missing)}"
            )
        chosen_scope = requested_scope
    else:
        full_scopes = [scope for scope, covered in scope_coverage.items() if covered == port_names]
        if not full_scopes:
            missing_ports = sorted(name for name, cands in per_port_candidates.items() if not cands)
            raise ValueError(
                "Cannot find a VCD scope that contains all DUT ports. "
                f"Ports without candidates: {', '.join(missing_ports)}"
            )
        full_scopes.sort(key=lambda item: (-item.count("."), len(item), item))
        chosen_scope = full_scopes[0]

    port_refs: dict[str, str] = {}
    for port in ports:
        candidates = [ref for scope, ref in per_port_candidates[port.name] if scope == chosen_scope]
        if not candidates:
            raise ValueError(
                f"Unable to map port '{port.name}' into VCD scope '{chosen_scope}'"
            )
        width_matched: list[str] = []
        for ref in candidates:
            try:
                if port.width > 0 and int(vcd[ref].size) == port.width:
                    width_matched.append(ref)
            except KeyError:
                continue
        filtered = width_matched if width_matched else candidates
        filtered.sort(key=lambda item: (-item.count("["), len(item)))
        port_refs[port.name] = filtered[0]
    return chosen_scope, port_refs


def _extract_edges(clock_tv: list[tuple[int, str]], edge: str) -> list[int]:
    last_value: str | None = None
    edges: list[int] = []
    for time_point, raw_value in clock_tv:
        value = _normalize_bits(raw_value, 1)
        if last_value is not None:
            if edge == "posedge" and last_value == "0" and value == "1":
                edges.append(int(time_point))
            elif edge == "negedge" and last_value == "1" and value == "0":
                edges.append(int(time_point))
        last_value = value
    return edges


def _format_timescale(timescale_dict: dict[str, Decimal | str]) -> str:
    magnitude = timescale_dict.get("magnitude", Decimal(1))
    unit = str(timescale_dict.get("unit", "ns"))
    if isinstance(magnitude, Decimal) and magnitude == int(magnitude):
        magnitude_text = str(int(magnitude))
    else:
        magnitude_text = str(magnitude)
    return f"{magnitude_text}{unit}"


def build_ir_from_vcd(
    vcd_path: Path,
    ports: list[PortSpec],
    top_module: str,
    clock_name: str,
    clock_edge: str = "posedge",
    scope: str | None = None,
) -> WaveIR:
    if clock_edge not in {"posedge", "negedge"}:
        raise ValueError("clock_edge must be 'posedge' or 'negedge'")
    clock_port = next((port for port in ports if port.name == clock_name), None)
    if clock_port is None:
        raise ValueError(f"Clock port '{clock_name}' is not a port of top module '{top_module}'")
    if clock_port.direction not in {"input", "inout"}:
        raise ValueError(f"Clock port '{clock_name}' direction must be input/inout")

    vcd = VCDVCD(str(vcd_path), store_tvs=True)
    chosen_scope, port_refs = _detect_scope(vcd=vcd, ports=ports, requested_scope=scope)

    resolved_ports: list[PortSpec] = []
    for port in ports:
        resolved_width = int(vcd[port_refs[port.name]].size)
        resolved_ports.append(
            PortSpec(
                name=port.name,
                direction=port.direction,
                width=resolved_width if resolved_width > 0 else max(port.width, 1),
            )
        )

    samplers: dict[str, _TVSampler] = {}
    for port in resolved_ports:
        tv = vcd[port_refs[port.name]].tv
        samplers[port.name] = _TVSampler.from_tv(tv=tv, width=port.width)

    clock_edges = _extract_edges(vcd[port_refs[clock_name]].tv, clock_edge)
    if not clock_edges:
        raise ValueError(f"No {clock_edge} events found on clock '{clock_name}'")

    input_ports = [
        port
        for port in resolved_ports
        if port.direction in {"input", "inout"} and port.name != clock_name
    ]
    output_ports = [port for port in resolved_ports if port.direction in {"output", "inout"}]
    cycles: list[CycleVector] = []
    for idx, edge_time in enumerate(clock_edges):
        inputs = {port.name: samplers[port.name].at(edge_time) for port in input_ports}
        outputs = {port.name: samplers[port.name].at(edge_time) for port in output_ports}
        cycles.append(
            CycleVector(
                index=idx,
                time=int(edge_time),
                inputs=inputs,
                outputs=outputs,
            )
        )

    return WaveIR(
        schema_version=2,
        top_module=top_module,
        scope=chosen_scope,
        timescale=_format_timescale(vcd.get_timescale()),
        clock_name=clock_name,
        clock_edge=clock_edge,
        ports=resolved_ports,
        cycles=cycles,
    )
