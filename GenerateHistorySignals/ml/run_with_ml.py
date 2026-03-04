# -*- coding: utf-8 -*-
"""
Run Backtest with ML Enhancement

Compares original backtest results with ML-filtered results.

Usage:
    # Train per-strategy models and compare
    python -m ml.run_with_ml --data-dir outputNEWARCH --train

    # Use existing models
    python -m ml.run_with_ml --data-dir outputNEWARCH
"""

import argparse
import warnings
from pathlib import Path
from typing import Dict

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

from .adapter import BacktestDataAdapter
from .trainer_per_strategy import PerStrategyTrainer
from .filter import MLSignalFilter


def load_backtest_data(data_dir: str) -> pd.DataFrame:
    """Load all backtest Excel files."""
    data_path = Path(data_dir)
    xlsx_files = list(data_path.glob('backtest_*.xlsx'))

    all_trades = []
    for xlsx_path in xlsx_files:
        if xlsx_path.name.startswith('~$'):
            continue
        strategy = xlsx_path.stem.replace('backtest_', '').rsplit('_', 2)[0]
        try:
            trades = pd.read_excel(xlsx_path, sheet_name='Trades')
            trades['Strategy'] = strategy
            all_trades.append(trades)
        except:
            continue

    return pd.concat(all_trades, ignore_index=True)


def apply_ml_filter(df: pd.DataFrame, ml_filter: MLSignalFilter) -> pd.DataFrame:
    """Apply ML filter to trades DataFrame."""
    results = []

    for idx, row in df.iterrows():
        if row.get('Result') == 'SKIPPED':
            results.append({'ml_should_trade': False, 'ml_confidence': 0.0})
            continue

        # Pass HONEST features from Excel row (no look-ahead bias)
        # Uses PREVIOUS DAY's candle data (Prev High, Prev Low, etc.)
        signal_data = {
            # Market Data
            'Long %': row.get('Long %', 0.5),
            'Short %': row.get('Short %', 0.5),
            'Funding Rate': row.get('Funding Rate', 0),
            'OI USD': row.get('OI USD', 0),
            'OI Contracts': row.get('OI Contracts', 0),

            # Entry Day - only OPEN is known at entry time
            'Open': row.get('Open', 0),

            # PREVIOUS DAY's Candle Data (HONEST - no look-ahead bias)
            'Prev High': row.get('Prev High', 0),
            'Prev Low': row.get('Prev Low', 0),
            'Prev Close': row.get('Prev Close', 0),
            'Prev Volume': row.get('Prev Volume', 0),
            'Prev Volume USD': row.get('Prev Volume USD', 0),
            'Prev Trades Count': row.get('Prev Trades Count', 0),
            'Prev Taker Buy Vol': row.get('Prev Taker Buy Vol', 0),
            'Prev Taker Buy USD': row.get('Prev Taker Buy USD', 0),

            # Indicators
            'ADX': row.get('ADX', 0),

            # Trade params
            'SL %': row.get('SL %', 4.0),
            'TP %': row.get('TP %', 10.0),
            'R:R Ratio': row.get('R:R Ratio', 2.5),

            # Chain - only past-looking features
            'Chain Seq': row.get('Chain Seq', 0),
            'Gap Days': row.get('Gap Days', 0),
            'Chain First': row.get('Chain First', False),
            # REMOVED: Chain Total, Chain Last - requires future knowledge

            # Time (extract from Signal Date)
            'DayOfWeek': pd.to_datetime(row.get('Signal Date')).dayofweek if pd.notna(row.get('Signal Date')) else 0,
            'Month': pd.to_datetime(row.get('Signal Date')).month if pd.notna(row.get('Signal Date')) else 1,
            'Hour': pd.to_datetime(row.get('Signal Date')).hour if pd.notna(row.get('Signal Date')) else 0,
        }

        try:
            pred = ml_filter.predict(
                signal_data, row.get('Strategy', ''),
                row.get('Symbol', ''), row.get('Direction', '')
            )
            results.append({
                'ml_should_trade': pred.should_trade,
                'ml_confidence': pred.confidence,
                'ml_filter_score': pred.filter_score,
                'ml_direction': pred.predicted_direction,
                'ml_sl': pred.predicted_sl,
                'ml_tp': pred.predicted_tp,
                'ml_lifetime': pred.predicted_lifetime,
            })
        except Exception as e:
            results.append({
                'ml_should_trade': True, 'ml_confidence': 0.5,
                'ml_filter_score': 0.5, 'ml_direction': 0,
                'ml_sl': 4.0, 'ml_tp': 10.0, 'ml_lifetime': 3.0
            })

    return pd.concat([df.reset_index(drop=True), pd.DataFrame(results)], axis=1)


