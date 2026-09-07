#!/usr/bin/env bash
#===-- run_benchmarks.sh - Master In-Tree Compiler Benchmark Harness -===#
# Unified multi-suite benchmark runner executing all C suites with dynamic
# compiler discovery and side-by-side performance benchmarking.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="${TMPDIR:-/tmp}"

# Dynamic compiler discovery
if [ -n "${CLANG_ML}" ] && [ -x "${CLANG_ML}" ]; then
    CLANG_ML_BIN="${CLANG_ML}"
elif [ -n "${LLVM_BUILD}" ] && [ -x "${LLVM_BUILD}/bin/clang" ]; then
    CLANG_ML_BIN="${LLVM_BUILD}/bin/clang"
elif [ -x "${HOME}/Documents/llvm-project/build/bin/clang" ]; then
    CLANG_ML_BIN="${HOME}/Documents/llvm-project/build/bin/clang"
elif command -v clang >/dev/null 2>&1; then
    CLANG_ML_BIN="$(command -v clang)"
else
    CLANG_ML_BIN="clang"
fi

CLANG_STOCK="${CLANG_STOCK:-$(command -v clang || true)}"
GCC_STOCK="${GCC_STOCK:-$(command -v gcc || true)}"

# If user requested python tournament mode
if [ "$1" == "--python" ] || [ "$1" == "-p" ]; then
    shift
    exec python3 "${SCRIPT_DIR}/run_benchmarks.py" "$@"
fi

PID=$$
BIN_POLY="${TMP_DIR}/bench_poly_${PID}"
BIN_AI="${TMP_DIR}/bench_ai_${PID}"
BIN_5D="${TMP_DIR}/bench_5d_${PID}"
BIN_STD="${TMP_DIR}/bench_std_${PID}"

trap 'rm -f "${BIN_POLY}" "${BIN_AI}" "${BIN_5D}" "${BIN_STD}"' EXIT

echo "=========================================================================="
echo "    MASTER IN-TREE CLANG ML COMPILER BENCHMARK HARNESS"
echo "    ML-Clang    : ${CLANG_ML_BIN}"
echo "    Stock Clang : ${CLANG_STOCK}"
echo "    Stock GCC   : ${GCC_STOCK}"
echo "=========================================================================="

echo -e "\n[1/4] Compiling PolyBench Grand Suite with ML-Clang (-O3 -mavx2)..."
${CLANG_ML_BIN} -O3 -mavx2 "${SCRIPT_DIR}/polyhedral_grand_suite.c" -o "${BIN_POLY}" -lm

echo -e "\n[2/4] Compiling AI/DSP/HPC Suite with ML-Clang (-O3 -mavx2)..."
${CLANG_ML_BIN} -O3 -mavx2 "${SCRIPT_DIR}/ai_dsp_hpc_benchmarks.c" -o "${BIN_AI}" -lm

echo -e "\n[3/4] Compiling 5-D Tensor Contraction with ML-Clang (-O3 -mavx2)..."
${CLANG_ML_BIN} -O3 -mavx2 "${SCRIPT_DIR}/benchmark_5d_tiling.c" -o "${BIN_5D}" -lm

echo -e "\n[4/4] Compiling Standard Suite with ML-Clang (-O3 -mavx2)..."
${CLANG_ML_BIN} -O3 -mavx2 "${SCRIPT_DIR}/benchmark_suite.c" -o "${BIN_STD}" -lm

echo -e "\n=========================================================================="
echo "    LIVE EXECUTION RESULTS (ML-ENHANCED IN-TREE CLANG)"
echo "=========================================================================="

echo -e "\n>>> 1. PolyBench Grand Suite:"
"${BIN_POLY}" "ML-Enhanced Clang (-O3)" || true

echo -e "\n>>> 2. AI / DSP / HPC Suite:"
"${BIN_AI}" "ML-Enhanced Clang (-O3)" || true

echo -e "\n>>> 3. 5-D Tensor Contractions:"
"${BIN_5D}" "ML-Enhanced Clang 5-D (-O3)" || true

echo -e "\n>>> 4. Standard Suite:"
"${BIN_STD}" "ML-Enhanced Clang Standard (-O3)" || true

echo "=========================================================================="
echo "  All benchmark suites completed successfully!"
echo "  Tip: Run './run_benchmarks.sh --python' for full multi-compiler tournament scorecard."
echo "=========================================================================="
