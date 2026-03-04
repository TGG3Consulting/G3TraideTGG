# -*- coding: utf-8 -*-
"""
Volatility Filter Calibration Script.

Finds optimal vol_filter_low and vol_filter_high thresholds for each strategy
using grid search with out-of-sample validation.

Usage:
    python calibrate_volatility.py
    python calibrate_volatility.py --output-dir ./output
    python calibrate_volatility.py --train-end 2025-01-01
"""
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np


def load_all_xlsx(output_dir: Path) -> Dict[str, pd.DataFrame]:
    """Load all backtest xlsx files from output directory."""
    strategies = {}

    for xlsx_file in output_dir.glob("backtest_*.xlsx"):
        # Extract strategy name from filename: backtest_momentum_20260304_...xlsx
        parts = xlsx_file.stem.split("_")
        if len(parts) >= 2:
            strategy = parts[1]

            # Read trades sheet
            try:
                df = pd.read_excel(xlsx_file, sheet_name="Trades")
                if strategy in strategies:
                    strategies[strategy] = pd.concat([strategies[strategy], df], ignore_index=True)
                else:
                    strategies[strategy] = df
                print(f"Loaded {len(df)} trades from {xlsx_file.name}")
            except Exception as e:
                print(f"Error loading {xlsx_file.name}: {e}")

    return strategies


# Column name mapping (xlsx header names)
COL_DATE = "Signal Date"
COL_RESULT = "Result"
COL_NET_PNL = "Net PnL %"
COL_VOL = "Coin Vol %"


def calculate_metrics(df: pd.DataFrame) -> Dict:
    """Calculate trading metrics for a DataFrame of trades."""
    if len(df) == 0:
        return {"trades": 0, "win_rate": 0, "net_pnl": 0, "max_dd": 0, "calmar": 0}

    trades = len(df)
    wins = len(df[df[COL_RESULT] == "WIN"])
    win_rate = wins / trades * 100 if trades > 0 else 0

    net_pnl = df[COL_NET_PNL].sum()

    # Calculate max drawdown from cumulative PnL
    cumulative = df[COL_NET_PNL].cumsum()
    running_max = cumulative.cummax()
    drawdown = running_max - cumulative
    max_dd = drawdown.max() if len(drawdown) > 0 else 0

    # Calmar ratio (annualized would need date range, using simple ratio)
    calmar = net_pnl / max_dd if max_dd > 0 else net_pnl / 100

    return {
        "trades": trades,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2),
        "max_dd": round(max_dd, 2),
        "calmar": round(calmar, 3)
    }


def grid_search_low(df: pd.DataFrame, thresholds: List[float], max_filter_pct: float = 0.30) -> List[Dict]:
    """Grid search for vol_filter_low threshold."""
    results = []
    baseline_trades = len(df)

    for threshold in thresholds:
        filtered = df[df[COL_VOL] >= threshold]
        filtered_pct = 1 - len(filtered) / baseline_trades if baseline_trades > 0 else 0

        if filtered_pct > max_filter_pct:
            # Too many trades filtered, stop
            metrics = calculate_metrics(filtered)
            metrics["threshold"] = threshold
            metrics["filtered_pct"] = round(filtered_pct * 100, 1)
            metrics["status"] = "TOO_MANY_FILTERED"
            results.append(metrics)
            continue

        metrics = calculate_metrics(filtered)
        metrics["threshold"] = threshold
        metrics["filtered_pct"] = round(filtered_pct * 100, 1)
        metrics["status"] = "OK"
        results.append(metrics)

    return results


def grid_search_high(df: pd.DataFrame, thresholds: List[float], max_filter_pct: float = 0.30) -> List[Dict]:
    """Grid search for vol_filter_high threshold."""
    results = []
    baseline_trades = len(df)

    for threshold in thresholds:
        filtered = df[df[COL_VOL] <= threshold]
        filtered_pct = 1 - len(filtered) / baseline_trades if baseline_trades > 0 else 0

        if filtered_pct > max_filter_pct:
            metrics = calculate_metrics(filtered)
            metrics["threshold"] = threshold
            metrics["filtered_pct"] = round(filtered_pct * 100, 1)
            metrics["status"] = "TOO_MANY_FILTERED"
            results.append(metrics)
            continue

        metrics = calculate_metrics(filtered)
        metrics["threshold"] = threshold
        metrics["filtered_pct"] = round(filtered_pct * 100, 1)
        metrics["status"] = "OK"
        results.append(metrics)

    return results


def find_best_threshold(results: List[Dict], metric: str = "calmar") -> Dict:
    """Find best threshold from grid search results."""
    valid = [r for r in results if r["status"] == "OK" and r["trades"] >= 100]
    if not valid:
        return results[0] if results else None

    return max(valid, key=lambda x: x[metric])


