# -*- coding: utf-8 -*-
"""
GenerateHistorySignals v2.0 - Main entry point.

Interactive CLI for generating historical trading signals.
Supports both legacy AccumulationDetector and modular strategy-based generation.

Usage:
    python main.py                          # Interactive mode (legacy)
    python main.py --batch ...              # Batch mode (legacy)
    python main.py --strategy ls_fade ...   # Strategy mode (new)

Available strategies:
    ls_fade       - Fade crowd extremes (>65% one direction)
    momentum      - Trade with strong price momentum
    reversal      - Trade reversals after extreme price moves
    momentum_ls   - Momentum with L/S ratio confirmation
    mean_reversion - Mean reversion on extreme moves
"""

import gc
import signal
import sys
import time
import io
from datetime import datetime, timezone, timedelta
from typing import List, Optional

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from config import AppConfig
from data_downloader import BinanceHistoryDownloader, SymbolHistoryData
from hybrid_downloader import HybridHistoryDownloader
from state_builder import StateBuilder
from signal_runner import SignalRunner
from output_writer import OutputWriter


VERSION = "2.0"


# =============================================================================
# SIGNAL HANDLER (Ctrl+C)
# =============================================================================

_writer_instance: Optional[OutputWriter] = None


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    print("\n\n[!] Interrupted. Saving progress...")
    if _writer_instance:
        try:
            _writer_instance.close()
            print("[OK] Output files saved.")
        except Exception as e:
            print(f"[ERROR] Failed to save: {e}")
    sys.exit(1)


signal.signal(signal.SIGINT, signal_handler)


# =============================================================================
# INPUT HELPERS
# =============================================================================

def input_date(prompt: str) -> datetime:
    """Get date input from user."""
    while True:
        try:
            date_str = input(prompt).strip()
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            print("  Invalid format. Use YYYY-MM-DD (e.g., 2024-01-01)")


def input_yes_no(prompt: str, default: bool = False) -> bool:
    """Get yes/no input from user."""
    suffix = " (Y/n)" if default else " (y/N)"
    while True:
        response = input(prompt + suffix + ": ").strip().lower()
        if response == "":
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("  Please enter y or n")


def input_int(prompt: str, default: int) -> int:
    """Get integer input from user."""
    while True:
        try:
            response = input(f"{prompt} (default {default}): ").strip()
            if response == "":
                return default
            return int(response)
        except ValueError:
            print("  Please enter a number")


def input_symbols(prompt: str) -> List[str]:
    """Get comma-separated symbol list from user."""
    while True:
        response = input(prompt).strip().upper()
        if not response:
            print("  Please enter at least one symbol")
            continue
        symbols = [s.strip() for s in response.split(",") if s.strip()]
        if symbols:
            return symbols
        print("  Invalid format. Use: BTCUSDT,ETHUSDT,SOLUSDT")


# =============================================================================
# MAIN INTERACTIVE FLOW
# =============================================================================

