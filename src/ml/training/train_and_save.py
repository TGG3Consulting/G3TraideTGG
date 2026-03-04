# -*- coding: utf-8 -*-
"""
Train best model and save it.

Usage:
    # Train and save
    python -m src.ml.training.train_and_save backtester/output/backtest_20260222_200616.xlsx

    # Predict on file using saved model
    python -m src.ml.training.train_and_save --predict backtester/output/backtest_20260222_200616.xlsx
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

MODEL_DIR = Path("models/signal_classifier")


def train_and_save(xlsx_path: str):
    """Train best models and save them."""

    print("="*70)
    print("TRAIN AND SAVE BEST MODELS")
    print("="*70)

    # Load data
    print(f"\nLoading {xlsx_path}...")
    df = pd.read_excel(xlsx_path)
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)
    wins = (df["label_win"] == 1).sum()
    print(f"Win rate: {wins/len(df)*100:.1f}%")

    # Features
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)
    feature_cols = ["Prob", "R/R", "SL %", "TP1 %", "TP2 %", "TP3 %", "direction_num"]
    available_cols = [c for c in feature_cols if c in df.columns]

    df_clean = df.dropna(subset=available_cols + ["label_win"])
    print(f"Clean rows: {len(df_clean)}")

    # Temporal split
    df_clean = df_clean.sort_values("Timestamp").reset_index(drop=True)
    split_idx = int(len(df_clean) * 0.8)
    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"Train: {len(train_df)}, Test: {len(test_df)}")

    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)
    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Scale
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Train top 3 models
    from sklearn.svm import SVC
    from sklearn.ensemble import AdaBoostClassifier, RandomForestClassifier
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.metrics import f1_score, accuracy_score, precision_score, recall_score

    models = {}

    # 1. SVM (RBF) - winner
    print("\n[1/3] Training SVM (RBF)...")
    svm = SVC(kernel='rbf', C=1.0, gamma='scale', class_weight='balanced',
              probability=True, random_state=42)
    svm.fit(X_train_scaled, y_train)
    models['svm'] = {'model': svm, 'needs_scaling': True}
    y_pred = svm.predict(X_test_scaled)
    print(f"      F1: {f1_score(y_test, y_pred):.3f}, Acc: {accuracy_score(y_test, y_pred):.3f}")

    # 2. AdaBoost
    print("[2/3] Training AdaBoost...")
    base = DecisionTreeClassifier(max_depth=4, class_weight="balanced")
    ada = AdaBoostClassifier(estimator=base, n_estimators=200, learning_rate=0.1, random_state=42)
    ada.fit(X_train, y_train)
    models['adaboost'] = {'model': ada, 'needs_scaling': False}
    y_pred = ada.predict(X_test)
    print(f"      F1: {f1_score(y_test, y_pred):.3f}, Acc: {accuracy_score(y_test, y_pred):.3f}")

    # 3. Random Forest
    print("[3/3] Training Random Forest...")
    rf = RandomForestClassifier(n_estimators=300, max_depth=10, min_samples_leaf=5,
                                 class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)
    models['random_forest'] = {'model': rf, 'needs_scaling': False}
    y_pred = rf.predict(X_test)
    print(f"      F1: {f1_score(y_test, y_pred):.3f}, Acc: {accuracy_score(y_test, y_pred):.3f}")

    # Save models
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
        'models': list(models.keys()),
        'train_samples': len(train_df),
        'test_samples': len(test_df),
        'source_file': xlsx_path,
    }
    with open(MODEL_DIR / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  Saved: meta.json")

    print("\n✅ Models saved successfully!")


def predict_file(xlsx_path: str):
    """Load saved models and predict on file."""

    print("="*70)
    print("PREDICT USING SAVED MODELS")
    print("="*70)

    # Check models exist
    if not MODEL_DIR.exists():
        print(f"ERROR: No saved models in {MODEL_DIR}/")
        print("Run training first: python -m src.ml.training.train_and_save <xlsx>")
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
    for name in meta['models']:
        models[name] = joblib.load(MODEL_DIR / f"{name}.pkl")
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

    print("\n" + "="*70)
    print("PREDICTIONS")
    print("="*70)

    print(f"\n{'Model':<20} {'Acc':>8} {'Prec':>8} {'Recall':>8} {'F1':>8} {'WIN pred':>10} {'Actual WIN':>12}")
    print("-"*70)

    for name, model in models.items():
        if name == 'svm':
            y_pred = model.predict(X_scaled)
        else:
            y_pred = model.predict(X)

        acc = accuracy_score(y_true, y_pred)
        prec = precision_score(y_true, y_pred, zero_division=0)
        rec = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        win_pred = (y_pred == 1).sum()
        actual_win = (y_true == 1).sum()

        print(f"{name:<20} {acc:>7.1%} {prec:>7.1%} {rec:>7.1%} {f1:>8.3f} {win_pred:>10} {actual_win:>12}")

    # Detailed analysis with best model (SVM)
    print("\n" + "="*70)
    print("DETAILED ANALYSIS (SVM)")
    print("="*70)

    y_pred_svm = models['svm'].predict(X_scaled)
    df_clean['predicted_win'] = y_pred_svm

    # When model predicts WIN
    predicted_wins = df_clean[df_clean['predicted_win'] == 1]
    if len(predicted_wins) > 0:
        actual_wr = (predicted_wins['label_win'] == 1).mean()
        avg_pnl = predicted_wins['Net %'].mean()
        print(f"\nКогда модель говорит WIN ({len(predicted_wins)} сделок):")
        print(f"  Реальный Win Rate: {actual_wr:.1%}")
        print(f"  Средний PnL: {avg_pnl:+.2f}%")

    # When model predicts LOSS
    predicted_losses = df_clean[df_clean['predicted_win'] == 0]
    if len(predicted_losses) > 0:
        actual_wr = (predicted_losses['label_win'] == 1).mean()
        avg_pnl = predicted_losses['Net %'].mean()
        print(f"\nКогда модель говорит LOSS ({len(predicted_losses)} сделок):")
        print(f"  Реальный Win Rate: {actual_wr:.1%}")
        print(f"  Средний PnL: {avg_pnl:+.2f}%")

    # Baseline
    print(f"\nBaseline (все сделки): {len(df_clean)}")
    print(f"  Win Rate: {(y_true == 1).mean():.1%}")
    print(f"  Средний PnL: {df_clean['Net %'].mean():+.2f}%")

    print("\n" + "="*70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Train:   python -m src.ml.training.train_and_save <xlsx>")
        print("  Predict: python -m src.ml.training.train_and_save --predict <xlsx>")
        sys.exit(1)

    if sys.argv[1] == "--predict":
        if len(sys.argv) < 3:
            print("ERROR: Specify xlsx file for prediction")
            sys.exit(1)
        predict_file(sys.argv[2])
    else:
        train_and_save(sys.argv[1])
