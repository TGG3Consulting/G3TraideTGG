# -*- coding: utf-8 -*-
"""Deep analysis of backtest results - fast version using pandas."""
import pandas as pd
import numpy as np

FILE = r"G:\BinanceFriend\backtester\output\backtest_signals_part2_GrossPnL-377_WR+36_10020_5643.xlsx"

print("Loading data...")
df = pd.read_excel(FILE, sheet_name="Backtest Results")
print(f"Loaded {len(df)} rows")

# Rename columns for easier access (remove special chars)
df.columns = [str(c).replace(' ', '_').replace('%', 'pct').replace('/', '_') for c in df.columns]

print("=" * 70)
print("ГЛУБОКИЙ АНАЛИЗ БЭКТЕСТА")
print("=" * 70)

# Filter filled only
filled = df[df['Filled'] == 'YES'].copy()
not_filled = df[df['Filled'] == 'NO']

print(f"\n[1] ОБЩАЯ СТАТИСТИКА")
print(f"    Всего сигналов: {len(df)}")
print(f"    Исполнено: {len(filled)} ({len(filled)/len(df)*100:.1f}%)")
print(f"    Не исполнено: {len(not_filled)} ({len(not_filled)/len(df)*100:.1f}%)")

# Win/Loss
wins = filled[filled['Net_PnL'] > 0]
losses = filled[filled['Net_PnL'] < 0]
print(f"    Прибыльных: {len(wins)} ({len(wins)/len(filled)*100:.1f}%)")
print(f"    Убыточных: {len(losses)} ({len(losses)/len(filled)*100:.1f}%)")

# PnL stats
print(f"    Сумма PnL: {filled['Net_PnL'].sum():.4f}")
print(f"    Средний PnL: {filled['Net_PnL'].mean():.6f}")
print(f"    Медиана PnL: {filled['Net_PnL'].median():.6f}")

# Exit reasons
print(f"\n[2] ПРИЧИНЫ ВЫХОДА")
exit_group = filled.groupby('Exit_Reason').agg(
    count=('Net_PnL', 'count'),
    pnl=('Net_PnL', 'sum')
).sort_values('pnl')
for reason, row in exit_group.iterrows():
    print(f"    {reason:20s}: {row['count']:5.0f} сделок, PnL: {row['pnl']:+.4f}")

# By Symbol
print(f"\n[3] АНАЛИЗ ПО МОНЕТАМ")
sym_group = filled.groupby('Symbol').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum'),
    avg=('Net_PnL', 'mean')
).sort_values('pnl')
for sym, row in sym_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    {sym:12s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+10.4f}, Avg: {row['avg']:+.6f}")

# By Probability
print(f"\n[4] АНАЛИЗ ПО ВЕРОЯТНОСТИ (Probability)")
prob_group = filled.groupby('Prob').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_index()
for prob, row in prob_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Prob {prob:3.0f}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# By Accumulation Score
print(f"\n[5] АНАЛИЗ ПО ACCUMULATION SCORE")
filled['acc_bucket'] = pd.cut(filled['acc_total'], bins=[45, 50, 55, 60, 65, 70, 100], right=False)
acc_group = filled.groupby('acc_bucket', observed=True).agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
)
for bucket, row in acc_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Score {bucket}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# By Confidence
print(f"\n[6] АНАЛИЗ ПО УВЕРЕННОСТИ (Confidence)")
conf_group = filled.groupby('Conf').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_values('pnl')
for conf, row in conf_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    {conf:15s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# By Hour
print(f"\n[7] АНАЛИЗ ПО ЧАСАМ (UTC)")
hour_group = filled.groupby('signal_hour').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
)
best_hours = hour_group.nlargest(5, 'pnl')
worst_hours = hour_group.nsmallest(5, 'pnl')
print("    Лучшие часы:")
for h, row in best_hours.iterrows():
    wr = row['wins']/row['count']*100
    print(f"      {h:02.0f}:00 - {row['count']:4.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")
