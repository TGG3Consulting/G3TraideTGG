# -*- coding: utf-8 -*-
"""
Train ML models using ALL available features from backtest results.

Features used (55+):
  - Basic: Prob, R/R, SL %, TP1-3 %, direction, signal_type, valid_hours
  - Accumulation Score: 22 components
  - Futures: OI changes, funding, LS ratio, price changes
  - Spot: spread, price changes, volume spike, orderbook imbalance, buy ratio
  - Orderbook: bid/ask volumes, imbalances
  - Trigger: type, severity, score

Models (fast, <2 min):
  - XGBoost
  - LightGBM
  - CatBoost
  - Random Forest
  - Logistic Regression

Usage:
    # Train and save
    python -m src.ml.training.train_full_features backtester/output/backtest_YYYYMMDD_HHMMSS.xlsx

    # Predict on file
    python -m src.ml.training.train_full_features --predict backtester/output/backtest_YYYYMMDD_HHMMSS.xlsx
"""

import sys
import json
import time
import warnings
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings('ignore')

MODEL_DIR = Path("models/full_features")

# ============================================================================
# ТОП-30 ФИЧЕЙ - ИМЕНА 1:1 С EXCEL КОЛОНКАМИ
# ============================================================================

# Все 87 колонок Excel (для справки):
# 1-39: базовые + результаты бектеста
# 40-61: acc_* (accumulation score components)
# 62-70: futures data (oi_change, funding, ls_ratio, price_change)
# 71-78: spot data (spread, price_change, volume, orderbook, trades)
# 79-84: orderbook volumes (bid/ask ATR)
# 85-87: trigger (type, severity, score)

# ТОП-10 по важности (без orderbook - нет истор. данных)
TOP_20_FEATURES = [
    # === РЕЙТИНГ 18 (contrarian + funding) ===
    "acc_crowd_bearish",           # Excel col 44
    "acc_crowd_bullish",           # Excel col 45
    "funding_rate_pct",            # Excel col 65

    # === РЕЙТИНГ 17 (R/R + positioning) ===
    "Reward %",                    # Excel col 22
    "long_short_ratio",            # Excel col 68

    # === РЕЙТИНГ 16 (risk management) ===
    "R/R",                         # Excel col 8
    "acc_funding_cheap",           # Excel col 42
    "long_account_pct",            # Excel col 66
    "short_account_pct",           # Excel col 67

    # === РЕЙТИНГ 15 ===
    "Risk %",                      # Excel col 21
]

# Категориальная фича
CATEGORICAL_FEATURES = ["Direction"]

# Используем TOP_30
ALL_NUMERIC_FEATURES = TOP_20_FEATURES


