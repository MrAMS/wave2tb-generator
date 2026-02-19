from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class PortSpec:
    name: str
    direction: str
    width: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "direction": self.direction,
            "width": self.width,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PortSpec":
        return cls(
            name=str(data["name"]),
            direction=str(data["direction"]),
            width=int(data["width"]),
        )


@dataclass(frozen=True)
class CycleVector:
    index: int
    time: int
    inputs: dict[str, str]
    outputs: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "time": self.time,
            "inputs": self.inputs,
            "outputs": self.outputs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CycleVector":
        return cls(
            index=int(data["index"]),
            time=int(data["time"]),
            inputs={str(k): str(v) for k, v in dict(data["inputs"]).items()},
            outputs={str(k): str(v) for k, v in dict(data["outputs"]).items()},
        )


@dataclass(frozen=True)
class WaveIR:
    schema_version: int
    top_module: str
    scope: str
    timescale: str
    clock_name: str
    clock_edge: str
    ports: list[PortSpec]
    cycles: list[CycleVector]

    def to_dict(self) -> dict[str, object]:
        # Backward-compatible full serialization.
        return {
            "schema_version": max(self.schema_version, 1),
            "encoding": "full",
            "top_module": self.top_module,
            "scope": self.scope,
            "timescale": self.timescale,
            "clock_name": self.clock_name,
            "clock_edge": self.clock_edge,
            "ports": [port.to_dict() for port in self.ports],
            "cycles": [cycle.to_dict() for cycle in self.cycles],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "WaveIR":
        schema_version = int(data.get("schema_version", 1))
        encoding = str(data.get("encoding", "full"))
        top_module = str(data["top_module"])
        scope = str(data["scope"])
        timescale = str(data["timescale"])
        clock_name = str(data["clock_name"])
        clock_edge = str(data["clock_edge"])
        ports = [PortSpec.from_dict(item) for item in list(data["ports"])]
        cycles_raw = list(data["cycles"])
        cycles = _decode_cycles(
            cycles_raw=cycles_raw,
            ports=ports,
            clock_name=clock_name,
            encoding=encoding,
        )
        return cls(
            schema_version=schema_version,
            top_module=top_module,
            scope=scope,
            timescale=timescale,
            clock_name=clock_name,
            clock_edge=clock_edge,
            ports=ports,
            cycles=cycles,
        )


def _zero_bits(width: int) -> str:
    return "0" if width <= 1 else ("0" * width)


def _signal_orders(ports: list[PortSpec], clock_name: str) -> tuple[list[str], list[str], dict[str, int]]:
    input_order = [p.name for p in ports if p.direction in {"input", "inout"} and p.name != clock_name]
    output_order = [p.name for p in ports if p.direction in {"output", "inout"}]
    width_by_name = {p.name: p.width for p in ports}
    return input_order, output_order, width_by_name


def _encode_delta_cycles(ir: WaveIR) -> list[dict[str, object]]:
    input_order, output_order, width_by_name = _signal_orders(ir.ports, ir.clock_name)
    last_inputs = {name: _zero_bits(width_by_name[name]) for name in input_order}
    last_outputs = {name: _zero_bits(width_by_name[name]) for name in output_order}

    encoded: list[dict[str, object]] = []
    for cycle in ir.cycles:
        input_delta: dict[str, str] = {}
        output_delta: dict[str, str] = {}
        for name in input_order:
            value = cycle.inputs[name]
            if last_inputs[name] != value:
                input_delta[name] = value
                last_inputs[name] = value
        for name in output_order:
            value = cycle.outputs[name]
            if last_outputs[name] != value:
                output_delta[name] = value
                last_outputs[name] = value
        encoded.append(
            {
                "index": cycle.index,
                "time": cycle.time,
                "inputs": input_delta,
                "outputs": output_delta,
            }
        )
    return encoded


def _decode_cycles(
    cycles_raw: list[object],
    ports: list[PortSpec],
    clock_name: str,
    encoding: str,
) -> list[CycleVector]:
    input_order, output_order, width_by_name = _signal_orders(ports, clock_name)
    last_inputs = {name: _zero_bits(width_by_name[name]) for name in input_order}
    last_outputs = {name: _zero_bits(width_by_name[name]) for name in output_order}

    cycles: list[CycleVector] = []
    for idx, item in enumerate(cycles_raw):
        raw = dict(item)
        raw_inputs = {str(k): str(v) for k, v in dict(raw.get("inputs", {})).items()}
        raw_outputs = {str(k): str(v) for k, v in dict(raw.get("outputs", {})).items()}

        if encoding == "full":
            for name in input_order:
                if name in raw_inputs:
                    last_inputs[name] = raw_inputs[name]
            for name in output_order:
                if name in raw_outputs:
                    last_outputs[name] = raw_outputs[name]
        else:
            for name, value in raw_inputs.items():
                if name in last_inputs:
                    last_inputs[name] = value
            for name, value in raw_outputs.items():
                if name in last_outputs:
                    last_outputs[name] = value

        cycles.append(
            CycleVector(
                index=int(raw.get("index", idx)),
                time=int(raw.get("time", 0)),
                inputs={name: last_inputs[name] for name in input_order},
                outputs={name: last_outputs[name] for name in output_order},
            )
        )
    return cycles


def _ir_to_dict(ir: WaveIR) -> dict[str, object]:
    return {
        "schema_version": max(ir.schema_version, 2),
        "encoding": "delta",
        "top_module": ir.top_module,
        "scope": ir.scope,
        "timescale": ir.timescale,
        "clock_name": ir.clock_name,
        "clock_edge": ir.clock_edge,
        "ports": [port.to_dict() for port in ir.ports],
        "cycles": _encode_delta_cycles(ir),
    }


def save_ir(path: Path, ir: WaveIR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_ir_to_dict(ir=ir), f, indent=2, sort_keys=True)
        f.write("\n")


def load_ir(path: Path) -> WaveIR:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # schema_version==1 had no explicit encoding and always used full cycles.
    if "encoding" not in raw:
        raw["encoding"] = "full"
    return WaveIR.from_dict(raw)
