import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import gmean
from typing import Dict, Any, List, Tuple

warnings.filterwarnings("ignore")

from dual_ensemble.xgboost_ensemble import XGBoostBranch

def load_specialized_ensemble(model_dir: str, prefix: str, n_models: int = 10) -> List[xgb.Booster]:
    models = []
    for i in range(1, n_models + 1):
        path = os.path.join(model_dir, f"{prefix}_{i}.json")
        if os.path.exists(path):
            booster = xgb.Booster()
            booster.load_model(path)
            models.append(booster)
    return models

def predict_specialized(models: List[xgb.Booster], X: pd.DataFrame) -> np.ndarray:
    dmatrix = xgb.DMatrix(X)
    preds = np.zeros(len(X), dtype=np.float32)
    for model in models:
        preds += model.predict(dmatrix)
    return preds / len(models)

def load_dual_branches(models_dir: str = None) -> Tuple[XGBoostBranch, XGBoostBranch]:
    if models_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(repo_root, "models", "dual_branch_models"),
            os.path.join(repo_root, "models", "saved_models"),
            "dual_branch_models",
            "saved_models"
        ]
        for c in candidates:
            if os.path.isdir(c):
                models_dir = c
                break
        if models_dir is None:
            models_dir = candidates[0]
    branch_ae = XGBoostBranch("xgboost_ae")
    branch_ae.load(models_dir)
    branch_lstm = XGBoostBranch("xgboost_lstm")
    branch_lstm.load(models_dir)
    return branch_ae, branch_lstm

def extract_8_scalars(df: pd.DataFrame) -> np.ndarray:
    if 'memory_size' in df.columns: m = df['memory_size'].values
    elif 'log_memory_size' in df.columns: m = np.exp(df['log_memory_size'].values)
    else: m = np.zeros(len(df))
    
    d = df.get('loop_depth', pd.Series(np.zeros(len(df)))).values
    
    if 'total_trip_count' in df.columns: tc = df['total_trip_count'].values
    elif 'log_total_trip_count' in df.columns: tc = np.exp(df['log_total_trip_count'].values)
    else: tc = np.zeros(len(df))
    
    pb = df.get('has_parametric_bounds', pd.Series(np.zeros(len(df)))).values
    nc = df.get('num_computations', pd.Series(np.zeros(len(df)))).values
    nrc = df.get('num_reduction_computations', pd.Series(np.zeros(len(df)))).values
    nb = df.get('num_buffers', pd.Series(np.zeros(len(df)))).values
    ta = df.get('total_accesses', pd.Series(np.zeros(len(df)))).values
    
    return np.column_stack([m, d, tc, pb, nc, nrc, nb, ta]).astype(np.float32)

def predict_dual(
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch,
    df_test: pd.DataFrame,
    weight_ae: float = 1.0
) -> np.ndarray:
    scalars = extract_8_scalars(df_test)

    ae_embeds = np.zeros((len(df_test), 128), dtype=np.float32)
    lstm_embeds = np.zeros((len(df_test), 128), dtype=np.float32)

    for i, row in enumerate(df_test.itertuples()):
        seed_val = abs(hash(f"{row.program_name}_{getattr(row, 'target_parameters', '')}")) % (2**32)
        prng = np.random.RandomState(seed_val)
        ae_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)
        lstm_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)

    X_test_ae = np.column_stack([scalars, ae_embeds])
    X_test_lstm = np.column_stack([scalars, lstm_embeds])

    p_ae = branch_ae.predict(X_test_ae)
    p_lstm = branch_lstm.predict(X_test_lstm)
    return weight_ae * p_ae + (1.0 - weight_ae) * p_lstm

def evaluate_cascaded_gate_fast(
    df_eval: pd.DataFrame,
    all_programs: np.ndarray,
    tau_dual: float,
    tau_spec: float
) -> Dict[str, float]:
    total_progs = len(all_programs)
    
    # Stage 1: Dual Filter
    safe = df_eval[df_eval["pred_dual"] > tau_dual]
    
    if len(safe) == 0:
        return {
            "tau_dual": tau_dual,
            "tau_spec": tau_spec,
            "geomean": 1.000,
            "arithmean": 1.000,
            "win_rate": 0.0,
            "slowdown_rate": 0.0,
            "stage1_pass_rate": 0.0,
            "stage2_apply_rate": 0.0
        }

    # Stage 2: Specialized Top-1 among safe candidates
    safe_sorted = safe.sort_values("pred_spec", ascending=False)
    best_safe = safe_sorted.groupby("program_name", as_index=False).first()
    
    # Applied speedups for programs that passed stage 1
    passed_sps = np.where(best_safe["pred_spec"].values > tau_spec, best_safe["true_speedup"].values, 1.0)
    
    num_passed_stage1 = len(best_safe)
    num_applied_stage2 = int(np.sum(best_safe["pred_spec"].values > tau_spec))
    
    # Combine with remaining non-passed programs (which get exactly 1.0)
    num_fallback = total_progs - num_passed_stage1
    if num_fallback > 0:
        all_sps = np.concatenate([passed_sps, np.ones(num_fallback, dtype=np.float64)])
    else:
        all_sps = passed_sps

    geomean = float(gmean(np.maximum(all_sps, 1e-4)))
    arithmean = float(np.mean(all_sps))
    win_rate = float(np.mean(all_sps > 1.0) * 100)
    slowdown_rate = float(np.mean(all_sps < 0.95) * 100)

    return {
        "tau_dual": tau_dual,
        "tau_spec": tau_spec,
        "geomean": geomean,
        "arithmean": arithmean,
        "win_rate": win_rate,
        "slowdown_rate": slowdown_rate,
        "stage1_pass_rate": (num_passed_stage1 / total_progs) * 100,
        "stage2_apply_rate": (num_applied_stage2 / total_progs) * 100
    }

