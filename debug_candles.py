# -*- coding: utf-8 -*-
"""Debug candle dates."""
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

# Download up to March 22 00:00 UTC
end = datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)
start = end - timedelta(days=30)

history = downloader.download_with_coinalyze_backfill(['BTCUSDT'], start, end)

daily = StrategyRunner.aggregate_to_daily(history['BTCUSDT'].klines)

print()
print("=" * 60)
print("LAST 10 CANDLES IN DATA:")
print("=" * 60)
for c in daily[-10:]:
    print(f"  {c.date.strftime('%Y-%m-%d')} | Open={c.open:.2f} | Close={c.close:.2f}")

print()
print(f"Total candles: {len(daily)}")
print(f"First: {daily[0].date.strftime('%Y-%m-%d')}")
print(f"Last:  {daily[-1].date.strftime('%Y-%m-%d')}")

print()
print("=" * 60)
print("SIGNAL GENERATION LOGIC:")
print("=" * 60)
print(f"Loop range: range(7, {len(daily) - 1}) = indices 7 to {len(daily) - 2}")
print(f"Last signal candle (i): index {len(daily) - 2} = {daily[-2].date.strftime('%Y-%m-%d')}")
print(f"Entry candle (i+1):     index {len(daily) - 1} = {daily[-1].date.strftime('%Y-%m-%d')}")
print()
print("For March 22 signal we need:")
print("  - Signal check: March 21 close (candle i)")
print("  - Entry price:  March 22 open (candle i+1)")
print()
if daily[-1].date.date() == datetime(2026, 3, 22).date():
    print("March 22 candle EXISTS -> signal CAN be generated")
elif daily[-1].date.date() == datetime(2026, 3, 21).date():
    print("Last candle is March 21 -> NO March 22 candle -> NO signal possible!")
else:
    print(f"Last candle is {daily[-1].date.date()} - unexpected")
