# -*- coding: utf-8 -*-
"""Проверка что данные реальные"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

print("=" * 80)
print("СЫРЫЕ ДАННЫЕ ИЗ ФАЙЛА (первые 5 и последние 5 строк)")
print("=" * 80)
print(df[['Date', 'Open', 'High', 'Low', 'Close']].head(5).to_string(index=False))
print("...")
print(df[['Date', 'Open', 'High', 'Low', 'Close']].tail(5).to_string(index=False))
print()

# Расчёт SMA(200)
df['SMA_200'] = df['Close'].rolling(window=200).mean()

print("=" * 80)
print("SMA(200) - последние 10 дней")
print("=" * 80)
print(df[['Date', 'Close', 'SMA_200']].tail(10).to_string(index=False))
print()

# Проверка: Close > SMA(200)?
last_close = df['Close'].iloc[-1]
last_sma = df['SMA_200'].iloc[-1]
print(f"Последний день: Close={last_close:.2f}, SMA(200)={last_sma:.2f}")
print(f"Close > SMA(200)? {last_close > last_sma} → {'БЫК' if last_close > last_sma else 'МЕДВЕДЬ'}")
print()

# 7d change
df['Price_Change_7d'] = (df['Close'] - df['Close'].shift(7)) / df['Close'].shift(7) * 100

print("=" * 80)
print("7-дневное изменение цены - последние 10 дней")
print("=" * 80)
print(df[['Date', 'Close', 'Price_Change_7d']].tail(10).to_string(index=False))
print()

# Пример расчёта одной сделки
print("=" * 80)
print("ПРИМЕР: Сделка из бэктеста (2026-02-05 MOMENTUM SHORT)")
print("=" * 80)

# Найдём день 2026-02-05
idx = df[df['Date'] == '2026-02-05'].index
if len(idx) > 0:
    i = idx[0]
    print(f"Дата входа: {df['Date'].iloc[i]}")
    print(f"Open (цена входа): {df['Open'].iloc[i]:.2f}")
    print(f"High: {df['High'].iloc[i]:.2f}")
    print(f"Low: {df['Low'].iloc[i]:.2f}")
    print(f"Close: {df['Close'].iloc[i]:.2f}")
    print()

    entry_price = df['Open'].iloc[i]
    sl_price = entry_price * 1.015  # SHORT SL = +1.5%
    activation_price = entry_price * 0.985  # SHORT activation = -1.5%

    print(f"Для SHORT:")
    print(f"  Entry: {entry_price:.2f}")
    print(f"  SL (entry + 1.5%): {sl_price:.2f}")
    print(f"  Activation (entry - 1.5%): {activation_price:.2f}")
    print()

    # Смотрим следующие дни
    print("Следующие дни после входа:")
    for j in range(i, min(i+5, len(df))):
        print(f"  {df['Date'].iloc[j]}: O={df['Open'].iloc[j]:.0f} H={df['High'].iloc[j]:.0f} L={df['Low'].iloc[j]:.0f} C={df['Close'].iloc[j]:.0f}")
