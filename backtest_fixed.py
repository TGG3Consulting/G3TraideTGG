# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

# Параметры
momentum_threshold = 5.0
sl = 1.5
callback = 0.3
activation = 1.5
lookback = 7

# ФИКСИРОВАННЫЙ размер
fixed_size = 1500
commission_pct = 0.1

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

signals = []
for i in range(lookback, n_days - 1):
    price_change = df['Price_Change_7d'].iloc[i]
    if pd.isna(price_change):
        continue
    if price_change >= momentum_threshold:
        signals.append({'signal_day': i, 'entry_day': i + 1, 'direction': 'LONG'})
    elif price_change <= -momentum_threshold:
        signals.append({'signal_day': i, 'entry_day': i + 1, 'direction': 'SHORT'})

cumulative_pct = 0
cumulative_usd = 0
total_commission_usd = 0
trade_num = 0
wins = 0

i = 0
while i < len(signals):
    sig = signals[i]
    entry_day_idx = sig['entry_day']
    direction = sig['direction']

    if entry_day_idx >= n_days:
        i += 1
        continue

    trade_num += 1
    entry_price = opens[entry_day_idx]
    position_size = fixed_size

    if direction == 'LONG':
        sl_price = entry_price * (1 - sl / 100)
        activation_price = entry_price * (1 + activation / 100)
    else:
        sl_price = entry_price * (1 + sl / 100)
        activation_price = entry_price * (1 - activation / 100)

    highest_high = highs[entry_day_idx]
    lowest_low = lows[entry_day_idx]
    trailing_activated = False

    exit_day = entry_day_idx
    exit_pnl_pct = 0

    while exit_day < n_days:
        low = lows[exit_day]
        high = highs[exit_day]
        close = closes[exit_day]

        if high > highest_high:
            highest_high = high
        if low < lowest_low:
            lowest_low = low

        if direction == 'LONG':
            if low <= sl_price:
                exit_pnl_pct = -sl
                break
            if high >= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = highest_high * (1 - callback / 100)
                if low <= trailing_exit_price:
                    exit_pnl_pct = (trailing_exit_price - entry_price) / entry_price * 100
                    break
        else:
            if high >= sl_price:
                exit_pnl_pct = -sl
                break
            if low <= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = lowest_low * (1 + callback / 100)
                if high >= trailing_exit_price:
                    exit_pnl_pct = (entry_price - trailing_exit_price) / entry_price * 100
                    break

        if exit_day == n_days - 1:
            if direction == 'LONG':
                exit_pnl_pct = (close - entry_price) / entry_price * 100
            else:
                exit_pnl_pct = (entry_price - close) / entry_price * 100
            break

        exit_day += 1

    commission_total_pct = commission_pct * 2
    commission_usd = position_size * commission_total_pct / 100
    pnl_pct_after_comm = exit_pnl_pct - commission_total_pct
    pnl_usd = position_size * exit_pnl_pct / 100 - commission_usd

    cumulative_pct += pnl_pct_after_comm
    cumulative_usd += pnl_usd
    total_commission_usd += commission_usd

    if pnl_usd > 0:
        wins += 1

    while i < len(signals) and signals[i]['entry_day'] <= exit_day:
        i += 1

print('=' * 60)
print('  FIXED SIZE $1500 (для сравнения)')
print('=' * 60)
print()
print(f'Всего сделок: {trade_num}')
print(f'Винрейт: {wins/trade_num*100:.1f}% ({wins} побед / {trade_num-wins} проигрышей)')
print()
print(f'PnL % (net):   {cumulative_pct:+.2f}%')
print(f'PnL USD (net): ${cumulative_usd:+,.2f}')
print(f'Комиссии:      ${total_commission_usd:,.2f}')
print(f'Gross PnL:     ${cumulative_usd + total_commission_usd:+,.2f}')

# === ПОСЛЕДНИЕ 2 МЕСЯЦА ===
# Пересчитываем для last 60 days
last_60_start = dates[-60]

cumulative_pct_2m = 0
cumulative_usd_2m = 0
total_commission_usd_2m = 0
trade_num_2m = 0
wins_2m = 0

