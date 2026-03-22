# -*- coding: utf-8 -*-
"""
Compare Momentum Strategy: Our 5% threshold vs Standard 0% threshold
"""
import pandas as pd
import numpy as np

def run_momentum_backtest(symbol, filepath, threshold):
    """Run momentum backtest with given threshold."""
    df = pd.read_excel(filepath)

    position_size = 1500
    commission_pct = 0.1
    sl = 1.5
    activation = 1.5
    callback = 0.3

    opens = df['Open'].values
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    dates = df['Date'].values
    n_days = len(df)

    # 7-day price change
    lookback = 7
    df['PC_7d'] = (df['Close'] - df['Close'].shift(lookback)) / df['Close'].shift(lookback) * 100
    pc = df['PC_7d'].values

    last_60 = dates[-60]

    def simulate_trade(entry_day, direction):
        if entry_day >= n_days:
            return entry_day, 0, 'SKIP'
        entry_price = opens[entry_day]
        if direction == 'LONG':
            sl_price = entry_price * (1 - sl / 100)
            activation_price = entry_price * (1 + activation / 100)
        else:
            sl_price = entry_price * (1 + sl / 100)
            activation_price = entry_price * (1 - activation / 100)
        highest_high = highs[entry_day]
        lowest_low = lows[entry_day]
        trailing_activated = False
        exit_day = entry_day
        while exit_day < n_days:
            low, high, close = lows[exit_day], highs[exit_day], closes[exit_day]
            if high > highest_high: highest_high = high
            if low < lowest_low: lowest_low = low
            if direction == 'LONG':
                if low <= sl_price:
                    return exit_day, -sl, 'SL'
                if high >= activation_price:
                    trailing_activated = True
                if trailing_activated:
                    trail_price = highest_high * (1 - callback / 100)
                    if low <= trail_price:
                        return exit_day, (trail_price - entry_price) / entry_price * 100, 'TRAIL'
            else:
                if high >= sl_price:
                    return exit_day, -sl, 'SL'
                if low <= activation_price:
                    trailing_activated = True
                if trailing_activated:
                    trail_price = lowest_low * (1 + callback / 100)
                    if high >= trail_price:
                        return exit_day, (entry_price - trail_price) / entry_price * 100, 'TRAIL'
            if exit_day == n_days - 1:
                if direction == 'LONG':
                    return exit_day, (close - entry_price) / entry_price * 100, 'CLOSE'
                else:
                    return exit_day, (entry_price - close) / entry_price * 100, 'CLOSE'
            exit_day += 1
        return exit_day, 0, 'SKIP'

    def signal_func(d):
        if pd.isna(pc[d]):
            return None
        if threshold == 0:
            # Standard TSMOM: any positive = LONG, any negative = SHORT
            if pc[d] > 0:
                return 'LONG'
            elif pc[d] < 0:
                return 'SHORT'
            return None
        else:
            # Our approach: threshold-based
            if pc[d] >= threshold:
                return 'LONG'
            elif pc[d] <= -threshold:
                return 'SHORT'
            return None

    # Run backtest
    results = []
    cumulative_usd = 0
    peak = 0
    max_dd = 0
    long_trades = 0
    short_trades = 0
    long_wins = 0
    short_wins = 0
    long_pnl = 0
    short_pnl = 0

    signal_day = lookback
    while signal_day < n_days - 1:
        direction = signal_func(signal_day)
        if direction is None:
            signal_day += 1
            continue

        entry_day = signal_day + 1
        if entry_day >= n_days:
            break

        exit_day, pnl_pct, result = simulate_trade(entry_day, direction)
        if result == 'SKIP':
            signal_day += 1
            continue

        comm = position_size * commission_pct * 2 / 100
        pnl_usd = position_size * pnl_pct / 100 - comm
        cumulative_usd += pnl_usd

        if cumulative_usd > peak:
            peak = cumulative_usd
        dd = peak - cumulative_usd
        if dd > max_dd:
            max_dd = dd

        if direction == 'LONG':
            long_trades += 1
            long_pnl += pnl_usd
            if pnl_usd > 0:
                long_wins += 1
        else:
            short_trades += 1
            short_pnl += pnl_usd
            if pnl_usd > 0:
                short_wins += 1

        results.append({'Entry_Date': dates[entry_day], 'Direction': direction, 'PnL_USD': pnl_usd})
        signal_day = exit_day + 1

    if len(results) == 0:
        return {
            'trades': 0, 'wins': 0, 'pnl': 0, 'max_dd': 0,
            'trades_2m': 0, 'wins_2m': 0, 'pnl_2m': 0,
            'long_trades': 0, 'long_wins': 0, 'long_pnl': 0,
            'short_trades': 0, 'short_wins': 0, 'short_pnl': 0
        }

    rdf = pd.DataFrame(results)
    total = len(rdf)
    wins = len(rdf[rdf['PnL_USD'] > 0])

    rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
    total_2m = len(rdf_2m)
    wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
    pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0

    return {
        'trades': total,
        'wins': wins,
        'pnl': cumulative_usd,
        'max_dd': max_dd,
        'trades_2m': total_2m,
        'wins_2m': wins_2m,
        'pnl_2m': pnl_2m,
        'long_trades': long_trades,
        'long_wins': long_wins,
        'long_pnl': long_pnl,
        'short_trades': short_trades,
        'short_wins': short_wins,
        'short_pnl': short_pnl
    }


# Run for both thresholds and both symbols
print("=" * 100)
print("MOMENTUM STRATEGY: OUR 5% vs STANDARD 0% THRESHOLD")
print("Parameters: SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%")
print("=" * 100)
print()

