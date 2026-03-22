# -*- coding: utf-8 -*-
"""
COMBINED ANALYSIS: BTC + XRP
"""
import pandas as pd
import numpy as np

def run_full_analysis(symbol, filepath):
    df = pd.read_excel(filepath)

    position_size = 1500
    commission_pct = 0.1
    sl = 1.5
    activation = 1.5
    callback = 0.3

    opens = df['Open'].values
    highs = df['High'].values
    lows = df['Low'].values
    closes = df['Close'].values
    dates = df['Date'].values
    long_pct = df['longAccount'].values
    short_pct = df['shortAccount'].values
    n_days = len(df)

    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    sma_200 = df['SMA_200'].values

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
        s_plus = np.mean(plus_dm[1:period+1])
        s_minus = np.mean(minus_dm[1:period+1])
        p_di = 100 * s_plus / atr[period] if atr[period] > 0 else 0
        m_di = 100 * s_minus / atr[period] if atr[period] > 0 else 0
        di_sum = p_di + m_di
        dx_arr = [100 * abs(p_di - m_di) / di_sum if di_sum > 0 else 0]
        for i in range(period + 1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            s_plus = (s_plus * (period - 1) + plus_dm[i]) / period
            s_minus = (s_minus * (period - 1) + minus_dm[i]) / period
            p_di = 100 * s_plus / atr[i] if atr[i] > 0 else 0
            m_di = 100 * s_minus / atr[i] if atr[i] > 0 else 0
            di_sum = p_di + m_di
            dx_arr.append(100 * abs(p_di - m_di) / di_sum if di_sum > 0 else 0)
        adx[period * 2] = np.mean(dx_arr[:period+1])
        for i in range(period * 2 + 1, n):
            adx[i] = (adx[i-1] * (period - 1) + dx_arr[i - period]) / period
        return adx

    adx = calculate_adx(highs, lows, closes, 14)
    last_60 = dates[-60]

    for lb in [7]:
        df[f'PC_{lb}d'] = (df['Close'] - df['Close'].shift(lb)) / df['Close'].shift(lb) * 100
    pc = df['PC_7d'].values

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

    def run_backtest(signal_func, start_day=200, direction_filter=None, adx_threshold=20, use_regime=True):
        results = []
        cumulative_usd = 0
        peak = 0
        max_dd = 0

        signal_day = start_day
        while signal_day < n_days - 1:
            if use_regime:
                if pd.isna(sma_200[signal_day]) or adx[signal_day] == 0:
                    signal_day += 1
                    continue
                if adx[signal_day] < adx_threshold:
                    signal_day += 1
                    continue

            direction = signal_func(signal_day)
            if direction is None:
                signal_day += 1
                continue

            if direction_filter and direction != direction_filter:
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

            if cumulative_usd > peak:
                peak = cumulative_usd
            dd = peak - cumulative_usd
            if dd > max_dd:
                max_dd = dd

            results.append({'Entry_Date': dates[entry_day], 'PnL_USD': pnl_usd})
            signal_day = exit_day + 1

        if len(results) == 0:
            return 0, 0, 0, 0, 0, 0, 0

        rdf = pd.DataFrame(results)
        total = len(rdf)
        wins = len(rdf[rdf['PnL_USD'] > 0])
        rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
        total_2m = len(rdf_2m)
        wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
        pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0

        return total, wins, cumulative_usd, max_dd, total_2m, wins_2m, pnl_2m

    results_all = []

    # Key tests
    tests = [
        ('MOMENTUM', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 else None), False),
        ('MOM_LS 55%', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 and long_pct[d] < 0.55 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 and short_pct[d] < 0.55 else None), False),
        ('MOM_LS 60%', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 and long_pct[d] < 0.60 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 and short_pct[d] < 0.60 else None), False),
        ('LS_FADE 60%', lambda d: 'LONG' if short_pct[d] >= 0.60 else ('SHORT' if long_pct[d] >= 0.60 else None), False),
        ('LS_FADE 65%', lambda d: 'LONG' if short_pct[d] >= 0.65 else ('SHORT' if long_pct[d] >= 0.65 else None), False),
        ('Priority+Regime', lambda d: (
            'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 and long_pct[d] < 0.60 else
            'SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 and short_pct[d] < 0.60 else
            'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 else
            'SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 else
            'LONG' if short_pct[d] >= 0.65 else
            'SHORT' if long_pct[d] >= 0.65 else None
        ), True),
    ]

    for name, func, use_regime in tests:
        t, w, pnl, dd, t2, w2, pnl2 = run_backtest(func, start_day=200, use_regime=use_regime)
        results_all.append({
            'Symbol': symbol,
            'Test': name,
            'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl, 'MaxDD': dd,
            'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
        })

    # Direction tests
    for name, func in [
        ('MOMENTUM', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 else None)),
        ('MOM_LS', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 and long_pct[d] < 0.60 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 and short_pct[d] < 0.60 else None)),
    ]:
        for dir_f in ['LONG', 'SHORT']:
            t, w, pnl, dd, t2, w2, pnl2 = run_backtest(func, start_day=200, direction_filter=dir_f, use_regime=False)
            results_all.append({
                'Symbol': symbol,
                'Test': f'{name} {dir_f}',
                'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl, 'MaxDD': dd,
                'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
            })

    return results_all


