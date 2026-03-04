# -*- coding: utf-8 -*-
"""Monthly Performance Analysis"""
import pandas as pd
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

data_path = Path('G:/BinanceFriend/outputNEWARCH/obucheniyeML24_26gg_68_monet_primerno_outputNEWARCH')
xlsx_files = list(data_path.glob('backtest_*.xlsx'))

all_trades = []
for f in xlsx_files:
    if f.name.startswith('~$'):
        continue
    try:
        df = pd.read_excel(f, sheet_name='Trades')
        if 'ls_fade' in f.stem:
            strategy = 'ls_fade'
        elif 'momentum_ls' in f.stem:
            strategy = 'momentum_ls'
        elif 'mean_reversion' in f.stem:
            strategy = 'mean_reversion'
        elif 'momentum' in f.stem:
            strategy = 'momentum'
        elif 'reversal' in f.stem:
            strategy = 'reversal'
        else:
            continue
        df['Strategy'] = strategy
        all_trades.append(df)
    except:
        continue

df = pd.concat(all_trades, ignore_index=True)
df = df[df['Result'].isin(['WIN', 'LOSS'])].copy()
df['Signal Date'] = pd.to_datetime(df['Signal Date'], errors='coerce')
df['Month'] = df['Signal Date'].dt.month
df['Year'] = df['Signal Date'].dt.year
df['IsWin'] = (df['Result'] == 'WIN').astype(int)
df['PnL'] = df['Net PnL %'].fillna(0)

print('='*80)
print('MONTHLY PERFORMANCE (all strategies, all years)')
print('='*80)
print(f"{'Month':<12} {'Trades':>8} {'Wins':>8} {'WinRate':>10} {'TotalPnL':>12} {'AvgPnL':>10}")
print('-'*80)

monthly = df.groupby('Month').agg({
    'IsWin': ['count', 'sum', 'mean'],
    'PnL': ['sum', 'mean']
}).round(2)

monthly.columns = ['Trades', 'Wins', 'WinRate', 'TotalPnL', 'AvgPnL']
monthly = monthly.sort_values('WinRate', ascending=False)

month_names = {1:'January', 2:'February', 3:'March', 4:'April', 5:'May', 6:'June',
               7:'July', 8:'August', 9:'September', 10:'October', 11:'November', 12:'December'}

for month, row in monthly.iterrows():
    name = month_names.get(month, str(month))
    wr = row['WinRate']*100
    print(f"{name:<12} {int(row['Trades']):>8} {int(row['Wins']):>8} {wr:>9.1f}% {row['TotalPnL']:>+12.1f}% {row['AvgPnL']:>+9.2f}%")

print('-'*80)

best = monthly['WinRate'].idxmax()
worst = monthly['WinRate'].idxmin()
print(f"\nBEST:  {month_names[best]} (WR {monthly.loc[best, 'WinRate']*100:.1f}%)")
print(f"WORST: {month_names[worst]} (WR {monthly.loc[worst, 'WinRate']*100:.1f}%)")
print(f"Spread: {(monthly.loc[best, 'WinRate'] - monthly.loc[worst, 'WinRate'])*100:.1f} pp")

# By strategy breakdown
print('\n' + '='*80)
print('MONTHLY PERFORMANCE BY STRATEGY')
print('='*80)

for strategy in ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']:
    sdf = df[df['Strategy'] == strategy]
    if len(sdf) < 100:
        continue

    print(f"\n--- {strategy.upper()} ---")
    print(f"{'Month':<12} {'Trades':>8} {'WinRate':>10} {'TotalPnL':>12}")
    print('-'*50)

    sm = sdf.groupby('Month').agg({
        'IsWin': ['count', 'mean'],
        'PnL': 'sum'
    }).round(2)
    sm.columns = ['Trades', 'WinRate', 'TotalPnL']
    sm = sm.sort_values('WinRate', ascending=False)

    for month, row in sm.iterrows():
        name = month_names.get(month, str(month))
        wr = row['WinRate']*100
        print(f"{name:<12} {int(row['Trades']):>8} {wr:>9.1f}% {row['TotalPnL']:>+12.1f}%")

# Year-Month heatmap data
print('\n' + '='*80)
print('YEAR-MONTH WINRATE MATRIX')
print('='*80)

pivot = df.pivot_table(values='IsWin', index='Year', columns='Month', aggfunc='mean')
pivot = (pivot * 100).round(1)

print(f"\n{'Year':<6}", end='')
for m in range(1, 13):
    print(f"{month_names[m][:3]:>8}", end='')
print()
print('-'*102)

for year in sorted(pivot.index):
    print(f"{year:<6}", end='')
    for m in range(1, 13):
        if m in pivot.columns:
            val = pivot.loc[year, m]
            if pd.notna(val):
                print(f"{val:>7.1f}%", end='')
            else:
                print(f"{'---':>8}", end='')
        else:
            print(f"{'---':>8}", end='')
    print()
