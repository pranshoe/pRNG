#!/usr/bin/env python3
"""
train_enriched_models.py - Training Pipeline for Specialized Enriched XGBoost Experts

Trains 10-model bagging ensembles with 35-D geometric & architectural features
for specialized loop transformation passes:
  1. Tiling Expert      -> models/tiling_expert/
  2. Unrolling Expert   -> models/unrolling_expert/
  3. Fusion Expert      -> models/fusion_expert/

Transpiles winning decision trees to zero-latency C++ headers in:
  models/in_tree_llvm_headers/
"""

import os
import sys
import ast
import json
import argparse
import numpy as np
import pandas as pd
import xgboost as xgb
from typing import Dict, Any, List
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from scipy.stats import gmean

current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(current_dir)
src_dir = os.path.join(repo_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from enriched_features import extract_enriched_program_features
from json_to_cpp import transpile_xgboost_to_cpp_header


def parse_list_col(val):
    if isinstance(val, str):
        try:
            return ast.literal_eval(val)
        except Exception:
            return []
    elif isinstance(val, list):
        return val
    return []


def build_enriched_feature_dataframe(
    csv_path: str,
    json_dir: str = None,
    pass_type: str = "tiling",
    max_programs: int = 15000,
    random_state: int = 42
) -> pd.DataFrame:
    print(f"\n[Feature Pipeline] Loading dataset: {csv_path} (Pass: {pass_type})...")
    df = pd.read_csv(csv_path)

    unique_all_progs = np.array(df['program_name'].unique())
    if len(unique_all_progs) > max_programs:
        print(f"  Subsampling {max_programs} programs from {len(unique_all_progs)} total unique programs...")
        np.random.seed(random_state)
        sampled_progs = np.random.choice(unique_all_progs, size=max_programs, replace=False)
        df = df[df['program_name'].isin(sampled_progs)].copy()
    else:
        print(f"  Dataset has {len(unique_all_progs)} programs, using all.")

    scalar_cols = [
        'memory_size', 'loop_depth', 'total_trip_count', 'num_computations',
        'num_reduction_computations', 'num_buffers', 'total_accesses', 'max_access_offset'
    ]
    scalar_cols = [c for c in scalar_cols if c in df.columns]

    if 'target_loop_levels' in df.columns:
        df['target_loop_levels_clean'] = df['target_loop_levels'].apply(parse_list_col)
    else:
        df['target_loop_levels_clean'] = [[] for _ in range(len(df))]

    if 'target_parameters' in df.columns:
        df['target_parameters_clean'] = df['target_parameters'].apply(parse_list_col)
    else:
        df['target_parameters_clean'] = [[] for _ in range(len(df))]

    max_depth = int(df['loop_depth'].max()) if 'loop_depth' in df.columns else 5
    max_depth = min(max(max_depth, 5), 10)

    # Extract JSON annotations if present
    json_features_list = []
    if json_dir and os.path.isdir(json_dir):
        for prog in df['program_name']:
            jpath = os.path.join(json_dir, f"{prog}.json")
            if os.path.exists(jpath):
                try:
                    with open(jpath, 'r') as f:
                        jdata = json.load(f)
                    json_features_list.append(extract_enriched_program_features(jdata, max_depth=max_depth))
                except Exception:
                    json_features_list.append(extract_enriched_program_features({}, max_depth=max_depth))
            else:
                json_features_list.append(extract_enriched_program_features({}, max_depth=max_depth))
        df_json_feat = pd.DataFrame(json_features_list, index=df.index)
    else:
        # Default mock-free geometric template
        for _ in range(len(df)):
            json_features_list.append(extract_enriched_program_features({}, max_depth=max_depth))
        df_json_feat = pd.DataFrame(json_features_list, index=df.index)

    # Pass-specific parameter encodings
    if pass_type == "tiling":
        multi_hot_levels = []
        tile_size_vectors = []
        for levels, params in zip(df['target_loop_levels_clean'], df['target_parameters_clean']):
            m_hot = [0] * max_depth
            t_vec = [1] * max_depth
            for idx, lvl in enumerate(levels):
                if isinstance(lvl, int) and 0 <= lvl < max_depth:
                    m_hot[lvl] = 1
                    if idx < len(params) and isinstance(params[idx], (int, float)):
                        t_vec[lvl] = params[idx]
            multi_hot_levels.append(m_hot)
            tile_size_vectors.append(t_vec)

        df_params = pd.DataFrame(
            np.column_stack([multi_hot_levels, tile_size_vectors]),
            columns=[f'level_target_{i}' for i in range(max_depth)] + [f'tile_size_{i}' for i in range(max_depth)],
            index=df.index
        )
    elif pass_type == "unrolling":
        unroll_factors = [p[0] if len(p) > 0 and isinstance(p[0], (int, float)) else 1 for p in df['target_parameters_clean']]
        unroll_levels = [l[0] if len(l) > 0 and isinstance(l[0], (int, float)) else 0 for l in df['target_loop_levels_clean']]
        df_params = pd.DataFrame({
            'unroll_factor': unroll_factors,
            'log_unroll_factor': np.log1p(unroll_factors),
            'unroll_level': unroll_levels
        }, index=df.index)
    else:  # fusion
        fusion_levels = [l[0] if len(l) > 0 and isinstance(l[0], (int, float)) else 0 for l in df['target_loop_levels_clean']]
        df_params = pd.DataFrame({'fusion_level': fusion_levels}, index=df.index)

    # Base feature concatenation
    feature_df = pd.concat([df[scalar_cols], df_json_feat, df_params], axis=1)

    if 'total_trip_count' in feature_df.columns:
        feature_df['log_total_trip_count'] = np.log1p(feature_df['total_trip_count'])
        feature_df = feature_df.drop(columns=['total_trip_count'])
    if 'memory_size' in feature_df.columns:
        feature_df['log_memory_size'] = np.log1p(feature_df['memory_size'])
        feature_df = feature_df.drop(columns=['memory_size'])

    # Rank 1 Cache Lines & Cross Features
    if pass_type == "tiling":
        tile_cols = [f'tile_size_{i}' for i in range(max_depth) if f'tile_size_{i}' in feature_df.columns]
        active_tile_vol = feature_df[tile_cols].replace(0, 1).prod(axis=1)
        feature_df['active_tile_volume'] = active_tile_vol
        feature_df['log_active_tile_volume'] = np.log1p(active_tile_vol)
        
        num_buf = df['num_buffers'].values if 'num_buffers' in df.columns else np.ones(len(df))
        tiled_footprint_kb = (active_tile_vol * 8.0 * num_buf) / 1024.0
        feature_df['tiled_footprint_kb'] = np.log1p(tiled_footprint_kb)
        feature_df['l1_cache_ratio'] = tiled_footprint_kb / 32.0
        feature_df['l2_cache_ratio'] = tiled_footprint_kb / 512.0
        feature_df['touched_cache_lines'] = np.ceil((active_tile_vol * 8.0) / 64.0)

    feature_df['program_name'] = df['program_name']
    feature_df['log_speedup'] = df['log_speedup']

    print(f"  Constructed enriched feature matrix of shape: {feature_df.shape}")
    return feature_df


def train_pass_ensemble(
    feature_df: pd.DataFrame,
    output_dir: str,
    pass_name: str,
    n_models: int = 10,
    random_state: int = 42,
    device: str = "cuda" if xgb.rabit is not None else "cpu"
) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)
    print(f"\n[Training] Starting 10-Bag XGBoost Ensemble Training for {pass_name} on {device}...")

    # Strict program-level train/test split (80/20)
    unique_progs = np.array(feature_df['program_name'].unique())
    train_progs, test_progs = train_test_split(unique_progs, test_size=0.2, random_state=random_state)

    train_set = set(train_progs)
    test_set = set(test_progs)

    with open(os.path.join(output_dir, "train_programs.txt"), "w") as f:
        f.write("\n".join(str(p) for p in train_progs))
    with open(os.path.join(output_dir, "test_programs.txt"), "w") as f:
        f.write("\n".join(str(p) for p in test_progs))

    train_mask = feature_df['program_name'].isin(train_set)
    test_mask = feature_df['program_name'].isin(test_set)

    feature_cols = [c for c in feature_df.columns if c not in ['program_name', 'log_speedup']]

    X_train = feature_df.loc[train_mask, feature_cols]
    y_train = feature_df.loc[train_mask, 'log_speedup']
    X_test = feature_df.loc[test_mask, feature_cols]
    y_test = feature_df.loc[test_mask, 'log_speedup']

    print(f"  Training samples: {len(X_train):,} across {len(train_progs):,} programs | Test samples: {len(X_test):,} across {len(test_progs):,} programs")

    dtrain = xgb.DMatrix(X_train, label=y_train)
    dtest = xgb.DMatrix(X_test, label=y_test)

    # Save features CSV
    feat_csv_path = os.path.join(output_dir, f"features_enriched_{pass_name}.csv")
    feature_df.to_csv(feat_csv_path, index=False)

    test_preds = np.zeros((n_models, len(X_test)), dtype=np.float32)

    for i in range(1, n_models + 1):
        fold_seed = random_state + (i * 17)
        params = {
            'tree_method': 'hist',
            'device': device,
            'max_depth': 7,
            'max_leaves': 128,
            'learning_rate': 0.05,
            'subsample': 0.85,
            'colsample_bytree': 0.85,
            'objective': 'reg:squarederror',
            'random_state': fold_seed
        }
        
        booster = xgb.train(
            params,
            dtrain,
            num_boost_round=150,
            evals=[(dtest, "test")],
            verbose_eval=False
        )
        
        # Save model
        model_path = os.path.join(output_dir, f"xgboost_enriched_model_{i}.json")
        booster.save_model(model_path)
        test_preds[i - 1] = booster.predict(dtest)

    # Ensemble Average
    ensemble_pred = np.mean(test_preds, axis=0)
    mae = float(mean_absolute_error(y_test, ensemble_pred))
    rmse = float(root_mean_squared_error(y_test, ensemble_pred))

    # Gated Speedup on Test Set (tau = 0.30 for tiling, 0.45 for unrolling, 0.60 for fusion)
    tau = 0.30 if pass_name == "tiling" else (0.45 if pass_name == "unrolling" else 0.60)
    df_eval = pd.DataFrame({
        'program': feature_df.loc[test_mask, 'program_name'],
        'pred': ensemble_pred,
        'sp': np.exp(y_test.values)
    })
    top1 = df_eval.sort_values('pred', ascending=False).groupby('program', as_index=False).first()
    gsp = np.where(top1['pred'].values > tau, top1['sp'].values, 1.0)
    
    geomean = float(gmean(np.maximum(gsp, 1e-4)))
    slowdown = float(np.mean(gsp < 0.95) * 100)
    win = float(np.mean(gsp > 1.0) * 100)

    print(f"  [Result: {pass_name.upper()}] Enriched MAE: {mae:.4f} | RMSE: {rmse:.4f} | Gated GeoMean: {geomean:.3f}x | Slowdown: {slowdown:.1f}% | Win Rate: {win:.1f}%")

    # Transpile header to output_dir and in_tree_llvm_headers
    header_name = f"predict_{'unroll' if pass_name == 'unrolling' else pass_name}_gate.h"
    local_header_path = os.path.join(output_dir, header_name)
    best_model_path = os.path.join(output_dir, "xgboost_enriched_model_1.json")
    
    llvm_headers_dir = os.path.join(repo_root, "models", "in_tree_llvm_headers")
    os.makedirs(llvm_headers_dir, exist_ok=True)
    in_tree_header_path = os.path.join(llvm_headers_dir, header_name)
    
    transpile_xgboost_to_cpp_header(best_model_path, local_header_path, function_name=f"predict_{pass_name}_gate")
    transpile_xgboost_to_cpp_header(best_model_path, in_tree_header_path, function_name=f"predict_{pass_name}_gate")

    metrics = {
        'pass_name': pass_name,
        'mae': mae,
        'rmse': rmse,
        'geomean': geomean,
        'slowdown_rate': slowdown,
        'win_rate': win,
        'n_train': len(X_train),
        'n_test': len(X_test)
    }

    with open(os.path.join(output_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Train Specialized Enriched XGBoost Cost Models")
    parser.add_argument("--device", default="cuda" if xgb.rabit is not None else "cpu", choices=["cuda", "cpu"])
    parser.add_argument("--max-programs", type=int, default=15000)
    args = parser.parse_args()

    print("=" * 85)
    print("ENRICHED SPECIALIZED COST MODEL TRAINING (Ranks 1, 2, 3)")
    print("=" * 85)

    passes = [
        ("tiling", os.path.join(repo_root, "data", "training_data_tiling.csv"), os.path.join(repo_root, "models", "tiling_expert")),
        ("unrolling", os.path.join(repo_root, "data", "training_data_unrolling.csv"), os.path.join(repo_root, "models", "unrolling_expert")),
        ("fusion", os.path.join(repo_root, "data", "training_data_fusion.csv"), os.path.join(repo_root, "models", "fusion_expert"))
    ]

    all_metrics = {}
    for pass_name, csv_file, out_dir in passes:
        if os.path.exists(csv_file):
            feat_df = build_enriched_feature_dataframe(
                csv_path=csv_file,
                json_dir=os.path.join(repo_root, "data", "program_graphs_raw"),
                pass_type=pass_name,
                max_programs=args.max_programs,
                random_state=42
            )
            met = train_pass_ensemble(feat_df, out_dir, pass_name, n_models=10, device=args.device)
            all_metrics[pass_name] = met
        else:
            print(f"[-] Dataset {csv_file} not found locally. Skipping {pass_name}.")

    summary_path = os.path.join(repo_root, "models", "all_enriched_specialized_metrics.json")
    with open(summary_path, "w") as f:
        json.dump(all_metrics, f, indent=2)

    print("\n" + "=" * 85)
    print("ALL ENRICHED SPECIALIZED MODELS SUCCESSFULLY TRAINED & TRANSPILED!")
    print("=" * 85)


if __name__ == "__main__":
    main()