results_all = []

for symbol, filepath in [
    ('BTC', r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx'),
    ('XRP', r'G:\BinanceFriend\XRPUSDT_volatility_2y_with_LS.xlsx')
]:
    for threshold in [5, 0]:
        r = run_momentum_backtest(symbol, filepath, threshold)
        r['symbol'] = symbol
        r['threshold'] = f"{threshold}%"
        results_all.append(r)

# Create comparison table
print("=" * 100)
print("FULL 2-YEAR RESULTS")
print("=" * 100)
print()
print(f"{'Config':<20} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'MaxDD':>10} | {'LONG':>6} {'L_Win%':>7} {'L_PnL':>10} | {'SHORT':>6} {'S_Win%':>7} {'S_PnL':>10}")
print("-" * 120)

for r in results_all:
    config = f"{r['symbol']} {r['threshold']}"
    win_pct = r['wins']/r['trades']*100 if r['trades'] > 0 else 0
    l_win = r['long_wins']/r['long_trades']*100 if r['long_trades'] > 0 else 0
    s_win = r['short_wins']/r['short_trades']*100 if r['short_trades'] > 0 else 0
    print(f"{config:<20} {r['trades']:>7} {win_pct:>6.1f}% ${r['pnl']:>+10,.0f} ${r['max_dd']:>9,.0f} | {r['long_trades']:>6} {l_win:>6.1f}% ${r['long_pnl']:>+9,.0f} | {r['short_trades']:>6} {s_win:>6.1f}% ${r['short_pnl']:>+9,.0f}")

print()
print()

# Combined BTC + XRP
print("=" * 100)
print("COMBINED BTC + XRP")
print("=" * 100)
print()
print(f"{'Threshold':<15} {'Trades':>7} {'PnL':>12} {'MaxDD':>10} | {'LONG':>6} {'L_PnL':>10} | {'SHORT':>6} {'S_PnL':>10} | {'Tr_2M':>6} {'PnL_2M':>10}")
print("-" * 110)

for threshold in ['5%', '0%']:
    btc = [r for r in results_all if r['symbol'] == 'BTC' and r['threshold'] == threshold][0]
    xrp = [r for r in results_all if r['symbol'] == 'XRP' and r['threshold'] == threshold][0]

    combined = {
        'trades': btc['trades'] + xrp['trades'],
        'pnl': btc['pnl'] + xrp['pnl'],
        'max_dd': max(btc['max_dd'], xrp['max_dd']),
        'long_trades': btc['long_trades'] + xrp['long_trades'],
        'long_pnl': btc['long_pnl'] + xrp['long_pnl'],
        'short_trades': btc['short_trades'] + xrp['short_trades'],
        'short_pnl': btc['short_pnl'] + xrp['short_pnl'],
        'trades_2m': btc['trades_2m'] + xrp['trades_2m'],
        'pnl_2m': btc['pnl_2m'] + xrp['pnl_2m'],
    }

    label = "OUR (5%)" if threshold == '5%' else "STANDARD (0%)"
    print(f"{label:<15} {combined['trades']:>7} ${combined['pnl']:>+10,.0f} ${combined['max_dd']:>9,.0f} | {combined['long_trades']:>6} ${combined['long_pnl']:>+9,.0f} | {combined['short_trades']:>6} ${combined['short_pnl']:>+9,.0f} | {combined['trades_2m']:>6} ${combined['pnl_2m']:>+9,.0f}")

print()
print()

# 2-Month results
print("=" * 100)
print("LAST 2 MONTHS RESULTS")
print("=" * 100)
print()
print(f"{'Config':<20} {'Tr_2M':>7} {'Win%_2M':>8} {'PnL_2M':>12}")
print("-" * 50)

for r in results_all:
    config = f"{r['symbol']} {r['threshold']}"
    win_pct = r['wins_2m']/r['trades_2m']*100 if r['trades_2m'] > 0 else 0
    print(f"{config:<20} {r['trades_2m']:>7} {win_pct:>7.1f}% ${r['pnl_2m']:>+10,.0f}")

print()
print()
print("=" * 100)
print("SUMMARY: 5% vs 0% THRESHOLD")
print("=" * 100)

# Calculate differences
btc_5 = [r for r in results_all if r['symbol'] == 'BTC' and r['threshold'] == '5%'][0]
btc_0 = [r for r in results_all if r['symbol'] == 'BTC' and r['threshold'] == '0%'][0]
xrp_5 = [r for r in results_all if r['symbol'] == 'XRP' and r['threshold'] == '5%'][0]
xrp_0 = [r for r in results_all if r['symbol'] == 'XRP' and r['threshold'] == '0%'][0]

total_5 = btc_5['pnl'] + xrp_5['pnl']
total_0 = btc_0['pnl'] + xrp_0['pnl']
trades_5 = btc_5['trades'] + xrp_5['trades']
trades_0 = btc_0['trades'] + xrp_0['trades']

print()
print(f"OUR (5%):      {trades_5} trades, PnL ${total_5:+,.0f}")
print(f"STANDARD (0%): {trades_0} trades, PnL ${total_0:+,.0f}")
print()
print(f"Difference: {trades_0 - trades_5} more trades with 0%, PnL diff: ${total_0 - total_5:+,.0f}")
print()
if total_5 > total_0:
    print("WINNER: OUR 5% THRESHOLD")
elif total_0 > total_5:
    print("WINNER: STANDARD 0% THRESHOLD")
else:
    print("TIE")
