# -*- coding: utf-8 -*-
"""
ManipBackTester - Entry point.

Backtester for BinanceFriend signals.

Usage:
    python -m backtester.main
    python -m backtester.main --signals logs/signals.jsonl
    python -m backtester.main --clear-cache
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.config import BacktestConfig
from backtester.log_parser import LogParser
from backtester.data_loader import BinanceDataLoader
from backtester.position_simulator import PositionSimulator
from backtester.report_generator import ReportGenerator


def print_banner():
    """Print banner."""
    print("""
============================================================
  ManipBackTester v1.0
  BinanceFriend Signal Backtester
============================================================
    """, flush=True)


def parse_args():
    """Parse arguments."""
    parser = argparse.ArgumentParser(
        description="ManipBackTester - Backtest BinanceFriend signals",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--signals",
        type=str,
        default="logs/signals.jsonl",
        help="Path to signals JSONL file (default: logs/signals.jsonl)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output XLSX path (default: backtester/output/backtest_YYYYMMDD_HHMMSS.xlsx)"
    )

    parser.add_argument(
        "--interval",
        type=str,
        default="1m",
        choices=["1m", "3m", "5m", "15m"],
        help="Kline interval for precision (default: 1m)"
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear klines cache before running"
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output"
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    if not args.quiet:
        print_banner()

    # Config
    config = BacktestConfig(
        signals_file=Path(args.signals),
        kline_interval=args.interval,
        verbose=not args.quiet
    )

    # Clear cache if needed
    if args.clear_cache:
        with BinanceDataLoader(config) as loader:
            count = loader.clear_cache()
        print(f"Cleared {count} cached files", flush=True)
        return

    print(f"\n[1/4] Parsing signals from {config.signals_file}...", flush=True)

    # 1. Parse signals
    parser = LogParser(config)
    signals = parser.parse_all_signals()

    if not signals:
        print("\n[ERROR] No signals found!", flush=True)
        print(f"   Expected file: {config.signals_file}", flush=True)
        print("\n   Make sure BinanceFriend has generated trade signals.", flush=True)
        print("   Signals are logged to logs/signals.jsonl when trades are triggered.", flush=True)
        return

    parser.print_summary()

    # Get unique symbols and time range
    symbols = parser.get_unique_symbols()
    start_time, end_time = parser.get_time_range()

    print(f"\n[2/4] Loading historical klines from Binance Futures...", flush=True)
    print(f"   Symbols: {len(symbols)}", flush=True)
    print(f"   Interval: {config.kline_interval}", flush=True)

    # 2. Load historical data
    with BinanceDataLoader(config) as loader:
        all_klines = loader.load_all_symbols(symbols, start_time, end_time)

    if not all_klines:
        print("\n[ERROR] Failed to load klines!", flush=True)
        return

    print(f"\n[3/4] Running backtest simulation...", flush=True)

    # 3. Simulate each signal
    simulator = PositionSimulator(config)
    results = []

    for i, signal in enumerate(signals, 1):
        klines = all_klines.get(signal.symbol, [])

        if not klines:
            if config.verbose:
                print(f"   [{i}/{len(signals)}] {signal.symbol}: No klines, skipping", flush=True)
            continue

        result = simulator.simulate(signal, klines)
        results.append(result)

        if config.verbose:
            status = "+" if result.entry_filled else "-"
            pnl = f"{result.net_pnl_percent:+.2f}%" if result.entry_filled else "N/A"
            print(f"   [{i}/{len(signals)}] {status} {signal.symbol} {signal.direction.value}: {pnl}", flush=True)

    print(f"\n[4/4] Generating report...", flush=True)

    # 4. Generate report
    output_path = Path(args.output) if args.output else None
    report = ReportGenerator(config)
    summary = report.generate(results, output_path)

    print("\n[OK] Backtest complete!", flush=True)


def run():
    """Wrapper for running."""
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user", flush=True)
    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run()
