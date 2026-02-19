from __future__ import annotations

import re

from .models import PortSpec, WaveIR


def _signal_decl(port: PortSpec) -> str:
    if port.width <= 1:
        return f"  logic {port.name};"
    return f"  logic [{port.width - 1}:0] {port.name};"


def _bits_to_sv(width: int, bits: str) -> str:
    return f"{width}'b{bits.lower()}"


def _zero_bits(width: int) -> str:
    return "0" if width <= 1 else ("0" * width)


def _sanitize_comment(text: str) -> str:
    # Keep generated comments simple and robust in SV.
    return re.sub(r"[^0-9a-zA-Z_./:+-]", "_", text)


def render_testbench(
    ir: WaveIR,
    tb_module_name: str | None = None,
    dumpfile: str | None = None,
) -> str:
    if not ir.cycles:
        raise ValueError("IR does not contain cycles")

    module_name = tb_module_name or f"{ir.top_module}_auto_tb"
    clock_port = next((port for port in ir.ports if port.name == ir.clock_name), None)
    if clock_port is None:
        raise ValueError(f"Clock '{ir.clock_name}' not found in IR ports")

    input_ports = [
        port for port in ir.ports if port.direction in {"input", "inout"} and port.name != ir.clock_name
    ]
    output_ports = [port for port in ir.ports if port.direction in {"output", "inout"}]
    input_by_name = {port.name: port for port in input_ports}
    output_by_name = {port.name: port for port in output_ports}
    idle_clock = "1'b0" if ir.clock_edge == "posedge" else "1'b1"
    edge_clock = "1'b1" if ir.clock_edge == "posedge" else "1'b0"

    lines: list[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append("")
    lines.append(f"module {module_name};")
    lines.append("")
    for port in ir.ports:
        lines.append(_signal_decl(port))
    lines.append("")
    lines.append(f"  {ir.top_module} u_dut (")
    for idx, port in enumerate(ir.ports):
        suffix = "," if idx != len(ir.ports) - 1 else ""
        lines.append(f"    .{port.name}({port.name}){suffix}")
    lines.append("  );")
    lines.append("")
    lines.append(f"  localparam int NUM_CYCLES = {len(ir.cycles)};")
    lines.append(f"  localparam logic CLK_IDLE = {idle_clock};")
    lines.append(f"  localparam logic CLK_EDGE = {edge_clock};")
    lines.append("")
    lines.append("  // Helper macro keeps per-cycle checks compact while preserving clear messages.")
    lines.append("`define CHECK_EQ(SIG, EXP, CYC, SIG_NAME) \\")
    lines.append("  if ((SIG) !== (EXP)) begin \\")
    lines.append('    $error("Cycle %0d: %s mismatch, expect=%b got=%b", (CYC), (SIG_NAME), (EXP), (SIG)); \\')
    lines.append("    $fatal(1); \\")
    lines.append("  end")
    lines.append("")

    if dumpfile:
        lines.append("  initial begin")
        lines.append(f'    $dumpfile("{dumpfile}");')
        lines.append(f"    $dumpvars(0, {module_name});")
        lines.append("  end")
        lines.append("")

    prev_inputs = {port.name: _zero_bits(port.width) for port in input_ports}

    comment_scope = _sanitize_comment(ir.scope)
    lines.append(f"  // Source scope: {comment_scope}")
    lines.append("  // step_to_edge(): apply #1 then active clock edge then #1 for sampling checks.")
    lines.append("  task automatic step_to_edge;")
    lines.append("    begin")
    lines.append("      #1;")
    lines.append(f"      {ir.clock_name} = CLK_EDGE;")
    lines.append("      #1;")
    lines.append("    end")
    lines.append("  endtask")
    lines.append("")
    lines.append("  // step_to_idle(): restore the clock to idle phase.")
    lines.append("  task automatic step_to_idle;")
    lines.append("    begin")
    lines.append("      #1;")
    lines.append(f"      {ir.clock_name} = CLK_IDLE;")
    lines.append("    end")
    lines.append("  endtask")
    lines.append("")

    lines.append("  initial begin")
    lines.append(f"    {ir.clock_name} = CLK_IDLE;")
    for port in input_ports:
        lines.append(f"    {port.name} = '0;")
    lines.append("")

    for cycle in ir.cycles:
        lines.append(f"    // cycle {cycle.index}, source_time={cycle.time} ({ir.timescale})")

        changed_inputs = [
            name
            for name in cycle.inputs
            if name in prev_inputs and cycle.inputs[name] != prev_inputs[name]
        ]
        for name in changed_inputs:
            port = input_by_name[name]
            value = cycle.inputs[name]
            lines.append(f"    {name} = {_bits_to_sv(port.width, value)};")
            prev_inputs[name] = value

        lines.append("    step_to_edge();")

        # Check all outputs every cycle to avoid missing mismatches that hold the same value.
        for name in output_by_name:
            port = output_by_name[name]
            value = cycle.outputs[name]
            expected = _bits_to_sv(port.width, value)
            lines.append(f'    `CHECK_EQ({name}, {expected}, {cycle.index}, "{name}");')

        lines.append("    step_to_idle();")
        lines.append("")

    lines.append(
        f'    $display("PASS: %0d cycles checked for {ir.top_module} from scope {ir.scope}", '
        "NUM_CYCLES);"
    )
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("")
    lines.append("`undef CHECK_EQ")
    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)
