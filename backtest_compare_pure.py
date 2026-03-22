# -*- coding: utf-8 -*-
"""
Сравнение:
1. Только MOMENTUM (7d >= +5% = LONG, 7d <= -5% = SHORT)
2. Только тупой LONG каждый день

Параметры: SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

sl = 1.5
activation = 1.5
callback = 0.3
position_size = 1500
commission_pct = 0.1
momentum_threshold = 5.0
lookback = 7

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100
price_change_7d = df['Price_Change_7d'].values

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


# ==========================================
# ТЕСТ 1: ТОЛЬКО MOMENTUM
# ==========================================
print('=' * 70)
print('  TEST 1: MOMENTUM ONLY')
print('  (LONG при 7d >= +5%, SHORT при 7d <= -5%)')
print('=' * 70)

mom_results = []
mom_cumul = 0
mom_comm = 0

i = lookback
while i < n_days - 1:
    if pd.isna(price_change_7d[i]):
        i += 1
        continue

    direction = None
    if price_change_7d[i] >= momentum_threshold:
        direction = 'LONG'
    elif price_change_7d[i] <= -momentum_threshold:
        direction = 'SHORT'
    else:
        i += 1
        continue

    entry_day = i + 1
    if entry_day >= n_days:
        break

    exit_day, pnl_pct, result = simulate_trade(entry_day, direction)
    if result == 'SKIP':
        i += 1
        continue

    comm = position_size * commission_pct * 2 / 100
    pnl_usd = position_size * pnl_pct / 100 - comm
    mom_cumul += pnl_usd
    mom_comm += comm

    mom_results.append({
        'Entry_Date': dates[entry_day],
        'Direction': direction,
        'Result': result,
        'PnL_USD': round(pnl_usd, 2)
    })

    i = exit_day + 1

mom_df = pd.DataFrame(mom_results)
mom_total = len(mom_df)
mom_wins = len(mom_df[mom_df['PnL_USD'] > 0])

print()
print('=== FULL PERIOD ===')
print(f'Trades: {mom_total}')
print(f'Win rate: {mom_wins/mom_total*100:.1f}% ({mom_wins}/{mom_total})')
print(f'PnL USD: ${mom_cumul:+,.2f}')
print(f'Commission: ${mom_comm:,.2f}')

# Last 2 months
last_60 = dates[-60]
mom_2m = mom_df[mom_df['Entry_Date'] >= last_60]
if len(mom_2m) > 0:
    mom_2m_wins = len(mom_2m[mom_2m['PnL_USD'] > 0])
    print()
    print('=== LAST 2 MONTHS ===')
    print(f'Trades: {len(mom_2m)}')
    print(f'Win rate: {mom_2m_wins/len(mom_2m)*100:.1f}%')
    print(f'PnL USD: ${mom_2m["PnL_USD"].sum():+,.2f}')


# ==========================================
# ТЕСТ 2: ТОЛЬКО ТУПОЙ LONG
# ==========================================
print()
print()
print('=' * 70)
print('  TEST 2: STUPID LONG ONLY')
print('  (LONG каждый день)')
print('=' * 70)

sl_results = []
sl_cumul = 0
sl_comm = 0

i = 0
while i < n_days - 1:
    entry_day = i + 1
    if entry_day >= n_days:
        break

    exit_day, pnl_pct, result = simulate_trade(entry_day, 'LONG')
    if result == 'SKIP':
        i += 1
        continue

    comm = position_size * commission_pct * 2 / 100
    pnl_usd = position_size * pnl_pct / 100 - comm
    sl_cumul += pnl_usd
    sl_comm += comm

    sl_results.append({
        'Entry_Date': dates[entry_day],
        'Direction': 'LONG',
        'Result': result,
        'PnL_USD': round(pnl_usd, 2)
    })

    i = exit_day + 1

sl_df = pd.DataFrame(sl_results)
sl_total = len(sl_df)
sl_wins = len(sl_df[sl_df['PnL_USD'] > 0])

print()
print('=== FULL PERIOD ===')
print(f'Trades: {sl_total}')
print(f'Win rate: {sl_wins/sl_total*100:.1f}% ({sl_wins}/{sl_total})')
print(f'PnL USD: ${sl_cumul:+,.2f}')
print(f'Commission: ${sl_comm:,.2f}')

# Last 2 months
sl_2m = sl_df[sl_df['Entry_Date'] >= last_60]
if len(sl_2m) > 0:
    sl_2m_wins = len(sl_2m[sl_2m['PnL_USD'] > 0])
    print()
    print('=== LAST 2 MONTHS ===')
    print(f'Trades: {len(sl_2m)}')
    print(f'Win rate: {sl_2m_wins/len(sl_2m)*100:.1f}%')
    print(f'PnL USD: ${sl_2m["PnL_USD"].sum():+,.2f}')


# ==========================================
# СРАВНЕНИЕ
# ==========================================
print()
print()
print('=' * 70)
print('  COMPARISON')
print('=' * 70)
print()
print(f'{"":20} {"MOMENTUM":>15} {"STUPID LONG":>15}')
print('-' * 52)
print(f'{"Trades (full)":20} {mom_total:>15} {sl_total:>15}')
print(f'{"Win % (full)":20} {mom_wins/mom_total*100:>14.1f}% {sl_wins/sl_total*100:>14.1f}%')
print(f'{"PnL USD (full)":20} ${mom_cumul:>+13,.2f} ${sl_cumul:>+13,.2f}')
print('-' * 52)
print(f'{"Trades (2m)":20} {len(mom_2m):>15} {len(sl_2m):>15}')
print(f'{"Win % (2m)":20} {mom_2m_wins/len(mom_2m)*100:>14.1f}% {sl_2m_wins/len(sl_2m)*100:>14.1f}%')
print(f'{"PnL USD (2m)":20} ${mom_2m["PnL_USD"].sum():>+13,.2f} ${sl_2m["PnL_USD"].sum():>+13,.2f}')
