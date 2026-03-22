# -*- coding: utf-8 -*-
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
            if pc[d] > 0: return 'LONG'
            elif pc[d] < 0: return 'SHORT'
            return None
        else:
            if pc[d] >= threshold: return 'LONG'
            elif pc[d] <= -threshold: return 'SHORT'
            return None

    results = []
    cumulative_usd = 0
    peak = 0
    max_dd = 0
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
        results.append({'Entry_Date': dates[entry_day], 'PnL_USD': pnl_usd})
        signal_day = exit_day + 1

    if len(results) == 0:
        return {'trades': 0, 'wins': 0, 'pnl': 0, 'max_dd': 0, 'trades_2m': 0, 'wins_2m': 0, 'pnl_2m': 0}
    rdf = pd.DataFrame(results)
    total = len(rdf)
    wins = len(rdf[rdf['PnL_USD'] > 0])
    rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
    total_2m = len(rdf_2m)
    wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
    pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0
    return {'trades': total, 'wins': wins, 'pnl': cumulative_usd, 'max_dd': max_dd,
            'trades_2m': total_2m, 'wins_2m': wins_2m, 'pnl_2m': pnl_2m}

results_all = []
for symbol, filepath in [('BTC', r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx'),
                          ('XRP', r'G:\BinanceFriend\XRPUSDT_volatility_2y_with_LS.xlsx')]:
    for threshold in [0, 3, 4, 5, 10]:
        r = run_momentum_backtest(symbol, filepath, threshold)
        r['symbol'] = symbol
        r['threshold'] = threshold
        results_all.append(r)

print()
print("Threshold   Trades   Win%        PnL      MaxDD | 2M Tr 2M Win%    2M PnL")
print("-" * 75)

for threshold in [0, 3, 4, 5, 10]:
    btc = [r for r in results_all if r['symbol'] == 'BTC' and r['threshold'] == threshold][0]
    xrp = [r for r in results_all if r['symbol'] == 'XRP' and r['threshold'] == threshold][0]
    c = {
        'trades': btc['trades'] + xrp['trades'],
        'wins': btc['wins'] + xrp['wins'],
        'pnl': btc['pnl'] + xrp['pnl'],
        'max_dd': max(btc['max_dd'], xrp['max_dd']),
        'trades_2m': btc['trades_2m'] + xrp['trades_2m'],
        'wins_2m': btc['wins_2m'] + xrp['wins_2m'],
        'pnl_2m': btc['pnl_2m'] + xrp['pnl_2m'],
    }
    win_pct = c['wins']/c['trades']*100 if c['trades'] > 0 else 0
    win_2m = c['wins_2m']/c['trades_2m']*100 if c['trades_2m'] > 0 else 0
    label = str(threshold) + "%"
    print(f"{label:<10} {c['trades']:>6}  {win_pct:>5.1f}%  ${c['pnl']:>+9,.0f}  ${c['max_dd']:>7,.0f} | {c['trades_2m']:>5} {win_2m:>5.1f}%  ${c['pnl_2m']:>+7,.0f}")
