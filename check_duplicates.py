# -*- coding: utf-8 -*-
import pandas as pd

# Load both files
print("Loading files...")
df1 = pd.read_excel('backtester/output/backtest_20260224_233337.xlsx')
df2 = pd.read_excel('backtester/output/backtest_20260225_001102.xlsx')

print(f'File 1: {len(df1)} rows')
print(f'File 2: {len(df2)} rows')

print('\n=== Signal ID Analysis ===')
print(f'File 1 unique Signal IDs: {df1["Signal ID"].nunique()}')
print(f'File 2 unique Signal IDs: {df2["Signal ID"].nunique()}')

print('\nFile 1 first 10 Signal IDs:')
for sid in df1['Signal ID'].head(10).tolist():
    print(f'  {sid}')

print('\nFile 2 first 10 Signal IDs:')
for sid in df2['Signal ID'].head(10).tolist():
    print(f'  {sid}')

# Check overlap
ids1 = set(df1['Signal ID'].unique())
ids2 = set(df2['Signal ID'].unique())
overlap = ids1 & ids2
print(f'\nOverlap: {len(overlap)} common Signal IDs between files')

# Combined
combined = pd.concat([df1, df2], ignore_index=True)
print(f'\nCombined: {len(combined)} rows')
print(f'Combined unique Signal IDs: {combined["Signal ID"].nunique()}')
