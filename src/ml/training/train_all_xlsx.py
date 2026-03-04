# -*- coding: utf-8 -*-
"""
Train ML model from ALL XLSX backtest results in folder.

Usage:
    python -m src.ml.training.train_all_xlsx
    python -m src.ml.training.train_all_xlsx backtester/output
    python -m src.ml.training.train_all_xlsx backtester/output --save
"""

import sys
import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd


def load_all_xlsx(folder: str) -> pd.DataFrame:
    """Load and combine all XLSX files from folder."""
    folder_path = Path(folder)
    xlsx_files = sorted(folder_path.glob("backtest_*.xlsx"))

    if not xlsx_files:
        raise FileNotFoundError(f"No backtest_*.xlsx files found in {folder}")

    print(f"Found {len(xlsx_files)} XLSX files:")
    for f in xlsx_files:
        print(f"  - {f.name}")
    print()

    dfs = []
    for xlsx_path in xlsx_files:
        df = pd.read_excel(xlsx_path)
        df["_source_file"] = xlsx_path.name
        dfs.append(df)
        print(f"  {xlsx_path.name}: {len(df)} rows")

    combined = pd.concat(dfs, ignore_index=True)
    print(f"\nTotal combined: {len(combined)} rows")

    return combined


def train_from_combined(df: pd.DataFrame, test_size: float = 0.2, save_model: bool = False):
    """Train ML model from combined DataFrame."""

    # Filter filled only
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Remove duplicates by Signal ID
    if "Signal ID" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["Signal ID"], keep="last")
        after = len(df)
        if before != after:
            print(f"Removed {before - after} duplicates, kept {after}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)

    # Stats
    wins = (df["label_win"] == 1).sum()
    losses = (df["label_win"] == 0).sum()
    print(f"\nWins: {wins}, Losses: {losses}")
    print(f"Win rate: {wins/len(df)*100:.1f}%")

    # Avg PnL
    avg_pnl = df["Net %"].mean()
    print(f"Avg PnL: {avg_pnl:+.2f}%")

    # Features - use ALL numeric columns except target/result columns
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)

    # Columns to EXCLUDE (target, results, metadata)
    exclude_cols = [
        "№", "Signal ID", "Symbol", "Timestamp", "Direction", "Conf",
        "Signal Type", "Filled", "Entry Price", "Entry Time",
        "Exit Reason", "Exit Price", "Exit Time",
        "Gross PnL", "Fees", "Funding", "Net PnL", "PnL %", "Net %", "Hours",
        "TP1 Hit", "TP2 Hit", "TP3 Hit", "SL Hit",
        "trigger_type", "evidence_text",
        "logged_at", "futures_last_update", "spot_last_update",
        "oi_timestamp", "funding_time", "ls_ratio_timestamp",
        "_source_file", "label_win"
    ]

    # Get all numeric columns
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    # Filter: numeric, not excluded, and add direction_num
    feature_cols = [c for c in numeric_cols if c not in exclude_cols]
    if "direction_num" not in feature_cols:
        feature_cols.append("direction_num")

    print(f"\nTotal features: {len(feature_cols)}")
    print(f"Features: {feature_cols[:20]}...")  # Show first 20

    available_cols = feature_cols

    # Fill NaN with 0 for numeric features (many historical fields may be empty)
    df[available_cols] = df[available_cols].fillna(0)

    # Drop rows with NaN in label only
    df_clean = df.dropna(subset=["label_win"])
    print(f"Clean rows: {len(df_clean)}")

    if len(df_clean) < 100:
        print("ERROR: Not enough data for training!")
        return None

    # Temporal split (NEVER random!)
    df_clean = df_clean.sort_values("Timestamp").reset_index(drop=True)
    split_idx = int(len(df_clean) * (1 - test_size))

    train_df = df_clean.iloc[:split_idx]
    test_df = df_clean.iloc[split_idx:]

    print(f"\n=== Data Split ===")
    print(f"Train: {len(train_df)} ({train_df['Timestamp'].min()} to {train_df['Timestamp'].max()})")
    print(f"Test:  {len(test_df)} ({test_df['Timestamp'].min()} to {test_df['Timestamp'].max()})")

    X_train = train_df[available_cols].values.astype(np.float32)
    y_train = train_df["label_win"].values.astype(np.int32)

    X_test = test_df[available_cols].values.astype(np.float32)
    y_test = test_df["label_win"].values.astype(np.int32)

    # Train models
    try:
        from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import classification_report, confusion_matrix
        import joblib

        print("\n" + "="*60)
        print("TRAINING MODELS")
        print("="*60)

        # Scale features for Logistic Regression
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        results = {}

        # 1. Logistic Regression
        print("\n--- Logistic Regression ---")
        lr = LogisticRegression(random_state=42, max_iter=1000, class_weight="balanced")
        lr.fit(X_train_scaled, y_train)

        train_acc_lr = (lr.predict(X_train_scaled) == y_train).mean()
        test_acc_lr = (lr.predict(X_test_scaled) == y_test).mean()

        print(f"Train accuracy: {train_acc_lr:.3f}")
        print(f"Test accuracy:  {test_acc_lr:.3f}")
        print(f"Overfit gap:    {train_acc_lr - test_acc_lr:.3f}")

        print("\nTop 20 Coefficients:")
        for col, coef in sorted(zip(available_cols, lr.coef_[0]), key=lambda x: abs(x[1]), reverse=True)[:20]:
            print(f"  {col:40s}: {coef:+.4f}")

        results["logistic_regression"] = {"train": train_acc_lr, "test": test_acc_lr}

        # 2. Random Forest
        print("\n--- Random Forest ---")
        rf = RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        )
        rf.fit(X_train, y_train)

        train_acc_rf = (rf.predict(X_train) == y_train).mean()
        test_acc_rf = (rf.predict(X_test) == y_test).mean()

        print(f"Train accuracy: {train_acc_rf:.3f}")
        print(f"Test accuracy:  {test_acc_rf:.3f}")
        print(f"Overfit gap:    {train_acc_rf - test_acc_rf:.3f}")

        print("\nTop 20 Feature importance:")
        for col, imp in sorted(zip(available_cols, rf.feature_importances_), key=lambda x: x[1], reverse=True)[:20]:
            print(f"  {col:40s}: {imp:.4f}")

        results["random_forest"] = {"train": train_acc_rf, "test": test_acc_rf}

        # 3. Gradient Boosting
        print("\n--- Gradient Boosting ---")
        gb = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=4,
            min_samples_leaf=10,
            learning_rate=0.1,
            random_state=42
        )
        gb.fit(X_train, y_train)

        train_acc_gb = (gb.predict(X_train) == y_train).mean()
        test_acc_gb = (gb.predict(X_test) == y_test).mean()

        print(f"Train accuracy: {train_acc_gb:.3f}")
        print(f"Test accuracy:  {test_acc_gb:.3f}")
        print(f"Overfit gap:    {train_acc_gb - test_acc_gb:.3f}")

        results["gradient_boosting"] = {"train": train_acc_gb, "test": test_acc_gb}

        # ============================================================
        # THRESHOLD ANALYSIS FOR ALL MODELS
        # ============================================================
        print("\n" + "="*80)
        print("THRESHOLD ANALYSIS (0.50 - 0.95)")
        print("="*80)

        thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

        models_for_threshold = [
            ("Logistic Regression", lr, X_test_scaled),
            ("Random Forest", rf, X_test),
            ("Gradient Boosting", gb, X_test),
        ]

        test_df_for_analysis = test_df.copy()

        for model_name, model, X_data in models_for_threshold:
            print(f"\n{'='*80}")
            print(f"  {model_name} - Threshold Analysis")
            print(f"{'='*80}")

            # Get probabilities
            y_proba = model.predict_proba(X_data)[:, 1]  # Probability of WIN

            # Header
            print(f"\n{'Thresh':>6} | {'Pred':>6} | {'TP':>5} | {'FP':>5} | {'FN':>5} | {'TN':>6} | "
                  f"{'Prec':>6} | {'Recall':>6} | {'F1':>6} | {'Acc':>6} | {'WR%':>6} | {'AvgPnL':>8}")
            print("-" * 100)

            for thresh in thresholds:
                y_pred_t = (y_proba >= thresh).astype(int)

                # Confusion matrix values
                tp = ((y_pred_t == 1) & (y_test == 1)).sum()
                fp = ((y_pred_t == 1) & (y_test == 0)).sum()
                fn = ((y_pred_t == 0) & (y_test == 1)).sum()
                tn = ((y_pred_t == 0) & (y_test == 0)).sum()

                n_pred_win = (y_pred_t == 1).sum()

                # Metrics
                precision = tp / (tp + fp) if (tp + fp) > 0 else 0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 0
                f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
                accuracy = (tp + tn) / len(y_test)

                # PnL analysis
                test_df_for_analysis["_pred"] = y_pred_t
                filtered = test_df_for_analysis[test_df_for_analysis["_pred"] == 1]

                if len(filtered) > 0:
                    win_rate = (filtered["Net %"] > 0).mean() * 100
                    avg_pnl = filtered["Net %"].mean()
                else:
                    win_rate = 0
                    avg_pnl = 0

                print(f"{thresh:>6.2f} | {n_pred_win:>6} | {tp:>5} | {fp:>5} | {fn:>5} | {tn:>6} | "
                      f"{precision:>6.3f} | {recall:>6.3f} | {f1:>6.3f} | {accuracy:>6.3f} | "
                      f"{win_rate:>6.1f} | {avg_pnl:>+8.2f}%")

            # Summary row - baseline
            base_wr = (test_df["Net %"] > 0).mean() * 100
            base_pnl = test_df["Net %"].mean()
            print("-" * 100)
            print(f"{'BASE':>6} | {len(test_df):>6} | {'-':>5} | {'-':>5} | {'-':>5} | {'-':>6} | "
                  f"{'-':>6} | {'-':>6} | {'-':>6} | {'-':>6} | "
                  f"{base_wr:>6.1f} | {base_pnl:>+8.2f}%")

        print("\n" + "="*80)

        # Best model
        best_model_name = max(results.keys(), key=lambda k: results[k]["test"])
        best_test_acc = results[best_model_name]["test"]

        print("\n" + "="*60)
        print(f"BEST MODEL: {best_model_name} (test accuracy: {best_test_acc:.3f})")
        print("="*60)

        # Detailed analysis on best model
        if best_model_name == "random_forest":
            best_model = rf
            y_pred = rf.predict(X_test)
        elif best_model_name == "gradient_boosting":
            best_model = gb
            y_pred = gb.predict(X_test)
        else:
            best_model = lr
            y_pred = lr.predict(X_test_scaled)

        print("\n=== Test Set Analysis ===")
        print("\nConfusion Matrix:")
        cm = confusion_matrix(y_test, y_pred)
        print(f"                Predicted")
        print(f"              LOSS    WIN")
        print(f"Actual LOSS   {cm[0,0]:4d}   {cm[0,1]:4d}")
        print(f"Actual WIN    {cm[1,0]:4d}   {cm[1,1]:4d}")

        print("\nClassification Report:")
        print(classification_report(y_test, y_pred, target_names=["LOSS", "WIN"]))

        # Win prediction stats
        wins_pred = y_pred == 1
        wins_actual = y_test == 1

        if wins_pred.sum() > 0:
            precision = (wins_pred & wins_actual).sum() / wins_pred.sum()
            print(f"When model predicts WIN: {precision*100:.1f}% are actual wins")

        if wins_actual.sum() > 0:
            recall = (wins_pred & wins_actual).sum() / wins_actual.sum()
            print(f"Model catches {recall*100:.1f}% of actual wins")

        # Expected PnL with model
        print("\n=== Expected Performance ===")
        test_df_copy = test_df.copy()
        test_df_copy["model_pred"] = y_pred

        # If we only trade when model predicts WIN
        model_trades = test_df_copy[test_df_copy["model_pred"] == 1]
        if len(model_trades) > 0:
            model_win_rate = (model_trades["Net %"] > 0).mean()
            model_avg_pnl = model_trades["Net %"].mean()

            # Baseline (all trades)
            base_win_rate = (test_df["Net %"] > 0).mean()
            base_avg_pnl = test_df["Net %"].mean()

            print(f"\nBaseline (all trades):     WR={base_win_rate*100:.1f}%, Avg PnL={base_avg_pnl:+.2f}%, N={len(test_df)}")
            print(f"With model filter:         WR={model_win_rate*100:.1f}%, Avg PnL={model_avg_pnl:+.2f}%, N={len(model_trades)}")
            print(f"Improvement:               WR {(model_win_rate-base_win_rate)*100:+.1f}pp, Avg PnL {model_avg_pnl-base_avg_pnl:+.2f}%")

        # Save model if requested
        if save_model:
            print("\n=== Saving Model ===")
            model_dir = Path("models/signal_filter")
            model_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Save best model
            model_path = model_dir / f"{best_model_name}_{timestamp}.pkl"
            joblib.dump(best_model, model_path)
            print(f"Saved: {model_path}")

            # Save scaler (always needed for inference)
            scaler_path = model_dir / f"scaler_{timestamp}.pkl"
            joblib.dump(scaler, scaler_path)
            print(f"Saved: {scaler_path}")

            # Save feature columns
            meta_path = model_dir / f"meta_{timestamp}.json"
            with open(meta_path, "w") as f:
                json.dump({
                    "feature_columns": available_cols,
                    "model_type": best_model_name,
                    "test_accuracy": float(best_test_acc),
                    "train_samples": len(train_df),
                    "test_samples": len(test_df),
                    "created": timestamp,
                }, f, indent=2)

            print(f"Saved: {meta_path}")

            # Save as "latest"
            latest_model = model_dir / "latest_model.pkl"
            latest_scaler = model_dir / "latest_scaler.pkl"
            latest_meta = model_dir / "latest_meta.json"

            joblib.dump(best_model, latest_model)
            joblib.dump(scaler, latest_scaler)

            with open(latest_meta, "w") as f:
                json.dump({
                    "feature_columns": available_cols,
                    "model_type": best_model_name,
                    "test_accuracy": float(best_test_acc),
                    "train_samples": len(train_df),
                    "test_samples": len(test_df),
                    "total_features": len(available_cols),
                    "created": timestamp,
                }, f, indent=2)

            print(f"Saved: {latest_model}")
            print(f"Saved: {latest_scaler}")
            print(f"Saved: {latest_meta}")

        return results

    except ImportError as e:
        print(f"ERROR: scikit-learn not installed: {e}")
        print("Install with: pip install scikit-learn joblib")
        return None


def main():
    """Main entry point."""
    # Parse args
    folder = "backtester/output"
    save_model = False

    for arg in sys.argv[1:]:
        if arg == "--save":
            save_model = True
        elif not arg.startswith("-"):
            folder = arg

    print("="*60)
    print("ML TRAINING FROM XLSX BACKTEST RESULTS")
    print("="*60)
    print(f"Folder: {folder}")
    print(f"Save model: {save_model}")
    print()

    # Load all XLSX
    df = load_all_xlsx(folder)

    # Train
    train_from_combined(df, save_model=save_model)


if __name__ == "__main__":
    main()
