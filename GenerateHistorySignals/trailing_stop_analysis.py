"""
TRAILING STOP OPTIMIZATION ANALYSIS
====================================
Analyze all backtest Excel files to determine optimal trailing stop logic
for each strategy.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import warnings
import sys
import io

# Fix encoding for Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

warnings.filterwarnings('ignore')

OUTPUT_DIR = Path(r"G:\BinanceFriend\GenerateHistorySignals\output")

def load_all_backtest_files():
    """Load all backtest Excel files (excluding EMA/SMA)"""
    files = list(OUTPUT_DIR.glob("backtest_*.xlsx"))
    files = [f for f in files if 'ema' not in f.name.lower() and 'sma' not in f.name.lower()]
    print(f"Found {len(files)} Excel files")
    return files

def extract_strategy_name(filename):
    """Extract strategy name from filename"""
    name = filename.stem
    parts = name.split('_')
    if len(parts) >= 2:
        strategy_parts = []
        for i, part in enumerate(parts[1:], 1):
            if part.isdigit() and len(part) == 8:
                break
            strategy_parts.append(part)
        return '_'.join(strategy_parts)
    return 'unknown'

def analyze_single_file(filepath):
    """Analyze single backtest file"""
    try:
        df = pd.read_excel(filepath)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"Error reading {filepath.name}: {e}")
        return None

def calculate_trailing_stop_metrics(df):
    """Calculate metrics for trailing stop evaluation"""
    available_cols = df.columns.tolist()

    col_mapping = {}
    for col in available_cols:
        col_lower = col.lower()
        if 'entry' in col_lower and 'price' in col_lower:
            col_mapping['entry_price'] = col
        elif 'exit' in col_lower and 'price' in col_lower:
            col_mapping['exit_price'] = col
        elif col_lower in ['high', 'high_price', 'max_price', 'peak_price']:
            col_mapping['high_price'] = col
        elif col_lower in ['low', 'low_price', 'min_price', 'trough_price']:
            col_mapping['low_price'] = col
        elif col_lower in ['direction', 'side', 'signal_direction']:
            col_mapping['direction'] = col
        elif 'pnl' in col_lower and 'pct' in col_lower:
            col_mapping['pnl_pct'] = col
        elif col_lower == 'pnl%':
            col_mapping['pnl_pct'] = col
        elif col_lower in ['result', 'outcome', 'status']:
            col_mapping['result'] = col

    return col_mapping, df

def analyze_mfe_mae(df, col_mapping):
    """
    Analyze MFE/MAE for optimal trailing stop determination.

    MFE = Maximum Favorable Excursion (max profit during trade)
    MAE = Maximum Adverse Excursion (max drawdown during trade)
    """

    results = {
        'total_trades': len(df),
        'winning_trades': 0,
        'losing_trades': 0,
        'mfe_distribution': [],
        'mae_distribution': [],
        'giveback_distribution': [],
        'pnl_distribution': [],
    }

    if 'entry_price' not in col_mapping or 'exit_price' not in col_mapping:
        return results

    entry_col = col_mapping['entry_price']
    exit_col = col_mapping['exit_price']

    for idx, row in df.iterrows():
        try:
            entry = float(row[entry_col])
            exit_price = float(row[exit_col])

            direction = 'LONG'
            if 'direction' in col_mapping:
                dir_val = str(row[col_mapping['direction']]).upper()
                if 'SHORT' in dir_val or dir_val == 'S':
                    direction = 'SHORT'

            if direction == 'LONG':
                pnl_pct = (exit_price - entry) / entry * 100
            else:
                pnl_pct = (entry - exit_price) / entry * 100

            results['pnl_distribution'].append(pnl_pct)

            if 'high_price' in col_mapping and 'low_price' in col_mapping:
                high = float(row[col_mapping['high_price']])
                low = float(row[col_mapping['low_price']])

                if direction == 'LONG':
                    mfe = (high - entry) / entry * 100
                    mae = (entry - low) / entry * 100
                else:
                    mfe = (entry - low) / entry * 100
                    mae = (high - entry) / entry * 100

                if mfe >= 0 and mae >= 0:
                    results['mfe_distribution'].append(mfe)
                    results['mae_distribution'].append(mae)

                    if mfe > 0:
                        giveback = mfe - max(pnl_pct, -mae)
                        results['giveback_distribution'].append(giveback)

            if pnl_pct > 0:
                results['winning_trades'] += 1
            else:
                results['losing_trades'] += 1

        except (ValueError, TypeError):
            continue

    return results

def print_strategy_analysis(strategy_name, all_results):
    """Print analysis for strategy"""

    print(f"\n{'='*70}")
    print(f"STRATEGY: {strategy_name.upper()}")
    print(f"{'='*70}")

    total_trades = sum(r['total_trades'] for r in all_results)
    total_wins = sum(r['winning_trades'] for r in all_results)
    total_losses = sum(r['losing_trades'] for r in all_results)

    all_mfe = []
    all_mae = []
    all_giveback = []
    all_pnl = []

    for r in all_results:
        all_mfe.extend(r.get('mfe_distribution', []))
        all_mae.extend(r.get('mae_distribution', []))
        all_giveback.extend(r.get('giveback_distribution', []))
        all_pnl.extend(r.get('pnl_distribution', []))

    print(f"\nGENERAL STATISTICS:")
    print(f"  Total trades: {total_trades}")
    if total_trades > 0:
        print(f"  Winning: {total_wins} ({total_wins/total_trades*100:.1f}%)")
        print(f"  Losing: {total_losses} ({total_losses/total_trades*100:.1f}%)")

    if all_mfe:
        print(f"\nMFE (Maximum Favorable Excursion):")
        print(f"  Mean: {np.mean(all_mfe):.2f}%")
        print(f"  Median: {np.median(all_mfe):.2f}%")
        print(f"  P25/P50/P75: {np.percentile(all_mfe, 25):.2f}% / {np.percentile(all_mfe, 50):.2f}% / {np.percentile(all_mfe, 75):.2f}%")
        print(f"  P90: {np.percentile(all_mfe, 90):.2f}%")

    if all_mae:
        print(f"\nMAE (Maximum Adverse Excursion):")
        print(f"  Mean: {np.mean(all_mae):.2f}%")
        print(f"  Median: {np.median(all_mae):.2f}%")
        print(f"  P75/P90: {np.percentile(all_mae, 75):.2f}% / {np.percentile(all_mae, 90):.2f}%")

    if all_giveback:
        print(f"\nGIVEBACK (profit lost after MFE peak):")
        print(f"  Mean: {np.mean(all_giveback):.2f}%")
        print(f"  Median: {np.median(all_giveback):.2f}%")
        print(f"  P75: {np.percentile(all_giveback, 75):.2f}%")

        print(f"\n{'-'*70}")
        print(f"TRAILING STOP RECOMMENDATIONS:")
        print(f"{'-'*70}")

        avg_mfe = np.mean(all_mfe)
        median_mfe = np.median(all_mfe)
        p75_mfe = np.percentile(all_mfe, 75)
        avg_giveback = np.mean(all_giveback)
        median_mae = np.median(all_mae)
        p75_mae = np.percentile(all_mae, 75)
        p90_mae = np.percentile(all_mae, 90)

        # Calculate optimal trailing stop parameters
        # Activation: when profit reaches meaningful level (not too early to avoid noise)
        # Trail distance: wide enough to avoid noise, tight enough to lock profit

        # Method 1: Based on MFE/MAE distribution
        activation_pct = median_mfe * 0.5  # Activate at 50% of median MFE
        trail_distance = p75_mae * 0.7  # Trail at 70% of P75 MAE

        # Method 2: Based on giveback analysis
        activation_alt = avg_giveback  # Activate when profit = avg giveback
        trail_alt = median_mae  # Trail at median MAE

        print(f"\n  METHOD 1 (MFE/MAE based):")
        print(f"    Activation: {activation_pct:.1f}% profit")
        print(f"    Trail distance: {trail_distance:.1f}% from peak")
        print(f"    Logic: Activate trailing when profit >= {activation_pct:.1f}%,")
        print(f"           then trail stop at {trail_distance:.1f}% below peak price")

        print(f"\n  METHOD 2 (Giveback based):")
        print(f"    Activation: {activation_alt:.1f}% profit")
        print(f"    Trail distance: {trail_alt:.1f}% from peak")

        # Specific recommendation based on strategy type
        print(f"\n  STRATEGY-SPECIFIC RECOMMENDATION:")

        if 'momentum' in strategy_name.lower():
            # Momentum: wider trail, let winners run
            rec_activation = max(3.0, p75_mfe * 0.4)
            rec_trail = max(2.5, p75_mae * 0.6)
            print(f"    Type: MOMENTUM - let winners run")
            print(f"    Activation: {rec_activation:.1f}%")
            print(f"    Trail: {rec_trail:.1f}%")
            print(f"    Step trail: YES (move trail every {rec_trail/2:.1f}% gain)")

        elif 'mean_reversion' in strategy_name.lower():
            # Mean reversion: tighter trail, quick exits
            rec_activation = max(2.0, median_mfe * 0.4)
            rec_trail = max(1.5, median_mae * 0.8)
            print(f"    Type: MEAN REVERSION - quick profit lock")
            print(f"    Activation: {rec_activation:.1f}%")
            print(f"    Trail: {rec_trail:.1f}%")
            print(f"    Step trail: NO (continuous)")

        elif 'reversal' in strategy_name.lower():
            # Reversal: medium settings
            rec_activation = max(2.5, median_mfe * 0.5)
            rec_trail = max(2.0, p75_mae * 0.5)
            print(f"    Type: REVERSAL - balanced approach")
            print(f"    Activation: {rec_activation:.1f}%")
            print(f"    Trail: {rec_trail:.1f}%")

        elif 'ls_fade' in strategy_name.lower():
            # LS Fade: contrarian, tighter trail
            rec_activation = max(2.0, median_mfe * 0.35)
            rec_trail = max(1.5, median_mae * 0.7)
            print(f"    Type: LS FADE - contrarian quick lock")
            print(f"    Activation: {rec_activation:.1f}%")
            print(f"    Trail: {rec_trail:.1f}%")

        else:
            rec_activation = max(2.5, median_mfe * 0.45)
            rec_trail = max(2.0, p75_mae * 0.6)
            print(f"    Activation: {rec_activation:.1f}%")
            print(f"    Trail: {rec_trail:.1f}%")

        return {
            'strategy': strategy_name,
            'total_trades': total_trades,
            'win_rate': total_wins/total_trades*100 if total_trades > 0 else 0,
            'avg_mfe': avg_mfe,
            'median_mfe': median_mfe,
            'p75_mfe': p75_mfe,
            'avg_mae': np.mean(all_mae),
            'median_mae': median_mae,
            'p75_mae': p75_mae,
            'avg_giveback': avg_giveback,
            'recommended_activation': rec_activation if 'rec_activation' in dir() else activation_pct,
            'recommended_trail': rec_trail if 'rec_trail' in dir() else trail_distance,
        }

    return None

def main():
    print("="*70)
    print("TRAILING STOP OPTIMIZATION ANALYSIS")
    print("="*70)

    files = load_all_backtest_files()

    strategy_files = defaultdict(list)
    for f in files:
        strategy = extract_strategy_name(f)
        strategy_files[strategy].append(f)

    print(f"\nStrategies found: {len(strategy_files)}")
    for s, fs in strategy_files.items():
        print(f"  {s}: {len(fs)} files")

    all_recommendations = []

    for strategy, files_list in strategy_files.items():
        print(f"\n\n{'#'*70}")
        print(f"Analyzing: {strategy}")
        print(f"{'#'*70}")

        strategy_results = []

        # Take sample of files for analysis
        sample_files = files_list[:10]

        for f in sample_files:
            df = analyze_single_file(f)

            if df is not None and len(df) > 0:
                col_mapping, df = calculate_trailing_stop_metrics(df)
                results = analyze_mfe_mae(df, col_mapping)
                results['file'] = f.name
                strategy_results.append(results)

        if strategy_results:
            rec = print_strategy_analysis(strategy, strategy_results)
            if rec:
                all_recommendations.append(rec)

    # Final summary
    print("\n\n")
    print("="*70)
    print("FINAL SUMMARY: TRAILING STOP RECOMMENDATIONS BY STRATEGY")
    print("="*70)

    print("\n{:<20} {:>8} {:>8} {:>8} {:>8} {:>10} {:>8}".format(
        "Strategy", "WinR%", "AvgMFE", "MedMFE", "AvgMAE", "Activation", "Trail"))
    print("-"*70)

    for rec in all_recommendations:
        print("{:<20} {:>7.1f}% {:>7.2f}% {:>7.2f}% {:>7.2f}% {:>9.1f}% {:>7.1f}%".format(
            rec['strategy'],
            rec['win_rate'],
            rec['avg_mfe'],
            rec['median_mfe'],
            rec['avg_mae'],
            rec['recommended_activation'],
            rec['recommended_trail']))

    print("\n" + "="*70)
    print("ALGORITHM RECOMMENDATIONS:")
    print("="*70)

    print("""
    1. MOMENTUM / MOMENTUM_LS:
       - Use STEP TRAILING: move stop only after each X% gain
       - Activation: 3-4% profit
       - Trail: 2.5-3% from peak
       - Step: move trail every 1.5% additional gain
       - Rationale: Let trends run, avoid noise-triggered exits

    2. MEAN_REVERSION:
       - Use CONTINUOUS TRAILING: always trail behind peak
       - Activation: 2-2.5% profit
       - Trail: 1.5-2% from peak
       - Rationale: Quick profit lock, these trades have limited upside

    3. REVERSAL:
       - Use HYBRID: Step trail until 5%, then continuous
       - Activation: 2.5-3% profit
       - Trail: 2-2.5% from peak
       - Rationale: Initial move strong, then uncertain

    4. LS_FADE:
       - Use TIGHT CONTINUOUS TRAILING
       - Activation: 2% profit
       - Trail: 1.5% from peak
       - Rationale: Contrarian plays need quick exits

    UNIVERSAL ATR-BASED ALTERNATIVE:
       - Activation: 0.8 x ATR profit
       - Trail: 0.6 x ATR from peak
       - Adapts to coin volatility automatically
    """)

if __name__ == "__main__":
    main()
