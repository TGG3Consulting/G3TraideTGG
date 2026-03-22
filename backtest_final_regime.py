# -*- coding: utf-8 -*-
"""
ФИНАЛЬНЫЙ БЭКТЕСТ С РЕЖИМАМИ РЫНКА

Правила:
- ADX < 20 → БОКОВИК → НЕ ТОРГУЕМ
- ADX >= 20 AND Close > SMA(200) → БЫК → тупой LONG каждый день
- ADX >= 20 AND Close < SMA(200) → МЕДВЕДЬ → MOMENTUM SHORT (7d <= -5%)

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

    # True Range
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]

        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )

        if high_diff > low_diff and high_diff > 0:
            plus_dm[i] = high_diff
        else:
            plus_dm[i] = 0

        if low_diff > high_diff and low_diff > 0:
            minus_dm[i] = low_diff
        else:
            minus_dm[i] = 0

    # Smoothed averages (Wilder's smoothing)
    atr = np.zeros(n)
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)
    adx = np.zeros(n)

    # First values
    atr[period] = np.mean(tr[1:period+1])
    smoothed_plus_dm = np.mean(plus_dm[1:period+1])
    smoothed_minus_dm = np.mean(minus_dm[1:period+1])

    plus_di[period] = 100 * smoothed_plus_dm / atr[period] if atr[period] > 0 else 0
    minus_di[period] = 100 * smoothed_minus_dm / atr[period] if atr[period] > 0 else 0

    di_sum = plus_di[period] + minus_di[period]
    dx[period] = 100 * abs(plus_di[period] - minus_di[period]) / di_sum if di_sum > 0 else 0

    # Subsequent values
    for i in range(period + 1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
        smoothed_plus_dm = (smoothed_plus_dm * (period - 1) + plus_dm[i]) / period
        smoothed_minus_dm = (smoothed_minus_dm * (period - 1) + minus_dm[i]) / period

        plus_di[i] = 100 * smoothed_plus_dm / atr[i] if atr[i] > 0 else 0
        minus_di[i] = 100 * smoothed_minus_dm / atr[i] if atr[i] > 0 else 0

        di_sum = plus_di[i] + minus_di[i]
        dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum if di_sum > 0 else 0

    # ADX = smoothed DX
    adx[period * 2] = np.mean(dx[period:period*2+1])
    for i in range(period * 2 + 1, n):
        adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

    return adx

adx = calculate_adx(highs, lows, closes, adx_period)
df['ADX'] = adx

# === РАСЧЁТ 7d change для MOMENTUM ===
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

sma_200 = df['SMA_200'].values
price_change_7d = df['Price_Change_7d'].values

# Минимальный день для начала (нужен SMA200 и ADX)
start_day = max(sma_period, adx_period * 2, lookback)
print(f"Торговля начинается с дня {start_day} (нужны SMA200, ADX, 7d change)")
print()


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
    exit_pnl_pct = 0
    exit_result = 'OPEN'

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
                exit_result = 'SL'
                break
            if high >= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = highest_high * (1 - callback / 100)
                if low <= trailing_exit_price:
                    exit_pnl_pct = (trailing_exit_price - entry_price) / entry_price * 100
                    exit_result = 'TRAIL'
                    break
        else:
            if high >= sl_price:
                exit_pnl_pct = -sl
                exit_result = 'SL'
                break
            if low <= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = lowest_low * (1 + callback / 100)
                if high >= trailing_exit_price:
                    exit_pnl_pct = (entry_price - trailing_exit_price) / entry_price * 100
                    exit_result = 'TRAIL'
                    break

        if exit_day == n_days - 1:
            if direction == 'LONG':
                exit_pnl_pct = (close - entry_price) / entry_price * 100
            else:
                exit_pnl_pct = (entry_price - close) / entry_price * 100
            exit_result = 'CLOSE'
            break

        exit_day += 1

    return exit_day, exit_pnl_pct, exit_result


# === ФУНКЦИЯ ОПРЕДЕЛЕНИЯ РЕЖИМА ===
def get_regime(day_idx):
    if pd.isna(sma_200[day_idx]) or adx[day_idx] == 0:
        return 'UNKNOWN'

    if adx[day_idx] < 20:
        return 'SIDEWAYS'
    elif closes[day_idx] > sma_200[day_idx]:
        return 'BULL'
    else:
        return 'BEAR'


# === ОСНОВНОЙ БЭКТЕСТ ===
results = []
trade_num = 0
cumulative_pct = 0
cumulative_usd = 0
total_commission = 0

# Счётчики по режимам
bull_trades = 0
bear_trades = 0
sideways_skipped = 0

i = start_day
while i < n_days - 1:
    regime = get_regime(i)

    if regime == 'UNKNOWN':
        i += 1
        continue

    if regime == 'SIDEWAYS':
        sideways_skipped += 1
        i += 1
        continue

    signal = None
    direction = None

    if regime == 'BULL':
        # Тупой LONG каждый день
        signal = 'STUPID_LONG'
        direction = 'LONG'
        bull_trades += 1

    elif regime == 'BEAR':
        # MOMENTUM SHORT только если 7d <= -5%
        if not pd.isna(price_change_7d[i]) and price_change_7d[i] <= -momentum_threshold:
            signal = 'MOMENTUM_SHORT'
            direction = 'SHORT'
            bear_trades += 1
        else:
            i += 1
            continue

    # Открываем сделку на следующий день
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

    cumulative_pct += pnl_pct_net
    cumulative_usd += pnl_usd
    total_commission += comm_usd

    results.append({
        'Trade#': trade_num,
        'Signal': signal,
        'Direction': direction,
        'Regime': regime,
        'ADX': round(adx[i], 1),
        'Entry_Date': dates[entry_day],
        'Exit_Date': dates[exit_day],
        'Hold_Days': exit_day - entry_day + 1,
        'Result': exit_result,
        'PnL_%_raw': round(pnl_pct, 4),
        'PnL_%_net': round(pnl_pct_net, 4),
        'PnL_USD': round(pnl_usd, 2),
        'Cumul_USD': round(cumulative_usd, 2)
    })

    # Следующая сделка только после закрытия текущей
    i = exit_day + 1

result_df = pd.DataFrame(results)

# === СТАТИСТИКА ЗА ВЕСЬ ПЕРИОД ===
print('=' * 80)
print('  ФИНАЛЬНЫЙ БЭКТЕСТ: БЫК=LONG, МЕДВЕДЬ=MOMENTUM SHORT, БОКОВИК=НЕ ТОРГУЕМ')
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 80)
print()

total_trades = len(result_df)
wins = len(result_df[result_df['PnL_USD'] > 0])
losses = total_trades - wins

print('=== РЕЗУЛЬТАТЫ: ВЕСЬ ПЕРИОД ===')
print(f'Период: {dates[start_day]} - {dates[-1]}')
print(f'Всего сделок: {total_trades}')
print(f'  - BULL (тупой LONG): {bull_trades}')
print(f'  - BEAR (MOMENTUM SHORT): {bear_trades}')
print(f'  - Пропущено (SIDEWAYS): {sideways_skipped} дней')
print()
print(f'Винрейт: {wins/total_trades*100:.1f}% ({wins} побед / {losses} проигрышей)')
print()
print(f'PnL % (net):   {cumulative_pct:+.2f}%')
print(f'PnL USD (net): ${cumulative_usd:+,.2f}')
print(f'Комиссии:      ${total_commission:,.2f}')
print()

# Статистика по режимам
bull_df = result_df[result_df['Regime'] == 'BULL']
bear_df = result_df[result_df['Regime'] == 'BEAR']

if len(bull_df) > 0:
    bull_wins = len(bull_df[bull_df['PnL_USD'] > 0])
    print(f'BULL сделки: {len(bull_df)}, Win: {bull_wins/len(bull_df)*100:.1f}%, PnL: ${bull_df["PnL_USD"].sum():+,.2f}')

if len(bear_df) > 0:
    bear_wins = len(bear_df[bear_df['PnL_USD'] > 0])
    print(f'BEAR сделки: {len(bear_df)}, Win: {bear_wins/len(bear_df)*100:.1f}%, PnL: ${bear_df["PnL_USD"].sum():+,.2f}')

print()

# Сохраняем
output_path = r'G:\BinanceFriend\backtest_regime_full.xlsx'
result_df.to_excel(output_path, index=False, sheet_name='Trades')
print(f'Сохранено: {output_path}')
print()


# === СТАТИСТИКА ЗА ПОСЛЕДНИЕ 2 МЕСЯЦА ===
last_60_start = dates[-60]
df_last2m = result_df[result_df['Entry_Date'] >= last_60_start].copy()

if len(df_last2m) > 0:
    trades_2m = len(df_last2m)
    wins_2m = len(df_last2m[df_last2m['PnL_USD'] > 0])
    losses_2m = trades_2m - wins_2m
    pnl_pct_2m = df_last2m['PnL_%_net'].sum()
    pnl_usd_2m = df_last2m['PnL_USD'].sum()

    bull_2m = df_last2m[df_last2m['Regime'] == 'BULL']
    bear_2m = df_last2m[df_last2m['Regime'] == 'BEAR']

    print('=== РЕЗУЛЬТАТЫ: ПОСЛЕДНИЕ 2 МЕСЯЦА ===')
    print(f'Период: {last_60_start} - {dates[-1]}')
    print(f'Всего сделок: {trades_2m}')
    print(f'  - BULL (тупой LONG): {len(bull_2m)}')
    print(f'  - BEAR (MOMENTUM SHORT): {len(bear_2m)}')
    print()
    print(f'Винрейт: {wins_2m/trades_2m*100:.1f}% ({wins_2m} побед / {losses_2m} проигрышей)')
    print()
    print(f'PnL % (net):   {pnl_pct_2m:+.2f}%')
    print(f'PnL USD (net): ${pnl_usd_2m:+,.2f}')
    print()

    if len(bull_2m) > 0:
        bull_wins_2m = len(bull_2m[bull_2m['PnL_USD'] > 0])
        print(f'BULL сделки: {len(bull_2m)}, Win: {bull_wins_2m/len(bull_2m)*100:.1f}%, PnL: ${bull_2m["PnL_USD"].sum():+,.2f}')

    if len(bear_2m) > 0:
        bear_wins_2m = len(bear_2m[bear_2m['PnL_USD'] > 0])
        print(f'BEAR сделки: {len(bear_2m)}, Win: {bear_wins_2m/len(bear_2m)*100:.1f}%, PnL: ${bear_2m["PnL_USD"].sum():+,.2f}')

    print()
    output_path_2m = r'G:\BinanceFriend\backtest_regime_2m.xlsx'
    df_last2m.to_excel(output_path_2m, index=False, sheet_name='Trades')
    print(f'Сохранено: {output_path_2m}')
    print()

    print('=== СДЕЛКИ ПОСЛЕДНИЕ 2 МЕСЯЦА ===')
    cols = ['Trade#', 'Signal', 'Direction', 'Entry_Date', 'Result', 'PnL_%_net', 'PnL_USD', 'Cumul_USD']
    print(df_last2m[cols].to_string(index=False))
