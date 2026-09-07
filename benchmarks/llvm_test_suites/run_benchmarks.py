#!/usr/bin/env python3
"""
run_benchmarks.py - Unified Multi-Compiler Benchmark Tournament Runner

Executes live head-to-head performance evaluations comparing:
  1. Stock Upstream Clang (-O3)
  2. Stock GNU GCC (-O3)
  3. ML-Enhanced In-Tree Clang (-O3 with ML Tiling/Unroll/Fusion passes)
  4. LLVM Polly Polyhedral Optimizer (-O3 -mllvm -polly) [optional]

Suites Supported:
  - polybench   : Polyhedral Grand Suite (ATAX, BICG, 2MM, 3MM, GEMM, FDTD-2D, MVT, SYRK, SYR2K)
  - ai_hpc      : AI / DSP / HPC Kernels (FlashAttention, LLM GEMV, RMSNorm, Conv2D, 5-Pt Stencil, FFT)
  - 5d_tiling   : 5-D Deep Nested Tensor Contractions
  - standard    : Standard Polyhedral Kernel Benchmark Suite
  - tsvc        : Vectorization & Loop Transformation Suite (150 kernels)
  - all         : Comprehensive Tournament across all suites
"""

import argparse
import glob
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Tuple, Any, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = tempfile.gettempdir()


