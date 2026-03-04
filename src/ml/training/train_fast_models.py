# -*- coding: utf-8 -*-
"""
Train FAST ML models (<2 minutes) and save them.

Models included:
  - XGBoost (~1.2s)
  - LightGBM (~0.7s)
  - CatBoost (~3.2s)
  - Random Forest (~2.6s)
  - Logistic Regression (~0.2s)

Models EXCLUDED (too slow):
  - SVM RBF (7134s)
  - LSTM (91s)
  - GRU (91s)
  - MLP/ANN (99s)

Usage:
    # Train and save
    python -m src.ml.training.train_fast_models backtester/output/backtest_20260222_200616.xlsx

    # Predict on file using saved models
    python -m src.ml.training.train_fast_models --predict backtester/output/backtest_20260222_200616.xlsx
"""

import sys
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings('ignore')

MODEL_DIR = Path("models/fast_models")


def train_fast_models(xlsx_path: str):
    """Train fast models and save them."""

    print("=" * 70)
    print("TRAIN FAST MODELS (<2 min each)")
    print("=" * 70)

    # Load data
    print(f"\nLoading {xlsx_path}...")
    df = pd.read_excel(xlsx_path)
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)
    wins = (df["label_win"] == 1).sum()
    win_rate = wins / len(df) * 100
    print(f"Win rate: {win_rate:.1f}%")

    if win_rate < 10:
        print("\n" + "!" * 70)
        print("WARNING: Win rate < 10% - models may not be useful!")
        print("Consider using a dataset with higher win rate.")
        print("!" * 70)

    # Features
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)
    feature_cols = ["Prob", "R/R", "SL %", "TP1 %", "TP2 %", "TP3 %", "direction_num"]
    available_cols = [c for c in feature_cols if c in df.columns]

    df_clean = df.dropna(subset=available_cols + ["label_win"])
    print(f"Clean rows: {len(df_clean)}")
    print(f"Features: {available_cols}")

    if len(df_clean) < 100:
        print("ERROR: Not enough data!")
        return

    # Temporal split
    df_clean = df_clean.sort_values("Timestamp").reset_index(drop=True)
    split_idx = int(len(df_clean) * 0.8)
    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"\nTrain: {len(train_df)}, Test: {len(test_df)}")

    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)
    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Scale for Logistic Regression
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Metrics
    from sklearn.metrics import f1_score, accuracy_score, precision_score, recall_score, roc_auc_score

    models = {}
    results = []
    total_time = 0

    # ================================================================
    # 1. LOGISTIC REGRESSION
    # ================================================================
    print("\n[1/5] Training Logistic Regression...")
    start = time.time()
    try:
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(random_state=42, max_iter=1000, class_weight="balanced")
        lr.fit(X_train_scaled, y_train)

        elapsed = time.time() - start
        total_time += elapsed

        models['logistic_regression'] = {'model': lr, 'needs_scaling': True}

        y_pred = lr.predict(X_test_scaled)
        y_proba = lr.predict_proba(X_test_scaled)[:, 1]
        results.append({
            'name': 'Logistic Regression',
            'key': 'logistic_regression',
            'time': elapsed,
            'f1': f1_score(y_test, y_pred),
            'acc': accuracy_score(y_test, y_pred),
            'prec': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'auc': roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0,
        })
        print(f"      Done in {elapsed:.1f}s | F1: {results[-1]['f1']:.3f}")
    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # 2. RANDOM FOREST
    # ================================================================
    print("[2/5] Training Random Forest...")
    start = time.time()
    try:
        from sklearn.ensemble import RandomForestClassifier

        rf = RandomForestClassifier(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        )
        rf.fit(X_train, y_train)

        elapsed = time.time() - start
        total_time += elapsed

        models['random_forest'] = {'model': rf, 'needs_scaling': False}

        y_pred = rf.predict(X_test)
        y_proba = rf.predict_proba(X_test)[:, 1]
        results.append({
            'name': 'Random Forest',
            'key': 'random_forest',
            'time': elapsed,
            'f1': f1_score(y_test, y_pred),
            'acc': accuracy_score(y_test, y_pred),
            'prec': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'auc': roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0,
        })
        print(f"      Done in {elapsed:.1f}s | F1: {results[-1]['f1']:.3f}")
    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # 3. XGBOOST
    # ================================================================
    print("[3/5] Training XGBoost...")
    start = time.time()
    try:
        import xgboost as xgb

        scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)

        xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            n_jobs=-1,
            verbosity=0
        )
        xgb_model.fit(X_train, y_train)

        elapsed = time.time() - start
        total_time += elapsed

        models['xgboost'] = {'model': xgb_model, 'needs_scaling': False}

        y_pred = xgb_model.predict(X_test)
        y_proba = xgb_model.predict_proba(X_test)[:, 1]
        results.append({
            'name': 'XGBoost',
            'key': 'xgboost',
            'time': elapsed,
            'f1': f1_score(y_test, y_pred),
            'acc': accuracy_score(y_test, y_pred),
            'prec': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'auc': roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0,
        })
        print(f"      Done in {elapsed:.1f}s | F1: {results[-1]['f1']:.3f}")
    except ImportError:
        print("      XGBoost not installed. Run: pip install xgboost")
    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # 4. LIGHTGBM
    # ================================================================
    print("[4/5] Training LightGBM...")
    start = time.time()
    try:
        import lightgbm as lgb

        lgb_model = lgb.LGBMClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
            verbosity=-1
        )
        lgb_model.fit(X_train, y_train)

        elapsed = time.time() - start
        total_time += elapsed

        models['lightgbm'] = {'model': lgb_model, 'needs_scaling': False}

        y_pred = lgb_model.predict(X_test)
        y_proba = lgb_model.predict_proba(X_test)[:, 1]
        results.append({
            'name': 'LightGBM',
            'key': 'lightgbm',
            'time': elapsed,
            'f1': f1_score(y_test, y_pred),
            'acc': accuracy_score(y_test, y_pred),
            'prec': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'auc': roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0,
        })
        print(f"      Done in {elapsed:.1f}s | F1: {results[-1]['f1']:.3f}")
    except ImportError:
        print("      LightGBM not installed. Run: pip install lightgbm")
    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # 5. CATBOOST
    # ================================================================
    print("[5/5] Training CatBoost...")
    start = time.time()
    try:
        from catboost import CatBoostClassifier

        cb_model = CatBoostClassifier(
            iterations=200,
            depth=6,
            learning_rate=0.1,
            auto_class_weights="Balanced",
            random_state=42,
            verbose=False
        )
        cb_model.fit(X_train, y_train)

        elapsed = time.time() - start
        total_time += elapsed

        models['catboost'] = {'model': cb_model, 'needs_scaling': False}

        y_pred = cb_model.predict(X_test)
        y_proba = cb_model.predict_proba(X_test)[:, 1]
        results.append({
            'name': 'CatBoost',
            'key': 'catboost',
            'time': elapsed,
            'f1': f1_score(y_test, y_pred),
            'acc': accuracy_score(y_test, y_pred),
            'prec': precision_score(y_test, y_pred, zero_division=0),
            'recall': recall_score(y_test, y_pred, zero_division=0),
            'auc': roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else 0,
        })
        print(f"      Done in {elapsed:.1f}s | F1: {results[-1]['f1']:.3f}")
    except ImportError:
        print("      CatBoost not installed. Run: pip install catboost")
    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # RESULTS
    # ================================================================
    if not results:
        print("\nNo models trained successfully!")
        return

    print("\n" + "=" * 70)
    print("RESULTS (sorted by F1)")
    print("=" * 70)

    results_sorted = sorted(results, key=lambda x: x['f1'], reverse=True)

    print(f"\n{'Model':<22} {'Time':>6} {'Acc':>7} {'Prec':>7} {'Recall':>7} {'F1':>7} {'AUC':>7}")
    print("-" * 70)

    for r in results_sorted:
        print(f"{r['name']:<22} {r['time']:>5.1f}s {r['acc']:>6.1%} {r['prec']:>6.1%} {r['recall']:>6.1%} {r['f1']:>7.3f} {r['auc']:>7.3f}")

    print("-" * 70)
    print(f"Total training time: {total_time:.1f}s")

    # Best model
    best = results_sorted[0]
    print(f"\nBest model: {best['name']} (F1={best['f1']:.3f})")

    # ================================================================
    # SAVE MODELS
    # ================================================================
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nSaving models to {MODEL_DIR}/...")

    for name, data in models.items():
        joblib.dump(data['model'], MODEL_DIR / f"{name}.pkl")
        print(f"  Saved: {name}.pkl")

    joblib.dump(scaler, MODEL_DIR / "scaler.pkl")
    print(f"  Saved: scaler.pkl")

    # Save metadata
    meta = {
        'feature_columns': available_cols,
        'models': {r['key']: {'needs_scaling': models[r['key']]['needs_scaling'],
                              'f1': r['f1'],
                              'auc': r['auc']} for r in results},
        'best_model': best['key'],
        'train_samples': len(train_df),
        'test_samples': len(test_df),
        'source_file': xlsx_path,
        'win_rate': win_rate,
    }
    with open(MODEL_DIR / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Saved: meta.json")

    print("\nModels saved successfully!")


def predict_file(xlsx_path: str):
    """Load saved models and predict on file."""

    print("=" * 70)
    print("PREDICT USING SAVED FAST MODELS")
    print("=" * 70)

    # Check models exist
    if not MODEL_DIR.exists():
        print(f"ERROR: No saved models in {MODEL_DIR}/")
        print("Run training first: python -m src.ml.training.train_fast_models <xlsx>")
        return

    # Load metadata
    with open(MODEL_DIR / "meta.json") as f:
        meta = json.load(f)

    feature_cols = meta['feature_columns']
    print(f"\nFeatures: {feature_cols}")

    # Load scaler
    scaler = joblib.load(MODEL_DIR / "scaler.pkl")

    # Load models
    models = {}
    for name, info in meta['models'].items():
        models[name] = {
            'model': joblib.load(MODEL_DIR / f"{name}.pkl"),
            'needs_scaling': info['needs_scaling']
        }
    print(f"Loaded models: {list(models.keys())}")

    # Load data
    print(f"\nLoading {xlsx_path}...")
    df = pd.read_excel(xlsx_path)
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Prepare features
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)
    df["label_win"] = (df["Net %"] > 0).astype(int)

    df_clean = df.dropna(subset=feature_cols + ["label_win"]).copy()
    print(f"Clean rows: {len(df_clean)}")

    X = df_clean[feature_cols].values.astype(np.float32)
    y_true = df_clean["label_win"].values.astype(np.int32)
    X_scaled = scaler.transform(X)

    # Predict with each model
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

    print("\n" + "=" * 70)
    print("PREDICTIONS")
    print("=" * 70)

    print(f"\n{'Model':<22} {'Acc':>7} {'Prec':>7} {'Recall':>7} {'F1':>7} {'WIN pred':>9} {'Actual':>7}")
    print("-" * 70)

    for name, data in models.items():
        model = data['model']
        if data['needs_scaling']:
            y_pred = model.predict(X_scaled)
        else:
            y_pred = model.predict(X)

        acc = accuracy_score(y_true, y_pred)
        prec = precision_score(y_true, y_pred, zero_division=0)
        rec = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        win_pred = (y_pred == 1).sum()
        actual_win = (y_true == 1).sum()

        print(f"{name:<22} {acc:>6.1%} {prec:>6.1%} {rec:>6.1%} {f1:>7.3f} {win_pred:>9} {actual_win:>7}")

    # Detailed analysis with best model
    best_name = meta['best_model']
    best_model = models[best_name]['model']

    print("\n" + "=" * 70)
    print(f"DETAILED ANALYSIS ({best_name.upper()})")
    print("=" * 70)

    if models[best_name]['needs_scaling']:
        y_pred_best = best_model.predict(X_scaled)
    else:
        y_pred_best = best_model.predict(X)

    df_clean['predicted_win'] = y_pred_best

    # When model predicts WIN
    predicted_wins = df_clean[df_clean['predicted_win'] == 1]
    if len(predicted_wins) > 0:
        actual_wr = (predicted_wins['label_win'] == 1).mean()
        avg_pnl = predicted_wins['Net %'].mean()
        print(f"\nWhen model says WIN ({len(predicted_wins)} trades):")
        print(f"  Actual Win Rate: {actual_wr:.1%}")
        print(f"  Avg PnL: {avg_pnl:+.2f}%")

    # When model predicts LOSS
    predicted_losses = df_clean[df_clean['predicted_win'] == 0]
    if len(predicted_losses) > 0:
        actual_wr = (predicted_losses['label_win'] == 1).mean()
        avg_pnl = predicted_losses['Net %'].mean()
        print(f"\nWhen model says LOSS ({len(predicted_losses)} trades):")
        print(f"  Actual Win Rate: {actual_wr:.1%}")
        print(f"  Avg PnL: {avg_pnl:+.2f}%")

    # Baseline
    print(f"\nBaseline (all trades): {len(df_clean)}")
    print(f"  Win Rate: {(y_true == 1).mean():.1%}")
    print(f"  Avg PnL: {df_clean['Net %'].mean():+.2f}%")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Train:   python -m src.ml.training.train_fast_models <xlsx>")
        print("  Predict: python -m src.ml.training.train_fast_models --predict <xlsx>")
        sys.exit(1)

    if sys.argv[1] == "--predict":
        if len(sys.argv) < 3:
            print("ERROR: Specify xlsx file for prediction")
            sys.exit(1)
        predict_file(sys.argv[2])
    else:
        train_fast_models(sys.argv[1])