def compare_results(df: pd.DataFrame):
    """Print comparison of original vs ML-filtered results."""
    print("\n" + "=" * 70)
    print("COMPARISON: ORIGINAL vs ML-FILTERED")
    print("=" * 70)

    for strategy in df['Strategy'].unique():
        strat_df = df[df['Strategy'] == strategy]
        original = strat_df[strat_df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])]
        ml_filtered = strat_df[
            (strat_df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])) &
            (strat_df['ml_should_trade'] == True)
        ]
        ml_skipped = strat_df[
            (strat_df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])) &
            (strat_df['ml_should_trade'] == False)
        ]

        orig_wins = len(original[original['Result'] == 'WIN'])
        orig_wr = orig_wins / len(original) * 100 if len(original) > 0 else 0
        orig_pnl = original['Net PnL %'].sum()

        ml_wins = len(ml_filtered[ml_filtered['Result'] == 'WIN'])
        ml_wr = ml_wins / len(ml_filtered) * 100 if len(ml_filtered) > 0 else 0
        ml_pnl = ml_filtered['Net PnL %'].sum()

        skip_pnl = ml_skipped['Net PnL %'].sum() if len(ml_skipped) > 0 else 0

        print(f"\n{strategy.upper()}:")
        print(f"  Original:    {len(original):>5} trades, WR: {orig_wr:>5.1f}%, PnL: {orig_pnl:>+8.2f}%")
        print(f"  ML-Filtered: {len(ml_filtered):>5} trades, WR: {ml_wr:>5.1f}%, PnL: {ml_pnl:>+8.2f}%")
        print(f"  ML-Skipped:  {len(ml_skipped):>5} trades, PnL: {skip_pnl:>+8.2f}%")

        if skip_pnl < 0:
            print(f"  >>> ML filtered out {abs(skip_pnl):.2f}% of losses!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=str, default='outputNEWARCH')
    parser.add_argument('--model-dir', type=str, default='models')
    parser.add_argument('--train', action='store_true', help='Train models first')
    parser.add_argument('--strategy', type=str, default=None,
                        help='Specific strategy (ls_fade, momentum, reversal, mean_reversion, momentum_ls)')
    parser.add_argument('--min-confidence', type=float, default=0.35)
    args = parser.parse_args()

    print("=" * 70)
    print("ML-ENHANCED BACKTEST ANALYSIS")
    if args.strategy:
        print(f"Strategy: {args.strategy.upper()}")
    print("=" * 70)

    if args.train:
        print("\n[TRAINING MODELS]")
        from .trainer_per_strategy import train_per_strategy
        train_per_strategy(args.data_dir, args.model_dir, args.strategy)

    print("\n[LOADING ML MODELS]")
    ml_filter = MLSignalFilter(
        model_dir=args.model_dir,
        per_strategy=True,
        min_confidence=args.min_confidence,
    )

    try:
        ml_filter.load()
    except FileNotFoundError:
        print("Models not found. Training...")
        trainer = PerStrategyTrainer(base_model_dir=args.model_dir)
        trainer.train_all(args.data_dir)
        ml_filter.load()

    print("\n[LOADING BACKTEST DATA]")
    df = load_backtest_data(args.data_dir)

    # Filter by strategy if specified
    if args.strategy:
        df = df[df['Strategy'] == args.strategy]

    print(f"Loaded {len(df)} trades")

    print("\n[APPLYING ML FILTER]")
    df_ml = apply_ml_filter(df, ml_filter)

    compare_results(df_ml)

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == '__main__':
    main()