def prepare_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Подготовить все фичи из DataFrame.

    Returns:
        (DataFrame с фичами, список имён колонок)
    """
    feature_cols = []

    # 1. Числовые фичи - берём те что есть
    for col in ALL_NUMERIC_FEATURES:
        if col in df.columns:
            feature_cols.append(col)

    # 2. Категориальные фичи - кодируем
    # Direction - единственная категориальная фича
    if "Direction" in df.columns:
        df["direction_num"] = (df["Direction"] == "LONG").astype(int)
        feature_cols.append("direction_num")

    return df, feature_cols


def train_full_features(xlsx_path: str):
    """Train models using ALL features."""

    print("=" * 70)
    print("TRAIN ML MODELS - FULL FEATURES")
    print("=" * 70)

    # Load data
    print(f"\nLoading {xlsx_path}...")
    df = pd.read_excel(xlsx_path)
    print(f"Total rows: {len(df)}")
    print(f"Columns: {len(df.columns)}")

    # Filter filled only
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Check for ML columns from TOP_30
    ml_cols_present = [c for c in TOP_20_FEATURES if c in df.columns]
    ml_cols_missing = [c for c in TOP_20_FEATURES if c not in df.columns]

    print(f"ML columns found: {len(ml_cols_present)} / {len(TOP_20_FEATURES)}")
    if ml_cols_missing:
        print(f"  Missing: {ml_cols_missing[:5]}{'...' if len(ml_cols_missing) > 5 else ''}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)
    wins = (df["label_win"] == 1).sum()
    win_rate = wins / len(df) * 100
    print(f"Win rate: {win_rate:.1f}%")

    if win_rate < 10:
        print("\n" + "!" * 70)
        print("WARNING: Win rate < 10% - models may not be useful!")
        print("!" * 70)

    # Prepare features
    df, feature_cols = prepare_features(df)
    print(f"\nTotal features: {len(feature_cols)}")

    # Show features used
    print("\nFeatures used:")
    acc_count = len([c for c in feature_cols if c.startswith('acc_')])
    print(f"  Accumulation: {acc_count}")
    print(f"  Futures:      {len([c for c in feature_cols if c in ['funding_rate_pct', 'long_short_ratio', 'long_account_pct', 'short_account_pct', 'oi_change_1h_pct', 'oi_change_5m_pct']])}")
    print(f"  Spot:         {len([c for c in feature_cols if c in ['spot_orderbook_imbalance', 'volume_spike_ratio', 'spot_imbalance_atr', 'buy_ratio_5m']])}")
    print(f"  Risk/Reward:  {len([c for c in feature_cols if c in ['R/R', 'Risk %', 'Reward %', 'SL %']])}")
    print(f"  Trigger:      {len([c for c in feature_cols if c.startswith('trigger')])}")
    print(f"  Other:        {len(feature_cols) - acc_count - 6 - 4 - 4 - 2}")

    # Clean NaN
    df_clean = df.dropna(subset=feature_cols + ["label_win"])
    print(f"\nClean rows: {len(df_clean)}")

    if len(df_clean) < 100:
        print("ERROR: Not enough data!")
        return

    # Temporal split
    df_clean = df_clean.sort_values("Timestamp").reset_index(drop=True)
    split_idx = int(len(df_clean) * 0.8)
    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"Train: {len(train_df)}, Test: {len(test_df)}")

    X_train = train_df[feature_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)
    X_test = test_df[feature_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Replace NaN/Inf with 0
    X_train = np.nan_to_num(X_train, nan=0.0, posinf=0.0, neginf=0.0)
    X_test = np.nan_to_num(X_test, nan=0.0, posinf=0.0, neginf=0.0)

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

        # Feature importance
        print("\n      Top 10 important features:")
        importances = list(zip(feature_cols, rf.feature_importances_))
        importances.sort(key=lambda x: x[1], reverse=True)
        for feat, imp in importances[:10]:
            print(f"        {feat:30s} {imp:.4f}")

    except Exception as e:
        print(f"      Error: {e}")

    # ================================================================
    # 3. XGBOOST
    # ================================================================
    print("\n[3/5] Training XGBoost...")
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
    print(f"Total features used: {len(feature_cols)}")

    # Best model
    best = results_sorted[0]
    print(f"\nBest model: {best['name']} (F1={best['f1']:.3f})")

    # ================================================================
    # THRESHOLD ANALYSIS
    # ================================================================
    print("\n" + "=" * 70)
    print("THRESHOLD ANALYSIS (Best Model: " + best['name'] + ")")
    print("=" * 70)

    best_model = models[best['key']]['model']
    needs_scaling = models[best['key']]['needs_scaling']

    if needs_scaling:
        y_proba = best_model.predict_proba(X_test_scaled)[:, 1]
    else:
        y_proba = best_model.predict_proba(X_test)[:, 1]

    print(f"\n{'Threshold':<10} {'Precision':>10} {'Recall':>10} {'F1':>10} {'WIN pred':>10} {'Actual WIN':>12}")
    print("-" * 70)

    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    best_threshold = 0.5
    best_threshold_f1 = 0

    for thresh in thresholds:
        y_pred_thresh = (y_proba >= thresh).astype(int)

        tp = ((y_pred_thresh == 1) & (y_test == 1)).sum()
        fp = ((y_pred_thresh == 1) & (y_test == 0)).sum()
        fn = ((y_pred_thresh == 0) & (y_test == 1)).sum()

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0

        win_pred = y_pred_thresh.sum()
        actual_win = (y_test == 1).sum()

        marker = " <-- best F1" if f1 > best_threshold_f1 else ""
        if f1 > best_threshold_f1:
            best_threshold_f1 = f1
            best_threshold = thresh

        # Highlight high precision
        prec_marker = " *" if prec >= 0.5 else ""

        print(f"{thresh:<10.1f} {prec:>9.1%}{prec_marker} {rec:>9.1%} {f1:>10.3f} {win_pred:>10} {actual_win:>12}{marker}")

    print("-" * 70)
    print(f"\n* = Precision >= 50% (model is correct more than 50%)")
    print(f"\nRecommendation: use threshold={best_threshold} for best F1")

    # Show practical example
    print("\n" + "=" * 70)
    print("PRACTICAL COMPARISON")
    print("=" * 70)

    # Default threshold 0.5
    y_pred_05 = (y_proba >= 0.5).astype(int)
    # High precision threshold
    high_thresh = 0.7
    y_pred_high = (y_proba >= high_thresh).astype(int)

    test_df_analysis = test_df.copy()
    test_df_analysis['pred_05'] = y_pred_05
    test_df_analysis['pred_high'] = y_pred_high

    # Test multiple thresholds for practical comparison
    for thresh in [0.5, 0.7, 0.8, 0.9]:
        y_pred_t = (y_proba >= thresh).astype(int)
        test_df_analysis[f'pred_{thresh}'] = y_pred_t
        trades_t = test_df_analysis[test_df_analysis[f'pred_{thresh}'] == 1]
        if len(trades_t) > 0:
            wr_t = (trades_t['label_win'] == 1).mean()
            pnl_t = trades_t['Net %'].mean()
            print(f"\nThreshold {thresh}: {len(trades_t)} сделок")
            print(f"  Win Rate: {wr_t:.1%}")
            print(f"  Avg PnL:  {pnl_t:+.2f}%")
        else:
            print(f"\nThreshold {thresh}: 0 сделок (слишком строгий)")

    # Baseline
    print(f"\nBaseline (все сделки): {len(test_df)} сделок")
    print(f"  Win Rate: {(y_test == 1).mean():.1%}")
    print(f"  Avg PnL:  {test_df['Net %'].mean():+.2f}%")

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
        'feature_columns': feature_cols,
        'models': {r['key']: {'needs_scaling': models[r['key']]['needs_scaling'],
                              'f1': r['f1'],
                              'auc': r['auc']} for r in results},
        'best_model': best['key'],
        'train_samples': len(train_df),
        'test_samples': len(test_df),
        'source_file': xlsx_path,
        'win_rate': win_rate,
        'total_features': len(feature_cols),
    }
    with open(MODEL_DIR / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Saved: meta.json")

    print("\nModels saved successfully!")


def predict_file(xlsx_path: str):
    """Load saved models and predict on file."""

    print("=" * 70)
    print("PREDICT USING SAVED FULL-FEATURES MODELS")
    print("=" * 70)

    # Check models exist
    if not MODEL_DIR.exists():
        print(f"ERROR: No saved models in {MODEL_DIR}/")
        print("Run training first: python -m src.ml.training.train_full_features <xlsx>")
        return

    # Load metadata
    with open(MODEL_DIR / "meta.json") as f:
        meta = json.load(f)

    feature_cols = meta['feature_columns']
    print(f"\nFeatures: {len(feature_cols)} columns")

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

    # Prepare features (same encoding as training)
    df, _ = prepare_features(df)

    # Check all feature columns exist
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        print(f"\nWARNING: Missing columns: {missing[:5]}...")
        # Fill missing with 0
        for c in missing:
            df[c] = 0

    df["label_win"] = (df["Net %"] > 0).astype(int)

    df_clean = df.dropna(subset=["label_win"]).copy()
    print(f"Clean rows: {len(df_clean)}")

    X = df_clean[feature_cols].values.astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
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

    # ================================================================
    # THRESHOLD ANALYSIS
    # ================================================================
    print("\n" + "=" * 70)
    print(f"THRESHOLD ANALYSIS ({best_name.upper()})")
    print("=" * 70)

    if models[best_name]['needs_scaling']:
        y_proba = best_model.predict_proba(X_scaled)[:, 1]
    else:
        y_proba = best_model.predict_proba(X)[:, 1]

    print(f"\n{'Threshold':<10} {'Precision':>10} {'Recall':>10} {'WIN pred':>10} {'Avg PnL':>10}")
    print("-" * 60)

    for thresh in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        y_pred_thresh = (y_proba >= thresh).astype(int)

        tp = ((y_pred_thresh == 1) & (y_true == 1)).sum()
        fp = ((y_pred_thresh == 1) & (y_true == 0)).sum()
        fn = ((y_pred_thresh == 0) & (y_true == 1)).sum()

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0

        win_pred = y_pred_thresh.sum()

        # Calculate avg PnL for predicted wins
        df_clean['_pred'] = y_pred_thresh
        predicted_trades = df_clean[df_clean['_pred'] == 1]
        avg_pnl = predicted_trades['Net %'].mean() if len(predicted_trades) > 0 else 0

        prec_marker = " *" if prec >= 0.5 else ""
        print(f"{thresh:<10.1f} {prec:>9.1%}{prec_marker} {rec:>9.1%} {win_pred:>10} {avg_pnl:>+9.2f}%")

    print("-" * 60)
    print(f"* = Precision >= 50%")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Train:   python -m src.ml.training.train_full_features <xlsx>")
        print("  Predict: python -m src.ml.training.train_full_features --predict <xlsx>")
        sys.exit(1)

    if sys.argv[1] == "--predict":
        if len(sys.argv) < 3:
            print("ERROR: Specify xlsx file for prediction")
            sys.exit(1)
        predict_file(sys.argv[2])
    else:
        train_full_features(sys.argv[1])
