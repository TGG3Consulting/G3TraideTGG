# -*- coding: utf-8 -*-
"""
ПОЛНЫЙ АНАЛИЗ ВСЕХ ВАРИАНТОВ
"""
import pandas as pd
import numpy as np
from datetime import datetime

df = pd.read_excel(r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx')

# Base params
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

# Precompute
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


def run_backtest(signal_func, start_day=200, direction_filter=None, adx_threshold=20, use_regime=True, dynamic_size=False):
    """
    Run backtest with given signal function.
    Returns: (trades, wins, pnl, max_drawdown, max_loss_streak, trades_2m, wins_2m, pnl_2m)
    """
    results = []
    cumulative_usd = 0
    peak = 0
    max_dd = 0
    current_streak = 0
    max_loss_streak = 0
    last_was_win = True

    signal_day = start_day
    while signal_day < n_days - 1:
        # Regime check
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

        # Direction filter
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

        # Dynamic size
        size = position_size
        if dynamic_size:
            size = 1500 if last_was_win else 150

        comm = size * commission_pct * 2 / 100
        pnl_usd = size * pnl_pct / 100 - comm
        cumulative_usd += pnl_usd

        # Track drawdown
        if cumulative_usd > peak:
            peak = cumulative_usd
        dd = peak - cumulative_usd
        if dd > max_dd:
            max_dd = dd

        # Track loss streak
        if pnl_usd <= 0:
            current_streak += 1
            if current_streak > max_loss_streak:
                max_loss_streak = current_streak
            last_was_win = False
        else:
            current_streak = 0
            last_was_win = True

        results.append({
            'Entry_Date': dates[entry_day],
            'PnL_USD': pnl_usd
        })

        signal_day = exit_day + 1

    if len(results) == 0:
        return 0, 0, 0, 0, 0, 0, 0, 0

    rdf = pd.DataFrame(results)
    total = len(rdf)
    wins = len(rdf[rdf['PnL_USD'] > 0])

    # Last 2 months
    rdf_2m = rdf[rdf['Entry_Date'] >= last_60]
    total_2m = len(rdf_2m)
    wins_2m = len(rdf_2m[rdf_2m['PnL_USD'] > 0]) if total_2m > 0 else 0
    pnl_2m = rdf_2m['PnL_USD'].sum() if total_2m > 0 else 0

    return total, wins, cumulative_usd, max_dd, max_loss_streak, total_2m, wins_2m, pnl_2m


# Precompute price changes for different lookbacks
for lb in [3, 5, 7, 10, 14]:
    df[f'PC_{lb}d'] = (df['Close'] - df['Close'].shift(lb)) / df['Close'].shift(lb) * 100


results_all = []

print("Running tests...")
print()

# ============================================
# TEST 1: Momentum threshold optimization
# ============================================
print("1. Momentum threshold optimization...")
for mom_th in [3, 5, 7, 10]:
    pc = df['PC_7d'].values
    def signal_func(day):
        if pd.isna(pc[day]): return None
        if pc[day] >= mom_th: return 'LONG'
        if pc[day] <= -mom_th: return 'SHORT'
        return None

    t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_func, start_day=200, use_regime=False)
    results_all.append({
        'Test': f'MOMENTUM th={mom_th}%',
        'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
        'MaxDD': dd, 'MaxLossStreak': streak,
        'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
    })


# ============================================
# TEST 2: LS Confirm threshold optimization
# ============================================
print("2. LS Confirm threshold optimization...")
pc = df['PC_7d'].values
for ls_c in [0.55, 0.60, 0.65, 0.70]:
    def signal_func(day, ls_confirm=ls_c):
        if pd.isna(pc[day]) or pd.isna(long_pct[day]): return None
        if pc[day] >= 5 and long_pct[day] < ls_confirm: return 'LONG'
        if pc[day] <= -5 and short_pct[day] < ls_confirm: return 'SHORT'
        return None

    t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_func, start_day=200, use_regime=False)
    results_all.append({
        'Test': f'MOM_LS confirm={int(ls_c*100)}%',
        'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
        'MaxDD': dd, 'MaxLossStreak': streak,
        'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
    })


# ============================================
# TEST 3: LS Extreme threshold optimization
# ============================================
print("3. LS Extreme (LS_FADE) threshold optimization...")
for ls_e in [0.60, 0.65, 0.70, 0.75]:
    def signal_func(day, ls_ext=ls_e):
        if pd.isna(long_pct[day]): return None
        if short_pct[day] >= ls_ext: return 'LONG'
        if long_pct[day] >= ls_ext: return 'SHORT'
        return None

    t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_func, start_day=7, use_regime=False)
    results_all.append({
        'Test': f'LS_FADE extreme={int(ls_e*100)}%',
        'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
        'MaxDD': dd, 'MaxLossStreak': streak,
        'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
    })


# ============================================
# TEST 4: ADX threshold optimization
# ============================================
print("4. ADX threshold optimization...")
for adx_th in [15, 20, 25, 30]:
    def signal_func(day):
        if pd.isna(pc[day]) or pd.isna(long_pct[day]): return None
        # Priority: MOM_LS > MOM > FADE
        if pc[day] >= 5 and long_pct[day] < 0.60: return 'LONG'
        if pc[day] <= -5 and short_pct[day] < 0.60: return 'SHORT'
        if pc[day] >= 5: return 'LONG'
        if pc[day] <= -5: return 'SHORT'
        if short_pct[day] >= 0.65: return 'LONG'
        if long_pct[day] >= 0.65: return 'SHORT'
        return None

    t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_func, start_day=200, use_regime=True, adx_threshold=adx_th)
    results_all.append({
        'Test': f'Priority+Regime ADX>{adx_th}',
        'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
        'MaxDD': dd, 'MaxLossStreak': streak,
        'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
    })


