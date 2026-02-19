# wave2tb-generator

[![PyPI version](https://img.shields.io/pypi/v/wave2tb-generator)](https://pypi.org/project/wave2tb-generator/) [![CI](https://github.com/MrAMS/wave2tb-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/MrAMS/wave2tb-generator/actions/workflows/ci.yml)

Generate a cycle-based SystemVerilog testbench from a captured VCD waveform and DUT RTL.

## Why This Project Matters

- Small design team using [Chisel](https://www.chisel-lang.org/) can keep their verification written in [svsim](https://github.com/chipsalliance/chisel/tree/main/svsim).
- Outsourced backend/PD teams usually need plain, static SV testbenches for GLS handoff.
- This tool bridges both worlds by converting captured VCD behavior into deterministic, standalone SV TB code (no Chisel runtime dependency).
- It reduces manual TB rewrite effort and lowers handoff risk between frontend and backend flows.

## Workflow

1. Parse DUT ports from RTL in Python (`tree-sitter-verilog`).
2. Read VCD waveform and auto-match one DUT scope.
3. Build a JSON IR containing per-cycle input stimulus and expected outputs.
4. Emit a standalone SystemVerilog testbench that:
   - drives the same per-cycle stimulus (only emits changed input assignments),
   - steps the DUT clock,
   - checks expected outputs every cycle.

## Install

For end users (PyPI release):

```bash
pip install wave2tb-generator
wave2tb --help
```

For development and testing (`uv` workflow):

```bash
uv sync
uv run wave2tb --help
```

## CLI

The examples below target end users (`pip` install). For development and testing (`uv` workflow), prepend `uv run` to each command.

Generate testbench in one step from VCD + RTL:

```bash
wave2tb vcd-to-tb \
  --vcd test/out/reference.vcd \
  --rtl test/data/sample_dut.sv \
  --top-module sample_dut \
  --clock clk \
  --tb-out test/out/generated_tb.sv \
  --ir-out test/out/generated.ir.json
```

Generate IR and testbench (legacy two-output command):

```bash
wave2tb from-vcd \
  --vcd test/out/reference.vcd \
  --rtl test/data/sample_dut.sv \
  --top-module sample_dut \
  --clock clk \
  --ir-out test/out/generated.ir.json \
  --tb-out test/out/generated_tb.sv
```

Generate testbench from an existing IR:

```bash
wave2tb ir-to-tb \
  --ir test/out/generated.ir.json \
  --tb-out test/out/generated_tb.sv
```

Optional external equivalence verification:

```bash
wave2tb vcd-to-tb \
  --vcd test/out/reference.vcd \
  --rtl test/data/sample_dut.sv \
  --top-module sample_dut \
  --clock clk \
  --tb-out test/out/generated_tb.sv \
  --verify-with-verilator
```

## End-to-End Test

Run:

```bash
bash test/run_e2e.sh
```

Note: the optional `--verify-with-verilator` flow and `test/run_e2e.sh` require external Verilator.

## Generated Output Examples

### `generated.ir.json` (delta IR)

`generated.ir.json` uses `encoding: "delta"` and is the intermediate format consumed by the toolchain. Core rules:

- Top-level metadata includes `schema_version`, `encoding`, `top_module`, `scope`, `timescale`, `clock_name`, and `clock_edge`.
- The `ports` array stores the DUT port list; each item has `direction`, `name`, and `width`.
- The `cycles` array is time-ordered; each entry has `index`, `time`, `inputs`, and `outputs`.
- `inputs` / `outputs` only store signals that changed relative to the previous cycle; an empty object means no changes in that direction for this cycle.
- When loading IR, the tool performs carry-forward expansion to reconstruct full per-cycle vectors before TB generation.

### `generated_tb.sv` (generated SystemVerilog TB)

Below is the key excerpt copied from the real test artifact `test/out/generated_tb.sv`: the statements inside `initial begin ... end`.

```systemverilog
clk = CLK_IDLE;
rst_n = '0;
clear = '0;
en = '0;
din = '0;
mode = '0;

// cycle 0, source_time=5 (1ps)
step_to_edge();
`CHECK_EQ(accum, 6'b000000, 0, "accum");
`CHECK_EQ(dout, 4'b0000, 0, "dout");
`CHECK_EQ(valid, 1'b0, 0, "valid");
`CHECK_EQ(parity, 1'b0, 0, "parity");
step_to_idle();

// cycle 1, source_time=15 (1ps)
rst_n = 1'b1;
en = 1'b1;
din = 4'b0011;
step_to_edge();
`CHECK_EQ(accum, 6'b000011, 1, "accum");
`CHECK_EQ(dout, 4'b0011, 1, "dout");
`CHECK_EQ(valid, 1'b1, 1, "valid");
`CHECK_EQ(parity, 1'b1, 1, "parity");
step_to_idle();

$display("PASS: %0d cycles checked for sample_dut from scope reference_wave_tb.u_dut", NUM_CYCLES);
$finish;
```
