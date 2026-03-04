# -*- coding: utf-8 -*-
"""Test all 5 strategies with honest backtesting (no look-ahead bias)."""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import datetime, timezone
from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig, list_strategies
from strategy_runner import StrategyRunner

print('='*80)
print('HONEST BACKTEST - ALL 5 STRATEGIES (NO LOOK-AHEAD BIAS)')
print('='*80)

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

print(f'Period: {start.date()} to {end.date()}')
print(f'Symbols: {len(symbols)} altcoins')
print()
print('Loading data from cache...')
history = downloader.download_with_coinalyze_backfill(symbols, start, end)
print()

# Test all strategies
strategies_to_test = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

results = []
for strat_name in strategies_to_test:
    print(f'Testing {strat_name}...', end=' ', flush=True)
    runner = StrategyRunner(strategy_name=strat_name, output_dir='output')
    signals = runner.generate_signals(history, symbols)
    result = runner.backtest_signals(signals, history, max_hold_days=14)

    results.append({
        'name': strat_name,
        'signals': result.total_signals,
        'trades': result.total_trades,
        'win_rate': result.win_rate,
        'total_pnl': result.total_pnl,
        'long_pnl': result.long_pnl,
        'short_pnl': result.short_pnl,
        'wins': result.wins,
        'losses': result.losses,
        'timeouts': result.timeouts,
    })
    print(f'{result.total_signals} signals, {result.total_pnl:+.1f}% PnL')

print()
print('='*100)
print('FINAL RESULTS - ALL STRATEGIES (HONEST, NO LOOK-AHEAD BIAS)')
print('='*100)
print(f'{"Strategy":<16} {"Signals":>8} {"WinRate":>8} {"TotalPnL":>10} {"LongPnL":>10} {"ShortPnL":>10} {"W/L/T":>12}')
print('-'*100)

for r in sorted(results, key=lambda x: x['total_pnl'], reverse=True):
    wlt = f"{r['wins']}/{r['losses']}/{r['timeouts']}"
    status = 'PROFIT' if r['total_pnl'] > 0 else 'LOSS'
    print(f"{r['name']:<16} {r['signals']:>8} {r['win_rate']:>7.1f}% {r['total_pnl']:>+9.1f}% {r['long_pnl']:>+9.1f}% {r['short_pnl']:>+9.1f}% {wlt:>12}  [{status}]")

print('-'*100)

profitable = [r for r in results if r['total_pnl'] > 0]
print(f'Profitable strategies: {len(profitable)}/{len(results)}')
if profitable:
    best = max(profitable, key=lambda x: x['total_pnl'])
    print(f'BEST: {best["name"]} with {best["total_pnl"]:+.1f}% PnL')
