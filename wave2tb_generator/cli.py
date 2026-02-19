from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
import sys

from .models import load_ir, save_ir
from .rtl_parser import parse_top_ports
from .tb_writer import render_testbench
from .vcd_ir import build_ir_from_vcd
from .verifier import compare_waveforms_by_cycle, run_with_verilator


def _add_common_frontend_args(parser: ArgumentParser) -> None:
    parser.add_argument("--vcd", required=True, type=Path, help="Input VCD file")
    parser.add_argument(
        "--rtl",
        required=True,
        nargs="+",
        type=Path,
        help="RTL files containing the DUT top module and dependencies",
    )
    parser.add_argument("--top-module", required=True, help="DUT top module name")
    parser.add_argument("--clock", required=True, help="Clock port name in DUT")
    parser.add_argument(
        "--clock-edge",
        default="posedge",
        choices=["posedge", "negedge"],
        help="Clock edge used to sample expected outputs",
    )
    parser.add_argument(
        "--vcd-scope",
        default=None,
        help="Optional explicit VCD scope (for example: tb.u_dut)",
    )
    parser.add_argument(
        "-I",
        "--include-dir",
        action="append",
        type=Path,
        default=[],
        help="Include directory for RTL parser (`include` search path)",
    )
    parser.add_argument(
        "-D",
        "--define",
        action="append",
        default=[],
        help="Macro define for RTL parser (for example: WIDTH=32)",
    )


def _add_tb_emit_args(parser: ArgumentParser, *, require_ir_out: bool) -> None:
    parser.add_argument(
        "--ir-out",
        required=require_ir_out,
        type=Path,
        default=None,
        help="Optional IR JSON path (required for from-vcd, optional for vcd-to-tb)",
    )
    parser.add_argument("--tb-out", required=True, type=Path, help="Output testbench .sv path")
    parser.add_argument("--tb-module", default=None, help="Generated testbench module name")
    parser.add_argument(
        "--tb-dumpfile",
        default=None,
        type=Path,
        help="Optional dumpfile path in generated TB (auto-set when verification is enabled)",
    )


