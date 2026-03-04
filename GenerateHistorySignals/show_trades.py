# -*- coding: utf-8 -*-
"""Show 20 real trade examples."""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import datetime, timezone
from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig
from strategy_runner import StrategyRunner

print('='*80)
print('20 REAL TRADES - LS Fade Strategy (SL=4%, TP=10%)')
print('='*80)

config = StrategyConfig(
    sl_pct=4,
    tp_pct=10,
    max_hold_days=14,
    lookback=7,
    params={'ls_extreme': 0.65}
)

start = datetime(2024, 8, 1, tzinfo=timezone.utc)
end = datetime(2025, 1, 31, tzinfo=timezone.utc)
symbols = [
    'ZECUSDT', 'SUIUSDT', 'ALICEUSDT', 'TAOUSDT', 'ENAUSDT',
    'UNIUSDT', 'ATOMUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
    'INJUSDT', 'TIAUSDT', 'SEIUSDT', 'JUPUSDT', 'STRKUSDT',
    'WLDUSDT', 'MKRUSDT', 'LDOUSDT'
]

downloader = HybridHistoryDownloader(
    cache_dir='cache',
    coinalyze_api_key='adb282f9-7e9e-4b6c-a669-b01c0304d506'
)

print('Loading data (from cache)...\n')
history = downloader.download_with_coinalyze_backfill(symbols, start, end)

runner = StrategyRunner(strategy_name='ls_fade', config=config, output_dir='output')
signals = runner.generate_signals(history, symbols)
result = runner.backtest_signals(signals, history, max_hold_days=14)

trades = result.trades

# Get mix: 10 SHORT WINs, 5 LONG trades, 5 LOSSes from different coins
short_wins = [t for t in trades if t.result == 'WIN' and t.signal.direction == 'SHORT']
long_trades = [t for t in trades if t.signal.direction == 'LONG']
losses = [t for t in trades if t.result == 'LOSS']

# Take from different symbols
examples = []
seen_symbols_sw = set()
for t in short_wins:
    if t.signal.symbol not in seen_symbols_sw:
        examples.append(t)
        seen_symbols_sw.add(t.signal.symbol)
    if len(examples) >= 10:
        break

seen_symbols_long = set()
for t in long_trades:
    if t.signal.symbol not in seen_symbols_long:
        examples.append(t)
        seen_symbols_long.add(t.signal.symbol)
    if len(examples) >= 15:
        break

seen_symbols_loss = set()
for t in losses:
    if t.signal.symbol not in seen_symbols_loss:
        examples.append(t)
        seen_symbols_loss.add(t.signal.symbol)
    if len(examples) >= 20:
        break

print()
print('='*80)
print(f'{"#":<3} {"":2} {"Symbol":<10} {"Dir":<5} | {"Entry Date":<10} {"Price":<12} | {"L/S Ratio":<10} | {"Exit":<10} {"Days":<4} | {"PnL":<8} {"Result"}')
print('-'*80)

for i, trade in enumerate(examples[:20], 1):
    sig = trade.signal
    meta = sig.metadata
    long_pct = meta.get('long_pct', 0.5) * 100
    short_pct = meta.get('short_pct', 0.5) * 100

    icon = 'WIN' if trade.result == 'WIN' else ('LOSS' if trade.result == 'LOSS' else 'TIME')
    emoji = '+' if trade.result == 'WIN' else '-'

    ls_str = f"{long_pct:.0f}L/{short_pct:.0f}S"

    print(f'{i:2}. {emoji:2} {sig.symbol:<10} {sig.direction:<5} | {sig.date.strftime("%Y-%m-%d"):<10} ${sig.entry:<11.4f} | {ls_str:<10} | {trade.exit_date.strftime("%Y-%m-%d"):<10} {trade.hold_days:<4} | {trade.pnl_pct:+6.2f}%  {icon}')

print('-'*80)
print()

# Summary stats
wins_count = sum(1 for t in examples if t.result == 'WIN')
losses_count = sum(1 for t in examples if t.result == 'LOSS')
timeouts_count = sum(1 for t in examples if t.result == 'TIMEOUT')
total_pnl = sum(t.pnl_pct for t in examples)
short_pnl = sum(t.pnl_pct for t in examples if t.signal.direction == 'SHORT')
long_pnl = sum(t.pnl_pct for t in examples if t.signal.direction == 'LONG')

print('='*80)
print('SUMMARY OF 20 TRADES')
print('='*80)
print(f'  Wins:       {wins_count}')
print(f'  Losses:     {losses_count}')
print(f'  Timeouts:   {timeouts_count}')
print(f'  Total PnL:  {total_pnl:+.1f}%')
print(f'  SHORT PnL:  {short_pnl:+.1f}%')
print(f'  LONG PnL:   {long_pnl:+.1f}%')
print()
print('Strategy: LS Fade - trade AGAINST crowd when >65% one direction')
print('  - Crowd 65%+ LONG  -> SHORT (fade bullish euphoria)')
print('  - Crowd 65%+ SHORT -> LONG  (fade bearish panic)')
print('='*80)
