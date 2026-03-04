# -*- coding: utf-8 -*-
"""
TOP-10 ML Models for Trading Signal Classification.

Based on Kaggle competitions, hedge fund research, and academic papers.

Usage:
    python -m src.ml.training.train_top10 backtester/output/backtest_20260222_200616.xlsx
"""

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')


def train_top10(xlsx_path: str, test_size: float = 0.2):
    """Compare TOP-10 ML models for trading."""

    print("="*80)
    print("TOP-10 ML MODELS FOR TRADING — COMPARISON")
    print("="*80)
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
    win_rate = wins/len(df)*100
    print(f"Wins: {wins}, Losses: {losses}, Win rate: {win_rate:.1f}%")

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
    print(f"Train period: {train_df['Timestamp'].min()} → {train_df['Timestamp'].max()}")
    print(f"Test period:  {test_df['Timestamp'].min()} → {test_df['Timestamp'].max()}")

    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)
    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Scale for some models
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Class weight for imbalanced data
    scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)

    results = []
    total_models = 10

    print("\n" + "-"*80)
    print("TRAINING MODELS")
    print("-"*80)

    # ================================================================
    # 1. XGBOOST
    # ================================================================
    try:
        import xgboost as xgb
        print(f"\n[1/{total_models}] XGBoost...")
        start = time.time()

        model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=scale_pos_weight,
            random_state=42,
            n_jobs=-1,
            verbosity=0,
            eval_metric='logloss'
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("XGBoost", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except ImportError:
        print(f"[1/{total_models}] XGBoost — NOT INSTALLED (pip install xgboost)")
    except Exception as e:
        print(f"[1/{total_models}] XGBoost — Error: {e}")

    # ================================================================
    # 2. LIGHTGBM
    # ================================================================
    try:
        import lightgbm as lgb
        print(f"\n[2/{total_models}] LightGBM...")
        start = time.time()

        model = lgb.LGBMClassifier(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
            verbosity=-1
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("LightGBM", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except ImportError:
        print(f"[2/{total_models}] LightGBM — NOT INSTALLED (pip install lightgbm)")
    except Exception as e:
        print(f"[2/{total_models}] LightGBM — Error: {e}")

    # ================================================================
    # 3. CATBOOST
    # ================================================================
    try:
        from catboost import CatBoostClassifier
        print(f"\n[3/{total_models}] CatBoost...")
        start = time.time()

        model = CatBoostClassifier(
            iterations=300,
            depth=6,
            learning_rate=0.05,
            auto_class_weights="Balanced",
            random_state=42,
            verbose=False
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("CatBoost", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except ImportError:
        print(f"[3/{total_models}] CatBoost — NOT INSTALLED (pip install catboost)")
    except Exception as e:
        print(f"[3/{total_models}] CatBoost — Error: {e}")

    # ================================================================
    # 4. RANDOM FOREST
    # ================================================================
    try:
        from sklearn.ensemble import RandomForestClassifier
        print(f"\n[4/{total_models}] Random Forest...")
        start = time.time()

        model = RandomForestClassifier(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("Random Forest", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[4/{total_models}] Random Forest — Error: {e}")

    # ================================================================
    # 5. EXTRA TREES
    # ================================================================
    try:
        from sklearn.ensemble import ExtraTreesClassifier
        print(f"\n[5/{total_models}] Extra Trees...")
        start = time.time()

        model = ExtraTreesClassifier(
            n_estimators=300,
            max_depth=10,
            min_samples_leaf=5,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("Extra Trees", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[5/{total_models}] Extra Trees — Error: {e}")

    # ================================================================
    # 6. HIST GRADIENT BOOSTING
    # ================================================================
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        print(f"\n[6/{total_models}] HistGradientBoosting...")
        start = time.time()

        model = HistGradientBoostingClassifier(
            max_iter=300,
            max_depth=6,
            learning_rate=0.05,
            class_weight="balanced",
            random_state=42
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("HistGradientBoost", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[6/{total_models}] HistGradientBoosting — Error: {e}")

    # ================================================================
    # 7. ADABOOST
    # ================================================================
    try:
        from sklearn.ensemble import AdaBoostClassifier
        from sklearn.tree import DecisionTreeClassifier
        print(f"\n[7/{total_models}] AdaBoost...")
        start = time.time()

        base = DecisionTreeClassifier(max_depth=4, class_weight="balanced")
        model = AdaBoostClassifier(
            estimator=base,
            n_estimators=200,
            learning_rate=0.1,
            random_state=42
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("AdaBoost", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[7/{total_models}] AdaBoost — Error: {e}")

    # ================================================================
    # 8. SVM (RBF)
    # ================================================================
    try:
        from sklearn.svm import SVC
        print(f"\n[8/{total_models}] SVM (RBF)...")
        start = time.time()

        model = SVC(
            kernel='rbf',
            C=1.0,
            gamma='scale',
            class_weight='balanced',
            probability=True,
            random_state=42
        )
        model.fit(X_train_scaled, y_train)

        y_pred = model.predict(X_test_scaled)
        y_proba = model.predict_proba(X_test_scaled)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("SVM (RBF)", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[8/{total_models}] SVM — Error: {e}")

    # ================================================================
    # 9. LOGISTIC REGRESSION
    # ================================================================
    try:
        from sklearn.linear_model import LogisticRegression
        print(f"\n[9/{total_models}] Logistic Regression...")
        start = time.time()

        model = LogisticRegression(
            C=1.0,
            max_iter=1000,
            class_weight="balanced",
            random_state=42
        )
        model.fit(X_train_scaled, y_train)

        y_pred = model.predict(X_test_scaled)
        y_proba = model.predict_proba(X_test_scaled)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("Logistic Regression", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[9/{total_models}] Logistic Regression — Error: {e}")

    # ================================================================
    # 10. NEURAL NETWORK (MLP)
    # ================================================================
    try:
        from sklearn.neural_network import MLPClassifier
        print(f"\n[10/{total_models}] Neural Network (MLP)...")
        start = time.time()

        model = MLPClassifier(
            hidden_layer_sizes=(128, 64, 32),
            activation='relu',
            solver='adam',
            alpha=0.001,
            batch_size=64,
            max_iter=500,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=20,
            random_state=42,
            verbose=False
        )
        model.fit(X_train_scaled, y_train)

        y_pred = model.predict(X_test_scaled)
        y_proba = model.predict_proba(X_test_scaled)[:, 1]
        elapsed = time.time() - start

        results.append(calc_metrics("Neural Network", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[10/{total_models}] Neural Network — Error: {e}")

    # ================================================================
    # RESULTS TABLE
    # ================================================================
    if not results:
        print("\nNo models trained successfully!")
        return

    print("\n" + "="*80)
    print("RESULTS — SORTED BY F1 SCORE")
    print("="*80)

    # Sort by F1 Score
    results_sorted = sorted(results, key=lambda x: x['f1'], reverse=True)

    # Print table header
    print(f"\n{'#':<3} {'Model':<22} {'Acc':>7} {'Prec':>7} {'Recall':>7} {'F1':>7} {'AUC':>7} {'Time':>7}")
    print("-"*80)

    for i, r in enumerate(results_sorted, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"{medal}{i:<2} {r['name']:<22} {r['accuracy']:>6.1%} {r['precision']:>6.1%} {r['recall']:>6.1%} {r['f1']:>7.3f} {r['auc']:>7.3f} {r['time']:>6.1f}s")

    # Best models analysis
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)

    best = results_sorted[0]
    print(f"\n🏆 ЛУЧШАЯ МОДЕЛЬ: {best['name']}")
    print(f"   • Accuracy:  {best['accuracy']:.1%} — доля правильных предсказаний")
    print(f"   • Precision: {best['precision']:.1%} — когда говорит WIN, в {best['precision']:.0%} прав")
    print(f"   • Recall:    {best['recall']:.1%} — ловит {best['recall']:.0%} всех выигрышных сделок")
    print(f"   • F1 Score:  {best['f1']:.3f} — баланс precision/recall")
    print(f"   • AUC-ROC:   {best['auc']:.3f} — качество разделения классов")

    # Best by each metric
    print("\n📊 ЛУЧШИЕ ПО МЕТРИКАМ:")
    best_acc = max(results, key=lambda x: x['accuracy'])
    best_prec = max(results, key=lambda x: x['precision'])
    best_rec = max(results, key=lambda x: x['recall'])
    best_auc = max(results, key=lambda x: x['auc'])
    fastest = min(results, key=lambda x: x['time'])

    print(f"   • Accuracy:  {best_acc['name']} ({best_acc['accuracy']:.1%})")
    print(f"   • Precision: {best_prec['name']} ({best_prec['precision']:.1%})")
    print(f"   • Recall:    {best_rec['name']} ({best_rec['recall']:.1%})")
    print(f"   • AUC-ROC:   {best_auc['name']} ({best_auc['auc']:.3f})")
    print(f"   • Fastest:   {fastest['name']} ({fastest['time']:.1f}s)")

    # Recommendation
    print("\n" + "="*80)
    print("RECOMMENDATION")
    print("="*80)

    # Find models with good balance
    good_models = [r for r in results_sorted if r['f1'] >= 0.5 and r['auc'] >= 0.7]

    if good_models:
        print(f"\n✅ Рекомендуемые модели (F1 >= 0.5, AUC >= 0.7):")
        for r in good_models[:3]:
            print(f"   • {r['name']}: F1={r['f1']:.3f}, AUC={r['auc']:.3f}")
    else:
        print(f"\n⚠️ Нет моделей с F1 >= 0.5 и AUC >= 0.7")
        print(f"   Лучшая доступная: {best['name']}")

    print("\n" + "="*80)


def calc_metrics(name: str, y_true, y_pred, y_proba, elapsed: float) -> dict:
    """Calculate all metrics for a model."""
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

    return {
        'name': name,
        'accuracy': accuracy_score(y_true, y_pred),
        'precision': precision_score(y_true, y_pred, zero_division=0),
        'recall': recall_score(y_true, y_pred, zero_division=0),
        'f1': f1_score(y_true, y_pred, zero_division=0),
        'auc': roc_auc_score(y_true, y_proba) if len(np.unique(y_true)) > 1 else 0,
        'time': elapsed,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.ml.training.train_top10 <xlsx_path>")
        print("Example: python -m src.ml.training.train_top10 backtester/output/backtest_20260222_200616.xlsx")
        sys.exit(1)

    xlsx_path = sys.argv[1]
    train_top10(xlsx_path)
