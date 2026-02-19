from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from .models import WaveIR
from .vcd_ir import build_ir_from_vcd


@dataclass(frozen=True)
class VerificationArtifacts:
    build_log: Path
    run_log: Path
    obj_dir: Path


def run_with_verilator(
    tb_path: Path,
    rtl_files: list[Path],
    tb_top_module: str,
    out_dir: Path,
    include_dirs: list[Path] | None = None,
    defines: list[str] | None = None,
) -> VerificationArtifacts:
    include_dirs = include_dirs or []
    defines = defines or []
    if shutil.which("verilator") is None:
        raise RuntimeError(
            "Verification requires external Verilator, but 'verilator' is not found in PATH."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    obj_dir = out_dir / "obj_dir"
    build_log = out_dir / "verilator_build.log"
    run_log = out_dir / "verilator_run.log"

    cmd = ["verilator", "--binary", "--timing", "--trace", "--Wno-fatal"]
    for include_dir in include_dirs:
        cmd.append(f"-I{include_dir}")
    for define in defines:
        cmd.append(f"-D{define}")
    cmd.append(str(tb_path))
    cmd.extend(str(path) for path in rtl_files)
    cmd.extend(["--top-module", tb_top_module, "--Mdir", str(obj_dir)])

    with build_log.open("w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=False, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Verilator build failed during verification. "
            f"See log: {build_log}"
        )

    executable = obj_dir / f"V{tb_top_module}"
    if not executable.exists():
        raise RuntimeError(
            "Verification executable not found after Verilator build. "
            f"Expected: {executable}"
        )

    with run_log.open("w", encoding="utf-8") as f:
        proc = subprocess.run([str(executable)], stdout=f, stderr=subprocess.STDOUT, check=False, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Generated testbench failed during verification run. "
            f"See log: {run_log}"
        )
    return VerificationArtifacts(build_log=build_log, run_log=run_log, obj_dir=obj_dir)


def compare_waveforms_by_cycle(
    reference_ir: WaveIR,
    generated_vcd: Path,
    top_module: str,
    clock_name: str,
    clock_edge: str,
    max_errors: int = 20,
) -> tuple[WaveIR, list[str]]:
    candidate_ir = build_ir_from_vcd(
        vcd_path=generated_vcd,
        ports=reference_ir.ports,
        top_module=top_module,
        clock_name=clock_name,
        clock_edge=clock_edge,
        scope=None,
    )

    errors: list[str] = []
    if len(reference_ir.cycles) != len(candidate_ir.cycles):
        errors.append(
            f"Cycle count mismatch: reference={len(reference_ir.cycles)}, "
            f"generated={len(candidate_ir.cycles)}"
        )

    total = min(len(reference_ir.cycles), len(candidate_ir.cycles))
    for idx in range(total):
        ref_cycle = reference_ir.cycles[idx]
        cand_cycle = candidate_ir.cycles[idx]

        for signal in sorted(set(ref_cycle.inputs.keys()) | set(cand_cycle.inputs.keys())):
            ref_value = ref_cycle.inputs.get(signal)
            cand_value = cand_cycle.inputs.get(signal)
            if ref_value != cand_value:
                errors.append(
                    f"Cycle {idx} input '{signal}' mismatch: reference={ref_value}, generated={cand_value}"
                )
                if len(errors) >= max_errors:
                    return candidate_ir, errors

        for signal in sorted(set(ref_cycle.outputs.keys()) | set(cand_cycle.outputs.keys())):
            ref_value = ref_cycle.outputs.get(signal)
            cand_value = cand_cycle.outputs.get(signal)
            if ref_value != cand_value:
                errors.append(
                    f"Cycle {idx} output '{signal}' mismatch: reference={ref_value}, generated={cand_value}"
                )
                if len(errors) >= max_errors:
                    return candidate_ir, errors

    return candidate_ir, errors
