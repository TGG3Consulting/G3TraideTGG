# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

sl = 1.5
activation = 1.5
callback = 0.3

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

def simulate_trade(entry_day):
    if entry_day >= n_days:
        return entry_day, 0
    entry_price = opens[entry_day]
    sl_price = entry_price * (1 - sl / 100)
    activation_price = entry_price * (1 + activation / 100)
    highest_high = highs[entry_day]
    trailing_activated = False
    exit_day = entry_day
    while exit_day < n_days:
        low, high, close = lows[exit_day], highs[exit_day], closes[exit_day]
        if high > highest_high: highest_high = high
        if low <= sl_price:
            return exit_day, exit_day - entry_day + 1
        if high >= activation_price:
            trailing_activated = True
        if trailing_activated:
            trail_price = highest_high * (1 - callback / 100)
            if low <= trail_price:
                return exit_day, exit_day - entry_day + 1
        if exit_day == n_days - 1:
            return exit_day, exit_day - entry_day + 1
        exit_day += 1
    return exit_day, 0

hold_days = []
trade_count = 0
i = 0
while i < n_days - 1:
    entry_day = i + 1
    if entry_day >= n_days:
        break
    exit_day, hold = simulate_trade(entry_day)
    if hold > 0:
        hold_days.append(hold)
        trade_count += 1
    i = exit_day + 1

print(f"Total days in data: {n_days}")
print(f"Total trades: {trade_count}")
print(f"Hold days - Min: {min(hold_days)}, Max: {max(hold_days)}, Avg: {np.mean(hold_days):.2f}")
print(f"Sum of all hold days: {sum(hold_days)}")
print()
print(f"Check: {n_days} days / {np.mean(hold_days):.2f} avg = {n_days / np.mean(hold_days):.0f} trades")
print()

# Show first 10 trades
print("First 10 trades:")
i = 0
cnt = 0
while i < n_days - 1 and cnt < 10:
    entry_day = i + 1
    if entry_day >= n_days:
        break
    exit_day, hold = simulate_trade(entry_day)
    if hold > 0:
        cnt += 1
        print(f"  Trade {cnt}: Entry {dates[entry_day]}, Exit {dates[exit_day]}, Hold {hold} days")
    i = exit_day + 1
