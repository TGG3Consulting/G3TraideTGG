# -*- coding: utf-8 -*-
"""
Pattern Analyzer - Deep analysis of backtest results to find new trading patterns.

Analyzes Excel files to discover:
- Win rate by day of week, hour, month
- Win rate by coin regime and volatility
- Best/worst symbols
- Chain patterns
- L/S ratio correlation
- Direction analysis
- Volatility sweet spots
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')


def load_all_trades(output_dir: str = "output") -> pd.DataFrame:
    """Load all trades from xlsx files."""
    all_trades = []

    for xlsx_file in Path(output_dir).glob("backtest_*.xlsx"):
        try:
            # Extract strategy name
            parts = xlsx_file.stem.split("_")
            strategy = "_".join(parts[1:-2])

            df = pd.read_excel(xlsx_file, sheet_name="Trades")
            df['Strategy'] = strategy
            df['Source'] = xlsx_file.name
            all_trades.append(df)
        except Exception as e:
            print(f"Error loading {xlsx_file.name}: {e}")

    if not all_trades:
        return pd.DataFrame()

    combined = pd.concat(all_trades, ignore_index=True)
    print(f"Loaded {len(combined)} trades from {len(all_trades)} files")
    return combined


def analyze_by_day_of_week(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze win rate by day of week."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()
    df['DayOfWeek'] = pd.to_datetime(df['Signal Date']).dt.dayofweek
    df['DayName'] = pd.to_datetime(df['Signal Date']).dt.day_name()

    result = df.groupby(['Strategy', 'DayOfWeek', 'DayName']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    # Calculate wins
    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'DayOfWeek', 'DayName']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index().sort_values(['Strategy', 'DayOfWeek'])


def analyze_by_volatility(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze win rate by volatility ranges."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    # Create volatility bins
    bins = [0, 3, 5, 8, 12, 20, 100]
    labels = ['0-3%', '3-5%', '5-8%', '8-12%', '12-20%', '20%+']
    df['VolBin'] = pd.cut(df['Coin Vol %'], bins=bins, labels=labels)

    result = df.groupby(['Strategy', 'VolBin']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'VolBin']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_by_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze win rate by coin regime."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    if 'Coin Regime' not in df.columns:
        print("No Coin Regime column found")
        return pd.DataFrame()

    result = df.groupby(['Strategy', 'Coin Regime']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Coin Regime']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_by_direction(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze by direction (LONG vs SHORT)."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    result = df.groupby(['Strategy', 'Direction']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Direction']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_by_ls_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze by Long/Short ratio ranges."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    if 'Long %' not in df.columns:
        print("No Long % column found")
        return pd.DataFrame()

    # Create L/S bins
    bins = [0, 40, 45, 50, 55, 60, 100]
    labels = ['<40%', '40-45%', '45-50%', '50-55%', '55-60%', '>60%']
    df['LSBin'] = pd.cut(df['Long %'], bins=bins, labels=labels)

    result = df.groupby(['Strategy', 'LSBin']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'LSBin']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze by symbol - find best/worst performers."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    result = df.groupby(['Strategy', 'Symbol']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Symbol']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index().sort_values(['Strategy', 'TotalPnL'], ascending=[True, False])


def analyze_by_chain(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze by chain sequence position."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    if 'Chain Seq' not in df.columns:
        print("No Chain Seq column found")
        return pd.DataFrame()

    result = df.groupby(['Strategy', 'Chain Seq']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Chain Seq']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_direction_by_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze direction performance by regime - key pattern discovery."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    if 'Coin Regime' not in df.columns:
        return pd.DataFrame()

    result = df.groupby(['Strategy', 'Coin Regime', 'Direction']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Coin Regime', 'Direction']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)
    result['AvgPnL'] = (result['TotalPnL'] / result['Trades']).round(2)

    return result.reset_index()


def analyze_vol_by_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze volatility sweet spots by regime."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    if 'Coin Regime' not in df.columns or 'Coin Vol %' not in df.columns:
        return pd.DataFrame()

    bins = [0, 5, 10, 15, 20, 100]
    labels = ['0-5%', '5-10%', '10-15%', '15-20%', '20%+']
    df['VolBin'] = pd.cut(df['Coin Vol %'], bins=bins, labels=labels)

    result = df.groupby(['Strategy', 'Coin Regime', 'VolBin']).agg({
        'Result': 'count',
        'Net PnL %': 'sum'
    }).rename(columns={'Result': 'Trades', 'Net PnL %': 'TotalPnL'})

    wins = df[df['Result'] == 'WIN'].groupby(['Strategy', 'Coin Regime', 'VolBin']).size()
    result['Wins'] = wins
    result['WinRate'] = (result['Wins'] / result['Trades'] * 100).round(1)

    return result.reset_index()


def find_best_conditions(df: pd.DataFrame) -> Dict:
    """Find best trading conditions for each strategy."""
    df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()

    best = {}
    for strategy in df['Strategy'].unique():
        sdf = df[df['Strategy'] == strategy]

        # Best volatility range
        if 'Coin Vol %' in sdf.columns:
            bins = [0, 3, 5, 8, 12, 20, 100]
            labels = ['0-3%', '3-5%', '5-8%', '8-12%', '12-20%', '20%+']
            sdf_copy = sdf.copy()
            sdf_copy['VolBin'] = pd.cut(sdf_copy['Coin Vol %'], bins=bins, labels=labels)
            vol_stats = sdf_copy.groupby('VolBin').agg({
                'Result': lambda x: (x == 'WIN').sum() / len(x) * 100,
                'Net PnL %': 'sum'
            })
            best_vol = vol_stats['Net PnL %'].idxmax() if not vol_stats.empty else None
        else:
            best_vol = None

        # Best regime
        if 'Coin Regime' in sdf.columns:
            regime_stats = sdf.groupby('Coin Regime').agg({
                'Result': lambda x: (x == 'WIN').sum() / len(x) * 100,
                'Net PnL %': 'sum'
            })
            best_regime = regime_stats['Net PnL %'].idxmax() if not regime_stats.empty else None
        else:
            best_regime = None

        # Best direction
        dir_stats = sdf.groupby('Direction').agg({
            'Result': lambda x: (x == 'WIN').sum() / len(x) * 100,
            'Net PnL %': 'sum'
        })
        best_dir = dir_stats['Net PnL %'].idxmax() if not dir_stats.empty else None

        # Best day of week
        sdf_copy = sdf.copy()
        sdf_copy['DayOfWeek'] = pd.to_datetime(sdf_copy['Signal Date']).dt.day_name()
        day_stats = sdf_copy.groupby('DayOfWeek').agg({
            'Result': lambda x: (x == 'WIN').sum() / len(x) * 100,
            'Net PnL %': 'sum'
        })
        best_day = day_stats['Net PnL %'].idxmax() if not day_stats.empty else None

        best[strategy] = {
            'best_volatility': best_vol,
            'best_regime': best_regime,
            'best_direction': best_dir,
            'best_day': best_day,
        }

    return best


def print_pattern_report(df: pd.DataFrame):
    """Print comprehensive pattern analysis report."""
    print("\n" + "=" * 80)
    print("PATTERN ANALYSIS REPORT")
    print("=" * 80)
    print(f"Total trades analyzed: {len(df)}")
    print(f"Strategies: {df['Strategy'].unique().tolist()}")

    # 1. Day of week analysis
    print("\n" + "-" * 80)
    print("1. WIN RATE BY DAY OF WEEK")
    print("-" * 80)
    day_df = analyze_by_day_of_week(df)
    if not day_df.empty:
        for strategy in day_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = day_df[day_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " ***" if row['WinRate'] >= 40 else " XXX" if row['WinRate'] < 30 else ""
                print(f"  {row['DayName']:10} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 2. Volatility analysis
    print("\n" + "-" * 80)
    print("2. WIN RATE BY VOLATILITY")
    print("-" * 80)
    vol_df = analyze_by_volatility(df)
    if not vol_df.empty:
        for strategy in vol_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = vol_df[vol_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " ***" if row['WinRate'] >= 40 else " XXX" if row['WinRate'] < 30 else ""
                print(f"  {row['VolBin']:10} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 3. Regime analysis
    print("\n" + "-" * 80)
    print("3. WIN RATE BY COIN REGIME")
    print("-" * 80)
    regime_df = analyze_by_regime(df)
    if not regime_df.empty:
        for strategy in regime_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = regime_df[regime_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " ***" if row['WinRate'] >= 40 else " XXX" if row['WinRate'] < 30 else ""
                print(f"  {row['Coin Regime']:12} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 4. Direction analysis
    print("\n" + "-" * 80)
    print("4. WIN RATE BY DIRECTION")
    print("-" * 80)
    dir_df = analyze_by_direction(df)
    if not dir_df.empty:
        for strategy in dir_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = dir_df[dir_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " ***" if row['WinRate'] >= 40 else " XXX" if row['WinRate'] < 30 else ""
                print(f"  {row['Direction']:6} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 5. L/S Ratio analysis
    print("\n" + "-" * 80)
    print("5. WIN RATE BY L/S RATIO")
    print("-" * 80)
    ls_df = analyze_by_ls_ratio(df)
    if not ls_df.empty:
        for strategy in ls_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = ls_df[ls_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " ***" if row['WinRate'] >= 40 else " XXX" if row['WinRate'] < 30 else ""
                print(f"  Long {row['LSBin']:8} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 6. Direction by Regime (KEY PATTERN)
    print("\n" + "-" * 80)
    print("6. DIRECTION x REGIME MATRIX (KEY PATTERNS)")
    print("-" * 80)
    dir_regime_df = analyze_direction_by_regime(df)
    if not dir_regime_df.empty:
        for strategy in dir_regime_df['Strategy'].unique():
            print(f"\n{strategy}:")
            sdf = dir_regime_df[dir_regime_df['Strategy'] == strategy]
            for _, row in sdf.iterrows():
                marker = " !!!" if row['WinRate'] >= 45 else " ***" if row['WinRate'] >= 38 else " XXX" if row['WinRate'] < 28 else ""
                print(f"  {row['Coin Regime']:12} {row['Direction']:6} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}{marker}")

    # 7. Best conditions summary
    print("\n" + "-" * 80)
    print("7. BEST CONDITIONS PER STRATEGY")
    print("-" * 80)
    best = find_best_conditions(df)
    for strategy, conditions in best.items():
        print(f"\n{strategy}:")
        print(f"  Best Volatility: {conditions['best_volatility']}")
        print(f"  Best Regime:     {conditions['best_regime']}")
        print(f"  Best Direction:  {conditions['best_direction']}")
        print(f"  Best Day:        {conditions['best_day']}")

    # 8. Top/Bottom symbols
    print("\n" + "-" * 80)
    print("8. TOP 5 / BOTTOM 5 SYMBOLS BY PNL")
    print("-" * 80)
    symbol_df = analyze_by_symbol(df)
    if not symbol_df.empty:
        for strategy in symbol_df['Strategy'].unique():
            sdf = symbol_df[symbol_df['Strategy'] == strategy].sort_values('TotalPnL', ascending=False)
            print(f"\n{strategy}:")
            print("  TOP 5:")
            for _, row in sdf.head(5).iterrows():
                print(f"    {row['Symbol']:15} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}")
            print("  BOTTOM 5:")
            for _, row in sdf.tail(5).iterrows():
                print(f"    {row['Symbol']:15} | WR: {row['WinRate']:5.1f}% | PnL: {row['TotalPnL']:+8.1f}% | Trades: {row['Trades']:4}")

    print("\n" + "=" * 80)
    print("END OF PATTERN ANALYSIS")
    print("=" * 80)


def main():
    df = load_all_trades("output")
    if df.empty:
        print("No trades found!")
        return

    print_pattern_report(df)


if __name__ == "__main__":
    main()