def calibrate_strategy(
    strategy: str,
    df: pd.DataFrame,
    train_end: str,
    low_thresholds: List[float],
    high_thresholds: List[float]
) -> Dict:
    """Calibrate volatility filters for a single strategy."""

    # Parse dates
    df["_date"] = pd.to_datetime(df[COL_DATE])
    train_end_dt = pd.to_datetime(train_end)

    # Split train/test
    train_df = df[df["_date"] < train_end_dt].copy()
    test_df = df[df["_date"] >= train_end_dt].copy()

    print(f"\n{'='*70}")
    print(f"STRATEGY: {strategy.upper()}")
    print(f"{'='*70}")
    print(f"Training: {len(train_df)} trades (before {train_end})")
    print(f"Validation: {len(test_df)} trades (from {train_end})")

    if len(train_df) < 500:
        print(f"WARNING: Not enough training data ({len(train_df)} < 500)")
        return None

    # Baseline metrics
    baseline_train = calculate_metrics(train_df)
    baseline_test = calculate_metrics(test_df) if len(test_df) > 0 else None

    print(f"\nBaseline (no filter):")
    print(f"  Train: WR={baseline_train['win_rate']}%, PnL={baseline_train['net_pnl']}%, DD={baseline_train['max_dd']}%, Calmar={baseline_train['calmar']}")
    if baseline_test:
        print(f"  Test:  WR={baseline_test['win_rate']}%, PnL={baseline_test['net_pnl']}%, DD={baseline_test['max_dd']}%, Calmar={baseline_test['calmar']}")

    # Grid search vol_filter_low
    print(f"\n--- vol_filter_low scan ---")
    print(f"{'Threshold':>10} | {'Trades':>7} | {'Filtered':>8} | {'WinRate':>7} | {'NetPnL':>10} | {'MaxDD':>8} | {'Calmar':>7} | Status")
    print("-" * 85)

    low_results = grid_search_low(train_df, low_thresholds)
    for r in low_results:
        status_mark = "←BEST" if r == find_best_threshold(low_results) else ""
        print(f"{r['threshold']:>9.1f}% | {r['trades']:>7} | {r['filtered_pct']:>7.1f}% | {r['win_rate']:>6.1f}% | {r['net_pnl']:>+9.1f}% | {r['max_dd']:>7.1f}% | {r['calmar']:>7.2f} | {r['status']} {status_mark}")

    best_low = find_best_threshold(low_results)

    # Grid search vol_filter_high (only for mean_reversion)
    best_high = None
    if strategy == "mean_reversion":
        print(f"\n--- vol_filter_high scan ---")
        print(f"{'Threshold':>10} | {'Trades':>7} | {'Filtered':>8} | {'WinRate':>7} | {'NetPnL':>10} | {'MaxDD':>8} | {'Calmar':>7} | Status")
        print("-" * 85)

        high_results = grid_search_high(train_df, high_thresholds)
        for r in high_results:
            status_mark = "←BEST" if r == find_best_threshold(high_results) else ""
            print(f"{r['threshold']:>9.1f}% | {r['trades']:>7} | {r['filtered_pct']:>7.1f}% | {r['win_rate']:>6.1f}% | {r['net_pnl']:>+9.1f}% | {r['max_dd']:>7.1f}% | {r['calmar']:>7.2f} | {r['status']} {status_mark}")

        best_high = find_best_threshold(high_results)

    # Validate on test set
    print(f"\n--- VALIDATION (out-of-sample) ---")

    if len(test_df) < 100:
        print(f"WARNING: Not enough test data ({len(test_df)} < 100) for validation")
        validation_status = "INSUFFICIENT_DATA"
    else:
        # Apply best thresholds to test
        test_filtered = test_df.copy()
        if best_low and best_low["threshold"] > 0:
            test_filtered = test_filtered[test_filtered[COL_VOL] >= best_low["threshold"]]
        if best_high and best_high["threshold"] < 100:
            test_filtered = test_filtered[test_filtered[COL_VOL] <= best_high["threshold"]]

        test_metrics = calculate_metrics(test_filtered)

        print(f"Test baseline: Calmar={baseline_test['calmar']:.2f}" if baseline_test else "No test data")
        print(f"Test filtered: Calmar={test_metrics['calmar']:.2f}")

        # Validation check: filtered should be at least 70% of baseline
        if baseline_test and baseline_test["calmar"] > 0:
            ratio = test_metrics["calmar"] / baseline_test["calmar"]
            if ratio >= 0.7:
                validation_status = "VALIDATED"
                print(f"Status: ✓ VALIDATED (ratio={ratio:.2f})")
            else:
                validation_status = "POSSIBLE_OVERFIT"
                print(f"Status: ⚠ POSSIBLE OVERFIT (ratio={ratio:.2f} < 0.7)")
        else:
            validation_status = "VALIDATED" if test_metrics["calmar"] > 0 else "UNCERTAIN"
            print(f"Status: {validation_status}")

    # Build result
    result = {
        "strategy": strategy,
        "train_trades": len(train_df),
        "test_trades": len(test_df),
        "baseline_train_calmar": baseline_train["calmar"],
        "baseline_test_calmar": baseline_test["calmar"] if baseline_test else None,
        "vol_filter_low": best_low["threshold"] if best_low else 0,
        "vol_filter_low_calmar": best_low["calmar"] if best_low else baseline_train["calmar"],
        "vol_filter_high": best_high["threshold"] if best_high else None,
        "vol_filter_high_calmar": best_high["calmar"] if best_high else None,
        "validation_status": validation_status,
        "improvement_pct": round((best_low["calmar"] / baseline_train["calmar"] - 1) * 100, 1) if best_low and baseline_train["calmar"] > 0 else 0
    }

    return result


