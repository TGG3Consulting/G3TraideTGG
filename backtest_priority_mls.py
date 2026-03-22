# -*- coding: utf-8 -*-
"""
Backtest with priority: MOMENTUM_LS > MOMENTUM > LS_FADE
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

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
long_pct = df['longAccount'].values
short_pct = df['shortAccount'].values
n_days = len(df)

df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100
price_change_7d = df['Price_Change_7d'].values
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

    # 1. MOMENTUM_LS (priority 1)
    if not pd.isna(pc):
        if pc >= momentum_threshold and lp < ls_confirm:
            return 'MOMENTUM_LS', 'LONG'
        if pc <= -momentum_threshold and sp < ls_confirm:
            return 'MOMENTUM_LS', 'SHORT'

    # 2. MOMENTUM (priority 2)
    if not pd.isna(pc):
        if pc >= momentum_threshold:
            return 'MOMENTUM', 'LONG'
        if pc <= -momentum_threshold:
            return 'MOMENTUM', 'SHORT'

    # 3. LS_FADE (priority 3)
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

signal_day = lookback
while signal_day < n_days - 1:
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

    results.append({
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
print('  SL=1.5%, Activation=1.5%, Callback=0.3%, Size=$1500, Comm=0.1%')
print('=' * 70)
print()

print('=== FULL PERIOD ===')
print(f'Total trades: {total_trades}')
print(f'  MOMENTUM_LS: {strategy_counts["MOMENTUM_LS"]}')
print(f'  MOMENTUM: {strategy_counts["MOMENTUM"]}')
print(f'  LS_FADE: {strategy_counts["LS_FADE"]}')
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

result_df.to_excel(r'G:\BinanceFriend\backtest_priority_mls.xlsx', index=False)
print()
print('Saved: backtest_priority_mls.xlsx')
