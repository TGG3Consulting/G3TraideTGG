# -*- coding: utf-8 -*-
"""
ПРАВИЛЬНЫЙ БЭКТЕСТ

- БЫК (ADX >= 20, Close > SMA200): тупой LONG
- БОКОВИК (ADX < 20): не торгуем
- МЕДВЕДЬ (ADX >= 20, Close < SMA200): стратегия MOMENTUM
  (она сама решает LONG при +5% или SHORT при -5%)
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx')

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

df['SMA_200'] = df['Close'].rolling(window=sma_period).mean()

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
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

sma_200 = df['SMA_200'].values
price_change_7d = df['Price_Change_7d'].values
start_day = max(sma_period, adx_period * 2, lookback)

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

results = []
trade_num = 0
cumulative_usd = 0
total_commission = 0
stupid_long_count = 0
momentum_count = 0
sideways_skip = 0

i = start_day
while i < n_days - 1:
    if pd.isna(sma_200[i]) or adx[i] == 0:
        i += 1
        continue

    strategy = None
    direction = None

    if adx[i] < 20:
        sideways_skip += 1
        i += 1
        continue
    elif closes[i] > sma_200[i]:
        # БЫК - тупой LONG
        strategy = 'STUPID_LONG'
        direction = 'LONG'
        stupid_long_count += 1
    else:
        # МЕДВЕДЬ - стратегия MOMENTUM
        if pd.isna(price_change_7d[i]):
            i += 1
            continue
        if price_change_7d[i] >= momentum_threshold:
            strategy = 'MOMENTUM'
            direction = 'LONG'
            momentum_count += 1
        elif price_change_7d[i] <= -momentum_threshold:
            strategy = 'MOMENTUM'
            direction = 'SHORT'
            momentum_count += 1
        else:
            i += 1
            continue

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
        'Strategy': strategy,
        'Direction': direction,
        'Entry_Date': dates[entry_day],
        'Exit_Date': dates[exit_day],
        'Result': exit_result,
        'PnL_%_net': round(pnl_pct_net, 4),
        'PnL_USD': round(pnl_usd, 2),
        'Cumul_USD': round(cumulative_usd, 2)
    })
    i = exit_day + 1

result_df = pd.DataFrame(results)
total_trades = len(result_df)
wins = len(result_df[result_df['PnL_USD'] > 0])

print('=' * 70)
print('  BYK = STUPID LONG | MEDVED = MOMENTUM | BOKOVIK = skip')
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 70)
print()
print('=== FULL PERIOD ===')
print(f'Period: {dates[start_day]} - {dates[-1]}')
print(f'Total trades: {total_trades}')
print(f'  - STUPID_LONG: {stupid_long_count}')
print(f'  - MOMENTUM: {momentum_count}')
print(f'  - Skipped (sideways): {sideways_skip} days')
print()
print(f'Win rate: {wins/total_trades*100:.1f}% ({wins}/{total_trades})')
print(f'PnL USD (net): ${cumulative_usd:+,.2f}')
print(f'Commission: ${total_commission:,.2f}')
print()

sl_df = result_df[result_df['Strategy'] == 'STUPID_LONG']
mom_df = result_df[result_df['Strategy'] == 'MOMENTUM']

if len(sl_df) > 0:
    sl_wins = len(sl_df[sl_df['PnL_USD'] > 0])
    print(f'STUPID_LONG: {len(sl_df)} trades, Win {sl_wins/len(sl_df)*100:.1f}%, PnL ${sl_df["PnL_USD"].sum():+,.2f}')
if len(mom_df) > 0:
    mom_wins = len(mom_df[mom_df['PnL_USD'] > 0])
    print(f'MOMENTUM: {len(mom_df)} trades, Win {mom_wins/len(mom_df)*100:.1f}%, PnL ${mom_df["PnL_USD"].sum():+,.2f}')

print()
result_df.to_excel(r'G:\BinanceFriend\backtest_final_full.xlsx', index=False)

# LAST 2 MONTHS
last_60_start = dates[-60]
df_2m = result_df[result_df['Entry_Date'] >= last_60_start]

if len(df_2m) > 0:
    trades_2m = len(df_2m)
    wins_2m = len(df_2m[df_2m['PnL_USD'] > 0])
    pnl_2m = df_2m['PnL_USD'].sum()

    sl_2m = df_2m[df_2m['Strategy'] == 'STUPID_LONG']
    mom_2m = df_2m[df_2m['Strategy'] == 'MOMENTUM']

    print('=== LAST 2 MONTHS ===')
    print(f'Period: {last_60_start} - {dates[-1]}')
    print(f'Total trades: {trades_2m}')
    print(f'  - STUPID_LONG: {len(sl_2m)}')
    print(f'  - MOMENTUM: {len(mom_2m)}')
    print()
    print(f'Win rate: {wins_2m/trades_2m*100:.1f}% ({wins_2m}/{trades_2m})')
    print(f'PnL USD (net): ${pnl_2m:+,.2f}')
    print()

    if len(sl_2m) > 0:
        print(f'STUPID_LONG: {len(sl_2m)} trades, PnL ${sl_2m["PnL_USD"].sum():+,.2f}')
    if len(mom_2m) > 0:
        print(f'MOMENTUM: {len(mom_2m)} trades, PnL ${mom_2m["PnL_USD"].sum():+,.2f}')
    print()

    df_2m.to_excel(r'G:\BinanceFriend\backtest_final_2m.xlsx', index=False)

    print('=== TRADES LAST 2 MONTHS ===')
    print(df_2m[['Trade#', 'Strategy', 'Direction', 'Entry_Date', 'Result', 'PnL_USD']].to_string(index=False))