print("    Худшие часы:")
for h, row in worst_hours.iterrows():
    wr = row['wins']/row['count']*100
    print(f"      {h:02.0f}:00 - {row['count']:4.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# By Day of Week
print(f"\n[8] АНАЛИЗ ПО ДНЯМ НЕДЕЛИ")
dow_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
dow_group = filled.groupby('signal_day_of_week').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_index()
for dow, row in dow_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    {dow_names.get(int(dow), dow)}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# TP progression
print(f"\n[9] ПРОГРЕССИЯ TP")
tp1_hit = filled['TP1_Hit'] == 'YES'
tp2_hit = filled['TP2_Hit'] == 'YES'
tp3_hit = filled['TP3_Hit'] == 'YES'

no_tp = (~tp1_hit).sum()
tp1_only = (tp1_hit & ~tp2_hit).sum()
tp2_only = (tp2_hit & ~tp3_hit).sum()
tp3_full = tp3_hit.sum()

print(f"    Без TP (сразу SL/Timeout): {no_tp} ({no_tp/len(filled)*100:.1f}%)")
print(f"    Только TP1: {tp1_only} ({tp1_only/len(filled)*100:.1f}%)")
print(f"    До TP2: {tp2_only} ({tp2_only/len(filled)*100:.1f}%)")
print(f"    Полный TP3: {tp3_full} ({tp3_full/len(filled)*100:.1f}%)")

# Trigger analysis
print(f"\n[10] АНАЛИЗ ПО ТРИГГЕРАМ")
filled['trigger_clean'] = filled['trigger_type'].fillna('NO_TRIGGER')
trig_group = filled.groupby('trigger_clean').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_values('pnl')
for trig, row in trig_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    {trig:25s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# Hold time analysis
print(f"\n[11] АНАЛИЗ ВРЕМЕНИ УДЕРЖАНИЯ")
print(f"    Среднее время: {filled['Hours'].mean():.1f} часов")
print(f"    Медиана: {filled['Hours'].median():.1f} часов")
print(f"    Среднее WINS: {wins['Hours'].mean():.1f} ч (медиана: {wins['Hours'].median():.1f} ч)")
print(f"    Среднее LOSSES: {losses['Hours'].mean():.1f} ч (медиана: {losses['Hours'].median():.1f} ч)")

# SL % analysis
print(f"\n[12] АНАЛИЗ STOP LOSS %")
filled['sl_bucket'] = filled['SL_pct'].round()
sl_group = filled.groupby('sl_bucket').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_index()
for sl, row in sl_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    SL {sl:.0f}%: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# Funding analysis
print(f"\n[13] АНАЛИЗ ПО ФАНДИНГУ")
bins = [-1, -0.1, -0.01, 0.01, 0.1, 1]
labels = ['<-0.1%', '-0.1 to -0.01%', '-0.01 to 0.01%', '0.01 to 0.1%', '>0.1%']
filled['funding_bucket'] = pd.cut(filled['futures_funding_rate_pct'], bins=bins, labels=labels)
fund_group = filled.groupby('funding_bucket', observed=True).agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
)
for bucket, row in fund_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Funding {bucket:20s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# acc_crowd_bearish analysis
print(f"\n[14] АНАЛИЗ acc_crowd_bearish (контрарный сигнал)")
crowd_group = filled.groupby('acc_crowd_bearish').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_index()
for crowd, row in crowd_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Crowd={crowd:2.0f}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# Volume spike analysis
print(f"\n[15] АНАЛИЗ VOLUME SPIKE")
vol_bins = [0, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 10]
vol_labels = ['0-0.5', '0.5-0.8', '0.8-1.0', '1.0-1.2', '1.2-1.5', '1.5-2.0', '2.0+']
filled['vol_bucket'] = pd.cut(filled['spot_volume_spike_ratio'], bins=vol_bins, labels=vol_labels)
vol_group = filled.groupby('vol_bucket', observed=True).agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
)
for bucket, row in vol_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Spike {bucket:10s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# acc_coordinated_buying analysis
print(f"\n[16] АНАЛИЗ acc_coordinated_buying")
coord_group = filled.groupby('acc_coordinated_buying').agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
).sort_index()
for coord, row in coord_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    Coord={coord:2.0f}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

# Risk/Reward analysis
print(f"\n[17] АНАЛИЗ RISK/REWARD")
rr_bins = [0, 2.0, 2.5, 3.0, 3.5, 4.0, 10]
rr_labels = ['<2.0', '2.0-2.5', '2.5-3.0', '3.0-3.5', '3.5-4.0', '4.0+']
filled['rr_bucket'] = pd.cut(filled['R_R'], bins=rr_bins, labels=rr_labels)
rr_group = filled.groupby('rr_bucket', observed=True).agg(
    count=('Net_PnL', 'count'),
    wins=('Net_PnL', lambda x: (x > 0).sum()),
    pnl=('Net_PnL', 'sum')
)
for bucket, row in rr_group.iterrows():
    wr = row['wins']/row['count']*100
    print(f"    R/R {bucket:10s}: {row['count']:5.0f} сделок, WR: {wr:5.1f}%, PnL: {row['pnl']:+.4f}")

print("\n" + "=" * 70)
