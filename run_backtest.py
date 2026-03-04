# -*- coding: utf-8 -*-
import sys
import os

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
os.environ['PYTHONUNBUFFERED'] = '1'

print("1. Start", flush=True)

print("2. Import requests...", flush=True)
import requests
print("3. requests OK", flush=True)

print("4. Import xlsxwriter...", flush=True)
import xlsxwriter
print("5. xlsxwriter OK", flush=True)

print("6. Import argparse...", flush=True)
import argparse
from pathlib import Path
from datetime import datetime
print("7. stdlib OK", flush=True)

print("8. Setup path...", flush=True)
sys.path.insert(0, str(Path(__file__).parent))
print("9. Path OK", flush=True)

print("10. Import backtester.config...", flush=True)
from backtester.config import BacktestConfig
print("11. config OK", flush=True)

print("12. Import backtester.log_parser...", flush=True)
from backtester.log_parser import LogParser
print("13. log_parser OK", flush=True)

print("14. Import backtester.data_loader...", flush=True)
from backtester.data_loader import BinanceDataLoader
print("15. data_loader OK", flush=True)

print("16. Import backtester.position_simulator...", flush=True)
from backtester.position_simulator import PositionSimulator
print("17. position_simulator OK", flush=True)

print("18. Import backtester.report_generator...", flush=True)
from backtester.report_generator import ReportGenerator
print("19. report_generator OK", flush=True)

print("20. All imports done!", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=str, required=True)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    signals_path = Path(args.signals)
    if not signals_path.exists():
        print(f"[ERROR] File not found: {signals_path}", flush=True)
        sys.exit(1)

    print(f"\n=== BACKTESTER ===", flush=True)
    print(f"Signals: {signals_path}", flush=True)

    config = BacktestConfig(signals_file=signals_path, verbose=True)

    print("\n[1/4] Parsing signals...", flush=True)
    log_parser = LogParser(config)
    signals = log_parser.parse_all_signals()
    if not signals:
        print("[ERROR] No signals!", flush=True)
        sys.exit(1)
    print(f"Found {len(signals)} signals", flush=True)
    log_parser.print_summary()

    symbols = log_parser.get_unique_symbols()
    start_time, end_time = log_parser.get_time_range()

    print(f"\n[2/4] Loading klines for {len(symbols)} symbols...", flush=True)
    with BinanceDataLoader(config) as loader:
        all_klines = loader.load_all_symbols(symbols, start_time, end_time)
    print(f"Loaded {len(all_klines)} symbols", flush=True)

    print(f"\n[3/4] Simulating {len(signals)} signals...", flush=True)
    simulator = PositionSimulator(config)
    results = []
    for i, signal in enumerate(signals, 1):
        klines = all_klines.get(signal.symbol, [])
        if klines:
            result = simulator.simulate(signal, klines)
            results.append(result)
            if i <= 5 or i % 100 == 0:
                pnl = f"{float(result.net_pnl_percent):+.2f}%" if result.entry_filled else "N/A"
                print(f"  [{i}] {signal.symbol}: {pnl}", flush=True)

    print(f"\n[4/4] Generating XLSX...", flush=True)
    report = ReportGenerator(config)
    report.generate(results, Path(args.output) if args.output else None)

    print("\n[DONE]", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
