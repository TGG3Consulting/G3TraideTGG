# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

def run_momentum_backtest(filepath, threshold):
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
    long_t = 0
    short_t = 0
    long_w = 0
    short_w = 0
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
            long_t += 1
            long_pnl += pnl_usd
            if pnl_usd > 0: long_w += 1
        else:
            short_t += 1
            short_pnl += pnl_usd
            if pnl_usd > 0: short_w += 1
        results.append({'Entry_Date': dates[entry_day], 'PnL_USD': pnl_usd})
        signal_day = exit_day + 1

    if len(results) == 0:
        return None
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
        'long_t': long_t, 'long_w': long_w, 'long_pnl': long_pnl,
        'short_t': short_t, 'short_w': short_w, 'short_pnl': short_pnl
    }

print()
print("SOL ONLY - MOMENTUM THRESHOLDS")
print("=" * 95)
print()
print("Thresh  Trades  Win%       PnL     MaxDD | LONG  L_Win%    L_PnL | SHORT S_Win%    S_PnL")
print("-" * 95)

for t in [0, 3, 4, 5]:
    r = run_momentum_backtest(r'G:\BinanceFriend\SOLUSDT_volatility_2y_with_LS.xlsx', t)
    win = r['wins']/r['trades']*100
    lw = r['long_w']/r['long_t']*100 if r['long_t'] > 0 else 0
    sw = r['short_w']/r['short_t']*100 if r['short_t'] > 0 else 0
    print(f"{t}%      {r['trades']:>5}  {win:>5.1f}%  ${r['pnl']:>+7,.0f}  ${r['max_dd']:>6,.0f} | {r['long_t']:>4}  {lw:>5.1f}%  ${r['long_pnl']:>+6,.0f} | {r['short_t']:>4}  {sw:>5.1f}%  ${r['short_pnl']:>+6,.0f}")

print()
print()
print("LAST 2 MONTHS")
print("-" * 50)
for t in [0, 3, 4, 5]:
    r = run_momentum_backtest(r'G:\BinanceFriend\SOLUSDT_volatility_2y_with_LS.xlsx', t)
    w2m = r['wins_2m']/r['trades_2m']*100 if r['trades_2m'] > 0 else 0
    print(f"{t}%:  {r['trades_2m']:>3} trades, {w2m:>5.1f}% win, PnL ${r['pnl_2m']:>+7,.0f}")
