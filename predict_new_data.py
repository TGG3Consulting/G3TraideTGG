# -*- coding: utf-8 -*-
"""
Apply trained model to new data (out-of-sample test).
"""

import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path


def main():
    print("=" * 80)
    print("OUT-OF-SAMPLE TEST: Apply trained model to new data")
    print("=" * 80)

    # 1. Load trained model
    model_dir = Path("models/signal_filter")

    model = joblib.load(model_dir / "latest_model.pkl")
    scaler = joblib.load(model_dir / "latest_scaler.pkl")

    with open(model_dir / "latest_meta.json", "r") as f:
        meta = json.load(f)

    feature_cols = meta["feature_columns"]
    print(f"\nLoaded model: {meta['model_type']}")
    print(f"Trained on: {meta['train_samples']} samples")
    print(f"Features: {meta['total_features']}")

    # 2. Load new Excel
    new_file = Path("backtester/output/backtest_20260225_003331.xlsx")
    print(f"\nLoading new data: {new_file.name}")

    df = pd.read_excel(new_file)
    print(f"Total rows: {len(df)}")

    # Filter filled only
    df = df[df["Filled"] == "YES"].copy()
    print(f"Filled trades: {len(df)}")

    # Create target
    df["label_win"] = (df["Net %"] > 0).astype(int)
    df["direction_num"] = (df["Direction"] == "LONG").astype(int)

    # Check all features exist
    missing_cols = [c for c in feature_cols if c not in df.columns]
    if missing_cols:
        print(f"WARNING: Missing columns: {missing_cols[:10]}...")

    available_cols = [c for c in feature_cols if c in df.columns]
    print(f"Available features: {len(available_cols)} / {len(feature_cols)}")

    # Fill NaN
    df[available_cols] = df[available_cols].fillna(0)

    # Prepare features
    X = df[available_cols].values.astype(np.float32)
    y = df["label_win"].values

    # Get probabilities
    y_proba = model.predict_proba(X)[:, 1]

    # Baseline stats
    total_wins = (y == 1).sum()
    total_losses = (y == 0).sum()
    base_wr = total_wins / len(y) * 100
    base_pnl = df["Net %"].mean()

    print(f"\n{'=' * 80}")
    print("BASELINE (all trades):")
    print(f"{'=' * 80}")
    print(f"Total trades: {len(df)}")
    print(f"Wins: {total_wins}, Losses: {total_losses}")
    print(f"Win Rate: {base_wr:.1f}%")
    print(f"Avg PnL: {base_pnl:+.2f}%")
    print(f"Total PnL: {df['Net %'].sum():+.2f}%")

    # Test thresholds
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

    print(f"\n{'=' * 80}")
    print("MODEL PREDICTIONS BY THRESHOLD:")
    print(f"{'=' * 80}")

    print(f"\n{'Thresh':>6} | {'Pred':>6} | {'TP':>5} | {'FP':>5} | {'Prec':>6} | {'Recall':>6} | "
          f"{'WR%':>6} | {'AvgPnL':>8} | {'TotalPnL':>10}")
    print("-" * 90)

    results = []

    for thresh in thresholds:
        pred_win = (y_proba >= thresh).astype(int)

        tp = ((pred_win == 1) & (y == 1)).sum()
        fp = ((pred_win == 1) & (y == 0)).sum()
        fn = ((pred_win == 0) & (y == 1)).sum()
        tn = ((pred_win == 0) & (y == 0)).sum()

        n_pred = (pred_win == 1).sum()

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0

        # PnL for predicted WIN trades
        df["_pred"] = pred_win
        filtered = df[df["_pred"] == 1]

        if len(filtered) > 0:
            wr = (filtered["Net %"] > 0).mean() * 100
            avg_pnl = filtered["Net %"].mean()
            total_pnl = filtered["Net %"].sum()
        else:
            wr = 0
            avg_pnl = 0
            total_pnl = 0

        print(f"{thresh:>6.2f} | {n_pred:>6} | {tp:>5} | {fp:>5} | {precision:>6.1%} | {recall:>6.1%} | "
              f"{wr:>6.1f} | {avg_pnl:>+8.2f}% | {total_pnl:>+10.2f}%")

        results.append({
            "threshold": thresh,
            "predictions": n_pred,
            "tp": tp,
            "fp": fp,
            "precision": precision,
            "recall": recall,
            "win_rate": wr,
            "avg_pnl": avg_pnl,
            "total_pnl": total_pnl
        })

    print("-" * 90)
    print(f"{'BASE':>6} | {len(df):>6} | {'-':>5} | {'-':>5} | {'-':>6} | {'-':>6} | "
          f"{base_wr:>6.1f} | {base_pnl:>+8.2f}% | {df['Net %'].sum():>+10.2f}%")

    # Detailed analysis for 0.65 and 0.70
    print(f"\n{'=' * 80}")
    print("DETAILED ANALYSIS FOR THRESHOLDS 0.65 AND 0.70:")
    print(f"{'=' * 80}")

    for thresh in [0.65, 0.70]:
        pred_win = (y_proba >= thresh).astype(int)
        df["_pred"] = pred_win
        filtered = df[df["_pred"] == 1]

        if len(filtered) == 0:
            print(f"\nThreshold {thresh}: NO PREDICTIONS")
            continue

        print(f"\n--- Threshold {thresh} ---")
        print(f"Trades selected: {len(filtered)}")
        print(f"Wins: {(filtered['Net %'] > 0).sum()}")
        print(f"Losses: {(filtered['Net %'] <= 0).sum()}")
        print(f"Win Rate: {(filtered['Net %'] > 0).mean() * 100:.1f}%")
        print(f"Avg PnL: {filtered['Net %'].mean():+.2f}%")
        print(f"Total PnL: {filtered['Net %'].sum():+.2f}%")
        print(f"Best trade: {filtered['Net %'].max():+.2f}%")
        print(f"Worst trade: {filtered['Net %'].min():+.2f}%")

        # By direction
        longs = filtered[filtered["Direction"] == "LONG"]
        shorts = filtered[filtered["Direction"] == "SHORT"]

        if len(longs) > 0:
            print(f"LONG:  {len(longs)} trades, WR={(longs['Net %'] > 0).mean()*100:.1f}%, "
                  f"Avg PnL={longs['Net %'].mean():+.2f}%")
        if len(shorts) > 0:
            print(f"SHORT: {len(shorts)} trades, WR={(shorts['Net %'] > 0).mean()*100:.1f}%, "
                  f"Avg PnL={shorts['Net %'].mean():+.2f}%")

    print(f"\n{'=' * 80}")
    print("DONE")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
