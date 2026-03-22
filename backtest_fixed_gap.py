# -*- coding: utf-8 -*-
"""
ИСПРАВЛЕННЫЙ ТЕСТ: без пропуска дней

После закрытия сделки - сразу на следующий день новая
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
print('=' * 70)

mom_cumul = 0
mom_trades = 0
mom_wins = 0
mom_comm = 0

# Для MOMENTUM нужен сигнал на предыдущий день
entry_day = lookback  # первый возможный вход
while entry_day < n_days:
    signal_day = entry_day - 1  # проверяем сигнал на день ДО входа

    if signal_day < lookback or pd.isna(price_change_7d[signal_day]):
        entry_day += 1
        continue

    direction = None
    if price_change_7d[signal_day] >= momentum_threshold:
        direction = 'LONG'
    elif price_change_7d[signal_day] <= -momentum_threshold:
        direction = 'SHORT'
    else:
        entry_day += 1
        continue

    exit_day, pnl_pct, result = simulate_trade(entry_day, direction)
    if result == 'SKIP':
        entry_day += 1
        continue

    comm = position_size * commission_pct * 2 / 100
    pnl_usd = position_size * pnl_pct / 100 - comm
    mom_cumul += pnl_usd
    mom_comm += comm
    mom_trades += 1
    if pnl_usd > 0:
        mom_wins += 1

    entry_day = exit_day + 1  # следующий вход сразу после закрытия

print(f'Trades: {mom_trades}')
print(f'Win rate: {mom_wins/mom_trades*100:.1f}%')
print(f'PnL USD: ${mom_cumul:+,.2f}')
print()


# ==========================================
# ТЕСТ 2: ТОЛЬКО ТУПОЙ LONG
# ==========================================
print('=' * 70)
print('  TEST 2: STUPID LONG ONLY')
print('=' * 70)

sl_cumul = 0
sl_trades = 0
sl_wins = 0
sl_comm = 0
sl_hold_days = []

entry_day = 0  # начинаем с первого дня
while entry_day < n_days:
    exit_day, pnl_pct, result = simulate_trade(entry_day, 'LONG')
    if result == 'SKIP':
        entry_day += 1
        continue

    comm = position_size * commission_pct * 2 / 100
    pnl_usd = position_size * pnl_pct / 100 - comm
    sl_cumul += pnl_usd
    sl_comm += comm
    sl_trades += 1
    sl_hold_days.append(exit_day - entry_day + 1)
    if pnl_usd > 0:
        sl_wins += 1

    entry_day = exit_day + 1  # следующий вход сразу после закрытия

print(f'Trades: {sl_trades}')
print(f'Win rate: {sl_wins/sl_trades*100:.1f}%')
print(f'PnL USD: ${sl_cumul:+,.2f}')
print(f'Avg hold days: {np.mean(sl_hold_days):.2f}')
print(f'Total hold days: {sum(sl_hold_days)}')
print()
print(f'Check: 730 days in data, {sum(sl_hold_days)} days in trades')
print(f'Expected trades: 730 / {np.mean(sl_hold_days):.2f} = {730/np.mean(sl_hold_days):.0f}')
print(f'Actual trades: {sl_trades}')


# ==========================================
# СРАВНЕНИЕ
# ==========================================
print()
print('=' * 70)
print('  COMPARISON')
print('=' * 70)
print()
print(f'{"":20} {"MOMENTUM":>15} {"STUPID LONG":>15}')
print('-' * 52)
print(f'{"Trades":20} {mom_trades:>15} {sl_trades:>15}')
print(f'{"Win %":20} {mom_wins/mom_trades*100:>14.1f}% {sl_wins/sl_trades*100:>14.1f}%')
print(f'{"PnL USD":20} ${mom_cumul:>+13,.2f} ${sl_cumul:>+13,.2f}')
