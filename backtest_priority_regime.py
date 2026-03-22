# -*- coding: utf-8 -*-
"""
Backtest with:
- Priority: MOMENTUM_LS > MOMENTUM > LS_FADE
- Market regime: ADX < 20 = SIDEWAYS (skip), ADX >= 20 + SMA(200)
"""
import pandas as pd
import numpy as np

df = pd.read_excel(r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx')

sl = 1.5
activation = 1.5
callback = 0.3
position_size = 1500
commission_pct = 0.1
momentum_threshold = 5.0
ls_confirm = 0.60
ls_extreme = 0.65
lookback = 7
sma_period = 200
adx_period = 14

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
long_pct = df['longAccount'].values
short_pct = df['shortAccount'].values
n_days = len(df)

# SMA(200)
df['SMA_200'] = df['Close'].rolling(window=sma_period).mean()
sma_200 = df['SMA_200'].values

# ADX
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

# 7d change
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100
price_change_7d = df['Price_Change_7d'].values

start_day = max(sma_period, adx_period * 2, lookback)
last_60 = dates[-60]


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


def get_regime(day):
    """
    ADX < 20 = SIDEWAYS
    ADX >= 20 AND Close > SMA(200) = BULL
    ADX >= 20 AND Close < SMA(200) = BEAR
    """
    if pd.isna(sma_200[day]) or adx[day] == 0:
        return 'UNKNOWN'
    if adx[day] < 20:
        return 'SIDEWAYS'
    if closes[day] > sma_200[day]:
        return 'BULL'
    return 'BEAR'


def get_signal(signal_day):
    """
    Priority: MOMENTUM_LS > MOMENTUM > LS_FADE
    """
    if signal_day < lookback or signal_day >= n_days:
        return None, None

    pc = price_change_7d[signal_day]
    lp = long_pct[signal_day]
    sp = short_pct[signal_day]

    if pd.isna(lp) or pd.isna(sp):
        return None, None

    # 1. MOMENTUM_LS
    if not pd.isna(pc):
        if pc >= momentum_threshold and lp < ls_confirm:
            return 'MOMENTUM_LS', 'LONG'
        if pc <= -momentum_threshold and sp < ls_confirm:
            return 'MOMENTUM_LS', 'SHORT'

    # 2. MOMENTUM
    if not pd.isna(pc):
        if pc >= momentum_threshold:
            return 'MOMENTUM', 'LONG'
        if pc <= -momentum_threshold:
            return 'MOMENTUM', 'SHORT'

    # 3. LS_FADE
    if sp >= ls_extreme:
        return 'LS_FADE', 'LONG'
    if lp >= ls_extreme:
        return 'LS_FADE', 'SHORT'

    return None, None


# BACKTEST
results = []
cumulative_usd = 0
total_commission = 0
strategy_counts = {'MOMENTUM_LS': 0, 'MOMENTUM': 0, 'LS_FADE': 0}
regime_counts = {'BULL': 0, 'BEAR': 0, 'SIDEWAYS_SKIP': 0}

signal_day = start_day
while signal_day < n_days - 1:
    regime = get_regime(signal_day)

    if regime == 'UNKNOWN':
        signal_day += 1
        continue

    if regime == 'SIDEWAYS':
        regime_counts['SIDEWAYS_SKIP'] += 1
        signal_day += 1
        continue

    strategy, direction = get_signal(signal_day)

    if strategy is None:
        signal_day += 1
        continue

    entry_day = signal_day + 1
    if entry_day >= n_days:
        break

    exit_day, pnl_pct, result = simulate_trade(entry_day, direction)
    if result == 'SKIP':
        signal_day += 1
        continue

    comm = position_size * commission_pct * 2 / 100
    pnl_usd = position_size * pnl_pct / 100 - comm
    cumulative_usd += pnl_usd
    total_commission += comm
    strategy_counts[strategy] += 1
    regime_counts[regime] += 1

    results.append({
        'Regime': regime,
        'Strategy': strategy,
        'Direction': direction,
        'Entry_Date': dates[entry_day],
        'Exit_Date': dates[exit_day],
        'Result': result,
        'PnL_USD': round(pnl_usd, 2),
        'Cumul_USD': round(cumulative_usd, 2)
    })

    signal_day = exit_day + 1

result_df = pd.DataFrame(results)
total_trades = len(result_df)
wins = len(result_df[result_df['PnL_USD'] > 0])

# OUTPUT
print('=' * 70)
print('  PRIORITY: MOMENTUM_LS > MOMENTUM > LS_FADE')
print('  REGIME: ADX<20=SKIP, ADX>=20+SMA(200)')
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 70)
print()

print('=== FULL PERIOD ===')
print(f'Total trades: {total_trades}')
print(f'  MOMENTUM_LS: {strategy_counts["MOMENTUM_LS"]}')
print(f'  MOMENTUM: {strategy_counts["MOMENTUM"]}')
print(f'  LS_FADE: {strategy_counts["LS_FADE"]}')
print(f'Regime: BULL={regime_counts["BULL"]}, BEAR={regime_counts["BEAR"]}, SIDEWAYS skipped={regime_counts["SIDEWAYS_SKIP"]} days')
print()
print(f'Win rate: {wins/total_trades*100:.1f}% ({wins}/{total_trades})')
print(f'PnL USD: ${cumulative_usd:+,.2f}')
print(f'Commission: ${total_commission:,.2f}')
print()

for strat in ['MOMENTUM_LS', 'MOMENTUM', 'LS_FADE']:
    sdf = result_df[result_df['Strategy'] == strat]
    if len(sdf) > 0:
        sw = len(sdf[sdf['PnL_USD'] > 0])
        print(f'{strat}: {len(sdf)} trades, Win {sw/len(sdf)*100:.1f}%, PnL ${sdf["PnL_USD"].sum():+,.2f}')

print()

# Last 2 months
df_2m = result_df[result_df['Entry_Date'] >= last_60]

if len(df_2m) > 0:
    trades_2m = len(df_2m)
    wins_2m = len(df_2m[df_2m['PnL_USD'] > 0])

    print('=== LAST 2 MONTHS ===')
    print(f'Total trades: {trades_2m}')

    for strat in ['MOMENTUM_LS', 'MOMENTUM', 'LS_FADE']:
        cnt = len(df_2m[df_2m['Strategy'] == strat])
        if cnt > 0:
            print(f'  {strat}: {cnt}')

    print()
    print(f'Win rate: {wins_2m/trades_2m*100:.1f}% ({wins_2m}/{trades_2m})')
    print(f'PnL USD: ${df_2m["PnL_USD"].sum():+,.2f}')
    print()

    for strat in ['MOMENTUM_LS', 'MOMENTUM', 'LS_FADE']:
        sdf = df_2m[df_2m['Strategy'] == strat]
        if len(sdf) > 0:
            sw = len(sdf[sdf['PnL_USD'] > 0])
            print(f'{strat}: {len(sdf)} trades, Win {sw/len(sdf)*100:.1f}%, PnL ${sdf["PnL_USD"].sum():+,.2f}')

result_df.to_excel(r'G:\BinanceFriend\backtest_priority_regime.xlsx', index=False)
print()
print('Saved: backtest_priority_regime.xlsx')