# Run for both symbols
print("Analyzing BTC...")
btc_results = run_full_analysis('BTC', r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx')

print("Analyzing XRP...")
xrp_results = run_full_analysis('XRP', r'G:\BinanceFriend\XRPUSDT_volatility_2y_with_LS.xlsx')

all_results = btc_results + xrp_results
df_all = pd.DataFrame(all_results)

# Combined (sum of both)
combined = []
for test in df_all['Test'].unique():
    btc_row = df_all[(df_all['Symbol'] == 'BTC') & (df_all['Test'] == test)]
    xrp_row = df_all[(df_all['Symbol'] == 'XRP') & (df_all['Test'] == test)]
    if len(btc_row) > 0 and len(xrp_row) > 0:
        combined.append({
            'Test': test,
            'Trades': btc_row['Trades'].values[0] + xrp_row['Trades'].values[0],
            'PnL': btc_row['PnL'].values[0] + xrp_row['PnL'].values[0],
            'MaxDD': max(btc_row['MaxDD'].values[0], xrp_row['MaxDD'].values[0]),
            'Trades_2M': btc_row['Trades_2M'].values[0] + xrp_row['Trades_2M'].values[0],
            'PnL_2M': btc_row['PnL_2M'].values[0] + xrp_row['PnL_2M'].values[0],
            'BTC_PnL': btc_row['PnL'].values[0],
            'XRP_PnL': xrp_row['PnL'].values[0],
            'BTC_PnL_2M': btc_row['PnL_2M'].values[0],
            'XRP_PnL_2M': xrp_row['PnL_2M'].values[0],
        })

df_combined = pd.DataFrame(combined)
df_combined = df_combined.sort_values('PnL', ascending=False)

print()
print('=' * 130)
print('  BTC + XRP COMBINED RESULTS')
print('=' * 130)
print()

print('=== SORTED BY 2-YEAR COMBINED PnL ===')
print()
print(f'{"Test":<20} {"Trades":>7} {"PnL_Total":>12} {"BTC":>10} {"XRP":>10} {"MaxDD":>8} | {"Tr_2M":>6} {"PnL_2M":>10} {"BTC_2M":>9} {"XRP_2M":>9}')
print('-' * 130)

for _, r in df_combined.iterrows():
    print(f'{r["Test"]:<20} {r["Trades"]:>7} ${r["PnL"]:>+10,.0f} ${r["BTC_PnL"]:>+8,.0f} ${r["XRP_PnL"]:>+8,.0f} ${r["MaxDD"]:>7,.0f} | {r["Trades_2M"]:>6} ${r["PnL_2M"]:>+9,.0f} ${r["BTC_PnL_2M"]:>+8,.0f} ${r["XRP_PnL_2M"]:>+8,.0f}')

print()
print()

# Sort by 2M
df_combined_2m = df_combined.sort_values('PnL_2M', ascending=False)
print('=== SORTED BY 2-MONTH COMBINED PnL ===')
print()
print(f'{"Test":<20} {"Tr_2M":>6} {"PnL_2M":>10} {"BTC_2M":>9} {"XRP_2M":>9} | {"Trades":>7} {"PnL_Total":>12}')
print('-' * 100)

for _, r in df_combined_2m.iterrows():
    print(f'{r["Test"]:<20} {r["Trades_2M"]:>6} ${r["PnL_2M"]:>+9,.0f} ${r["BTC_PnL_2M"]:>+8,.0f} ${r["XRP_PnL_2M"]:>+8,.0f} | {r["Trades"]:>7} ${r["PnL"]:>+10,.0f}')

print()
print()

# Individual results
print('=== INDIVIDUAL RESULTS ===')
print()
for sym in ['BTC', 'XRP']:
    print(f'--- {sym} ---')
    sym_df = df_all[df_all['Symbol'] == sym].sort_values('PnL', ascending=False)
    print(f'{"Test":<20} {"Trades":>7} {"Win%":>7} {"PnL":>10} | {"Tr_2M":>6} {"Win%_2M":>7} {"PnL_2M":>10}')
    print('-' * 80)
    for _, r in sym_df.iterrows():
        print(f'{r["Test"]:<20} {r["Trades"]:>7} {r["Win%"]:>6.1f}% ${r["PnL"]:>+8,.0f} | {r["Trades_2M"]:>6} {r["Win%_2M"]:>6.1f}% ${r["PnL_2M"]:>+8,.0f}')
    print()

df_all.to_excel(r'G:\BinanceFriend\btc_xrp_analysis.xlsx', index=False)
print('Saved: btc_xrp_analysis.xlsx')
