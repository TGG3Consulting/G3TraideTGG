# -*- coding: utf-8 -*-
"""Check today's signals for BTC and XRP using momentum strategy."""
import sys
import io

# Fix Windows console encoding
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
end = datetime.now(timezone.utc)
start = end - timedelta(days=30)

print(f"Downloading data for {symbols}...")
print(f"Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
print()

history = downloader.download_with_coinalyze_backfill(symbols, start, end)

# Strategy config matching backtest params
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

print(f"Total signals generated: {len(signals)}")
print()

# Show last 10 days of signals
cutoff = end - timedelta(days=10)
recent = [s for s in signals if s.date >= cutoff]

print("=== LAST 10 DAYS SIGNALS ===")
print()
for sig in sorted(recent, key=lambda x: x.date):
    print(f"{sig.date.strftime('%Y-%m-%d')} | {sig.symbol:10} | {sig.direction:5} | entry={sig.entry:.4f}")

print()

# Check today specifically
today = datetime.now(timezone.utc).date()
today_signals = [s for s in signals if s.date.date() == today]

print(f"=== TODAY ({today}) ===")
if today_signals:
    for sig in today_signals:
        print(f"{sig.symbol} {sig.direction} | entry={sig.entry:.6f} | SL={sig.stop_loss:.6f} | TP={sig.take_profit:.6f}")
else:
    print("NO SIGNALS TODAY")

# Also show raw price change for debugging
print()
print("=== RAW 7-DAY PRICE CHANGE (for signal generation) ===")
for sym in symbols:
    if sym in history:
        daily = runner.aggregate_to_daily(history[sym].klines)
        if len(daily) >= 8:
            today_candle = daily[-1]
            week_ago = daily[-8]
            pct_change = (today_candle.close - week_ago.close) / week_ago.close * 100
            print(f"{sym}: Close={today_candle.close:.4f}, 7d ago={week_ago.close:.4f}, Change={pct_change:+.2f}%")
            if abs(pct_change) >= 5:
                direction = "LONG" if pct_change >= 5 else "SHORT"
                print(f"  -> SIGNAL: {direction} (threshold >= 5%)")
            else:
                print(f"  -> NO SIGNAL (|{pct_change:.2f}%| < 5%)")
