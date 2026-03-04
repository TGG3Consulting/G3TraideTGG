# -*- coding: utf-8 -*-
"""
Compare ALL ML models on backtest results.

Usage:
    python -m src.ml.training.train_compare_all backtester/output/backtest_20260222_200616.xlsx
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')


def train_compare_all(xlsx_path: str, test_size: float = 0.2):
    """Compare all ML models."""

    print("="*70)
    print("ML MODEL COMPARISON")
    print("="*70)
    print(f"\nLoading {xlsx_path}...")

    # Load data
    df = pd.read_excel(xlsx_path)
    print(f"Total rows: {len(df)}")

    # Filter filled only
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)

    # Stats
    wins = (df["label_win"] == 1).sum()
    losses = (df["label_win"] == 0).sum()
    print(f"Wins: {wins}, Losses: {losses}, Win rate: {wins/len(df)*100:.1f}%")

    # Features
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)
    feature_cols = ["Prob", "R/R", "SL %", "TP1 %", "TP2 %", "TP3 %", "direction_num"]
    available_cols = [c for c in feature_cols if c in df.columns]

    # Clean
    df_clean = df.dropna(subset=available_cols + ["label_win"])
    print(f"Clean rows: {len(df_clean)}")
    print(f"Features: {available_cols}")

    if len(df_clean) < 100:
        print("ERROR: Not enough data!")
        return

    # Temporal split
    df_clean = df_clean.sort_values("Timestamp").reset_index(drop=True)
    split_idx = int(len(df_clean) * (1 - test_size))

    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"\nTrain: {len(train_df)}, Test: {len(test_df)}")

    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)
    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Results storage
    results = []

    # ================================================================
    # 1. LOGISTIC REGRESSION
    # ================================================================
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        print("\n[1/7] Training Logistic Regression...")
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        lr = LogisticRegression(random_state=42, max_iter=1000, class_weight="balanced")
        lr.fit(X_train_scaled, y_train)

        y_pred = lr.predict(X_test_scaled)
        results.append(calc_metrics("Logistic Regression", y_test, y_pred, lr.predict_proba(X_test_scaled)[:, 1]))
        print("       Done!")

    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 2. RANDOM FOREST
    # ================================================================
    try:
        from sklearn.ensemble import RandomForestClassifier

        print("[2/7] Training Random Forest...")
        rf = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        )
        rf.fit(X_train, y_train)

        y_pred = rf.predict(X_test)
        results.append(calc_metrics("Random Forest", y_test, y_pred, rf.predict_proba(X_test)[:, 1]))
        print("       Done!")

    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 3. GRADIENT BOOSTING (sklearn)
    # ================================================================
    try:
        from sklearn.ensemble import GradientBoostingClassifier

        print("[3/7] Training Gradient Boosting...")
        gb = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_leaf=10,
            learning_rate=0.1,
            random_state=42
        )
        gb.fit(X_train, y_train)

        y_pred = gb.predict(X_test)
        results.append(calc_metrics("Gradient Boosting", y_test, y_pred, gb.predict_proba(X_test)[:, 1]))
        print("       Done!")

    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 4. XGBOOST
    # ================================================================
    try:
        import xgboost as xgb

        print("[4/7] Training XGBoost...")

        # Calculate scale_pos_weight for imbalanced data
        scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()

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

        y_pred = xgb_model.predict(X_test)
        results.append(calc_metrics("XGBoost", y_test, y_pred, xgb_model.predict_proba(X_test)[:, 1]))
        print("       Done!")

    except ImportError:
        print("       XGBoost not installed. Run: pip install xgboost")
    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 5. LIGHTGBM
    # ================================================================
    try:
        import lightgbm as lgb

        print("[5/7] Training LightGBM...")

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

        y_pred = lgb_model.predict(X_test)
        results.append(calc_metrics("LightGBM", y_test, y_pred, lgb_model.predict_proba(X_test)[:, 1]))
        print("       Done!")

    except ImportError:
        print("       LightGBM not installed. Run: pip install lightgbm")
    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 6. CATBOOST
    # ================================================================
    try:
        from catboost import CatBoostClassifier

        print("[6/7] Training CatBoost...")

        cb_model = CatBoostClassifier(
            iterations=200,
            depth=6,
            learning_rate=0.1,
            auto_class_weights="Balanced",
            random_state=42,
            verbose=False
        )
        cb_model.fit(X_train, y_train)

        y_pred = cb_model.predict(X_test)
        results.append(calc_metrics("CatBoost", y_test, y_pred, cb_model.predict_proba(X_test)[:, 1]))
        print("       Done!")

    except ImportError:
        print("       CatBoost not installed. Run: pip install catboost")
    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # 7. NEURAL NETWORK (MLP)
    # ================================================================
    try:
        from sklearn.neural_network import MLPClassifier
        from sklearn.preprocessing import StandardScaler

        print("[7/7] Training Neural Network (MLP)...")

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        mlp = MLPClassifier(
            hidden_layer_sizes=(64, 32, 16),
            activation='relu',
            max_iter=500,
            early_stopping=True,
            validation_fraction=0.1,
            random_state=42,
            verbose=False
        )
        mlp.fit(X_train_scaled, y_train)

        y_pred = mlp.predict(X_test_scaled)
        results.append(calc_metrics("Neural Network", y_test, y_pred, mlp.predict_proba(X_test_scaled)[:, 1]))
        print("       Done!")

    except Exception as e:
        print(f"       Error: {e}")

    # ================================================================
    # RESULTS TABLE
    # ================================================================
    if not results:
        print("\nNo models trained successfully!")
        return

    print("\n" + "="*70)
    print("RESULTS COMPARISON")
    print("="*70)

    # Sort by F1 Score
    results_sorted = sorted(results, key=lambda x: x['f1'], reverse=True)

    # Print table
    print(f"\n{'Model':<22} {'Accuracy':>8} {'Precision':>10} {'Recall':>8} {'F1':>8} {'AUC':>8}")
    print("-"*70)

    for r in results_sorted:
        print(f"{r['name']:<22} {r['accuracy']:>8.1%} {r['precision']:>10.1%} {r['recall']:>8.1%} {r['f1']:>8.3f} {r['auc']:>8.3f}")

    # Best model
    best = results_sorted[0]
    print("-"*70)
    print(f"\n🏆 BEST MODEL: {best['name']}")
    print(f"   Accuracy:  {best['accuracy']:.1%}")
    print(f"   Precision: {best['precision']:.1%} (когда говорит WIN — в {best['precision']:.0%} прав)")
    print(f"   Recall:    {best['recall']:.1%} (ловит {best['recall']:.0%} всех WIN)")
    print(f"   F1 Score:  {best['f1']:.3f}")
    print(f"   AUC-ROC:   {best['auc']:.3f}")

    # Recommendations
    print("\n" + "="*70)
    print("RECOMMENDATIONS")
    print("="*70)

    # Find best precision model
    best_precision = max(results, key=lambda x: x['precision'])
    best_recall = max(results, key=lambda x: x['recall'])
    best_auc = max(results, key=lambda x: x['auc'])

    print(f"\n• Лучший Precision (меньше ложных WIN): {best_precision['name']} ({best_precision['precision']:.1%})")
    print(f"• Лучший Recall (больше пойманных WIN):  {best_recall['name']} ({best_recall['recall']:.1%})")
    print(f"• Лучший AUC-ROC (общее качество):       {best_auc['name']} ({best_auc['auc']:.3f})")

    print("\n" + "="*70)


def calc_metrics(name: str, y_true, y_pred, y_proba) -> dict:
    """Calculate all metrics for a model."""
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

    return {
        'name': name,
        'accuracy': accuracy_score(y_true, y_pred),
        'precision': precision_score(y_true, y_pred, zero_division=0),
        'recall': recall_score(y_true, y_pred, zero_division=0),
        'f1': f1_score(y_true, y_pred, zero_division=0),
        'auc': roc_auc_score(y_true, y_proba) if len(np.unique(y_true)) > 1 else 0,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.ml.training.train_compare_all <xlsx_path>")
        print("Example: python -m src.ml.training.train_compare_all backtester/output/backtest_20260222_200616.xlsx")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    train_compare_all(xlsx_path)
