# -*- coding: utf-8 -*-
"""
Train ML model from XLSX backtest results.

Uses ALL 109 features from backtester output.
Saves trained model to models/xlsx_trained/

Usage:
    python -m src.ml.training.train_from_xlsx backtester/output/backtest_XXXXXX.xlsx
"""

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def train_from_xlsx(xlsx_path: str, test_size: float = 0.2, save_model: bool = True):
    """
    Train ML model from XLSX backtest results with ALL features.

    Args:
        xlsx_path: Path to XLSX file
        test_size: Fraction for test set
        save_model: Whether to save trained model
    """
    print(f"Loading {xlsx_path}...")

    # Load data
    df = pd.read_excel(xlsx_path)
    print(f"Total rows: {len(df)}")

    # Filter filled only
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    if len(df) < 100:
        print("ERROR: Not enough filled trades!")
        return

    # Create target from REAL PnL
    df["label_win"] = (df["Net %"] > 0).astype(int)

    # Stats
    wins = (df["label_win"] == 1).sum()
    losses = (df["label_win"] == 0).sum()
    print(f"Wins: {wins}, Losses: {losses}")
    print(f"Win rate: {wins/len(df)*100:.1f}%")
    print()

    # Get ALL numeric feature columns (109 features)
    feature_prefixes = [
        'acc_', 'futures_', 'spot_', 'signal_details_', 'trigger_',
        'config_', 'oi_history_', 'funding_history_', 'price_history_',
        'entry_zone_', 'evidence_'
    ]
    feature_cols = [c for c in df.columns if any(c.startswith(p) for p in feature_prefixes)]

    # Add basic signal columns
    basic_cols = ['Prob', 'R/R', 'SL %', 'TP1 %', 'TP2 %', 'TP3 %', 'signal_hour', 'signal_minute', 'signal_day_of_week']
    feature_cols.extend([c for c in basic_cols if c in df.columns])

    # Remove non-numeric columns
    feature_cols = [c for c in feature_cols if c not in ['trigger_type', 'scenario_bullish', 'scenario_bearish', 'evidence_text']]

    print(f"Total features: {len(feature_cols)}")

    # Clean data - convert to numeric
    df_clean = df.dropna(subset=['label_win']).copy()
    for c in feature_cols:
        if c in df_clean.columns:
            df_clean[c] = pd.to_numeric(df_clean[c], errors='coerce').fillna(0)

    # Temporal split (NEVER random for time series!)
    df_clean = df_clean.sort_values('Timestamp').reset_index(drop=True)
    split_idx = int(len(df_clean) * (1 - test_size))

    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"Train: {len(train_df)}, Test: {len(test_df)}")
    print(f"Train wins: {train_df['label_win'].sum()} ({train_df['label_win'].mean()*100:.1f}%)")
    print(f"Test wins: {test_df['label_win'].sum()} ({test_df['label_win'].mean()*100:.1f}%)")
    print()

    available_cols = [c for c in feature_cols if c in train_df.columns]
    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df['label_win'].values.astype(np.int32)
    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df['label_win'].values.astype(np.int32)

    # Train models
    try:
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score, classification_report

        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        print("=" * 60)
        print(f"TRAINING WITH {len(available_cols)} FEATURES")
        print("=" * 60)

        models = {
            'GradientBoosting': GradientBoostingClassifier(n_estimators=100, max_depth=5, random_state=42),
            'RandomForest': RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=-1),
            'LogisticRegression': LogisticRegression(max_iter=1000, random_state=42),
        }

        best_model = None
        best_f1 = 0
        best_name = ''
        results = {}

        for name, model in models.items():
            print(f"\n{name}:")
            model.fit(X_train_scaled, y_train)
            y_pred = model.predict(X_test_scaled)

            acc = accuracy_score(y_test, y_pred)
            prec = precision_score(y_test, y_pred, zero_division=0)
            rec = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)

            print(f"  Accuracy:  {acc*100:.2f}%")
            print(f"  Precision: {prec*100:.2f}%")
            print(f"  Recall:    {rec*100:.2f}%")
            print(f"  F1 Score:  {f1*100:.2f}%")

            results[name] = {'accuracy': acc, 'precision': prec, 'recall': rec, 'f1': f1}

            if f1 > best_f1:
                best_f1 = f1
                best_model = model
                best_name = name

        # Best model detailed report
        print()
        print("=" * 60)
        print(f"BEST MODEL: {best_name}")
        print("=" * 60)

        y_pred_best = best_model.predict(X_test_scaled)
        print()
        print(classification_report(y_test, y_pred_best, target_names=['LOSS', 'WIN']))

        # ============================================================
        # THRESHOLD ANALYSIS (0.50 to 0.95)
        # ============================================================
        print()
        print("=" * 60)
        print("THRESHOLD ANALYSIS FOR ALL MODELS")
        print("=" * 60)

        thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

        for name, model in models.items():
            print(f"\n{'='*60}")
            print(f"MODEL: {name}")
            print(f"{'='*60}")
            print(f"{'Threshold':<10} {'Precision':<12} {'Recall':<12} {'F1':<12} {'Predicted WIN':<15} {'Actual WIN':<12}")
            print("-" * 75)

            y_proba = model.predict_proba(X_test_scaled)[:, 1]
            actual_wins = y_test.sum()

            for thresh in thresholds:
                y_pred_thresh = (y_proba >= thresh).astype(int)

                prec = precision_score(y_test, y_pred_thresh, zero_division=0)
                rec = recall_score(y_test, y_pred_thresh, zero_division=0)
                f1 = f1_score(y_test, y_pred_thresh, zero_division=0)
                predicted_wins = y_pred_thresh.sum()

                print(f"{thresh:<10.2f} {prec*100:<12.2f} {rec*100:<12.2f} {f1*100:<12.2f} {predicted_wins:<15} {actual_wins:<12}")

        print()
        print("=" * 60)
        print("LEGEND:")
        print("  Precision = TP / (TP + FP) - % правильных среди предсказанных WIN")
        print("  Recall    = TP / (TP + FN) - % найденных реальных WIN")
        print("  F1        = гармоническое среднее Precision и Recall")
        print("=" * 60)

        # Feature importance
        if hasattr(best_model, 'feature_importances_'):
            importances = best_model.feature_importances_
            top_idx = np.argsort(importances)[-15:][::-1]
            print("\nTop 15 most important features:")
            for i in top_idx:
                print(f"  {available_cols[i]}: {importances[i]:.4f}")

        # Save model
        if save_model:
            model_dir = Path("models/xlsx_trained")
            model_dir.mkdir(parents=True, exist_ok=True)

            with open(model_dir / "win_classifier.pkl", "wb") as f:
                pickle.dump(best_model, f)

            with open(model_dir / "scaler.pkl", "wb") as f:
                pickle.dump(scaler, f)

            with open(model_dir / "feature_columns.json", "w") as f:
                json.dump(available_cols, f, indent=2)

            with open(model_dir / "training_results.json", "w") as f:
                json.dump({
                    'best_model': best_name,
                    'features_count': len(available_cols),
                    'train_size': len(train_df),
                    'test_size': len(test_df),
                    'results': results,
                    'xlsx_path': xlsx_path,
                }, f, indent=2)

            print()
            print("=" * 60)
            print("MODEL SAVED")
            print("=" * 60)
            print(f"  Directory: {model_dir}")
            print(f"  Files:")
            print(f"    - win_classifier.pkl")
            print(f"    - scaler.pkl")
            print(f"    - feature_columns.json ({len(available_cols)} features)")
            print(f"    - training_results.json")

    except ImportError as e:
        print(f"sklearn not installed: {e}")
        print("Install with: pip install scikit-learn")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Find latest xlsx
        output_dir = Path("backtester/output")
        xlsx_files = sorted(output_dir.glob("backtest_*.xlsx"))
        if xlsx_files:
            xlsx_path = str(xlsx_files[-1])
            print(f"Using latest: {xlsx_path}")
        else:
            print("ERROR: No xlsx files found in backtester/output/")
            sys.exit(1)
    else:
        xlsx_path = sys.argv[1]

    train_from_xlsx(xlsx_path)