def find_compilers() -> Dict[str, Optional[str]]:
    """Dynamically locates compiler binaries on the host system."""
    stock_clang = os.environ.get("CLANG_STOCK") or shutil.which("clang")
    stock_gcc = os.environ.get("GCC_STOCK") or shutil.which("gcc")
    
    # ML-Clang search candidates
    ml_clang = None
    if "CLANG_ML" in os.environ and os.path.isfile(os.environ["CLANG_ML"]):
        ml_clang = os.environ["CLANG_ML"]
    elif "LLVM_BUILD" in os.environ:
        cand = os.path.join(os.environ["LLVM_BUILD"], "bin", "clang")
        if os.path.isfile(cand):
            ml_clang = cand
    if not ml_clang:
        home_cand = os.path.expanduser("~/Documents/llvm-project/build/bin/clang")
        if os.path.isfile(home_cand):
            ml_clang = home_cand
        else:
            ml_clang = stock_clang

    # Polly search
    polly_avail = False
    if ml_clang:
        try:
            res = subprocess.run([ml_clang, "-O3", "-mllvm", "-polly", "-x", "c", "-c", "/dev/null", "-o", "/dev/null"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            polly_avail = (res.returncode == 0)
        except Exception:
            polly_avail = False

    return {
        "stock_clang": stock_clang,
        "stock_gcc": stock_gcc,
        "ml_clang": ml_clang,
        "polly_available": polly_avail
    }


def compile_binary(compiler: str, src_path: str, out_path: str, extra_flags: List[str] = None) -> Tuple[bool, float, str]:
    """Compiles a C source file and returns (success, compile_time_sec, stderr)."""
    flags = ["-O3", "-mavx2"]
    if extra_flags:
        flags.extend(extra_flags)
    cmd = [compiler] + flags + [src_path, "-o", out_path, "-lm"]
    
    t0 = time.perf_counter()
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        compile_time = time.perf_counter() - t0
        return (res.returncode == 0, compile_time, res.stderr)
    except Exception as e:
        return (False, 0.0, str(e))


def measure_execution_time(bin_path: str, label: str, runs: int = 3) -> Tuple[float, str]:
    """Executes a benchmark binary multiple times and returns the median runtime."""
    times = []
    last_output = ""
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            res = subprocess.run([bin_path, label], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            elapsed = time.perf_counter() - t0
            last_output = res.stdout
            
            # Attempt to parse kernel-reported elapsed time if present
            reported_time = None
            for line in res.stdout.splitlines():
                if "Total Time" in line or "Total Execution Time" in line or "Total:" in line or "Elapsed Time" in line or "TOTAL" in line:
                    parts = line.replace(":", " ").replace("s", " ").split()
                    for p in parts:
                        try:
                            val = float(p)
                            if val > 0:
                                reported_time = val
                        except ValueError:
                            pass
            if reported_time is not None:
                times.append(reported_time)
            else:
                times.append(elapsed)
        except Exception as e:
            print(f"  [!] Error executing {bin_path}: {e}")
            times.append(float("inf"))

    return (float(statistics.median(times)), last_output) if times else (float("inf"), "")


def run_c_suite_tournament(suite_name: str, src_file: str, compilers: Dict[str, Any], runs: int = 3) -> Dict[str, Any]:
    """Runs a head-to-head tournament for a C source benchmark suite."""
    src_path = os.path.join(SCRIPT_DIR, src_file)
    if not os.path.isfile(src_path):
        print(f"[!] Source file not found: {src_path}")
        return {}

    print(f"\n" + "=" * 85)
    print(f"  RUNNING SUITE: {suite_name.upper()} ({src_file})")
    print("=" * 85)

    compiler_configs = {}
    if compilers["stock_clang"]:
        compiler_configs["Stock Clang -O3"] = (compilers["stock_clang"], [])
    if compilers["stock_gcc"]:
        compiler_configs["Stock GCC -O3"] = (compilers["stock_gcc"], [])
    if compilers["ml_clang"]:
        compiler_configs["ML-Clang (In-Tree)"] = (compilers["ml_clang"], [])
    if compilers["polly_available"]:
        compiler_configs["LLVM Polly (-O3)"] = (compilers["ml_clang"], ["-mllvm", "-polly", "-mllvm", "-polly-vectorizer=stripmine"])

    results = {}
    binaries = {}

    # Compile phase
    print("[1/2] Compiling benchmark binaries...")
    for label, (comp, flags) in compiler_configs.items():
        bin_path = os.path.join(TMP_DIR, f"bench_{suite_name}_{label.replace(' ', '_').replace('-', '_').replace('(', '').replace(')', '')}_{os.getpid()}")
        binaries[label] = bin_path
        success, comp_time, err = compile_binary(comp, src_path, bin_path, flags)
        if success:
            print(f"  ✓ {label:<24} : Compiled in {comp_time:.3f}s")
            results[label] = {"compile_time": comp_time, "success": True}
        else:
            print(f"  ✗ {label:<24} : Compilation FAILED\n{err}")
            results[label] = {"compile_time": 0.0, "success": False, "error": err}

    # Execution phase
    print(f"\n[2/2] Running performance tournament ({runs} runs, median measurement)...")
    for label, bin_path in binaries.items():
        if results[label]["success"] and os.path.isfile(bin_path):
            med_time, out = measure_execution_time(bin_path, label, runs=runs)
            results[label]["runtime"] = med_time
            results[label]["output"] = out
            print(f"  ⚡ {label:<24} : {med_time:.4f}s")
            try:
                os.remove(bin_path)
            except OSError:
                pass
        else:
            results[label]["runtime"] = float("inf")

    # Calculate speedups
    baseline_clang = results.get("Stock Clang -O3", {}).get("runtime", float("inf"))
    baseline_gcc = results.get("Stock GCC -O3", {}).get("runtime", float("inf"))
    ml_time = results.get("ML-Clang (In-Tree)", {}).get("runtime", float("inf"))

    if ml_time < float("inf"):
        if baseline_clang < float("inf") and baseline_clang > 0:
            sp_clang = baseline_clang / ml_time
            results["speedup_vs_clang"] = sp_clang
        if baseline_gcc < float("inf") and baseline_gcc > 0:
            sp_gcc = baseline_gcc / ml_time
            results["speedup_vs_gcc"] = sp_gcc

    return results


def run_tsvc_tournament(compilers: Dict[str, Any], runs: int = 2) -> Dict[str, Any]:
    """Runs the TSVC vectorization tournament if TSVC directory is present."""
    tsvc_dir = os.environ.get("TSVC_DIR")
    if not tsvc_dir:
        candidates = [
            os.path.expanduser("~/haikuos/buildtools/gcc/gcc/testsuite/gcc.dg/vect/tsvc"),
            os.path.expanduser("~/tsvc"),
            os.path.join(SCRIPT_DIR, "tsvc")
        ]
        for c in candidates:
            if os.path.isdir(c):
                tsvc_dir = c
                break

    if not tsvc_dir or not os.path.isdir(tsvc_dir):
        print("\n[-] TSVC suite directory not detected. (Set $TSVC_DIR to enable TSVC evaluation).")
        return {}

    c_files = sorted(glob.glob(os.path.join(tsvc_dir, "vect-tsvc-*.c")))
    if not c_files:
        return {}

    print(f"\n" + "=" * 85)
    print(f"  RUNNING TSVC VECTORIZATION TOURNAMENT ({len(c_files)} KERNELS)")
    print("=" * 85)

    clang_stock = compilers["stock_clang"]
    gcc_stock = compilers["stock_gcc"]
    ml_clang = compilers["ml_clang"]

    totals = {"Stock Clang -O3": 0.0, "Stock GCC -O3": 0.0, "ML-Clang (In-Tree)": 0.0}
    kernel_wins = {"ML-Clang": 0, "Stock Clang": 0, "GCC": 0, "Tie": 0}

    for f in c_files:
        kname = os.path.basename(f)
        bin_stock = os.path.join(TMP_DIR, f"tsvc_stock_{os.getpid()}")
        bin_gcc = os.path.join(TMP_DIR, f"tsvc_gcc_{os.getpid()}")
        bin_ml = os.path.join(TMP_DIR, f"tsvc_ml_{os.getpid()}")

        s1, _, _ = compile_binary(clang_stock, f, bin_stock)
        s2, _, _ = compile_binary(gcc_stock, f, bin_gcc)
        s3, _, _ = compile_binary(ml_clang, f, bin_ml)

        if s1 and s2 and s3:
            t_stock, _ = measure_execution_time(bin_stock, "Stock", runs=runs)
            t_gcc, _ = measure_execution_time(bin_gcc, "GCC", runs=runs)
            t_ml, _ = measure_execution_time(bin_ml, "ML", runs=runs)

            totals["Stock Clang -O3"] += t_stock
            totals["Stock GCC -O3"] += t_gcc
            totals["ML-Clang (In-Tree)"] += t_ml

            min_t = min(t_stock, t_gcc, t_ml)
            if t_ml <= min_t * 1.01:
                kernel_wins["ML-Clang"] += 1
            elif t_stock <= min_t * 1.01:
                kernel_wins["Stock Clang"] += 1
            else:
                kernel_wins["GCC"] += 1

        for b in [bin_stock, bin_gcc, bin_ml]:
            if os.path.isfile(b):
                try:
                    os.remove(b)
                except OSError:
                    pass

    return {
        "totals": totals,
        "kernel_wins": kernel_wins,
        "num_kernels": len(c_files)
    }


def print_summary_table(all_results: Dict[str, Dict[str, Any]]):
    """Prints a consolidated multi-compiler performance scoreboard."""
    print("\n" + "=" * 90)
    print("                FINAL MULTI-COMPILER TOURNAMENT SCORECARD")
    print("=" * 90)
    print(f"{'Benchmark Suite':<30} | {'Stock Clang':<12} | {'Stock GCC':<12} | {'ML-Clang':<12} | {'vs Clang':<9} | {'vs GCC':<9}")
    print("-" * 90)

    speedups_clang = []
    speedups_gcc = []

    for suite_name, res in all_results.items():
        if not res or "Stock Clang -O3" not in res:
            continue
        t_clang = res.get("Stock Clang -O3", {}).get("runtime", 0.0)
        t_gcc = res.get("Stock GCC -O3", {}).get("runtime", 0.0)
        t_ml = res.get("ML-Clang (In-Tree)", {}).get("runtime", 0.0)

        sp_clang = (t_clang / t_ml) if (t_ml > 0 and t_clang > 0) else 1.0
        sp_gcc = (t_gcc / t_ml) if (t_ml > 0 and t_gcc > 0) else 1.0

        speedups_clang.append(sp_clang)
        speedups_gcc.append(sp_gcc)

        print(f"{suite_name:<30} | {t_clang:<10.4f}s | {t_gcc:<10.4f}s | {t_ml:<10.4f}s | {sp_clang:<7.3f}x | {sp_gcc:<7.3f}x")

    print("=" * 90)
    if speedups_clang:
        geomean_clang = float(math.exp(sum(math.log(x) for x in speedups_clang) / len(speedups_clang)))
        geomean_gcc = float(math.exp(sum(math.log(x) for x in speedups_gcc) / len(speedups_gcc)))
        print(f"{'GEOMETRIC MEAN SPEEDUP':<30} | {'-':<12} | {'-':<12} | {'-':<12} | {geomean_clang:<7.3f}x | {geomean_gcc:<7.3f}x")
        print("=" * 90)


def main():
    parser = argparse.ArgumentParser(description="Unified Multi-Compiler Benchmark Suite Runner")
    parser.add_argument("--suite", default="all", choices=["all", "polybench", "ai_hpc", "5d_tiling", "standard", "tsvc"],
                        help="Benchmark suite to execute (default: all)")
    parser.add_argument("--runs", type=int, default=3, help="Number of measurement runs per benchmark (default: 3)")
    parser.add_argument("--save-json", type=str, default=None, help="Save structured metrics JSON to path")
    args = parser.parse_args()

    compilers = find_compilers()
    print("=" * 85)
    print("          UNIFIED IN-TREE COMPILER BENCHMARK TOURNAMENT HARNESS")
    print(f"  Stock Clang : {compilers['stock_clang']}")
    print(f"  Stock GCC   : {compilers['stock_gcc']}")
    print(f"  ML-Clang    : {compilers['ml_clang']}")
    print(f"  Polly Opt   : {'Available' if compilers['polly_available'] else 'Not detected'}")
    print("=" * 85)

    all_results = {}

    suites_to_run = []
    if args.suite in ["all", "polybench"]:
        suites_to_run.append(("PolyBench Grand Suite", "polyhedral_grand_suite.c"))
    if args.suite in ["all", "ai_hpc"]:
        suites_to_run.append(("AI / DSP / HPC Kernels", "ai_dsp_hpc_benchmarks.c"))
    if args.suite in ["all", "5d_tiling"]:
        suites_to_run.append(("5-D Nested Tensor Contraction", "benchmark_5d_tiling.c"))
    if args.suite in ["all", "standard"]:
        suites_to_run.append(("Standard Suite", "benchmark_suite.c"))

    for suite_label, src_file in suites_to_run:
        res = run_c_suite_tournament(suite_label, src_file, compilers, runs=args.runs)
        if res:
            all_results[suite_label] = res

    if args.suite in ["all", "tsvc"]:
        tsvc_res = run_tsvc_tournament(compilers, runs=args.runs)
        if tsvc_res and "totals" in tsvc_res:
            all_results["TSVC Suite"] = {
                "Stock Clang -O3": {"runtime": tsvc_res["totals"]["Stock Clang -O3"]},
                "Stock GCC -O3": {"runtime": tsvc_res["totals"]["Stock GCC -O3"]},
                "ML-Clang (In-Tree)": {"runtime": tsvc_res["totals"]["ML-Clang (In-Tree)"]}
            }

    print_summary_table(all_results)

    if args.save_json:
        # Strip large raw outputs before saving
        clean_res = {}
        for k, v in all_results.items():
            clean_res[k] = {ik: iv for ik, iv in v.items() if ik != "output"}
        with open(args.save_json, "w") as f:
            json.dump(clean_res, f, indent=2)
        print(f"\n[✓] Results saved to {args.save_json}")


if __name__ == "__main__":
    main()