def _add_verify_args(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--verify-with-verilator",
        action="store_true",
        help=(
            "Optionally run generated TB with external Verilator, dump a new VCD, "
            "and compare against input VCD at cycle boundaries"
        ),
    )
    parser.add_argument(
        "--verify-out-dir",
        type=Path,
        default=None,
        help="Directory for Verilator verification artifacts (obj_dir + logs)",
    )
    parser.add_argument(
        "--verify-vcd-out",
        type=Path,
        default=None,
        help="Output VCD path produced during verification run",
    )
    parser.add_argument(
        "--verify-max-errors",
        type=int,
        default=20,
        help="Maximum mismatch lines printed by cycle-level waveform comparison",
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="wave2tb",
        description=(
            "Generate cycle-based SystemVerilog testbench from VCD + DUT RTL. "
            "The tool first builds an IR JSON file, then emits a concrete SV testbench."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    from_vcd = subparsers.add_parser("from-vcd", help="Generate IR and testbench from VCD + RTL")
    _add_common_frontend_args(from_vcd)
    _add_tb_emit_args(from_vcd, require_ir_out=True)
    _add_verify_args(from_vcd)

    vcd_to_tb = subparsers.add_parser(
        "vcd-to-tb",
        help="One-step command: generate TB directly from VCD + RTL (IR optional)",
    )
    _add_common_frontend_args(vcd_to_tb)
    _add_tb_emit_args(vcd_to_tb, require_ir_out=False)
    _add_verify_args(vcd_to_tb)

    ir_to_tb = subparsers.add_parser("ir-to-tb", help="Generate testbench from existing IR JSON")
    ir_to_tb.add_argument("--ir", required=True, type=Path, help="Input IR JSON")
    ir_to_tb.add_argument("--tb-out", required=True, type=Path, help="Output testbench .sv path")
    ir_to_tb.add_argument("--tb-module", default=None, help="Generated testbench module name")
    ir_to_tb.add_argument("--tb-dumpfile", default=None, type=Path, help="Optional dumpfile path in generated TB")

    return parser


def _build_ir(args: Namespace):
    ports = parse_top_ports(
        rtl_files=args.rtl,
        top_module=args.top_module,
        include_dirs=args.include_dir,
        defines=args.define,
    )
    ir = build_ir_from_vcd(
        vcd_path=args.vcd,
        ports=ports,
        top_module=args.top_module,
        clock_name=args.clock,
        clock_edge=args.clock_edge,
        scope=args.vcd_scope,
    )
    return ir


def _render_and_write_tb(
    ir,
    tb_out: Path,
    tb_module_name: str | None,
    tb_dumpfile: Path | None,
) -> str:
    dumpfile_text = str(tb_dumpfile) if tb_dumpfile is not None else None
    tb_code = render_testbench(
        ir=ir,
        tb_module_name=tb_module_name,
        dumpfile=dumpfile_text,
    )
    tb_out.parent.mkdir(parents=True, exist_ok=True)
    tb_out.write_text(tb_code, encoding="utf-8")
    return tb_code


def _run_verify(args: Namespace, ir, tb_module_name: str, tb_dumpfile: Path) -> None:
    verify_out_dir = args.verify_out_dir or (args.tb_out.parent / f"{tb_module_name}_verify")
    tb_dumpfile.parent.mkdir(parents=True, exist_ok=True)
    artifacts = run_with_verilator(
        tb_path=args.tb_out,
        rtl_files=args.rtl,
        tb_top_module=tb_module_name,
        out_dir=verify_out_dir,
        include_dirs=args.include_dir,
        defines=args.define,
    )
    if not tb_dumpfile.exists():
        raise RuntimeError(
            "Verification run finished but generated VCD was not found: "
            f"{tb_dumpfile}"
        )
    compared_ir, mismatches = compare_waveforms_by_cycle(
        reference_ir=ir,
        generated_vcd=tb_dumpfile,
        top_module=args.top_module,
        clock_name=args.clock,
        clock_edge=args.clock_edge,
        max_errors=max(args.verify_max_errors, 1),
    )
    if mismatches:
        details = "\n".join(f"  - {item}" for item in mismatches)
        raise RuntimeError(
            "Verification failed: generated VCD is not cycle-equivalent to input VCD.\n"
            f"{details}\n"
            f"Build log: {artifacts.build_log}\n"
            f"Run log: {artifacts.run_log}"
        )
    print(f"[wave2tb] Verification PASS (scope={compared_ir.scope})")
    print(f"[wave2tb] Verification build log: {artifacts.build_log}")
    print(f"[wave2tb] Verification run log: {artifacts.run_log}")
    print(f"[wave2tb] Verification generated VCD: {tb_dumpfile}")


def _run_vcd_flow(args: Namespace) -> int:
    ir = _build_ir(args)

    if args.ir_out is not None:
        save_ir(args.ir_out, ir)
        print(f"[wave2tb] IR written: {args.ir_out}")

    tb_module_name = args.tb_module or f"{args.top_module}_auto_tb"
    verify_enabled = bool(args.verify_with_verilator)
    verify_vcd_path = None
    if verify_enabled:
        verify_out_dir = args.verify_out_dir or (args.tb_out.parent / f"{tb_module_name}_verify")
        verify_vcd_path = args.verify_vcd_out or (verify_out_dir / "generated_verify.vcd")

    tb_dumpfile = args.tb_dumpfile or verify_vcd_path
    _render_and_write_tb(
        ir=ir,
        tb_out=args.tb_out,
        tb_module_name=tb_module_name,
        tb_dumpfile=tb_dumpfile,
    )

    print(f"[wave2tb] Testbench written: {args.tb_out}")
    print(f"[wave2tb] Parsed scope: {ir.scope}")
    print(f"[wave2tb] Cycles: {len(ir.cycles)}")

    if verify_enabled:
        assert tb_dumpfile is not None
        _run_verify(
            args=args,
            ir=ir,
            tb_module_name=tb_module_name,
            tb_dumpfile=tb_dumpfile,
        )
    return 0


def _run_ir_to_tb(args: Namespace) -> int:
    ir = load_ir(args.ir)
    tb_code = render_testbench(
        ir=ir,
        tb_module_name=args.tb_module,
        dumpfile=str(args.tb_dumpfile) if args.tb_dumpfile else None,
    )
    args.tb_out.parent.mkdir(parents=True, exist_ok=True)
    args.tb_out.write_text(tb_code, encoding="utf-8")
    print(f"[wave2tb] Testbench written: {args.tb_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "from-vcd":
        return _run_vcd_flow(args)
    if args.command == "vcd-to-tb":
        return _run_vcd_flow(args)
    if args.command == "ir-to-tb":
        return _run_ir_to_tb(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