i = 0
while i < len(signals):
    sig = signals[i]
    entry_day_idx = sig['entry_day']
    direction = sig['direction']

    if entry_day_idx >= n_days:
        i += 1
        continue

    entry_date = dates[entry_day_idx]

    # Пропускаем сделки до last_60_start
    if entry_date < last_60_start:
        # Но нужно найти exit_day чтобы правильно пропустить
        entry_price = opens[entry_day_idx]
        if direction == 'LONG':
            sl_price = entry_price * (1 - sl / 100)
            activation_price = entry_price * (1 + activation / 100)
        else:
            sl_price = entry_price * (1 + sl / 100)
            activation_price = entry_price * (1 - activation / 100)

        highest_high = highs[entry_day_idx]
        lowest_low = lows[entry_day_idx]
        trailing_activated = False
        exit_day = entry_day_idx

        while exit_day < n_days:
            low = lows[exit_day]
            high = highs[exit_day]
            close = closes[exit_day]
            if high > highest_high:
                highest_high = high
            if low < lowest_low:
                lowest_low = low

            if direction == 'LONG':
                if low <= sl_price:
                    break
                if high >= activation_price:
                    trailing_activated = True
                if trailing_activated:
                    trailing_exit_price = highest_high * (1 - callback / 100)
                    if low <= trailing_exit_price:
                        break
            else:
                if high >= sl_price:
                    break
                if low <= activation_price:
                    trailing_activated = True
                if trailing_activated:
                    trailing_exit_price = lowest_low * (1 + callback / 100)
                    if high >= trailing_exit_price:
                        break

            if exit_day == n_days - 1:
                break
            exit_day += 1

        while i < len(signals) and signals[i]['entry_day'] <= exit_day:
            i += 1
        continue

    trade_num_2m += 1
    entry_price = opens[entry_day_idx]
    position_size = fixed_size

    if direction == 'LONG':
        sl_price = entry_price * (1 - sl / 100)
        activation_price = entry_price * (1 + activation / 100)
    else:
        sl_price = entry_price * (1 + sl / 100)
        activation_price = entry_price * (1 - activation / 100)

    highest_high = highs[entry_day_idx]
    lowest_low = lows[entry_day_idx]
    trailing_activated = False

    exit_day = entry_day_idx
    exit_pnl_pct = 0

    while exit_day < n_days:
        low = lows[exit_day]
        high = highs[exit_day]
        close = closes[exit_day]

        if high > highest_high:
            highest_high = high
        if low < lowest_low:
            lowest_low = low

        if direction == 'LONG':
            if low <= sl_price:
                exit_pnl_pct = -sl
                break
            if high >= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = highest_high * (1 - callback / 100)
                if low <= trailing_exit_price:
                    exit_pnl_pct = (trailing_exit_price - entry_price) / entry_price * 100
                    break
        else:
            if high >= sl_price:
                exit_pnl_pct = -sl
                break
            if low <= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = lowest_low * (1 + callback / 100)
                if high >= trailing_exit_price:
                    exit_pnl_pct = (entry_price - trailing_exit_price) / entry_price * 100
                    break

        if exit_day == n_days - 1:
            if direction == 'LONG':
                exit_pnl_pct = (close - entry_price) / entry_price * 100
            else:
                exit_pnl_pct = (entry_price - close) / entry_price * 100
            break

        exit_day += 1

    commission_total_pct = commission_pct * 2
    commission_usd = position_size * commission_total_pct / 100
    pnl_pct_after_comm = exit_pnl_pct - commission_total_pct
    pnl_usd = position_size * exit_pnl_pct / 100 - commission_usd

    cumulative_pct_2m += pnl_pct_after_comm
    cumulative_usd_2m += pnl_usd
    total_commission_usd_2m += commission_usd

    if pnl_usd > 0:
        wins_2m += 1

    while i < len(signals) and signals[i]['entry_day'] <= exit_day:
        i += 1

print()
print('=' * 60)
print('  FIXED SIZE $1500 - ПОСЛЕДНИЕ 2 МЕСЯЦА')
print('=' * 60)
print()
print(f'Период: {last_60_start} to {dates[-1]}')
print(f'Всего сделок: {trade_num_2m}')
if trade_num_2m > 0:
    print(f'Винрейт: {wins_2m/trade_num_2m*100:.1f}% ({wins_2m} побед / {trade_num_2m-wins_2m} проигрышей)')
    print()
    print(f'PnL % (net):   {cumulative_pct_2m:+.2f}%')
    print(f'PnL USD (net): ${cumulative_usd_2m:+,.2f}')
    print(f'Комиссии:      ${total_commission_usd_2m:,.2f}')
    print(f'Gross PnL:     ${cumulative_usd_2m + total_commission_usd_2m:+,.2f}')
