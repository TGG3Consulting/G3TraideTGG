# -*- coding: utf-8 -*-
"""
TOP-10 ML Models for Trading — Based on Academic Research

Sources:
- ScienceDirect 2025: Trading Signal Prediction
- PMC/PLOS One: ML Models Benchmark (91.27% RF, 85.51% XGBoost)
- Nature: Deep Learning Stock Prediction
- ArXiv: Gradient Boosting Benchmark
- Neptune.ai: CatBoost vs XGBoost vs LightGBM

Usage:
    python -m src.ml.training.train_top10_research backtester/output/backtest_20260222_200616.xlsx
"""

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')


def train_top10_research(xlsx_path: str, test_size: float = 0.2):
    """Compare TOP-10 ML models based on academic research."""

    print("="*80)
    print("TOP-10 ML MODELS FOR TRADING — RESEARCH-BASED BENCHMARK")
    print("="*80)
    print("\nSources: ScienceDirect, PMC, Nature, ArXiv, Neptune.ai")
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

    # For LSTM/GRU - reshape to 3D
    X_train_seq = X_train_scaled.reshape((X_train_scaled.shape[0], 1, X_train_scaled.shape[1]))
    X_test_seq = X_test_scaled.reshape((X_test_scaled.shape[0], 1, X_test_scaled.shape[1]))

    # Class weight
    scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)

    results = []
    total_models = 10

    print("\n" + "-"*80)
    print("TRAINING MODELS (Research-validated)")
    print("-"*80)

    # ================================================================
    # 1. XGBOOST (85.51% in PLOS One study)
    # ================================================================
    try:
        import xgboost as xgb
        print(f"\n[1/{total_models}] XGBoost (PLOS One: 85.51%)...")
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
        print(f"[1/{total_models}] XGBoost — pip install xgboost")
    except Exception as e:
        print(f"[1/{total_models}] XGBoost — Error: {e}")

    # ================================================================
    # 2. LIGHTGBM (AUC 0.95, F1 0.80 in Research Square)
    # ================================================================
    try:
        import lightgbm as lgb
        print(f"\n[2/{total_models}] LightGBM (Research Square: AUC 0.95)...")
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
        print(f"[2/{total_models}] LightGBM — pip install lightgbm")
    except Exception as e:
        print(f"[2/{total_models}] LightGBM — Error: {e}")

    # ================================================================
    # 3. CATBOOST (Most stable AUC/F1 in ArXiv benchmark)
    # ================================================================
    try:
        from catboost import CatBoostClassifier
        print(f"\n[3/{total_models}] CatBoost (ArXiv: most stable)...")
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
        print(f"[3/{total_models}] CatBoost — pip install catboost")
    except Exception as e:
        print(f"[3/{total_models}] CatBoost — Error: {e}")

    # ================================================================
    # 4. RANDOM FOREST (91.27% in PMC study)
    # ================================================================
    try:
        from sklearn.ensemble import RandomForestClassifier
        print(f"\n[4/{total_models}] Random Forest (PMC: 91.27%)...")
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
    # 5. SVM RBF (93.7% in Ghana Stock Exchange study)
    # ================================================================
    try:
        from sklearn.svm import SVC
        print(f"\n[5/{total_models}] SVM RBF (Ghana study: 93.7%)...")
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
        print(f"[5/{total_models}] SVM — Error: {e}")

    # ================================================================
    # 6. LSTM (Nature: best for temporal data)
    # ================================================================
    try:
        import os
        os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
        import tensorflow as tf
        tf.get_logger().setLevel('ERROR')
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        from tensorflow.keras.callbacks import EarlyStopping

        print(f"\n[6/{total_models}] LSTM (Nature: best temporal)...")
        start = time.time()

        model = Sequential([
            LSTM(64, input_shape=(1, len(available_cols)), return_sequences=True),
            Dropout(0.2),
            LSTM(32),
            Dropout(0.2),
            Dense(16, activation='relu'),
            Dense(1, activation='sigmoid')
        ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])

        early_stop = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
        model.fit(X_train_seq, y_train, epochs=100, batch_size=32,
                  validation_split=0.1, callbacks=[early_stop], verbose=0)

        y_proba = model.predict(X_test_seq, verbose=0).flatten()
        y_pred = (y_proba > 0.5).astype(int)
        elapsed = time.time() - start

        results.append(calc_metrics("LSTM", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except ImportError:
        print(f"[6/{total_models}] LSTM — pip install tensorflow")
    except Exception as e:
        print(f"[6/{total_models}] LSTM — Error: {e}")

    # ================================================================
    # 7. GRU (Nature: faster than LSTM, similar accuracy)
    # ================================================================
    try:
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import GRU, Dense, Dropout
        from tensorflow.keras.callbacks import EarlyStopping

        print(f"\n[7/{total_models}] GRU (Nature: faster LSTM)...")
        start = time.time()

        model = Sequential([
            GRU(64, input_shape=(1, len(available_cols)), return_sequences=True),
            Dropout(0.2),
            GRU(32),
            Dropout(0.2),
            Dense(16, activation='relu'),
            Dense(1, activation='sigmoid')
        ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])

        early_stop = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
        model.fit(X_train_seq, y_train, epochs=100, batch_size=32,
                  validation_split=0.1, callbacks=[early_stop], verbose=0)

        y_proba = model.predict(X_test_seq, verbose=0).flatten()
        y_pred = (y_proba > 0.5).astype(int)
        elapsed = time.time() - start

        results.append(calc_metrics("GRU", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except ImportError:
        print(f"[7/{total_models}] GRU — pip install tensorflow")
    except Exception as e:
        print(f"[7/{total_models}] GRU — Error: {e}")

    # ================================================================
    # 8. MLP / ANN (PLOS One: 70%+ on all indices)
    # ================================================================
    try:
        from sklearn.neural_network import MLPClassifier
        print(f"\n[8/{total_models}] MLP/ANN (PLOS: 70%+ all indices)...")
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

        results.append(calc_metrics("MLP/ANN", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[8/{total_models}] MLP/ANN — Error: {e}")

    # ================================================================
    # 9. LOGISTIC REGRESSION (PMC: 85.51% baseline)
    # ================================================================
    try:
        from sklearn.linear_model import LogisticRegression
        print(f"\n[9/{total_models}] Logistic Regression (PMC: 85.51%)...")
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

        results.append(calc_metrics("Logistic Reg.", y_test, y_pred, y_proba, elapsed))
        print(f"         Done! ({elapsed:.1f}s)")

    except Exception as e:
        print(f"[9/{total_models}] Logistic Regression — Error: {e}")

    # ================================================================
    # 10. ADABOOST (PLOS One: stable performer)
    # ================================================================
    try:
        from sklearn.ensemble import AdaBoostClassifier
        from sklearn.tree import DecisionTreeClassifier
        print(f"\n[10/{total_models}] AdaBoost (PLOS: stable)...")
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
        print(f"[10/{total_models}] AdaBoost — Error: {e}")

    # ================================================================
    # RESULTS TABLE
    # ================================================================
    if not results:
        print("\nNo models trained successfully!")
        return

    print("\n" + "="*80)
    print("RESULTS — SORTED BY F1 SCORE")
    print("="*80)

    results_sorted = sorted(results, key=lambda x: x['f1'], reverse=True)

    print(f"\n{'#':<3} {'Model':<18} {'Acc':>7} {'Prec':>7} {'Recall':>7} {'F1':>7} {'AUC':>7} {'Time':>7}")
    print("-"*80)

    for i, r in enumerate(results_sorted, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"{medal}{i:<2} {r['name']:<18} {r['accuracy']:>6.1%} {r['precision']:>6.1%} {r['recall']:>6.1%} {r['f1']:>7.3f} {r['auc']:>7.3f} {r['time']:>6.1f}s")

    # Analysis
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)

    best = results_sorted[0]
    print(f"\n🏆 ЛУЧШАЯ МОДЕЛЬ: {best['name']}")
    print(f"   Accuracy:  {best['accuracy']:.1%}")
    print(f"   Precision: {best['precision']:.1%}")
    print(f"   Recall:    {best['recall']:.1%}")
    print(f"   F1:        {best['f1']:.3f}")
    print(f"   AUC-ROC:   {best['auc']:.3f}")

    # Best by metric
    print("\n📊 ЛУЧШИЕ ПО МЕТРИКАМ:")
    print(f"   Accuracy:  {max(results, key=lambda x: x['accuracy'])['name']}")
    print(f"   Precision: {max(results, key=lambda x: x['precision'])['name']}")
    print(f"   Recall:    {max(results, key=lambda x: x['recall'])['name']}")
    print(f"   AUC-ROC:   {max(results, key=lambda x: x['auc'])['name']}")
    print(f"   Fastest:   {min(results, key=lambda x: x['time'])['name']}")

    # Recommendations
    good = [r for r in results_sorted if r['f1'] >= 0.5 and r['auc'] >= 0.7]
    print("\n✅ РЕКОМЕНДУЕМЫЕ (F1 >= 0.5, AUC >= 0.7):")
    for r in good[:5]:
        print(f"   • {r['name']}: F1={r['f1']:.3f}, AUC={r['auc']:.3f}")

    print("\n" + "="*80)


def calc_metrics(name: str, y_true, y_pred, y_proba, elapsed: float) -> dict:
    """Calculate metrics."""
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
        print("Usage: python -m src.ml.training.train_top10_research <xlsx_path>")
        sys.exit(1)

    train_top10_research(sys.argv[1])
