# -*- coding: utf-8 -*-
"""
Compare Momentum Strategy: 0%, 3%, 5% thresholds
"""
import pandas as pd
import numpy as np

def run_momentum_backtest(symbol, filepath, threshold):
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
            if pc[d] > 0:
                return 'LONG'
            elif pc[d] < 0:
                return 'SHORT'
            return None
        else:
            if pc[d] >= threshold:
                return 'LONG'
            elif pc[d] <= -threshold:
                return 'SHORT'
            return None

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
        return {'trades': 0, 'wins': 0, 'pnl': 0, 'max_dd': 0, 'trades_2m': 0, 'wins_2m': 0, 'pnl_2m': 0,
                'long_trades': 0, 'long_wins': 0, 'long_pnl': 0, 'short_trades': 0, 'short_wins': 0, 'short_pnl': 0}

    rdf = pd.DataFrame(results)
    total = len(rdf)
    wins = len(rdf[rdf['PnL_USD'] > 0])
    rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
    total_2m = len(rdf_2m)
    wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
    pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0

    return {
        'trades': total, 'wins': wins, 'pnl': cumulative_usd, 'max_dd': max_dd,
        'trades_2m': total_2m, 'wins_2m': wins_2m, 'pnl_2m': pnl_2m,
        'long_trades': long_trades, 'long_wins': long_wins, 'long_pnl': long_pnl,
        'short_trades': short_trades, 'short_wins': short_wins, 'short_pnl': short_pnl
    }


results_all = []

for symbol, filepath in [
    ('BTC', r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx'),
    ('XRP', r'G:\BinanceFriend\XRPUSDT_volatility_2y_with_LS.xlsx')
]:
    for threshold in [0, 3, 5]:
        r = run_momentum_backtest(symbol, filepath, threshold)
        r['symbol'] = symbol
        r['threshold'] = threshold
        results_all.append(r)

# Combined results
print()
print("=" * 90)
print("MOMENTUM: 0% vs 3% vs 5% THRESHOLD (BTC + XRP Combined)")
print("=" * 90)
print()
print(f"{'Threshold':<12} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'MaxDD':>10} | {'LONG':>5} {'L_PnL':>10} | {'SHORT':>5} {'S_PnL':>10} | {'2M':>4} {'PnL_2M':>10}")
print("-" * 100)

for threshold in [0, 3, 5]:
    btc = [r for r in results_all if r['symbol'] == 'BTC' and r['threshold'] == threshold][0]
    xrp = [r for r in results_all if r['symbol'] == 'XRP' and r['threshold'] == threshold][0]

    c = {
        'trades': btc['trades'] + xrp['trades'],
        'wins': btc['wins'] + xrp['wins'],
        'pnl': btc['pnl'] + xrp['pnl'],
        'max_dd': max(btc['max_dd'], xrp['max_dd']),
        'long_trades': btc['long_trades'] + xrp['long_trades'],
        'long_pnl': btc['long_pnl'] + xrp['long_pnl'],
        'short_trades': btc['short_trades'] + xrp['short_trades'],
        'short_pnl': btc['short_pnl'] + xrp['short_pnl'],
        'trades_2m': btc['trades_2m'] + xrp['trades_2m'],
        'pnl_2m': btc['pnl_2m'] + xrp['pnl_2m'],
    }

    win_pct = c['wins']/c['trades']*100 if c['trades'] > 0 else 0
    label = f"{threshold}%"
    print(f"{label:<12} {c['trades']:>7} {win_pct:>6.1f}% ${c['pnl']:>+10,.0f} ${c['max_dd']:>9,.0f} | {c['long_trades']:>5} ${c['long_pnl']:>+9,.0f} | {c['short_trades']:>5} ${c['short_pnl']:>+9,.0f} | {c['trades_2m']:>4} ${c['pnl_2m']:>+9,.0f}")

print()
print()
print("=" * 90)
print("BY SYMBOL")
print("=" * 90)
print()
print(f"{'Config':<15} {'Trades':>7} {'Win%':>7} {'PnL':>12} {'MaxDD':>10} | {'2M Tr':>5} {'2M Win%':>7} {'2M PnL':>10}")
print("-" * 85)

for symbol in ['BTC', 'XRP']:
    for threshold in [0, 3, 5]:
        r = [x for x in results_all if x['symbol'] == symbol and x['threshold'] == threshold][0]
        win_pct = r['wins']/r['trades']*100 if r['trades'] > 0 else 0
        win_2m = r['wins_2m']/r['trades_2m']*100 if r['trades_2m'] > 0 else 0
        config = f"{symbol} {threshold}%"
        print(f"{config:<15} {r['trades']:>7} {win_pct:>6.1f}% ${r['pnl']:>+10,.0f} ${r['max_dd']:>9,.0f} | {r['trades_2m']:>5} {win_2m:>6.1f}% ${r['pnl_2m']:>+9,.0f}")
    print()
