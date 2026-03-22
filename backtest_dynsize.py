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

# Dynamic sizing
size_after_win = 1500
size_after_loss = 150
commission_pct = 0.1  # 0.1% на вход + 0.1% на выход = 0.2% total

opens = df['Open'].values
highs = df['High'].values
lows = df['Low'].values
closes = df['Close'].values
dates = df['Date'].values
n_days = len(df)

# Рассчитываем 7d change
df['Close_7d_ago'] = df['Close'].shift(lookback)
df['Price_Change_7d'] = (df['Close'] - df['Close_7d_ago']) / df['Close_7d_ago'] * 100

# Генерируем сигналы MOMENTUM
signals = []
for i in range(lookback, n_days - 1):
    price_change = df['Price_Change_7d'].iloc[i]
    if pd.isna(price_change):
        continue
    if price_change >= momentum_threshold:
        signals.append({'signal_day': i, 'entry_day': i + 1, 'direction': 'LONG', 'price_change_7d': price_change})
    elif price_change <= -momentum_threshold:
        signals.append({'signal_day': i, 'entry_day': i + 1, 'direction': 'SHORT', 'price_change_7d': price_change})

# Sequential бэктест с Dynamic Sizing и комиссиями
results = []
cumulative_pct = 0
cumulative_usd = 0
total_commission_usd = 0
trade_num = 0
current_size = size_after_win  # начинаем с WIN

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
    entry_date = dates[entry_day_idx]
    position_size = current_size

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
    exit_result = None
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
                exit_result = 'SL'
                exit_pnl_pct = -sl
                break
            if high >= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = highest_high * (1 - callback / 100)
                if low <= trailing_exit_price:
                    exit_result = 'TRAIL'
                    exit_pnl_pct = (trailing_exit_price - entry_price) / entry_price * 100
                    break
        else:
            if high >= sl_price:
                exit_result = 'SL'
                exit_pnl_pct = -sl
                break
            if low <= activation_price:
                trailing_activated = True
            if trailing_activated:
                trailing_exit_price = lowest_low * (1 + callback / 100)
                if high >= trailing_exit_price:
                    exit_result = 'TRAIL'
                    exit_pnl_pct = (entry_price - trailing_exit_price) / entry_price * 100
                    break

        if exit_day == n_days - 1:
            exit_result = 'CLOSE'
            if direction == 'LONG':
                exit_pnl_pct = (close - entry_price) / entry_price * 100
            else:
                exit_pnl_pct = (entry_price - close) / entry_price * 100
            break

        exit_day += 1

    # Комиссия: 0.1% вход + 0.1% выход = 0.2% от размера
    commission_total_pct = commission_pct * 2  # 0.2%
    commission_usd = position_size * commission_total_pct / 100

    # PnL после комиссии
    pnl_pct_after_comm = exit_pnl_pct - commission_total_pct
    pnl_usd = position_size * exit_pnl_pct / 100 - commission_usd

    cumulative_pct += pnl_pct_after_comm
    cumulative_usd += pnl_usd
    total_commission_usd += commission_usd

    hold_days = exit_day - entry_day_idx + 1

    # Определяем размер следующей сделки
    is_win = pnl_usd > 0
    next_size = size_after_win if is_win else size_after_loss

    results.append({
        'Trade#': trade_num,
        'Direction': direction,
        'Entry_Date': entry_date,
        'Exit_Date': dates[exit_day],
        'Hold_Days': hold_days,
        'Size_USD': position_size,
        'Result': exit_result,
        'PnL_%_raw': round(exit_pnl_pct, 4),
        'Comm_%': commission_total_pct,
        'PnL_%_net': round(pnl_pct_after_comm, 4),
        'PnL_USD': round(pnl_usd, 2),
        'Comm_USD': round(commission_usd, 2),
        'Cumul_%': round(cumulative_pct, 4),
        'Cumul_USD': round(cumulative_usd, 2),
        'Next_Size': next_size
    })

    current_size = next_size

    while i < len(signals) and signals[i]['entry_day'] <= exit_day:
        i += 1

result_df = pd.DataFrame(results)

# === СТАТИСТИКА ЗА ВСЕ 2 ГОДА ===
print('=' * 80)
print('  MOMENTUM + DYNAMIC SIZE + COMMISSION (0.2% round-trip)')
print('  SL=1.5%, Callback=0.3%, Activation=1.5%')
print('  Size: $1500 after WIN, $150 after LOSS')
print('=' * 80)
print()

total_trades = len(result_df)
wins = len(result_df[result_df['PnL_USD'] > 0])
losses = len(result_df[result_df['PnL_USD'] <= 0])

print('=== RESULTS: FULL 2 YEARS ===')
print(f'Period: {dates[lookback]} to {dates[-1]}')
print(f'Total trades: {total_trades}')
print(f'Win Rate: {wins/total_trades*100:.1f}% ({wins} wins / {losses} losses)')
print()
print(f'PnL % (net):     {cumulative_pct:+.2f}%')
print(f'PnL USD (net):   ${cumulative_usd:+,.2f}')
print(f'Commissions:     ${total_commission_usd:,.2f}')
print()

# Сохраняем
output_path = r'G:\BinanceFriend\backtest_momentum_dynsize_full.xlsx'
result_df.to_excel(output_path, index=False, sheet_name='Trades')
print(f'Saved: {output_path}')
print()

# === СТАТИСТИКА ЗА ПОСЛЕДНИЕ 2 МЕСЯЦА ===
last_60_start = dates[-60]
df_last2m = result_df[result_df['Entry_Date'] >= last_60_start].copy()

if len(df_last2m) > 0:
    trades_2m = len(df_last2m)
    wins_2m = len(df_last2m[df_last2m['PnL_USD'] > 0])
    losses_2m = len(df_last2m[df_last2m['PnL_USD'] <= 0])
    pnl_pct_2m = df_last2m['PnL_%_net'].sum()
    pnl_usd_2m = df_last2m['PnL_USD'].sum()
    comm_usd_2m = df_last2m['Comm_USD'].sum()

    print('=== RESULTS: LAST 2 MONTHS ===')
    print(f'Period: {last_60_start} to {dates[-1]}')
    print(f'Total trades: {trades_2m}')
    print(f'Win Rate: {wins_2m/trades_2m*100:.1f}% ({wins_2m} wins / {losses_2m} losses)')
    print()
    print(f'PnL % (net):     {pnl_pct_2m:+.2f}%')
    print(f'PnL USD (net):   ${pnl_usd_2m:+,.2f}')
    print(f'Commissions:     ${comm_usd_2m:,.2f}')
    print()

    output_path_2m = r'G:\BinanceFriend\backtest_momentum_dynsize_2m.xlsx'
    df_last2m.to_excel(output_path_2m, index=False, sheet_name='Trades')
    print(f'Saved: {output_path_2m}')
    print()

    print('=== TRADES LAST 2 MONTHS ===')
    cols = ['Trade#', 'Direction', 'Entry_Date', 'Size_USD', 'Result', 'PnL_%_net', 'PnL_USD', 'Cumul_USD']
    print(df_last2m[cols].to_string(index=False))