# ============================================
# TEST 5: Direction filter (LONG only / SHORT only)
# ============================================
print("5. Direction filter tests...")
for strat_name, sig_func in [
    ('MOMENTUM', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 else None)),
    ('MOM_LS', lambda d: 'LONG' if not pd.isna(pc[d]) and pc[d] >= 5 and long_pct[d] < 0.60 else ('SHORT' if not pd.isna(pc[d]) and pc[d] <= -5 and short_pct[d] < 0.60 else None)),
    ('LS_FADE', lambda d: 'LONG' if short_pct[d] >= 0.65 else ('SHORT' if long_pct[d] >= 0.65 else None)),
]:
    for dir_filter in ['LONG', 'SHORT']:
        t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(sig_func, start_day=200, direction_filter=dir_filter, use_regime=False)
        results_all.append({
            'Test': f'{strat_name} {dir_filter} only',
            'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
            'MaxDD': dd, 'MaxLossStreak': streak,
            'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
        })


# ============================================
# TEST 6: Dynamic Size
# ============================================
print("6. Dynamic Size tests...")
def signal_priority(day):
    if pd.isna(long_pct[day]): return None
    if not pd.isna(pc[day]):
        if pc[day] >= 5 and long_pct[day] < 0.60: return 'LONG'
        if pc[day] <= -5 and short_pct[day] < 0.60: return 'SHORT'
        if pc[day] >= 5: return 'LONG'
        if pc[day] <= -5: return 'SHORT'
    if short_pct[day] >= 0.65: return 'LONG'
    if long_pct[day] >= 0.65: return 'SHORT'
    return None

# Without dynamic size
t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_priority, start_day=200, use_regime=True)
results_all.append({
    'Test': 'Priority+Regime (fixed $1500)',
    'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
    'MaxDD': dd, 'MaxLossStreak': streak,
    'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
})

# With dynamic size
t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_priority, start_day=200, use_regime=True, dynamic_size=True)
results_all.append({
    'Test': 'Priority+Regime (dyn $1500/$150)',
    'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
    'MaxDD': dd, 'MaxLossStreak': streak,
    'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
})


# ============================================
# TEST 7: Lookback period optimization
# ============================================
print("7. Lookback period optimization...")
for lb in [3, 5, 7, 10, 14]:
    pc_lb = df[f'PC_{lb}d'].values
    def signal_func(day):
        if day < lb or pd.isna(pc_lb[day]): return None
        if pc_lb[day] >= 5: return 'LONG'
        if pc_lb[day] <= -5: return 'SHORT'
        return None

    t, w, pnl, dd, streak, t2, w2, pnl2 = run_backtest(signal_func, start_day=max(200, lb), use_regime=False)
    results_all.append({
        'Test': f'MOMENTUM lookback={lb}d',
        'Trades': t, 'Win%': w/t*100 if t > 0 else 0, 'PnL': pnl,
        'MaxDD': dd, 'MaxLossStreak': streak,
        'Trades_2M': t2, 'Win%_2M': w2/t2*100 if t2 > 0 else 0, 'PnL_2M': pnl2
    })


# ============================================
# OUTPUT
# ============================================
print()
print('=' * 120)
print('  FULL ANALYSIS RESULTS')
print('=' * 120)
print()

# Sort by 2-year PnL
results_df = pd.DataFrame(results_all)
results_df = results_df.sort_values('PnL', ascending=False)

print('=== SORTED BY 2-YEAR PnL ===')
print()
print(f'{"Test":<35} {"Trades":>7} {"Win%":>7} {"PnL":>12} {"MaxDD":>10} {"Streak":>7} | {"Tr_2M":>6} {"Win%":>7} {"PnL_2M":>10}')
print('-' * 120)

for _, r in results_df.iterrows():
    print(f'{r["Test"]:<35} {r["Trades"]:>7} {r["Win%"]:>6.1f}% ${r["PnL"]:>+10,.0f} ${r["MaxDD"]:>9,.0f} {r["MaxLossStreak"]:>7} | {r["Trades_2M"]:>6} {r["Win%_2M"]:>6.1f}% ${r["PnL_2M"]:>+9,.0f}')

print()
print()

# Sort by 2-month PnL
results_df_2m = results_df.sort_values('PnL_2M', ascending=False)

print('=== SORTED BY 2-MONTH PnL ===')
print()
print(f'{"Test":<35} {"Tr_2M":>6} {"Win%":>7} {"PnL_2M":>10} | {"Trades":>7} {"Win%":>7} {"PnL":>12}')
print('-' * 100)

for _, r in results_df_2m.head(15).iterrows():
    print(f'{r["Test"]:<35} {r["Trades_2M"]:>6} {r["Win%_2M"]:>6.1f}% ${r["PnL_2M"]:>+9,.0f} | {r["Trades"]:>7} {r["Win%"]:>6.1f}% ${r["PnL"]:>+10,.0f}')

# Save
results_df.to_excel(r'G:\BinanceFriend\full_analysis_results.xlsx', index=False)
print()
print('Saved: full_analysis_results.xlsx')
