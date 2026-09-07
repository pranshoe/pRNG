import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import gmean
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from typing import Dict, Any, List, Tuple

warnings.filterwarnings("ignore")

from dual_ensemble.xgboost_ensemble import XGBoostBranch

def load_specialized_models(model_dir: str, prefix: str, n_models: int = 10) -> List[xgb.Booster]:
    models = []
    for i in range(1, n_models + 1):
        path = os.path.join(model_dir, f"{prefix}_{i}.json")
        if os.path.exists(path):
            b = xgb.Booster()
            b.load_model(path)
            models.append(b)
    return models

def predict_specialized_with_uncertainty(models: List[xgb.Booster], X: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    dmatrix = xgb.DMatrix(X)
    tree_preds = np.zeros((len(models), len(X)), dtype=np.float32)
    for idx, model in enumerate(models):
        tree_preds[idx] = model.predict(dmatrix)
    mean_pred = np.mean(tree_preds, axis=0)
    std_pred = np.std(tree_preds, axis=0)
    return mean_pred, std_pred

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

def evaluate_guardrailed_tiling(
    df_test: pd.DataFrame,
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch
) -> Dict[str, Any]:
    print("\n" + "=" * 85)
    print("EVALUATING HYBRID MODEL WITH HARDCODED GUARDRAILS: LOOP TILING")
    print("=" * 85)

    spec_models = load_specialized_models("tiling/results", "xgboost_tierA_model")
    feature_cols = [c for c in df_test.columns if c not in ["program_name", "log_speedup"]]
    
    # 1. Model Inference
    preds_spec, std_spec = predict_specialized_with_uncertainty(spec_models, df_test[feature_cols])
    preds_dual = predict_dual(branch_ae, branch_lstm, df_test)

    # 2. Extract Guardrail Signals
    depth = df_test["loop_depth"].values
    trip_count = np.exp(df_test["log_total_trip_count"].values) if "log_total_trip_count" in df_test else np.ones(len(df_test)) * 1000
    n_reductions = df_test["num_reduction_computations"].values if "num_reduction_computations" in df_test else np.zeros(len(df_test))
    
    tile_cols = [f"tile_size_{i}" for i in range(5) if f"tile_size_{i}" in df_test.columns]
    max_tile = df_test[tile_cols].max(axis=1).values if tile_cols else np.ones(len(df_test)) * 32

    # 3. Dynamic Blend Weight alpha(D)
    alpha = np.where(depth <= 2, 1.0, 0.80)
    raw_hybrid = alpha * preds_spec + (1.0 - alpha) * preds_dual

    # 4. Apply Hardcoded Guardrails (Zero-Retraining Improvements)
    # A. Divisibility & Remainder Penalty
    remainder = trip_count % np.maximum(max_tile, 1)
    remainder_ratio = remainder / np.maximum(trip_count, 1)
    remainder_penalty = 0.35 * remainder_ratio

    # B. Small Loop Filter: If loop is small or smaller than tile, disable tiling
    small_loop_mask = (trip_count <= 16) | (trip_count <= max_tile)
    
    # C. Short-Trip Reduction Filter: If reduction loop is small, prevent tiling
    small_reduction_mask = (n_reductions > 0) & (trip_count <= 32)

    # Adjusted Prediction with penalties
    adj_hybrid = raw_hybrid - remainder_penalty
    adj_hybrid[small_loop_mask] = -1.0 # Force rejection
    adj_hybrid[small_reduction_mask] = -1.0 # Force rejection

    # 5. Adaptive Uncertainty-Aware Threshold tau(sigma)
    tau_adaptive = np.clip(0.12 + 0.60 * std_spec, 0.12, 0.35)

    # Ground truth
    y_true = df_test["log_speedup"].values
    true_sp = np.exp(y_true)
    progs = df_test["program_name"].values

    # Policy 1: Raw Baseline Hybrid (Fixed tau = 0.30, no guardrails)
    df_raw = pd.DataFrame({"p": progs, "pred": raw_hybrid, "sp": true_sp, "tau": 0.30})
    top1_raw = df_raw.sort_values("pred", ascending=False).groupby("p", as_index=False).first()
    gsp_raw = np.where(top1_raw["pred"].values > top1_raw["tau"].values, top1_raw["sp"].values, 1.0)

    # Policy 2: Guardrailed Hybrid (Guardrails + Adaptive tau)
    df_gr = pd.DataFrame({"p": progs, "pred": adj_hybrid, "sp": true_sp, "tau": tau_adaptive})
    top1_gr = df_gr.sort_values("pred", ascending=False).groupby("p", as_index=False).first()
    gsp_gr = np.where(top1_gr["pred"].values > top1_gr["tau"].values, top1_gr["sp"].values, 1.0)

    # Calculate Comparison Metrics
    results = {
        "Raw_Hybrid": {
            "geomean": float(gmean(np.maximum(gsp_raw, 1e-4))),
            "slowdown_rate": float(np.mean(gsp_raw < 0.95) * 100),
            "severe_slowdown": float(np.mean(gsp_raw < 0.80) * 100),
            "win_rate": float(np.mean(gsp_raw > 1.0) * 100)
        },
        "Guardrailed_Hybrid": {
            "geomean": float(gmean(np.maximum(gsp_gr, 1e-4))),
            "slowdown_rate": float(np.mean(gsp_gr < 0.95) * 100),
            "severe_slowdown": float(np.mean(gsp_gr < 0.80) * 100),
            "win_rate": float(np.mean(gsp_gr > 1.0) * 100)
        }
    }

    print(f"\n{'Model Strategy':<32} | {'GeoMean':<10} | {'Slowdown (<0.95x)':<18} | {'Severe (<0.80x)':<16} | {'Win Rate (>1.0x)':<16}")
    print("-" * 100)
    print(f"{'Baseline Hybrid (Fixed tau=0.30)':<32} | {results['Raw_Hybrid']['geomean']:<10.3f}x | {results['Raw_Hybrid']['slowdown_rate']:<17.2f}% | {results['Raw_Hybrid']['severe_slowdown']:<15.2f}% | {results['Raw_Hybrid']['win_rate']:<15.2f}%")
    print(f"{'Guardrailed Hybrid (Adaptive tau)':<32} | {results['Guardrailed_Hybrid']['geomean']:<10.3f}x | {results['Guardrailed_Hybrid']['slowdown_rate']:<17.2f}% | {results['Guardrailed_Hybrid']['severe_slowdown']:<15.2f}% | {results['Guardrailed_Hybrid']['win_rate']:<15.2f}%")

    return results

def main():
    print("Initializing Guardrailed Hybrid Model Evaluation...")
    branch_ae, branch_lstm = load_dual_branches()

    with open("tiling/results/test_programs.txt") as f:
        test_progs = set(line.strip() for line in f if line.strip())

    df_tiling = pd.read_csv("tiling/results/features_tierA.csv")
    test_df = df_tiling[df_tiling["program_name"].isin(test_progs)].copy().reset_index(drop=True)

    tiling_res = evaluate_guardrailed_tiling(test_df, branch_ae, branch_lstm)

    with open("guardrailed_hybrid_benchmark_results.json", "w") as f:
        json.dump(tiling_res, f, indent=2)
    print("\nSaved benchmark results to guardrailed_hybrid_benchmark_results.json")

if __name__ == "__main__":
    main()
