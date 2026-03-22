# -*- coding: utf-8 -*-
"""
Сравнение стратегий по режимам рынка:
- БЫК (Close > SMA200): тупой LONG vs MOMENTUM LONG
- МЕДВЕДЬ (Close < SMA200): тупой SHORT vs MOMENTUM SHORT

Параметры: SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Commission=0.1%
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

print(f"Загружено {len(df)} дней")
print(f"Колонки: {list(df.columns)}")
print(f"Период: {df['Date'].iloc[0]} - {df['Date'].iloc[-1]}")
print()

# Параметры
sl = 1.5
activation = 1.5
callback = 0.3
position_size = 1500
commission_pct = 0.1  # 0.1% на сторону
momentum_threshold = 5.0
lookback = 7
sma_period = 200

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

# Расчёт SMA(200)
df['SMA_200'] = df['Close'].rolling(window=sma_period).mean()
sma_200 = df['SMA_200'].values

# Расчёт 7d change для MOMENTUM
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

print(f"SMA(200) доступен с дня {sma_period} (первые {sma_period-1} дней = NaN)")
print()

# === ФУНКЦИЯ СИМУЛЯЦИИ ОДНОЙ СДЕЛКИ ===
def simulate_trade(entry_day, direction, opens, highs, lows, closes, n_days, sl, activation, callback):
    """Возвращает (exit_day, pnl_pct)"""
    if entry_day >= n_days:
        return entry_day, 0

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
        else:  # SHORT
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

    return exit_day, exit_pnl_pct


# === ТЕСТ 1: ТУПОЙ LONG в БЫК периоды ===
print("=" * 80)
print("ТЕСТ 1: ТУПОЙ LONG когда Close > SMA(200)")
print("=" * 80)

stupid_long_bull_trades = 0
stupid_long_bull_wins = 0
stupid_long_bull_pnl_pct = 0
stupid_long_bull_pnl_usd = 0
stupid_long_bull_comm = 0

i = sma_period  # начинаем когда SMA(200) доступен
while i < n_days - 1:
    # Проверяем режим на день сигнала
    if pd.isna(sma_200[i]):
        i += 1
        continue

    # БЫК = Close > SMA(200)
    if closes[i] > sma_200[i]:
        entry_day = i + 1
        if entry_day >= n_days:
            break

        exit_day, pnl_pct = simulate_trade(entry_day, 'LONG', opens, highs, lows, closes, n_days, sl, activation, callback)

        comm_total = commission_pct * 2  # 0.2%
        comm_usd = position_size * comm_total / 100
        pnl_pct_net = pnl_pct - comm_total
        pnl_usd = position_size * pnl_pct / 100 - comm_usd

        stupid_long_bull_trades += 1
        if pnl_usd > 0:
            stupid_long_bull_wins += 1
        stupid_long_bull_pnl_pct += pnl_pct_net
        stupid_long_bull_pnl_usd += pnl_usd
        stupid_long_bull_comm += comm_usd

        i = exit_day + 1
    else:
        i += 1

print(f"Сделок: {stupid_long_bull_trades}")
if stupid_long_bull_trades > 0:
    print(f"Винрейт: {stupid_long_bull_wins/stupid_long_bull_trades*100:.1f}%")
    print(f"PnL %: {stupid_long_bull_pnl_pct:+.2f}%")
    print(f"PnL USD: ${stupid_long_bull_pnl_usd:+,.2f}")
    print(f"Комиссии: ${stupid_long_bull_comm:,.2f}")
print()


# === ТЕСТ 2: MOMENTUM LONG в БЫК периоды ===
print("=" * 80)
print("ТЕСТ 2: MOMENTUM LONG (7d >= +5%) когда Close > SMA(200)")
print("=" * 80)

momentum_long_bull_trades = 0
momentum_long_bull_wins = 0
momentum_long_bull_pnl_pct = 0
momentum_long_bull_pnl_usd = 0
momentum_long_bull_comm = 0

i = max(sma_period, lookback)
while i < n_days - 1:
    if pd.isna(sma_200[i]) or pd.isna(df['Price_Change_7d'].iloc[i]):
        i += 1
        continue

    price_change = df['Price_Change_7d'].iloc[i]

    # БЫК + MOMENTUM LONG сигнал
    if closes[i] > sma_200[i] and price_change >= momentum_threshold:
        entry_day = i + 1
        if entry_day >= n_days:
            break

        exit_day, pnl_pct = simulate_trade(entry_day, 'LONG', opens, highs, lows, closes, n_days, sl, activation, callback)

        comm_total = commission_pct * 2
        comm_usd = position_size * comm_total / 100
        pnl_pct_net = pnl_pct - comm_total
        pnl_usd = position_size * pnl_pct / 100 - comm_usd

        momentum_long_bull_trades += 1
        if pnl_usd > 0:
            momentum_long_bull_wins += 1
        momentum_long_bull_pnl_pct += pnl_pct_net
        momentum_long_bull_pnl_usd += pnl_usd
        momentum_long_bull_comm += comm_usd

        i = exit_day + 1
    else:
        i += 1

print(f"Сделок: {momentum_long_bull_trades}")
if momentum_long_bull_trades > 0:
    print(f"Винрейт: {momentum_long_bull_wins/momentum_long_bull_trades*100:.1f}%")
    print(f"PnL %: {momentum_long_bull_pnl_pct:+.2f}%")
    print(f"PnL USD: ${momentum_long_bull_pnl_usd:+,.2f}")
    print(f"Комиссии: ${momentum_long_bull_comm:,.2f}")
print()


# === ТЕСТ 3: ТУПОЙ SHORT в МЕДВЕДЬ периоды ===
print("=" * 80)
print("ТЕСТ 3: ТУПОЙ SHORT когда Close < SMA(200)")
print("=" * 80)

stupid_short_bear_trades = 0
stupid_short_bear_wins = 0
stupid_short_bear_pnl_pct = 0
stupid_short_bear_pnl_usd = 0
stupid_short_bear_comm = 0

i = sma_period
while i < n_days - 1:
    if pd.isna(sma_200[i]):
        i += 1
        continue

    # МЕДВЕДЬ = Close < SMA(200)
    if closes[i] < sma_200[i]:
        entry_day = i + 1
        if entry_day >= n_days:
            break

        exit_day, pnl_pct = simulate_trade(entry_day, 'SHORT', opens, highs, lows, closes, n_days, sl, activation, callback)

        comm_total = commission_pct * 2
        comm_usd = position_size * comm_total / 100
        pnl_pct_net = pnl_pct - comm_total
        pnl_usd = position_size * pnl_pct / 100 - comm_usd

        stupid_short_bear_trades += 1
        if pnl_usd > 0:
            stupid_short_bear_wins += 1
        stupid_short_bear_pnl_pct += pnl_pct_net
        stupid_short_bear_pnl_usd += pnl_usd
        stupid_short_bear_comm += comm_usd

        i = exit_day + 1
    else:
        i += 1

print(f"Сделок: {stupid_short_bear_trades}")
if stupid_short_bear_trades > 0:
    print(f"Винрейт: {stupid_short_bear_wins/stupid_short_bear_trades*100:.1f}%")
    print(f"PnL %: {stupid_short_bear_pnl_pct:+.2f}%")
    print(f"PnL USD: ${stupid_short_bear_pnl_usd:+,.2f}")
    print(f"Комиссии: ${stupid_short_bear_comm:,.2f}")
print()


# === ТЕСТ 4: MOMENTUM SHORT в МЕДВЕДЬ периоды ===
print("=" * 80)
print("ТЕСТ 4: MOMENTUM SHORT (7d <= -5%) когда Close < SMA(200)")
print("=" * 80)

momentum_short_bear_trades = 0
momentum_short_bear_wins = 0
momentum_short_bear_pnl_pct = 0
momentum_short_bear_pnl_usd = 0
momentum_short_bear_comm = 0

i = max(sma_period, lookback)
while i < n_days - 1:
    if pd.isna(sma_200[i]) or pd.isna(df['Price_Change_7d'].iloc[i]):
        i += 1
        continue

    price_change = df['Price_Change_7d'].iloc[i]

    # МЕДВЕДЬ + MOMENTUM SHORT сигнал
    if closes[i] < sma_200[i] and price_change <= -momentum_threshold:
        entry_day = i + 1
        if entry_day >= n_days:
            break

        exit_day, pnl_pct = simulate_trade(entry_day, 'SHORT', opens, highs, lows, closes, n_days, sl, activation, callback)

        comm_total = commission_pct * 2
        comm_usd = position_size * comm_total / 100
        pnl_pct_net = pnl_pct - comm_total
        pnl_usd = position_size * pnl_pct / 100 - comm_usd

        momentum_short_bear_trades += 1
        if pnl_usd > 0:
            momentum_short_bear_wins += 1
        momentum_short_bear_pnl_pct += pnl_pct_net
        momentum_short_bear_pnl_usd += pnl_usd
        momentum_short_bear_comm += comm_usd

        i = exit_day + 1
    else:
        i += 1

print(f"Сделок: {momentum_short_bear_trades}")
if momentum_short_bear_trades > 0:
    print(f"Винрейт: {momentum_short_bear_wins/momentum_short_bear_trades*100:.1f}%")
    print(f"PnL %: {momentum_short_bear_pnl_pct:+.2f}%")
    print(f"PnL USD: ${momentum_short_bear_pnl_usd:+,.2f}")
    print(f"Комиссии: ${momentum_short_bear_comm:,.2f}")
print()


# === СТАТИСТИКА РЕЖИМОВ ===
print("=" * 80)
print("СТАТИСТИКА РЕЖИМОВ РЫНКА")
print("=" * 80)

bull_days = 0
bear_days = 0
for i in range(sma_period, n_days):
    if not pd.isna(sma_200[i]):
        if closes[i] > sma_200[i]:
            bull_days += 1
        else:
            bear_days += 1

print(f"Дней БЫК (Close > SMA200): {bull_days} ({bull_days/(bull_days+bear_days)*100:.1f}%)")
print(f"Дней МЕДВЕДЬ (Close < SMA200): {bear_days} ({bear_days/(bull_days+bear_days)*100:.1f}%)")
print()


# === ИТОГОВАЯ ТАБЛИЦА ===
print("=" * 80)
print("ИТОГОВОЕ СРАВНЕНИЕ")
print("=" * 80)
print()
print(f"{'':40} {'Сделок':>8} {'Win%':>8} {'PnL USD':>12}")
print("-" * 70)
print(f"{'БЫК: Тупой LONG':40} {stupid_long_bull_trades:>8} {stupid_long_bull_wins/max(stupid_long_bull_trades,1)*100:>7.1f}% ${stupid_long_bull_pnl_usd:>+10,.2f}")
print(f"{'БЫК: MOMENTUM LONG':40} {momentum_long_bull_trades:>8} {momentum_long_bull_wins/max(momentum_long_bull_trades,1)*100:>7.1f}% ${momentum_long_bull_pnl_usd:>+10,.2f}")
print("-" * 70)
print(f"{'МЕДВЕДЬ: Тупой SHORT':40} {stupid_short_bear_trades:>8} {stupid_short_bear_wins/max(stupid_short_bear_trades,1)*100:>7.1f}% ${stupid_short_bear_pnl_usd:>+10,.2f}")
print(f"{'МЕДВЕДЬ: MOMENTUM SHORT':40} {momentum_short_bear_trades:>8} {momentum_short_bear_wins/max(momentum_short_bear_trades,1)*100:>7.1f}% ${momentum_short_bear_pnl_usd:>+10,.2f}")
print("-" * 70)