def print_summary(results: List[Dict]):
    """Print final summary and recommendations."""
    print("\n" + "=" * 70)
    print("CALIBRATION SUMMARY")
    print("=" * 70)

    print(f"\n{'Strategy':<15} | {'vol_low':>8} | {'vol_high':>8} | {'Improve':>8} | {'Status':<20}")
    print("-" * 70)

    for r in results:
        if r is None:
            continue
        vol_low = f"{r['vol_filter_low']:.1f}%" if r['vol_filter_low'] else "N/A"
        vol_high = f"{r['vol_filter_high']:.1f}%" if r['vol_filter_high'] else "N/A"
        improve = f"+{r['improvement_pct']:.1f}%"
        print(f"{r['strategy']:<15} | {vol_low:>8} | {vol_high:>8} | {improve:>8} | {r['validation_status']:<20}")

    print("\n" + "-" * 70)
    print("RECOMMENDED COMMAND:")

    # Find most common validated vol_filter_low
    validated = [r for r in results if r and r["validation_status"] == "VALIDATED"]
    if validated:
        avg_low = np.mean([r["vol_filter_low"] for r in validated if r["vol_filter_low"]])
        print(f"\npython run_all.py --vol-filter --vol-filter-low {avg_low:.1f}")

        # Check if mean_reversion has vol_filter_high
        mr = next((r for r in validated if r["strategy"] == "mean_reversion" and r["vol_filter_high"]), None)
        if mr:
            print(f"python run_all.py --vol-filter --vol-filter-low {avg_low:.1f} --vol-filter-high {mr['vol_filter_high']:.1f}")


def main():
    parser = argparse.ArgumentParser(description="Calibrate volatility filters")
    parser.add_argument("--output-dir", type=str, default="output", help="Directory with xlsx files")
    parser.add_argument("--train-end", type=str, default="2025-01-01", help="End date for training data (YYYY-MM-DD)")
    parser.add_argument("--low-min", type=float, default=0.0, help="Min threshold for vol_filter_low scan")
    parser.add_argument("--low-max", type=float, default=10.0, help="Max threshold for vol_filter_low scan")
    parser.add_argument("--low-step", type=float, default=0.5, help="Step for vol_filter_low scan")
    parser.add_argument("--high-min", type=float, default=10.0, help="Min threshold for vol_filter_high scan")
    parser.add_argument("--high-max", type=float, default=25.0, help="Max threshold for vol_filter_high scan")
    parser.add_argument("--high-step", type=float, default=1.0, help="Step for vol_filter_high scan")
    parser.add_argument("--save-json", type=str, help="Save results to JSON file")

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.exists():
        print(f"Error: Output directory {output_dir} does not exist")
        return

    # Generate thresholds
    low_thresholds = list(np.arange(args.low_min, args.low_max + args.low_step, args.low_step))
    high_thresholds = list(np.arange(args.high_min, args.high_max + args.high_step, args.high_step))

    print("=" * 70)
    print("VOLATILITY FILTER CALIBRATION")
    print("=" * 70)
    print(f"Output directory: {output_dir}")
    print(f"Training data: before {args.train_end}")
    print(f"vol_filter_low scan: {args.low_min}% to {args.low_max}% (step {args.low_step}%)")
    print(f"vol_filter_high scan: {args.high_min}% to {args.high_max}% (step {args.high_step}%)")

    # Load data
    strategies = load_all_xlsx(output_dir)

    if not strategies:
        print("No xlsx files found!")
        return

    # Calibrate each strategy
    results = []
    for strategy, df in strategies.items():
        result = calibrate_strategy(
            strategy=strategy,
            df=df,
            train_end=args.train_end,
            low_thresholds=low_thresholds,
            high_thresholds=high_thresholds
        )
        results.append(result)

    # Print summary
    print_summary(results)

    # Save to JSON if requested
    if args.save_json:
        valid_results = [r for r in results if r is not None]
        with open(args.save_json, "w") as f:
            json.dump(valid_results, f, indent=2)
        print(f"\nResults saved to {args.save_json}")


if __name__ == "__main__":
    main()
