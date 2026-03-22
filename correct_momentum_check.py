# -*- coding: utf-8 -*-
"""Correct momentum calculation for March 22 signal."""
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, r'G:\BinanceFriend\GenerateHistorySignals')

from datetime import datetime, timezone, timedelta
from hybrid_downloader import HybridHistoryDownloader
from strategy_runner import StrategyRunner
from config import AppConfig

config = AppConfig()
downloader = HybridHistoryDownloader(
    cache_dir=config.cache_dir,
    coinalyze_api_key=config.coinalyze_api_key or None
)

end = datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)
start = end - timedelta(days=30)

history = downloader.download_with_coinalyze_backfill(['BTCUSDT', 'XRPUSDT'], start, end)

print()
print("=" * 70)
print("CORRECT MOMENTUM CALCULATION FOR MARCH 22 SIGNAL")
print("=" * 70)
print()
print("Signal logic: Check March 21 CLOSE, entry at March 22 OPEN")
print("Threshold: >= +5% for LONG, <= -5% for SHORT")
print()

for sym in ['BTCUSDT', 'XRPUSDT']:
    daily = StrategyRunner.aggregate_to_daily(history[sym].klines)

    # Find March 21 and March 14 candles by date
    mar21 = None
    mar14 = None
    mar22 = None

    for c in daily:
        if c.date.date() == datetime(2026, 3, 21).date():
            mar21 = c
        if c.date.date() == datetime(2026, 3, 14).date():
            mar14 = c
        if c.date.date() == datetime(2026, 3, 22).date():
            mar22 = c

    print(f"{sym}:")
    print(f"  March 14 close: {mar14.close:.4f}" if mar14 else "  March 14: NOT FOUND")
    print(f"  March 21 close: {mar21.close:.4f}" if mar21 else "  March 21: NOT FOUND")
    print(f"  March 22 open:  {mar22.open:.4f}" if mar22 else "  March 22: NOT FOUND")

    if mar21 and mar14:
        pct_change = (mar21.close - mar14.close) / mar14.close * 100
        print(f"  7-day change:   {pct_change:+.2f}%")

        if pct_change >= 5:
            print(f"  RESULT: LONG SIGNAL (>= +5%)")
        elif pct_change <= -5:
            print(f"  RESULT: SHORT SIGNAL (<= -5%)")
        else:
            print(f"  RESULT: NO SIGNAL (|{pct_change:.2f}%| < 5%)")
    print()

print("=" * 70)
print("CONCLUSION:")
print("=" * 70)
