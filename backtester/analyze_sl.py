# -*- coding: utf-8 -*-
"""Анализ убыточных сделок по SL %"""

import pandas as pd
import glob
import os

os.chdir(r'G:\BinanceFriend')

files = glob.glob('backtester/output/*.xlsx')

all_losing = []

for f in files:
    try:
        df = pd.read_excel(f)
        if 'Exit Reason' in df.columns and 'SL %' in df.columns:
            losing = df[df['Exit Reason'] == 'STOP_LOSS'].copy()
            if len(losing) > 0:
                losing['file'] = os.path.basename(f)
                all_losing.append(losing[['SL %', 'file']])
    except Exception as e:
        print(f'Error: {e}')

if all_losing:
    combined = pd.concat(all_losing, ignore_index=True)
    print(f'Всего убыточных сделок (SL): {len(combined)}')
    print()

    sl_col = combined['SL %']

    thresholds = [3, 4, 5, 6, 7, 8]

    print('РАСПРЕДЕЛЕНИЕ УБЫТОЧНЫХ СДЕЛОК ПО SL %:')
    print('=' * 50)

    for t in thresholds:
        count = len(combined[sl_col <= t])
        pct = count / len(combined) * 100
        print(f'SL <= {t}%: {count:,} сделок ({pct:.1f}%)')

    print()
    print('ДЕТАЛЬНОЕ РАСПРЕДЕЛЕНИЕ:')
    print('=' * 50)

    bins = [0, 2, 3, 4, 5, 6, 7, 8, 100]
    labels = ['0-2%', '2-3%', '3-4%', '4-5%', '5-6%', '6-7%', '7-8%', '8%+']
    combined['SL_bin'] = pd.cut(sl_col, bins=bins, labels=labels, right=True)

    dist = combined['SL_bin'].value_counts().sort_index()
    for bin_name, count in dist.items():
        pct = count / len(combined) * 100
        print(f'{bin_name}: {count:,} ({pct:.1f}%)')

    print()
    print('СТАТИСТИКА SL %:')
    print(f'Min: {sl_col.min():.2f}%')
    print(f'Max: {sl_col.max():.2f}%')
    print(f'Mean: {sl_col.mean():.2f}%')
    print(f'Median: {sl_col.median():.2f}%')
else:
    print('Нет данных')