def interactive_mode() -> int:
    """Run interactive mode."""
    global _writer_instance

    print("\n", flush=True)
    print("═" * 50, flush=True)
    print(f"  GenerateHistorySignals v{VERSION}", flush=True)
    print("═" * 50, flush=True)
    print(flush=True)

    # Get inputs
    start_date = input_date("Enter start date (YYYY-MM-DD): ")
    end_date = input_date("Enter end date (YYYY-MM-DD): ")

    # Set end date to end of day
    end_date = end_date.replace(hour=23, minute=59, second=59)

    # Validate date range
    if end_date <= start_date:
        print("\n[ERROR] End date must be after start date")
        return 1

    # Get symbols
    use_custom = input_yes_no("\nUse custom symbol list?", default=False)

    if use_custom:
        symbols = input_symbols("Enter symbols separated by comma: ")
        top_n = len(symbols)
    else:
        top_n = input_int("\nHow many top symbols?", default=500)
        symbols = None  # Will fetch dynamically

    # Check if we need Coinalyze (data older than 30 days)
    now = datetime.now(timezone.utc)
    days_ago = (now - start_date).days
    needs_coinalyze = days_ago > 25

    if needs_coinalyze:
        import os
        has_coinalyze_key = bool(os.environ.get("COINALYZE_API_KEY"))
        if not has_coinalyze_key:
            print("\n[!] Data older than 30 days requires Coinalyze API for OI/LS history")
            print("    Set COINALYZE_API_KEY env var (free key at https://coinalyze.net/)")
            print("    Without it, OI and L/S Ratio will be incomplete.\n")

    # Calculate stats
    duration = end_date - start_date
    days = duration.days + 1
    estimated_signals_low = top_n * days * 10
    estimated_signals_high = top_n * days * 50

    # Show summary
    print("\n", flush=True)
    print("═" * 50, flush=True)
    print(f"  GenerateHistorySignals v{VERSION}", flush=True)
    print("═" * 50, flush=True)
    print(f"  Period: {start_date.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')} ({days} days)", flush=True)
    print(f"  Symbols: {top_n}" + (f" ({', '.join(symbols[:3])}...)" if symbols and len(symbols) > 3 else ""), flush=True)
    print(f"  Estimated signals: ~{estimated_signals_low:,}-{estimated_signals_high:,}", flush=True)
    print(f"  Data source: {'Binance + Coinalyze (hybrid)' if needs_coinalyze else 'Binance only'}", flush=True)
    print(f"  Output dir: output/", flush=True)
    print("═" * 50, flush=True)

    # Confirm
    if not input_yes_no("\nConfirm?", default=True):
        print("\nCancelled.")
        return 0

    print()

    # Create config
    config = AppConfig()

    # ==========================================================================
    # STEP 1: Download data
    # ==========================================================================
    print("═" * 50, flush=True)
    print("[1/3] Downloading historical data...", flush=True)
    print("═" * 50, flush=True)
    print(flush=True)

    download_start = time.time()

    # Use Hybrid downloader (Binance + Coinalyze)
    downloader = HybridHistoryDownloader(
        cache_dir=config.cache_dir,
        coinalyze_api_key=config.coinalyze_api_key or None
    )

    # Get symbols if not provided
    if symbols is None:
        print(f"Fetching top {top_n} symbols by volume...", flush=True)
        symbols = downloader.get_active_symbols(top_n=top_n)
        print(f"Selected: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}", flush=True)
        print(flush=True)

    # Download all data using hybrid approach
    # - Recent data (< 30 days): Binance with 5m granularity
    # - Old data (> 30 days): Coinalyze with daily granularity
    history = downloader.download_with_coinalyze_backfill(
        symbols=symbols,
        start_time=start_date,
        end_time=end_date,
    )

    download_time = time.time() - download_start
    download_mins = int(download_time // 60)
    download_secs = int(download_time % 60)

    print(flush=True)
    print(f"Downloaded {len(history)} symbols in {download_mins}m {download_secs}s", flush=True)
    print(flush=True)

    # ==========================================================================
    # STEP 2: Generate signals
    # ==========================================================================
    print("═" * 50, flush=True)
    print("[2/3] Generating signals...", flush=True)
    print("═" * 50, flush=True)
    print(flush=True)

    generate_start = time.time()

    # Build state builder
    builder = StateBuilder(history)

    # Create output writer
    writer = OutputWriter(
        output_dir=config.output_dir,
        max_signals_per_file=config.max_signals_per_file,
    )
    _writer_instance = writer  # For Ctrl+C handler

    # Create runner
    runner = SignalRunner(
        downloader_data=history,
        state_builder=builder,
        output_writer=writer,
        config=config,
    )

    # Run signal generation
    total_signals = runner.run(
        symbols=symbols,
        start_time=start_date,
        end_time=end_date,
    )

    generate_time = time.time() - generate_start
    generate_mins = int(generate_time // 60)
    generate_secs = int(generate_time % 60)

    print(flush=True)
    print(f"Generated {total_signals:,} signals in {generate_mins}m {generate_secs}s", flush=True)
    print(flush=True)

    # ==========================================================================
    # STEP 3: Summary
    # ==========================================================================
    print("═" * 50, flush=True)
    print("[3/3] Writing output...", flush=True)
    print("═" * 50, flush=True)
    print(flush=True)
    print(f"  Total signals: {writer.total_written:,}", flush=True)
    print(f"  Output: {writer.current_file_path}", flush=True)
    print(flush=True)
    print("Done!", flush=True)
    print(flush=True)

    _writer_instance = None
    return 0


# =============================================================================
# BATCH MODE (non-interactive)
# =============================================================================

def batch_mode(args) -> int:
    """Run in batch mode with command line arguments."""
    global _writer_instance

    import argparse

    parser = argparse.ArgumentParser(
        description="Generate historical trading signals (batch mode)"
    )
    parser.add_argument("--start", type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    parser.add_argument("--top", type=int, default=500, help="Top N symbols by volume")
    parser.add_argument("--output", type=str, default="output", help="Output directory")
    parser.add_argument("--min-score", type=int, default=45, help="Min accumulation score")
    parser.add_argument("--min-prob", type=int, default=45, help="Min probability")
    parser.add_argument("--min-rr", type=float, default=1.5, help="Min risk:reward")
    parser.add_argument("--chunk-size", type=int, default=20, help="Symbols per chunk (memory management)")

    parsed = parser.parse_args(args)

    # Parse dates
    try:
        start_date = datetime.strptime(parsed.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(parsed.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except ValueError as e:
        print(f"[ERROR] Invalid date format: {e}")
        return 1

    # Parse symbols
    if parsed.symbols:
        symbols = [s.strip().upper() for s in parsed.symbols.split(",") if s.strip()]
    else:
        symbols = None

    # Create config
    config = AppConfig(
        output_dir=parsed.output,
        min_accumulation_score=parsed.min_score,
        min_probability=parsed.min_prob,
        min_risk_reward=parsed.min_rr,
    )

    print(flush=True)
    print("═" * 50, flush=True)
    print(f"  GenerateHistorySignals v{VERSION} (batch mode)", flush=True)
    print("═" * 50, flush=True)
    print(f"  Period: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}", flush=True)
    print(f"  Symbols: {len(symbols) if symbols else parsed.top}", flush=True)
    print("═" * 50, flush=True)
    print(flush=True)

    # Download using hybrid approach
    print("[1/3] Downloading & generating signals...", flush=True)
    downloader = HybridHistoryDownloader(
        cache_dir=config.cache_dir,
        coinalyze_api_key=config.coinalyze_api_key or None
    )

    if symbols is None:
        symbols = downloader.get_active_symbols(top_n=parsed.top)

    # Process in chunks to avoid memory issues
    CHUNK_SIZE = parsed.chunk_size
    total_signals = 0

    writer = OutputWriter(output_dir=config.output_dir, max_signals_per_file=config.max_signals_per_file)
    _writer_instance = writer

    num_chunks = (len(symbols) + CHUNK_SIZE - 1) // CHUNK_SIZE

    for chunk_idx in range(num_chunks):
        chunk_start = chunk_idx * CHUNK_SIZE
        chunk_end = min(chunk_start + CHUNK_SIZE, len(symbols))
        chunk_symbols = symbols[chunk_start:chunk_end]

        print(f"\n{'='*50}", flush=True)
        print(f"CHUNK {chunk_idx + 1}/{num_chunks}: symbols {chunk_start + 1}-{chunk_end}", flush=True)
        print(f"{'='*50}", flush=True)

        # Download this chunk
        history = downloader.download_with_coinalyze_backfill(chunk_symbols, start_date, end_date)

        # Build state and generate signals
        builder = StateBuilder(history)
        runner = SignalRunner(
            downloader_data=history,
            state_builder=builder,
            output_writer=writer,
            config=config,
        )

        chunk_signals = runner.run(chunk_symbols, start_date, end_date)
        total_signals += chunk_signals

        print(f"\nChunk {chunk_idx + 1} complete: {chunk_signals:,} signals", flush=True)

        # Free memory
        del history
        del builder
        del runner
        gc.collect()

    # Close writer after all chunks
    writer.close()

    # Summary
    print(f"\n{'='*50}", flush=True)
    print(f"[DONE] Generated {total_signals:,} signals total", flush=True)
    print(f"  Output: {config.output_dir}/", flush=True)
    print(f"{'='*50}", flush=True)

    _writer_instance = None
    return 0


# =============================================================================
# STRATEGY MODE (new modular strategies)
# =============================================================================

def strategy_mode(args) -> int:
    """Run in strategy mode with modular strategies."""
    import argparse

    # Import strategy components
    from strategies import list_strategies, StrategyConfig
    from strategy_runner import StrategyRunner

    parser = argparse.ArgumentParser(
        description="Generate historical signals using modular strategies"
    )
    parser.add_argument("--strategy", type=str, required=True,
                        help="Strategy name (ls_fade, momentum, reversal, momentum_ls, mean_reversion)")
    parser.add_argument("--start", type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    parser.add_argument("--top", type=int, default=50, help="Top N symbols by volume")
    parser.add_argument("--output", type=str, default="output", help="Output directory")

    # Strategy parameters
    parser.add_argument("--sl", type=float, default=None, help="Stop Loss %% (default: strategy default)")
    parser.add_argument("--tp", type=float, default=None, help="Take Profit %% (default: strategy default)")
    parser.add_argument("--ls-extreme", type=float, default=0.65, help="L/S extreme threshold (for ls_fade)")
    parser.add_argument("--momentum-threshold", type=float, default=5.0, help="Momentum threshold %% (for momentum)")
    parser.add_argument("--max-hold", type=int, default=14, help="Max hold days")

    # Options
    parser.add_argument("--backtest", action="store_true", help="Run backtest after generating signals")
    parser.add_argument("--list", action="store_true", help="List available strategies and exit")

    parsed = parser.parse_args(args)

    # List strategies if requested
    if parsed.list:
        print("\nAvailable strategies:")
        print("=" * 50)
        for name, desc in list_strategies():
            print(f"  {name:15} - {desc}")
        print()
        return 0

    # Parse dates
    try:
        start_date = datetime.strptime(parsed.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(parsed.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except ValueError as e:
        print(f"[ERROR] Invalid date format: {e}")
        return 1

    # Parse symbols
    if parsed.symbols:
        symbols = [s.strip().upper() for s in parsed.symbols.split(",") if s.strip()]
    else:
        symbols = None

    # Build strategy config
    config_params = {
        "ls_extreme": parsed.ls_extreme,
        "momentum_threshold": parsed.momentum_threshold,
    }

    config = StrategyConfig(
        sl_pct=parsed.sl if parsed.sl else 4.0,  # Default from LS Fade optimization
        tp_pct=parsed.tp if parsed.tp else 10.0,
        max_hold_days=parsed.max_hold,
        lookback=7,
        params=config_params,
    )

    # Print header
    print(flush=True)
    print("═" * 60, flush=True)
    print(f"  GenerateHistorySignals v{VERSION} (Strategy Mode)", flush=True)
    print("═" * 60, flush=True)
    print(f"  Strategy:   {parsed.strategy}", flush=True)
    print(f"  Period:     {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}", flush=True)
    print(f"  Symbols:    {len(symbols) if symbols else parsed.top}", flush=True)
    print(f"  SL/TP:      {config.sl_pct}% / {config.tp_pct}%", flush=True)
    print(f"  Max Hold:   {config.max_hold_days} days", flush=True)
    print("═" * 60, flush=True)
    print(flush=True)

    # Download data
    print("[1/3] Downloading historical data...", flush=True)

    app_config = AppConfig()
    downloader = HybridHistoryDownloader(
        cache_dir=app_config.cache_dir,
        coinalyze_api_key=app_config.coinalyze_api_key or None
    )

    if symbols is None:
        symbols = downloader.get_active_symbols(top_n=parsed.top)

    history = downloader.download_with_coinalyze_backfill(symbols, start_date, end_date)

    # Create strategy runner
    print("\n[2/3] Generating signals...", flush=True)

    try:
        runner = StrategyRunner(
            strategy_name=parsed.strategy,
            config=config,
            output_dir=parsed.output,
        )
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1

    # Generate signals
    signals = runner.generate_signals(history, symbols)

    # Write output
    print("\n[3/3] Writing output...", flush=True)
    output_path = runner.write_signals_json(signals)

    # Backtest if requested
    if parsed.backtest and signals:
        print("\n[BACKTEST] Running backtest...", flush=True)
        result = runner.backtest_signals(signals, history, max_hold_days=config.max_hold_days)
        runner.print_backtest_summary(result)

    # Summary
    print(f"\n{'='*60}", flush=True)
    print(f"[DONE] Generated {len(signals):,} signals", flush=True)
    print(f"  Strategy: {parsed.strategy}", flush=True)
    print(f"  Output:   {output_path}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:
    """Main entry point."""
    # Check for strategy mode (new)
    if len(sys.argv) > 1 and sys.argv[1] == "--strategy":
        return strategy_mode(sys.argv[1:])

    # Check for --list (show strategies)
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        return strategy_mode(["--list", "--strategy", "ls_fade", "--start", "2024-01-01", "--end", "2024-01-01"])

    # Check if batch mode (legacy)
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        return batch_mode(sys.argv[2:])

    # Interactive mode (legacy)
    return interactive_mode()


if __name__ == "__main__":
    sys.exit(main())