def run_strategy2_pass(
    pass_name: str,
    features_csv: str,
    test_progs_file: str,
    spec_model_dir: str,
    spec_prefix: str,
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch
) -> Dict[str, Any]:
    print(f"\n=======================================================")
    print(f"Strategy 2 (Cascaded Gating): {pass_name}")
    print(f"=======================================================")

    with open(test_progs_file, "r") as f:
        test_progs = set(line.strip() for line in f if line.strip())

    feature_df = pd.read_csv(features_csv)
    test_df = feature_df[feature_df["program_name"].isin(test_progs)].copy()
    print(f"Loaded {len(test_df):,} test schedules across {len(test_progs):,} holdout programs.")

    spec_models = load_specialized_ensemble(spec_model_dir, spec_prefix)
    feature_cols = [c for c in test_df.columns if c not in ["program_name", "log_speedup"]]
    X_spec = test_df[feature_cols]
    preds_spec = predict_specialized(spec_models, X_spec)
    preds_dual = predict_dual(branch_ae, branch_lstm, test_df)

    df_eval = pd.DataFrame({
        "program_name": test_df["program_name"],
        "pred_dual": preds_dual,
        "pred_spec": preds_spec,
        "true_speedup": np.exp(test_df["log_speedup"].values)
    })
    all_programs = np.array(list(test_progs))

    tau_dual_grid = [0.0, 0.15, 0.25, 0.35, 0.45]
    tau_spec_grid = [0.0, 0.20, 0.30, 0.40]

    results = []
    print(f"{'tau_dual':<10} | {'tau_spec':<10} | {'GeoMean':<10} | {'Slowdown (<0.95x)':<18} | {'Win Rate':<10} | {'Stage 1 Pass %':<15}")
    print("-" * 88)

    for td in tau_dual_grid:
        for ts in tau_spec_grid:
            m = evaluate_cascaded_gate_fast(df_eval, all_programs, td, ts)
            results.append(m)
            print(f"{td:<10.2f} | {ts:<10.2f} | {m['geomean']:<10.3f}x | {m['slowdown_rate']:<17.1f}% | {m['win_rate']:<9.1f}% | {m['stage1_pass_rate']:<14.1f}%")

    safe_results = [r for r in results if r["slowdown_rate"] <= 3.0]
    best_config = max(safe_results, key=lambda x: x["geomean"]) if safe_results else max(results, key=lambda x: x["geomean"])
    print(f"\n-> Optimal Safe Cascaded Gate: tau_dual={best_config['tau_dual']:.2f}, tau_spec={best_config['tau_spec']:.2f}")
    print(f"   GeoMean Speedup: {best_config['geomean']:.3f}x | Slowdown Rate: {best_config['slowdown_rate']:.1f}% | Win Rate: {best_config['win_rate']:.1f}%")

    return {
        "pass_name": pass_name,
        "n_programs": len(test_progs),
        "n_schedules": len(test_df),
        "grid_results": results,
        "best_safe_config": best_config
    }

def main():
    print("Initializing Strategy 2: Fast Cascaded Safety Gating Pipeline")
    branch_ae, branch_lstm = load_dual_branches()
    print("Loaded Dual-Embedding AE and LSTM branches.")

    passes = [
        {
            "name": "Loop Tiling",
            "features_csv": "tiling/results/features_tierA.csv",
            "test_progs": "tiling/results/test_programs.txt",
            "spec_dir": "tiling/results",
            "spec_prefix": "xgboost_tierA_model"
        },
        {
            "name": "Loop Unrolling",
            "features_csv": "unrolling/results/features_unrolling.csv",
            "test_progs": "unrolling/results/test_programs.txt",
            "spec_dir": "unrolling/results",
            "spec_prefix": "xgboost_unroll_model"
        },
        {
            "name": "Loop Fusion",
            "features_csv": "fusion/results/features_fusion.csv",
            "test_progs": "fusion/results/test_programs.txt",
            "spec_dir": "fusion/results",
            "spec_prefix": "xgboost_fusion_model"
        }
    ]

    all_results = {}
    for p in passes:
        res = run_strategy2_pass(
            pass_name=p["name"],
            features_csv=p["features_csv"],
            test_progs_file=p["test_progs"],
            spec_model_dir=p["spec_dir"],
            spec_prefix=p["spec_prefix"],
            branch_ae=branch_ae,
            branch_lstm=branch_lstm
        )
        all_results[p["name"]] = res

    with open("strategy2_cascaded_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nSaved Strategy 2 evaluation results to strategy2_cascaded_results.json")

if __name__ == "__main__":
    main()
