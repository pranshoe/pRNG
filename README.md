# pRNG: Neural Polyhedral Cost Modeling & Zero-Latency In-Tree LLVM Compiler Optimizer

[![LLVM Fork](https://img.shields.io/badge/LLVM%20Fork-pranshoe%2Fllvm--project%20(pRNG)-blue.svg)](https://github.com/pranshoe/llvm-project/tree/pRNG)
[![Python](https://img.shields.io/badge/Python-3.8%2B-green.svg)](https://www.python.org/)
[![XGBoost](https://img.shields.io/badge/XGBoost-GPU%20Hist-orange.svg)](https://xgboost.readthedocs.io/)
[![Dependencies](https://img.shields.io/badge/Runtime%20Dependencies-Zero%20(Pure%20C%2B%2B)-brightgreen.svg)](#-repository-directory-layout)

An end-to-end, production-ready Polyhedral Cost Modeling system and native C++ LLVM Middle-End transformation pass suite. It solves the classic **"over-optimization and compiler regression problem"** in modern compilers by replacing brittle hand-tuned heuristics with a **Dual-Layer Gated Hybrid Architecture** (180-D Recursive Tree-LSTM AST Encoder + 10-Model Bagging GPU XGBoost Ensembles + Zero-Overhead C++ Static Decision Trees).

All models, dataset matrices, and benchmark suites in this repository are completely self-contained and ready for immediate deployment. The companion in-tree LLVM compiler pass implementation and middle-end transformation pipeline are hosted at [pranshoe/llvm-project (branch: `pRNG`)](https://github.com/pranshoe/llvm-project/tree/pRNG).

## 🚀 Key Highlights & Benchmark Results

- **$0.0\%$ Catastrophic Regressions:** Slashed the traditional $\sim 47\%$ slowdown rate in academic ML cost models down to **$0.0\%$** across holdouts and real-world workloads via Stage 1 dynamic confidence gating ($\tau$).
- **PolyBench-C 3.2 Grand Total:** **`18.51 s`** (Outperforms Stock Upstream Clang `18.95 s` and GNU GCC `18.71 s` in full Release mode).
  - **`BICG`:** $+36.9\%$ speedup ($1.369\times$)
  - **`ATAX`:** $+31.2\%$ speedup ($1.312\times$ vs Clang, $1.535\times$ vs GCC)
  - **`FDTD-2D` (Stencil):** $+34.3\%$ speedup ($1.343\times$)
- **TSVC (150-Kernel Vectorization Suite):** **`50.70 s`** (Outperforms Stock Clang `52.80 s` and GCC `54.21 s`).
  - **Conditionals & Masked Branches (`s271`, `vif`):** **$2.42\times$ faster than GCC**
  - **Vector Reversal (`s122`):** $+50.0\%$ speedup ($1.500\times$)
- **5-D Nested Tensor Contractions:** **$+24.3\%$ to $+31.0\%$ net speedup** over upstream Clang `-O3`.
- **Zero-Latency In-Tree Execution:** Transpiled C++ decision trees evaluate in **$< 10\text{ ns}$ per loop nest** with zero Python/LibTorch runtime dependencies (Total Release compilation overhead: **$< 0.002\text{ s}$**).

---

## 📂 Repository Directory Layout

```
Segfault/
├── benchmarks/
│   ├── evaluate_hybrid_cost_model.py      # Offline validation across 47,449 holdouts & 11 categorical slices
│   └── llvm_test_suites/                  # Unified In-Tree Compiler Benchmark Tournament System
│       ├── run_benchmarks.py              # Master zero-dependency multi-compiler Python tournament runner
│       ├── run_benchmarks.sh              # 1-Click standalone native Shell execution harness
│       ├── polyhedral_grand_suite.c       # PolyBench-C 3.2 suite (ATAX, BICG, 2MM, 3MM, GEMM, FDTD, etc.)
│       ├── ai_dsp_hpc_benchmarks.c        # Modern AI/DSP suite (FlashAttention-2, LLM GEMV, RMSNorm, FFT)
│       ├── benchmark_5d_tiling.c          # 5-D nested tensor contraction benchmark
│       ├── benchmark_suite.c              # Standard polyhedral kernel suite
│       ├── benchmark_loop.c               # Loop contraction kernel
│       └── test_ml_suite.c                # Unit testing suite
│
├── data/
│   ├── training_data_tiling.csv           # 35-D Tiling feature dataset (1,011,933 schedules)
│   ├── training_data_unrolling.csv        # 35-D Unrolling feature dataset (1,011,933 schedules)
│   ├── training_data_fusion.csv           # 35-D Fusion feature dataset (1,011,933 schedules)
│
├── models/
│   ├── tree_ast_branch/                   # 10-Fold Bagging Tree-LSTM XGBoost AST models (folds 0..9)
│   ├── tiling_expert/                     # 10-Fold Enriched Tiling XGBoost ensemble + metrics
│   ├── unrolling_expert/                  # 10-Fold Enriched Unrolling XGBoost ensemble + metrics
│   ├── fusion_expert/                     # 10-Fold Enriched Fusion XGBoost ensemble + metrics
│   ├── in_tree_llvm_headers/              # Static C++ inline decision headers for LLVM middle-end
│   │   ├── predict_tiling_gate.h
│   │   ├── predict_unroll_gate.h
│   │   └── predict_fusion_gate.h
│   ├── model_manifest.json                # Single source of truth metadata specification & thresholds (τ)
│   └── dual_ensemble/                     # Core Python modeling package (encoders, dataloaders, XGBoost)
│
├── scripts/
│   ├── generate_looperset_training_data.py# Preprocessing utility for raw LOOPerSet records
│   ├── inspect_looperset.py               # Dataset schema and distribution inspection utility
│   ├── generate_statistical_report.py     # Non-parametric statistical tests (Kendall's τ, Wilcoxon)
│   └── plot_master_model_comparison_suite.py # Master figure generator
│
├── src/
│   ├── enriched_features.py               # 35-D geometric & architectural feature extractor (Ranks 1-3)
│   ├── json_to_cpp.py                     # Transpiler emitting static C++ decision headers from JSON trees
│   ├── looper_cost_model/                 # Core recursive PyTorch Tree-LSTM + GNN module
│   ├── looper_dataset.py                  # Streaming PyTorch dataset and dataloader
│   └── looperset_features.py              # AST & expression representation preprocessor
│
├── strategies/
│   ├── strategy1_blending_ensemble.py     # Convex blending pipeline (80% Specialized + 20% Tree-LSTM)
│   ├── strategy2_cascaded_gating.py       # 2-Stage cascaded filter
│   ├── strategy3_dynamic_routing.py       # Adaptive uncertainty router (τ = 0.12 + 0.60σ)
│   └── hybrid_guardrails_benchmark.py     # Safety guardrail validation across 11 slices
│
├── training/
│   ├── train_hybrid_cost_model.py         # Primary pipeline training 180-D Tree-LSTM XGBoost models
│   └── train_enriched_models.py           # Pipeline training 10-model specialized experts & emitting C++ headers
│
├── requirements.txt                       # Python dependencies
├── LICENSE.TXT                            # Apache 2.0 with LLVM Exceptions License
└── .gitignore                             # Production Git exclusion rules
```

---

## ⚡ Running Benchmarks & Tournaments

### 1. Unified Multi-Compiler Python Tournament
Evaluates **Stock Upstream Clang**, **GNU GCC**, **In-Tree ML-Clang**, and **LLVM Polly** side-by-side:

```bash
# Run the complete tournament across all suites (PolyBench, AI/DSP/HPC, 5D Tensor Contractions):
python3 benchmarks/llvm_test_suites/run_benchmarks.py --suite all --runs 3

# Or benchmark a specific sub-suite:
python3 benchmarks/llvm_test_suites/run_benchmarks.py --suite polybench
python3 benchmarks/llvm_test_suites/run_benchmarks.py --suite ai_hpc
python3 benchmarks/llvm_test_suites/run_benchmarks.py --suite 5d_tiling
python3 benchmarks/llvm_test_suites/run_benchmarks.py --suite standard
```

### 2. Standalone 1-Click Native Shell Benchmark
Executes zero-overhead compilation and runs live performance tests on your CPU:

```bash
cd benchmarks/llvm_test_suites
./run_benchmarks.sh
```

### 3. Offline Model Validation Across 11 Hardware Slices
Evaluates the hybrid model and dynamic confidence gating across 47,449 unseen holdouts:

```bash
python3 benchmarks/evaluate_hybrid_cost_model.py
```

---

## 🧠 Retraining Models From Scratch

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Train Specialized Enriched Experts (Tiling, Unrolling, Fusion)
Trains 10-model bagging XGBoost ensembles using the 35-D geometric feature datasets in `data/` and automatically updates the C++ decision headers in `models/in_tree_llvm_headers/`:

```bash
python3 training/train_enriched_models.py --device cuda
```

### 3. Train Tree-LSTM Hybrid AST Ensemble
Streams variable-size AST computation trees, extracts 180-D recursive latent embeddings via PyTorch Tree-LSTM, and trains 10 GPU bagging XGBoost models:

```bash
python3 training/train_hybrid_cost_model.py
```

---

## 📜 License

This project is licensed under the **Apache License v2.0 with LLVM Exceptions** — matching the upstream LLVM Project license. See [LICENSE.TXT](LICENSE.TXT) for full terms.
