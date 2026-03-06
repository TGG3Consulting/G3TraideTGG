# -*- coding: utf-8 -*-
"""
Multi-Level Take Profit Optimizer.

Analyzes backtest trades to find optimal TP1/TP2/Split configuration.
Reads raw klines from cache/binance/ to calculate proper MFE across holding period.

Usage:
    python tp_optimizer.py
    python tp_optimizer.py --output-dir ./output --cache-dir ./cache/binance
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


@dataclass
class TPConfig:
    tp1_pct: float  # First TP level (e.g., 0.04 = 4%)
    tp2_pct: float  # Second TP level (e.g., 0.10 = 10%)
    split: float    # Portion closed at TP1 (e.g., 0.5 = 50%)


@dataclass
class SimResult:
    old_pnl: float
    new_pnl: float
    old_wins: int
    new_wins: int
    rescued_losses: int
    partial_wins: int
    total_trades: int


def load_klines_cache(cache_dir: str = "cache/binance") -> Dict[str, pd.DataFrame]:
    """Load all daily klines from cache into memory."""
    cache = {}
    cache_path = Path(cache_dir)

    if not cache_path.exists():
        print(f"Cache directory not found: {cache_dir}")
        return cache

    for symbol_dir in cache_path.iterdir():
        if not symbol_dir.is_dir():
            continue

        symbol = symbol_dir.name
        klines_file = symbol_dir / "klines_1d.json"

        if not klines_file.exists():
            # Try regular klines.json and aggregate
            klines_file = symbol_dir / "klines.json"
            if not klines_file.exists():
                continue

        try:
            with open(klines_file, 'r') as f:
                data = json.load(f)

            if not data:
                continue

            # Convert to DataFrame
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.date
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['open'] = df['open'].astype(float)
            df['close'] = df['close'].astype(float)

            # Index by date for fast lookup
            df = df.set_index('date')
            cache[symbol] = df

        except Exception as e:
            print(f"Error loading {symbol}: {e}")

    print(f"Loaded klines for {len(cache)} symbols")
    return cache


def calculate_mfe_from_klines(
    symbol: str,
    direction: str,
    entry_price: float,
    signal_date: datetime,
    exit_date: datetime,
    klines_cache: Dict[str, pd.DataFrame]
) -> float:
    """
    Calculate Maximum Favorable Excursion using raw klines.

    For LONG: MFE = (MAX_HIGH during hold - entry) / entry
    For SHORT: MFE = (entry - MIN_LOW during hold) / entry
    """
    if symbol not in klines_cache:
        return 0.0

    df = klines_cache[symbol]

    # Convert dates
    start_date = signal_date.date() if isinstance(signal_date, datetime) else signal_date
    end_date = exit_date.date() if isinstance(exit_date, datetime) else exit_date

    # Get candles during holding period
    try:
        mask = (df.index >= start_date) & (df.index <= end_date)
        hold_candles = df[mask]
    except:
        return 0.0

    if hold_candles.empty:
        return 0.0

    if entry_price <= 0:
        return 0.0

    if direction == 'LONG':
        max_high = hold_candles['high'].max()
        mfe = (max_high - entry_price) / entry_price
    else:  # SHORT
        min_low = hold_candles['low'].min()
        mfe = (entry_price - min_low) / entry_price

    return max(0, mfe)


def load_all_trades(output_dir: str = "output") -> pd.DataFrame:
    """Load all trades from xlsx files."""
    all_trades = []

    for xlsx_file in Path(output_dir).glob("backtest_*.xlsx"):
        try:
            parts = xlsx_file.stem.split("_")
            strategy = "_".join(parts[1:-2])

            df = pd.read_excel(xlsx_file, sheet_name="Trades")
            df['Strategy'] = strategy
            all_trades.append(df)
        except Exception as e:
            print(f"Error loading {xlsx_file.name}: {e}")

    if not all_trades:
        return pd.DataFrame()

    combined = pd.concat(all_trades, ignore_index=True)
    return combined


def simulate_trade(row, config: TPConfig, fee_pct: float = 0.0005) -> Tuple[float, str]:
    """Simulate a single trade with multi-level TP."""
    mfe = row['MFE']
    original_pnl = row['Net PnL %']
    original_result = row['Result']
    sl_pct = row['SL %']

    if mfe < config.tp1_pct:
        return original_pnl, original_result

    pnl_part1 = config.tp1_pct * config.split
    extra_fees_1 = fee_pct * 2 * config.split

    if mfe >= config.tp2_pct:
        pnl_part2 = config.tp2_pct * (1 - config.split)
        extra_fees_2 = fee_pct * 2 * (1 - config.split)
        new_result = 'WIN'
    else:
        if original_result == 'WIN':
            pnl_part2 = config.tp2_pct * (1 - config.split)
            extra_fees_2 = fee_pct * 2 * (1 - config.split)
            new_result = 'WIN'
        elif original_result == 'LOSS':
            pnl_part2 = -sl_pct * (1 - config.split)
            extra_fees_2 = fee_pct * 2 * (1 - config.split)
            new_result = 'PARTIAL'
        else:
            pnl_part2 = original_pnl * (1 - config.split)
            extra_fees_2 = fee_pct * 2 * (1 - config.split)
            new_result = 'PARTIAL'

    total_new_pnl = pnl_part1 + pnl_part2 - (extra_fees_1 + extra_fees_2 - fee_pct * 2)
    return total_new_pnl, new_result


def simulate_all(df: pd.DataFrame, config: TPConfig) -> SimResult:
    """Simulate all trades with given config."""
    old_pnl = 0.0
    new_pnl = 0.0
    old_wins = 0
    new_wins = 0
    rescued_losses = 0
    partial_wins = 0
    total_trades = 0

    for _, row in df.iterrows():
        if row['Result'] not in ['WIN', 'LOSS', 'TIMEOUT']:
            continue

        total_trades += 1
        original_pnl = row['Net PnL %']
        original_result = row['Result']

        old_pnl += original_pnl
        if original_result == 'WIN':
            old_wins += 1

        sim_pnl, sim_result = simulate_trade(row, config)
        new_pnl += sim_pnl

        if sim_pnl > 0:
            new_wins += 1

        if original_result == 'LOSS':
            if sim_pnl >= 0:
                rescued_losses += 1
            if sim_pnl > 0:
                partial_wins += 1

    return SimResult(
        old_pnl=old_pnl,
        new_pnl=new_pnl,
        old_wins=old_wins,
        new_wins=new_wins,
        rescued_losses=rescued_losses,
        partial_wins=partial_wins,
        total_trades=total_trades
    )


def analyze_mfe_distribution(df: pd.DataFrame, strategy: str = None):
    """Analyze MFE distribution for LOSS trades."""
    loss_df = df[df['Result'] == 'LOSS'].copy()

    if strategy:
        loss_df = loss_df[loss_df['Strategy'] == strategy]

    if len(loss_df) == 0:
        return

    bins = [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 1.0]
    labels = ['0-1%', '1-2%', '2-3%', '3-4%', '4-5%', '5-6%', '6-8%', '8%+']

    loss_df['MFE_bin'] = pd.cut(loss_df['MFE'], bins=bins, labels=labels)

    dist = loss_df.groupby('MFE_bin').size()
    total = len(loss_df)

    title = f"MFE Distribution for LOSS trades ({strategy})" if strategy else "MFE Distribution for ALL LOSS trades"
    print(f"\n{title}")
    print(f"Total LOSS trades: {total}")
    print("-" * 50)

    cumulative = 0
    for label in labels:
        count = dist.get(label, 0)
        pct = count / total * 100 if total > 0 else 0
        cumulative += count
        cum_pct = cumulative / total * 100 if total > 0 else 0
        bar = "#" * int(pct / 2)
        print(f"  MFE {label:6}: {count:6} ({pct:5.1f}%) {bar}")

    # Stats
    print(f"\n  Mean MFE:   {loss_df['MFE'].mean()*100:.2f}%")
    print(f"  Median MFE: {loss_df['MFE'].median()*100:.2f}%")
    print(f"  Max MFE:    {loss_df['MFE'].max()*100:.2f}%")


def grid_search(df: pd.DataFrame, strategy: str = None) -> Dict:
    """Grid search for optimal TP config."""

    if strategy:
        df = df[df['Strategy'] == strategy]

    df = df[df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])].copy()

    if len(df) == 0:
        return None

    tp1_levels = [0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05, 0.055, 0.06]
    tp2_levels = [0.08, 0.09, 0.10, 0.11, 0.12]
    splits = [0.3, 0.4, 0.5, 0.6, 0.7]

    best_config = None
    best_improvement = -float('inf')
    all_results = []

    baseline = simulate_all(df, TPConfig(tp1_pct=1.0, tp2_pct=1.0, split=0.0))

    for tp1 in tp1_levels:
        for tp2 in tp2_levels:
            if tp1 >= tp2:
                continue
            for split in splits:
                config = TPConfig(tp1_pct=tp1, tp2_pct=tp2, split=split)
                result = simulate_all(df, config)

                improvement = result.new_pnl - result.old_pnl

                all_results.append({
                    'tp1': tp1,
                    'tp2': tp2,
                    'split': split,
                    'old_pnl': result.old_pnl,
                    'new_pnl': result.new_pnl,
                    'improvement': improvement,
                    'old_wr': result.old_wins / result.total_trades * 100 if result.total_trades > 0 else 0,
                    'new_wr': result.new_wins / result.total_trades * 100 if result.total_trades > 0 else 0,
                    'rescued': result.rescued_losses,
                    'partial_wins': result.partial_wins,
                })

                if improvement > best_improvement:
                    best_improvement = improvement
                    best_config = config

    # Sort by improvement
    all_results = sorted(all_results, key=lambda x: x['improvement'], reverse=True)

    return {
        'strategy': strategy or 'ALL',
        'best_config': best_config,
        'baseline': baseline,
        'all_results': all_results,
        'df': df,
    }


def print_top_configs(results: Dict, top_n: int = 5):
    """Print top N configurations by improvement."""
    strategy = results['strategy']
    all_results = results['all_results']
    baseline = results['baseline']

    print(f"\n{'='*90}")
    print(f"TOP {top_n} CONFIGURATIONS: {strategy}")
    print(f"{'='*90}")
    print(f"Baseline: PnL={baseline.old_pnl*100:+.1f}%, WR={baseline.old_wins/baseline.total_trades*100:.1f}%, Trades={baseline.total_trades}")
    print()
    print(f"{'TP1':>6} | {'TP2':>6} | {'Split':>6} | {'Old PnL':>10} | {'New PnL':>10} | {'Improve':>10} | {'Old WR':>7} | {'New WR':>7} | {'Rescued':>8}")
    print("-" * 95)

    for r in all_results[:top_n]:
        print(f"{r['tp1']*100:>5.1f}% | {r['tp2']*100:>5.1f}% | {r['split']*100:>5.0f}% | "
              f"{r['old_pnl']*100:>+9.1f}% | {r['new_pnl']*100:>+9.1f}% | {r['improvement']*100:>+9.1f}% | "
              f"{r['old_wr']:>6.1f}% | {r['new_wr']:>6.1f}% | {r['rescued']:>8}")


def main():
    print("=" * 90)
    print("MULTI-LEVEL TP OPTIMIZER")
    print("=" * 90)
    print("Reading raw klines from cache/binance/ for accurate MFE calculation")
    print()

    # Load klines cache
    klines_cache = load_klines_cache("cache/binance")

    if not klines_cache:
        print("ERROR: No klines data found in cache!")
        return

    # Load trades
    df = load_all_trades("output")
    if df.empty:
        print("No trades found!")
        return

    print(f"Loaded {len(df)} trades from Excel")
    print(f"Strategies: {df['Strategy'].unique().tolist()}")

    # Calculate MFE for all trades using raw klines
    print("\nCalculating MFE from raw klines...")

    mfe_values = []
    mfe_calculated = 0
    mfe_failed = 0

    for idx, row in df.iterrows():
        symbol = row['Symbol']
        direction = row['Direction']
        entry_price = row['Entry Price']
        signal_date = pd.to_datetime(row['Signal Date'])
        exit_date = pd.to_datetime(row['Exit Date'])

        mfe = calculate_mfe_from_klines(
            symbol, direction, entry_price,
            signal_date, exit_date, klines_cache
        )
        mfe_values.append(mfe)

        if mfe > 0:
            mfe_calculated += 1
        else:
            mfe_failed += 1

    df['MFE'] = mfe_values
    print(f"MFE calculated: {mfe_calculated}, failed: {mfe_failed}")

    # Analyze MFE distribution for all LOSS trades
    analyze_mfe_distribution(df)

    # Grid search per strategy
    strategies = ['ls_fade', 'momentum', 'momentum_ls', 'mean_reversion', 'reversal']
    best_configs = {}

    for strategy in strategies:
        strat_df = df[df['Strategy'] == strategy]
        if len(strat_df) < 100:
            print(f"\n{strategy}: Not enough data ({len(strat_df)} trades)")
            continue

        analyze_mfe_distribution(df, strategy)
        results = grid_search(df, strategy)

        if results and results['all_results']:
            print_top_configs(results, top_n=5)
            best_configs[strategy] = results['all_results'][0]

    # Final summary
    print(f"\n{'='*90}")
    print("FINAL RECOMMENDATIONS")
    print(f"{'='*90}")
    print(f"{'Strategy':<15} | {'TP1':>6} | {'TP2':>6} | {'Split':>6} | {'Old PnL':>10} | {'New PnL':>10} | {'Improve':>10} | {'Rescued':>8}")
    print("-" * 100)

    total_old = 0
    total_new = 0
    total_rescued = 0

    for strategy in strategies:
        if strategy in best_configs:
            r = best_configs[strategy]
            total_old += r['old_pnl']
            total_new += r['new_pnl']
            total_rescued += r['rescued']
            print(f"{strategy:<15} | {r['tp1']*100:>5.1f}% | {r['tp2']*100:>5.1f}% | {r['split']*100:>5.0f}% | "
                  f"{r['old_pnl']*100:>+9.1f}% | {r['new_pnl']*100:>+9.1f}% | {r['improvement']*100:>+9.1f}% | {r['rescued']:>8}")

    print("-" * 100)
    improvement = total_new - total_old
    pct_improvement = (improvement / abs(total_old) * 100) if total_old != 0 else 0
    print(f"{'TOTAL':<15} | {'':>6} | {'':>6} | {'':>6} | "
          f"{total_old*100:>+9.1f}% | {total_new*100:>+9.1f}% | {improvement*100:>+9.1f}% | {total_rescued:>8}")
    print(f"\nRelative improvement: {pct_improvement:+.1f}%")
    print(f"Total LOSS trades rescued (became >= 0): {total_rescued}")

    print(f"\n{'='*90}")
    print("END OF ANALYSIS")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
