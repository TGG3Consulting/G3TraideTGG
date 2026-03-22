# -*- coding: utf-8 -*-
"""
Backtest each strategy SEPARATELY
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx')

sl = 1.5
activation = 1.5
callback = 0.3
position_size = 1500
commission_pct = 0.1
momentum_threshold = 5.0
ls_confirm = 0.60
ls_extreme = 0.65
lookback = 7

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
long_pct = df['longAccount'].values
short_pct = df['shortAccount'].values
n_days = len(df)

df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100
price_change_7d = df['Price_Change_7d'].values
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


def run_backtest(strategy_name, get_signal_func):
    results = []
    cumulative_usd = 0

    signal_day = lookback
    while signal_day < n_days - 1:
        direction = get_signal_func(signal_day)
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

        results.append({
            'Entry_Date': dates[entry_day],
            'Direction': direction,
            'PnL_USD': pnl_usd
        })

        signal_day = exit_day + 1

    return pd.DataFrame(results), cumulative_usd


# Signal functions
def momentum_signal(day):
    pc = price_change_7d[day]
    if pd.isna(pc): return None
    if pc >= momentum_threshold: return 'LONG'
    if pc <= -momentum_threshold: return 'SHORT'
    return None

def momentum_ls_signal(day):
    pc = price_change_7d[day]
    lp = long_pct[day]
    sp = short_pct[day]
    if pd.isna(pc) or pd.isna(lp) or pd.isna(sp): return None
    if pc >= momentum_threshold and lp < ls_confirm: return 'LONG'
    if pc <= -momentum_threshold and sp < ls_confirm: return 'SHORT'
    return None

def ls_fade_signal(day):
    lp = long_pct[day]
    sp = short_pct[day]
    if pd.isna(lp) or pd.isna(sp): return None
    if sp >= ls_extreme: return 'LONG'
    if lp >= ls_extreme: return 'SHORT'
    return None


# Run all
strategies = [
    ('MOMENTUM', momentum_signal),
    ('MOMENTUM_LS', momentum_ls_signal),
    ('LS_FADE', ls_fade_signal),
]

results_summary = []

for name, func in strategies:
    rdf, total_pnl = run_backtest(name, func)
    total = len(rdf)
    wins = len(rdf[rdf['PnL_USD'] > 0])

    # Last 2 months
    rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
    total_2m = len(rdf_2m)
    wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
    pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0

    results_summary.append({
        'Strategy': name,
        'Trades_Full': total,
        'Win%_Full': wins/total*100 if total > 0 else 0,
        'PnL_Full': total_pnl,
        'Trades_2M': total_2m,
        'Win%_2M': wins_2m/total_2m*100 if total_2m > 0 else 0,
        'PnL_2M': pnl_2m,
    })

# Print table
print('=' * 90)
print('  COMPARISON: Each Strategy Separately')
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 90)
print()
print(f'{"Strategy":<15} {"Trades":<10} {"Win%":<10} {"PnL USD":<15} {"Trades 2M":<12} {"Win% 2M":<10} {"PnL 2M":<12}')
print('-' * 90)

for r in results_summary:
    print(f'{r["Strategy"]:<15} {r["Trades_Full"]:<10} {r["Win%_Full"]:<9.1f}% ${r["PnL_Full"]:>+12,.2f} {r["Trades_2M"]:<12} {r["Win%_2M"]:<9.1f}% ${r["PnL_2M"]:>+10,.2f}')

print('-' * 90)
