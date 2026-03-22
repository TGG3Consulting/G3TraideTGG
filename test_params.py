# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

# Last 60 days (2 months)
df_last2m = df.tail(60).copy()

print('=== TEST LAST 2 MONTHS ===')
print(f'Period: {df_last2m.iloc[0]["Date"]} to {df_last2m.iloc[-1]["Date"]}')
print(f'Days: {len(df_last2m)}')
print()

opens = df_last2m['Open'].values
highs = df_last2m['High'].values
lows = df_last2m['Low'].values
closes = df_last2m['Close'].values
n_days = len(df_last2m)

configs = [
    {'name': 'RECOMMENDED (SL=1.0 ACT=1.5 CB=0.5)', 'sl': 1.0, 'callback': 0.5, 'activation': 1.5},
    {'name': 'MOMENTUM (SL=1.5 ACT=1.5 CB=0.5)', 'sl': 1.5, 'callback': 0.5, 'activation': 1.5},
    {'name': 'LS_FADE (SL=2.0 ACT=2.0 CB=0.5)', 'sl': 2.0, 'callback': 0.5, 'activation': 2.0},
    {'name': 'YOUR_CMD (SL=3.0 ACT=2.0 CB=1.0)', 'sl': 3.0, 'callback': 1.0, 'activation': 2.0},
]

print('=' * 85)
print(f'{"CONFIG":35} {"SL%":>5} {"CB%":>4} {"ACT%":>5} {"Win%":>6} {"PnL%":>8} {"PF":>5}')
print('=' * 85)

for cfg in configs:
    sl = cfg['sl']
    callback = cfg['callback']
    activation = cfg['activation']

    wins = 0
    total_pnl = 0
    gross_wins = 0
    gross_losses = 0

    for i in range(n_days):
        entry = opens[i]
        sl_price = entry * (1 - sl / 100)
        activation_price = entry * (1 + activation / 100)

        low = lows[i]
        high = highs[i]
        close = closes[i]

        if low <= sl_price:
            pnl = -sl
            gross_losses += sl
        elif high >= activation_price:
            trailing_exit = high * (1 - callback / 100)
            exit_price = trailing_exit if close < trailing_exit else close
            pnl = (exit_price - entry) / entry * 100
            if pnl > 0:
                wins += 1
                gross_wins += pnl
            else:
                gross_losses += abs(pnl)
        else:
            pnl = (close - entry) / entry * 100
            if pnl > 0:
                wins += 1
                gross_wins += pnl
            else:
                gross_losses += abs(pnl)

        total_pnl += pnl

    win_rate = wins / n_days * 100
    pf = gross_wins / gross_losses if gross_losses > 0 else 999

    print(f'{cfg["name"]:35} {sl:>5.1f} {callback:>4.1f} {activation:>5.1f} {win_rate:>5.1f}% {total_pnl:>7.2f}% {pf:>5.2f}')

print('=' * 85)
print()

# Detailed for recommended
print('=== DETAILS: RECOMMENDED (SL=1.0%, ACT=1.5%, CB=0.5%) ===')
print()

sl, callback, activation = 1.0, 0.5, 1.5

print(f'{"Date":^12} {"Open":>8} {"High":>8} {"Low":>8} {"Close":>8} {"Res":>5} {"PnL%":>7} {"Cum%":>8}')
print('-' * 75)

cumulative = 0
sl_cnt = trail_cnt = close_cnt = 0

for i in range(n_days):
    entry = opens[i]
    sl_price = entry * (1 - sl / 100)
    activation_price = entry * (1 + activation / 100)
    low, high, close = lows[i], highs[i], closes[i]
    date = df_last2m.iloc[i]['Date']

    if low <= sl_price:
        pnl, result = -sl, 'SL'
        sl_cnt += 1
    elif high >= activation_price:
        trailing_exit = high * (1 - callback / 100)
        exit_price = trailing_exit if close < trailing_exit else close
        pnl = (exit_price - entry) / entry * 100
        result = 'TRAIL'
        trail_cnt += 1
    else:
        pnl = (close - entry) / entry * 100
        result = 'CLOSE'
        close_cnt += 1

    cumulative += pnl
    pnl_s = f'+{pnl:.2f}' if pnl > 0 else f'{pnl:.2f}'
    cum_s = f'+{cumulative:.2f}' if cumulative > 0 else f'{cumulative:.2f}'

    print(f'{date:^12} {entry:>8.0f} {high:>8.0f} {low:>8.0f} {close:>8.0f} {result:>5} {pnl_s:>7} {cum_s:>8}')

print('-' * 75)
print(f'TOTAL: {cumulative:+.2f}%  |  SL: {sl_cnt}, TRAIL: {trail_cnt}, CLOSE: {close_cnt}')
