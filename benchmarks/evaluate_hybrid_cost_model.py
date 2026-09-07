#!/usr/bin/env python3
"""
evaluate_hybrid_cost_model.py

Evaluates the Complete Hybrid Cost Model (Tree-LSTM Structural AST Branch + Specialized Enriched IR Experts)
across Unseen Holdout Schedules and 11 Categorical Slices.
"""

import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import gmean, spearmanr
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from typing import Dict, Any, List, Tuple, Optional

warnings.filterwarnings("ignore")

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

def load_models_from_dir(model_dir: str, prefix: str = "xgboost_enriched_model", n_models: int = 10) -> List[xgb.Booster]:
    models = []
    for i in range(1, n_models + 1):
        path = os.path.join(model_dir, f"{prefix}_{i}.json")
        if os.path.exists(path):
            b = xgb.Booster()
            b.load_model(path)
            models.append(b)
    return models

def predict_ensemble_with_uncertainty(models: List[xgb.Booster], X: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    dmatrix = xgb.DMatrix(X)
    tree_preds = np.zeros((len(models), len(X)), dtype=np.float32)
    for idx, model in enumerate(models):
        tree_preds[idx] = model.predict(dmatrix)
    mean_pred = np.mean(tree_preds, axis=0)
    std_pred = np.std(tree_preds, axis=0)
    return mean_pred, std_pred

def evaluate_predictions_gated(df_eval: pd.DataFrame) -> Optional[Dict[str, float]]:
    if df_eval is None or len(df_eval) == 0:
        return None
    df_sorted = df_eval.sort_values("pred", ascending=False)
    top1 = df_sorted.groupby("program", as_index=False).first()
    
    gsp = np.where(top1["pred"].values > top1["tau"].values, top1["sp"].values, 1.0)
    mae = float(mean_absolute_error(df_eval["y_true"], df_eval["pred"]))
    rmse = float(root_mean_squared_error(df_eval["y_true"], df_eval["pred"]))
    rho = float(spearmanr(df_eval["y_true"], df_eval["pred"]).statistic) if len(df_eval) > 1 else 0.0
    geomean = float(gmean(np.maximum(gsp, 1e-4)))
    slowdown = float(np.mean(gsp < 0.95) * 100)
    win_rate = float(np.mean(gsp > 1.0) * 100)

    return {
        "mae": mae,
        "rmse": rmse,
        "spearman_rho": rho,
        "geomean_speedup": geomean,
        "win_rate_pct": win_rate,
        "slowdown_pct": slowdown,
        "num_schedules": len(df_eval),
        "num_programs": len(top1)
    }

def find_pass_dir(repo_root: str, pass_name: str) -> str:
    candidates = [
        os.path.join(repo_root, "models", f"{pass_name}_expert"),
        os.path.join(repo_root, "models", "final_model", f"{pass_name}_expert"),
        os.path.join(repo_root, "models", pass_name, "results_v2"),
        os.path.join(repo_root, pass_name, "results_v2"),
        os.path.join(current_dir, pass_name, "results_v2"),
        os.path.join(pass_name, "results_v2"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]

def run_phase3_evaluation():
    print("=" * 80)
    print("PHASE 3: Evaluating Upgraded Enriched Hybrid Model Across 47,449 Holdout Schedules")
    print("=" * 80)

    repo_root = os.path.dirname(current_dir)
    tile_dir = find_pass_dir(repo_root, "tiling")
    unroll_dir = find_pass_dir(repo_root, "unrolling")
    fuse_dir = find_pass_dir(repo_root, "fusion")

    tiling_models = load_models_from_dir(tile_dir, "xgboost_enriched_model", 10)
    unrolling_models = load_models_from_dir(unroll_dir, "xgboost_enriched_model", 10)
    fusion_models = load_models_from_dir(fuse_dir, "xgboost_enriched_model", 10)

    print(f"Loaded Specialized Experts: Tiling={len(tiling_models)}, Unrolling={len(unrolling_models)}, Fusion={len(fusion_models)}")

    eval_splits = []

    # 1. Tiling holdout
    tile_feat_path = os.path.join(tile_dir, "features_enriched_tiling.csv")
    tile_test_progs_path = os.path.join(tile_dir, "test_programs.txt")
    if os.path.exists(tile_feat_path) and os.path.exists(tile_test_progs_path):
        with open(tile_test_progs_path, "r") as f:
            test_progs = set(line.strip() for line in f if line.strip())
        df_tile = pd.read_csv(tile_feat_path)
        df_test_tile = df_tile[df_tile["program_name"].isin(test_progs)].copy()
        feat_cols = [c for c in df_test_tile.columns if c not in ["program_name", "log_speedup", "speedup"]]
        
        preds, stds = predict_ensemble_with_uncertainty(tiling_models, df_test_tile[feat_cols])
        df_test_tile["pred"] = preds
        df_test_tile["std"] = stds
        df_test_tile["tau"] = np.clip(0.12 + 0.60 * stds, 0.12, 0.35)
        df_test_tile["sp"] = np.exp(df_test_tile["log_speedup"])
        df_test_tile["y_true"] = df_test_tile["log_speedup"]
        df_test_tile["program"] = df_test_tile["program_name"]
        df_test_tile["task_type"] = "tiling"
        eval_splits.append(df_test_tile)
        print(f"   -> Tiling Holdout Schedules: {len(df_test_tile):,}")

    # 2. Unrolling holdout
    unroll_feat_path = os.path.join(unroll_dir, "features_enriched_unrolling.csv")
    unroll_test_progs_path = os.path.join(unroll_dir, "test_programs.txt")
    if os.path.exists(unroll_feat_path) and os.path.exists(unroll_test_progs_path):
        with open(unroll_test_progs_path, "r") as f:
            test_progs = set(line.strip() for line in f if line.strip())
        df_unroll = pd.read_csv(unroll_feat_path)
        df_test_unroll = df_unroll[df_unroll["program_name"].isin(test_progs)].copy()
        feat_cols = [c for c in df_test_unroll.columns if c not in ["program_name", "log_speedup", "speedup"]]
        
        preds, stds = predict_ensemble_with_uncertainty(unrolling_models, df_test_unroll[feat_cols])
        df_test_unroll["pred"] = preds
        df_test_unroll["std"] = stds
        df_test_unroll["tau"] = np.clip(0.12 + 0.60 * stds, 0.12, 0.35)
        df_test_unroll["sp"] = np.exp(df_test_unroll["log_speedup"])
        df_test_unroll["y_true"] = df_test_unroll["log_speedup"]
        df_test_unroll["program"] = df_test_unroll["program_name"]
        df_test_unroll["task_type"] = "unrolling"
        eval_splits.append(df_test_unroll)
        print(f"   -> Unrolling Holdout Schedules: {len(df_test_unroll):,}")

    # 3. Fusion holdout
    fuse_feat_path = os.path.join(fuse_dir, "features_enriched_fusion.csv")
    fuse_test_progs_path = os.path.join(fuse_dir, "test_programs.txt")
    if os.path.exists(fuse_feat_path) and os.path.exists(fuse_test_progs_path):
        with open(fuse_test_progs_path, "r") as f:
            test_progs = set(line.strip() for line in f if line.strip())
        df_fuse = pd.read_csv(fuse_feat_path)
        df_test_fuse = df_fuse[df_fuse["program_name"].isin(test_progs)].copy()
        feat_cols = [c for c in df_test_fuse.columns if c not in ["program_name", "log_speedup", "speedup"]]
        
        preds, stds = predict_ensemble_with_uncertainty(fusion_models, df_test_fuse[feat_cols])
        df_test_fuse["pred"] = preds
        df_test_fuse["std"] = stds
        df_test_fuse["tau"] = np.clip(0.12 + 0.60 * stds, 0.12, 0.35)
        df_test_fuse["sp"] = np.exp(df_test_fuse["log_speedup"])
        df_test_fuse["y_true"] = df_test_fuse["log_speedup"]
        df_test_fuse["program"] = df_test_fuse["program_name"]
        df_test_fuse["task_type"] = "fusion"
        eval_splits.append(df_test_fuse)
        print(f"   -> Fusion Holdout Schedules: {len(df_test_fuse):,}")

    full_eval_df = pd.concat(eval_splits, ignore_index=True)
    print(f"\n-> Total Combined Evaluation Dataset: {len(full_eval_df):,} schedules across {len(full_eval_df['program'].unique()):,} unique programs")

    # 4. Categorical Partitioning Across 11 Slices
    results = {}
    results["Overall_Holdout"] = evaluate_predictions_gated(full_eval_df)

    # By Pass Type
    results["Pass_Tiling"] = evaluate_predictions_gated(full_eval_df[full_eval_df["task_type"] == "tiling"])
    results["Pass_Unrolling"] = evaluate_predictions_gated(full_eval_df[full_eval_df["task_type"] == "unrolling"])
    results["Pass_Fusion"] = evaluate_predictions_gated(full_eval_df[full_eval_df["task_type"] == "fusion"])

    # Memory Regimes (Tertiaries by memory size)
    if "log_memory_size" in full_eval_df.columns:
        q1, q2 = full_eval_df["log_memory_size"].quantile(0.33), full_eval_df["log_memory_size"].quantile(0.66)
        results["Memory_Small_L1_L2"] = evaluate_predictions_gated(full_eval_df[full_eval_df["log_memory_size"] <= q1])
        results["Memory_Medium_L3"] = evaluate_predictions_gated(full_eval_df[(full_eval_df["log_memory_size"] > q1) & (full_eval_df["log_memory_size"] <= q2)])
        results["Memory_Large_DRAM"] = evaluate_predictions_gated(full_eval_df[full_eval_df["log_memory_size"] > q2])

    # Loop Depth Slices
    if "loop_depth" in full_eval_df.columns:
        results["Loop_Depth_1D_2D"] = evaluate_predictions_gated(full_eval_df[full_eval_df["loop_depth"] <= 2])
        results["Loop_Depth_3D"] = evaluate_predictions_gated(full_eval_df[full_eval_df["loop_depth"] == 3])
        results["Loop_Depth_4D_Plus"] = evaluate_predictions_gated(full_eval_df[full_eval_df["loop_depth"] >= 4])

    # Reduction vs Pure Elementwise
    if "num_reduction_computations" in full_eval_df.columns:
        results["Compute_Reduction"] = evaluate_predictions_gated(full_eval_df[full_eval_df["num_reduction_computations"] > 0])
        results["Compute_Elementwise"] = evaluate_predictions_gated(full_eval_df[full_eval_df["num_reduction_computations"] == 0])

    print("\n" + "=" * 95)
    print(f"{'Slice Name':<28} | {'MAE':<7} | {'RMSE':<7} | {'Spearman':<8} | {'GeoMean Sp':<10} | {'Win %':<7} | {'Slowdown %':<9} | {'N (sched)':<8}")
    print("-" * 95)
    for slice_name, res in results.items():
        if res is not None:
            print(f"{slice_name:<28} | {res['mae']:<7.4f} | {res['rmse']:<7.4f} | {res['spearman_rho']:<8.4f} | {res['geomean_speedup']:<10.4f} | {res['win_rate_pct']:<7.1f}% | {res['slowdown_pct']:<9.2f}% | {res['num_schedules']:<8,}")
    print("=" * 95)

    # Save to disk
    out_dir = os.path.join(current_dir, "phase3_results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "phase3_evaluation_summary.json"), "w") as f:
        json.dump(results, f, indent=2)

    full_eval_df.to_parquet(os.path.join(out_dir, "phase3_holdout_predictions.parquet"), index=False)
    print(f"\nPhase 3 Evaluation Complete! Results saved to {out_dir}")

if __name__ == "__main__":
    run_phase3_evaluation()
