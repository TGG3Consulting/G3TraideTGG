# -*- coding: utf-8 -*-
"""
БЭКТЕСТ С РЕАЛЬНОЙ MOMENTUM СТРАТЕГИЕЙ

Правила:
- ADX < 20 → БОКОВИК → НЕ ТОРГУЕМ
- ADX >= 20 AND Close > SMA(200) → БЫК → тупой LONG каждый день
- ADX >= 20 AND Close < SMA(200) → МЕДВЕДЬ → MOMENTUM стратегия:
    - 7d change >= +5% → LONG
    - 7d change <= -5% → SHORT

Параметры: SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Commission=0.1%
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

print(f"Загружено {len(df)} дней")
print(f"Период: {df['Date'].iloc[0]} - {df['Date'].iloc[-1]}")
print()

# Параметры
sl = 1.5
activation = 1.5
callback = 0.3
position_size = 1500
commission_pct = 0.1
momentum_threshold = 5.0
lookback = 7
sma_period = 200
adx_period = 14

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

# === РАСЧЁТ SMA(200) ===
df['SMA_200'] = df['Close'].rolling(window=sma_period).mean()

# === РАСЧЁТ ADX ===
def calculate_adx(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        plus_dm[i] = high_diff if high_diff > low_diff and high_diff > 0 else 0
        minus_dm[i] = low_diff if low_diff > high_diff and low_diff > 0 else 0

    atr = np.zeros(n)
    adx = np.zeros(n)

    atr[period] = np.mean(tr[1:period+1])
    smoothed_plus_dm = np.mean(plus_dm[1:period+1])
    smoothed_minus_dm = np.mean(minus_dm[1:period+1])

    plus_di = 100 * smoothed_plus_dm / atr[period] if atr[period] > 0 else 0
    minus_di = 100 * smoothed_minus_dm / atr[period] if atr[period] > 0 else 0
    di_sum = plus_di + minus_di
    dx_arr = [100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0]

    for i in range(period + 1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        smoothed_plus_dm = (smoothed_plus_dm * (period - 1) + plus_dm[i]) / period
        smoothed_minus_dm = (smoothed_minus_dm * (period - 1) + minus_dm[i]) / period
        plus_di = 100 * smoothed_plus_dm / atr[i] if atr[i] > 0 else 0
        minus_di = 100 * smoothed_minus_dm / atr[i] if atr[i] > 0 else 0
        di_sum = plus_di + minus_di
        dx_arr.append(100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0)

    adx[period * 2] = np.mean(dx_arr[:period+1])
    for i in range(period * 2 + 1, n):
        adx[i] = (adx[i-1] * (period - 1) + dx_arr[i - period]) / period

    return adx

adx = calculate_adx(highs, lows, closes, adx_period)
df['ADX'] = adx

# === РАСЧЁТ 7d change для MOMENTUM ===
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

sma_200 = df['SMA_200'].values
price_change_7d = df['Price_Change_7d'].values

start_day = max(sma_period, adx_period * 2, lookback)

# === ФУНКЦИЯ СИМУЛЯЦИИ СДЕЛКИ ===
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
                    pnl = (trail_price - entry_price) / entry_price * 100
                    return exit_day, pnl, 'TRAIL'
        else:
            if high >= sl_price:
                return exit_day, -sl, 'SL'
            if low <= activation_price:
                trailing_activated = True
            if trailing_activated:
                trail_price = lowest_low * (1 + callback / 100)
                if high >= trail_price:
                    pnl = (entry_price - trail_price) / entry_price * 100
                    return exit_day, pnl, 'TRAIL'

        if exit_day == n_days - 1:
            if direction == 'LONG':
                pnl = (close - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - close) / entry_price * 100
            return exit_day, pnl, 'CLOSE'

        exit_day += 1

    return exit_day, 0, 'SKIP'


# === ОСНОВНОЙ БЭКТЕСТ ===
results = []
trade_num = 0
cumulative_usd = 0
total_commission = 0

bull_count = bear_momentum_long = bear_momentum_short = sideways_skip = 0

i = start_day
while i < n_days - 1:
    # Проверяем данные
    if pd.isna(sma_200[i]) or adx[i] == 0:
        i += 1
        continue

    regime = None
    signal_type = None
    direction = None

    # Определяем режим
    if adx[i] < 20:
        # БОКОВИК - не торгуем
        sideways_skip += 1
        i += 1
        continue
    elif closes[i] > sma_200[i]:
        # БЫК - тупой LONG
        regime = 'BULL'
        signal_type = 'STUPID_LONG'
        direction = 'LONG'
        bull_count += 1
    else:
        # МЕДВЕДЬ - MOMENTUM стратегия
        regime = 'BEAR'
        if pd.isna(price_change_7d[i]):
            i += 1
            continue

        if price_change_7d[i] >= momentum_threshold:
            # MOMENTUM LONG
            signal_type = 'MOMENTUM_LONG'
            direction = 'LONG'
            bear_momentum_long += 1
        elif price_change_7d[i] <= -momentum_threshold:
            # MOMENTUM SHORT
            signal_type = 'MOMENTUM_SHORT'
            direction = 'SHORT'
            bear_momentum_short += 1
        else:
            # Нет сигнала MOMENTUM
            i += 1
            continue

    # Вход на следующий день
    entry_day = i + 1
    if entry_day >= n_days:
        break

    exit_day, pnl_pct, exit_result = simulate_trade(entry_day, direction)

    if exit_result == 'SKIP':
        i += 1
        continue

    trade_num += 1
    comm_total = commission_pct * 2
    comm_usd = position_size * comm_total / 100
    pnl_pct_net = pnl_pct - comm_total
    pnl_usd = position_size * pnl_pct / 100 - comm_usd
    cumulative_usd += pnl_usd
    total_commission += comm_usd

    results.append({
        'Trade#': trade_num,
        'Regime': regime,
        'Signal': signal_type,
        'Direction': direction,
        'Entry_Date': dates[entry_day],
        'Exit_Date': dates[exit_day],
        'Hold_Days': exit_day - entry_day + 1,
        'Result': exit_result,
        'PnL_%_net': round(pnl_pct_net, 4),
        'PnL_USD': round(pnl_usd, 2),
        'Cumul_USD': round(cumulative_usd, 2)
    })

    # Следующая сделка после закрытия
    i = exit_day + 1

result_df = pd.DataFrame(results)

# === СТАТИСТИКА ===
print('=' * 80)
print('  БЫК = тупой LONG | МЕДВЕДЬ = MOMENTUM (LONG/SHORT) | БОКОВИК = не торгуем')
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 80)
print()

total_trades = len(result_df)
wins = len(result_df[result_df['PnL_USD'] > 0])

print('=== ВЕСЬ ПЕРИОД ===')
print(f'Период: {dates[start_day]} - {dates[-1]}')
print(f'Всего сделок: {total_trades}')
print(f'  - БЫК (тупой LONG): {bull_count}')
print(f'  - МЕДВЕДЬ MOMENTUM LONG: {bear_momentum_long}')
print(f'  - МЕДВЕДЬ MOMENTUM SHORT: {bear_momentum_short}')
print(f'  - Пропущено (БОКОВИК): {sideways_skip} дней')
print()
print(f'Винрейт: {wins/total_trades*100:.1f}% ({wins}/{total_trades})')
print(f'PnL USD (net): ${cumulative_usd:+,.2f}')
print(f'Комиссии: ${total_commission:,.2f}')
print()

# По режимам
for regime in ['BULL', 'BEAR']:
    rdf = result_df[result_df['Regime'] == regime]
    if len(rdf) > 0:
        rw = len(rdf[rdf['PnL_USD'] > 0])
        print(f'{regime}: {len(rdf)} сделок, Win {rw/len(rdf)*100:.1f}%, PnL ${rdf["PnL_USD"].sum():+,.2f}')

print()
result_df.to_excel(r'G:\BinanceFriend\backtest_real_momentum_full.xlsx', index=False)
print('Сохранено: backtest_real_momentum_full.xlsx')
print()

# === ПОСЛЕДНИЕ 2 МЕСЯЦА ===
last_60_start = dates[-60]
df_2m = result_df[result_df['Entry_Date'] >= last_60_start]

if len(df_2m) > 0:
    trades_2m = len(df_2m)
    wins_2m = len(df_2m[df_2m['PnL_USD'] > 0])
    pnl_2m = df_2m['PnL_USD'].sum()

    print('=== ПОСЛЕДНИЕ 2 МЕСЯЦА ===')
    print(f'Период: {last_60_start} - {dates[-1]}')
    print(f'Всего сделок: {trades_2m}')

    bull_2m = df_2m[df_2m['Signal'] == 'STUPID_LONG']
    mom_long_2m = df_2m[df_2m['Signal'] == 'MOMENTUM_LONG']
    mom_short_2m = df_2m[df_2m['Signal'] == 'MOMENTUM_SHORT']

    print(f'  - БЫК (тупой LONG): {len(bull_2m)}')
    print(f'  - МЕДВЕДЬ MOMENTUM LONG: {len(mom_long_2m)}')
    print(f'  - МЕДВЕДЬ MOMENTUM SHORT: {len(mom_short_2m)}')
    print()
    print(f'Винрейт: {wins_2m/trades_2m*100:.1f}% ({wins_2m}/{trades_2m})')
    print(f'PnL USD (net): ${pnl_2m:+,.2f}')
    print()

    df_2m.to_excel(r'G:\BinanceFriend\backtest_real_momentum_2m.xlsx', index=False)
    print('Сохранено: backtest_real_momentum_2m.xlsx')
    print()

    print('=== ДЕТАЛИ ПОСЛЕДНИЕ 2 МЕСЯЦА ===')
    cols = ['Trade#', 'Signal', 'Direction', 'Entry_Date', 'Result', 'PnL_%_net', 'PnL_USD']
    print(df_2m[cols].to_string(index=False))
