# -*- coding: utf-8 -*-
"""Check if signal existed on March 22 00:00 UTC."""
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, r'G:\BinanceFriend\GenerateHistorySignals')

from datetime import datetime, timezone, timedelta
from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig
from strategy_runner import StrategyRunner
from config import AppConfig

# Download data
config = AppConfig()
downloader = HybridHistoryDownloader(
    cache_dir=config.cache_dir,
    coinalyze_api_key=config.coinalyze_api_key or None
)

symbols = ['BTCUSDT', 'XRPUSDT']

# End at March 22 00:00 UTC (exactly at candle close)
end = datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)
start = end - timedelta(days=30)

print(f"Checking signals at: {end}")
print(f"This means: candle of March 21 CLOSED, signal for March 22 OPEN")
print()

history = downloader.download_with_coinalyze_backfill(symbols, start, end)

# Strategy config
strat_config = StrategyConfig(
    sl_pct=1.5,
    tp_pct=1.5,
    max_hold_days=14,
    lookback=7,
)

runner = StrategyRunner(
    strategy_name='momentum',
    config=strat_config,
    output_dir='output',
)

# Generate signals
signals = runner.generate_signals(history, symbols, dedup_days=3)

# Check March 22 signals
march22 = datetime(2026, 3, 22, tzinfo=timezone.utc).date()
march22_signals = [s for s in signals if s.date.date() == march22]

print()
print("=" * 60)
print(f"SIGNALS FOR MARCH 22, 2026 (at 00:00 UTC):")
print("=" * 60)

if march22_signals:
    for sig in march22_signals:
        print(f"  {sig.symbol} {sig.direction}")
        print(f"    Entry: {sig.entry:.6f}")
        print(f"    SL: {sig.stop_loss:.6f}")
        print(f"    TP: {sig.take_profit:.6f}")
        print()
else:
    print("  NO SIGNALS")
    print()

# Show raw data for debugging
print()
print("=" * 60)
print("RAW 7-DAY PRICE CHANGE (as of March 21 close):")
print("=" * 60)

for sym in symbols:
    if sym in history:
        daily = runner.aggregate_to_daily(history[sym].klines)
        if len(daily) >= 8:
            # March 21 close
            candle_21 = daily[-1]  # Last candle = March 21
            candle_14 = daily[-8]  # 7 days before = March 14

            pct_change = (candle_21.close - candle_14.close) / candle_14.close * 100

            print(f"{sym}:")
            print(f"  March 21 close: {candle_21.close:.4f}")
            print(f"  March 14 close: {candle_14.close:.4f}")
            print(f"  7-day change: {pct_change:+.2f}%")

            if pct_change >= 5:
                print(f"  -> SHOULD BE LONG SIGNAL")
            elif pct_change <= -5:
                print(f"  -> SHOULD BE SHORT SIGNAL")
            else:
                print(f"  -> NO SIGNAL (|change| < 5%)")
            print()

# Also check last few days of signals
print()
print("=" * 60)
print("LAST 7 DAYS SIGNALS:")
print("=" * 60)
cutoff = end - timedelta(days=7)
recent = [s for s in signals if s.date >= cutoff]
for sig in sorted(recent, key=lambda x: x.date):
    print(f"  {sig.date.strftime('%Y-%m-%d')} | {sig.symbol:10} | {sig.direction:5}")
