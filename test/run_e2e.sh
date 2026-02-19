#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/test/out"

mkdir -p "${OUT_DIR}"
rm -rf "${OUT_DIR}/ref_obj" "${OUT_DIR}/generated_tb_verify" "${OUT_DIR}/from_ir_obj"

echo "[e2e] 1/3 Generate reference VCD from handwritten TB"
verilator --binary --timing --trace --Wno-fatal \
  "${ROOT_DIR}/test/data/reference_wave_tb.sv" \
  "${ROOT_DIR}/test/data/sample_dut.sv" \
  --top-module reference_wave_tb \
  --Mdir "${OUT_DIR}/ref_obj" \
  >"${OUT_DIR}/ref_build.log" 2>&1
"${OUT_DIR}/ref_obj/Vreference_wave_tb" >"${OUT_DIR}/ref_run.log" 2>&1

echo "[e2e] 2/3 Build generated TB and verify by external Verilator"
"${ROOT_DIR}/.venv/bin/python" -m wave2tb_generator.cli vcd-to-tb \
  --vcd "${OUT_DIR}/reference.vcd" \
  --rtl "${ROOT_DIR}/test/data/sample_dut.sv" \
  --top-module sample_dut \
  --clock clk \
  --ir-out "${OUT_DIR}/generated.ir.json" \
  --tb-out "${OUT_DIR}/generated_tb.sv" \
  --tb-module generated_tb \
  --tb-dumpfile "${OUT_DIR}/generated_tb.vcd" \
  --verify-with-verilator \
  --verify-out-dir "${OUT_DIR}/generated_tb_verify" \
  --verify-vcd-out "${OUT_DIR}/generated_tb_verify.vcd" \
  >"${OUT_DIR}/wave2tb.log" 2>&1

grep -q "Verification PASS" "${OUT_DIR}/wave2tb.log"

echo "[e2e] 3/3 Replay from delta IR and run generated TB"
"${ROOT_DIR}/.venv/bin/python" -m wave2tb_generator.cli ir-to-tb \
  --ir "${OUT_DIR}/generated.ir.json" \
  --tb-out "${OUT_DIR}/from_ir_tb.sv" \
  --tb-module from_ir_tb \
  --tb-dumpfile "${OUT_DIR}/from_ir_tb.vcd" \
  >"${OUT_DIR}/from_ir.log" 2>&1
verilator --binary --timing --trace --Wno-fatal \
  "${OUT_DIR}/from_ir_tb.sv" \
  "${ROOT_DIR}/test/data/sample_dut.sv" \
  --top-module from_ir_tb \
  --Mdir "${OUT_DIR}/from_ir_obj" \
  >"${OUT_DIR}/from_ir_build.log" 2>&1
"${OUT_DIR}/from_ir_obj/Vfrom_ir_tb" >"${OUT_DIR}/from_ir_run.log" 2>&1
grep -q "PASS:" "${OUT_DIR}/from_ir_run.log"

echo "[e2e] PASS"
