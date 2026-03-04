# -*- coding: utf-8 -*-
"""
Daily Strategy Backtester

Uses modular strategies from strategies/ module for backtesting.
Run parameter sweeps to find optimal strategy configurations.

Usage:
    python daily_strategy_test.py              # Run parameter sweep
    python daily_strategy_test.py --examples   # Show trade examples
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from datetime import datetime, timezone
from typing import List, Dict, Any

from hybrid_downloader import HybridHistoryDownloader
from strategies import (
    get_strategy,
    list_strategies,
    StrategyConfig,
    StrategyData,
    Signal,
    DailyCandle,
)
from strategy_runner import StrategyRunner, BacktestResult


def run_single_backtest(
    symbols: List[str],
    start: datetime,
    end: datetime,
    strategy_name: str,
    config: StrategyConfig,
) -> Dict[str, Any]:
    """Run a single backtest with given configuration."""

    downloader = HybridHistoryDownloader(
        cache_dir='cache',
        coinalyze_api_key='adb282f9-7e9e-4b6c-a669-b01c0304d506'
    )

    print(f"\nDownloading {len(symbols)} symbols...")
    history = downloader.download_with_coinalyze_backfill(symbols, start, end)

    runner = StrategyRunner(
        strategy_name=strategy_name,
        config=config,
        output_dir="output",
    )

    signals = runner.generate_signals(history, symbols)
    result = runner.backtest_signals(signals, history, max_hold_days=config.max_hold_days)

    # Calculate per-direction PnL
    long_trades = [t for t in result.trades if t.signal.direction == "LONG"]
    short_trades = [t for t in result.trades if t.signal.direction == "SHORT"]

    return {
        "total_signals": result.total_signals,
        "total_trades": result.total_trades,
        "wins": result.wins,
        "losses": result.losses,
        "timeouts": result.timeouts,
        "win_rate": result.win_rate,
        "total_pnl": result.total_pnl,
        "avg_pnl": result.avg_pnl,
        "long_pnl": result.long_pnl,
        "short_pnl": result.short_pnl,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "trades": result.trades,
    }


def run_parameter_sweep(symbols: List[str], start: datetime, end: datetime):
    """Test multiple strategy+parameter combinations to find profitable strategy."""

    # Test configurations for LS Fade strategy
    test_configs = [
        # SL optimization for LSFade_65 (TP fixed at 10%)
        {"name": "LSF65_SL3_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 3, "tp_pct": 10},
        {"name": "LSF65_SL4_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 4, "tp_pct": 10},
        {"name": "LSF65_SL5_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 5, "tp_pct": 10},
        {"name": "LSF65_SL6_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 6, "tp_pct": 10},
        {"name": "LSF65_SL7_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 7, "tp_pct": 10},
        {"name": "LSF65_SL8_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 8, "tp_pct": 10},
        {"name": "LSF65_SL10_TP10", "strategy": "ls_fade", "ls_extreme": 0.65, "sl_pct": 10, "tp_pct": 10},
    ]

    days = (end - start).days
    results = []

    for cfg in test_configs:
        config = StrategyConfig(
            sl_pct=cfg.get("sl_pct", 5),
            tp_pct=cfg.get("tp_pct", 10),
            max_hold_days=14,
            lookback=7,
            params={
                "ls_extreme": cfg.get("ls_extreme", 0.65),
                "momentum_threshold": cfg.get("momentum_threshold", 5),
            }
        )

        result = run_single_backtest(symbols, start, end, cfg["strategy"], config)

        if result.get("total_trades", 0) == 0:
            continue

        signals_per_day_per_coin = result['total_signals'] / days / len(symbols)

        results.append({
            "name": cfg["name"],
            "signals": result['total_signals'],
            "sig/day/coin": signals_per_day_per_coin,
            "win_rate": result['win_rate'],
            "total_pnl": result['total_pnl'],
            "avg_pnl": result['avg_pnl'],
            "wins": result['wins'],
            "losses": result['losses'],
            "timeouts": result['timeouts'],
            "long_pnl": result.get('long_pnl', 0),
            "short_pnl": result.get('short_pnl', 0),
        })

    return results


def show_trade_examples(symbols: List[str], start: datetime, end: datetime, num_examples: int = 4):
    """Show detailed trade examples with entry/exit information."""

    print("="*70)
    print("DETAILED TRADE EXAMPLES - LS Fade Strategy (SL=4%, TP=10%)")
    print("="*70)

    config = StrategyConfig(
        sl_pct=4,
        tp_pct=10,
        max_hold_days=14,
        lookback=7,
        params={"ls_extreme": 0.65}
    )

    downloader = HybridHistoryDownloader(
        cache_dir='cache',
        coinalyze_api_key='adb282f9-7e9e-4b6c-a669-b01c0304d506'
    )

    print(f"\nDownloading data for {len(symbols)} symbols...")
    history = downloader.download_with_coinalyze_backfill(symbols, start, end)

    runner = StrategyRunner(
        strategy_name="ls_fade",
        config=config,
        output_dir="output",
    )

    signals = runner.generate_signals(history, symbols)
    result = runner.backtest_signals(signals, history, max_hold_days=config.max_hold_days)

    all_trades = result.trades

    # Get examples: mix of WINs and LOSSes
    wins = [t for t in all_trades if t.result == "WIN"]
    losses = [t for t in all_trades if t.result == "LOSS"]

    examples = []

    # Get 2 SHORT WINs
    short_wins = [t for t in wins if t.signal.direction == "SHORT"]
    examples.extend(short_wins[:2])

    # Get 1 LONG example
    long_trades = [t for t in all_trades if t.signal.direction == "LONG"]
    if long_trades:
        examples.append(long_trades[0])

    # Get 1 LOSS example
    if losses:
        examples.append(losses[0])

    print(f"\nFound {len(all_trades)} trades total")
    print(f"Showing {len(examples)} example trades:\n")

    for i, trade in enumerate(examples[:num_examples], 1):
        sig = trade.signal
        metadata = sig.metadata

        long_pct = metadata.get("long_pct", 0.5) * 100
        short_pct = metadata.get("short_pct", 0.5) * 100

        print("="*70)
        print(f"TRADE #{i}: {sig.symbol} - {sig.direction} - {trade.result}")
        print("="*70)
        print(f"")
        print(f"  ENTRY:")
        print(f"    Date:       {sig.date.strftime('%Y-%m-%d')}")
        print(f"    Price:      ${sig.entry:.4f}")
        print(f"    L/S Ratio:  {long_pct:.1f}% LONG / {short_pct:.1f}% SHORT")
        print(f"    Reason:     {sig.reason}")
        print(f"")
        print(f"  TARGETS:")
        print(f"    Stop Loss:  ${sig.stop_loss:.4f} ({'-4%' if sig.direction=='LONG' else '+4%'})")
        print(f"    Take Profit: ${sig.take_profit:.4f} ({'+10%' if sig.direction=='LONG' else '-10%'})")
        print(f"")
        print(f"  EXIT:")
        print(f"    Date:       {trade.exit_date.strftime('%Y-%m-%d')} ({trade.hold_days} days)")
        print(f"    Price:      ${trade.exit_price:.4f}")
        print(f"    Result:     {trade.result}")
        print(f"    PnL:        {trade.pnl_pct:+.2f}%")
        print()

    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Strategy: LS Fade (trade against crowd when >65% one direction)")
    print(f"Logic: When crowd is 65%+ LONG -> SHORT (fade bullishness)")
    print(f"       When crowd is 65%+ SHORT -> LONG (fade bearishness)")
    print(f"Parameters: SL=4%, TP=10%, Max Hold=14 days")
    print()


def main():
    print("="*60)
    print("DAILY STRATEGY BACKTEST - PARAMETER SWEEP")
    print("="*60)

    # Test period: 6 months
    start = datetime(2024, 8, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 31, tzinfo=timezone.utc)

    # 18 valid altcoins with 6-month history
    symbols = [
        'ZECUSDT', 'SUIUSDT', 'ALICEUSDT', 'TAOUSDT', 'ENAUSDT',
        'UNIUSDT', 'ATOMUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
        'INJUSDT', 'TIAUSDT', 'SEIUSDT', 'JUPUSDT', 'STRKUSDT',
        'WLDUSDT', 'MKRUSDT', 'LDOUSDT'
    ]

    days = (end - start).days
    print(f"Period: {start.date()} to {end.date()} ({days} days)")
    print(f"Symbols: {len(symbols)} altcoins")
    print(f"Target: 0.5-4 signals/day/coin = {int(0.5*days)}-{int(4*days)} signals/coin")
    print()

    print("Running parameter sweep...")
    print()

    results = run_parameter_sweep(symbols, start, end)

    print("\n" + "="*100)
    print("PARAMETER SWEEP RESULTS")
    print("="*100)
    print(f"{'Config':<18} {'Sig':>6} {'S/d/c':>6} {'WR%':>6} {'TotPnL':>9} {'AvgPnL':>7} {'W/L/T':>12} {'LongPnL':>9} {'ShortPnL':>9}")
    print("-"*100)

    for r in results:
        wlt = f"{r['wins']}/{r['losses']}/{r['timeouts']}"
        print(f"{r['name']:<18} {r['signals']:>6} {r['sig/day/coin']:>6.2f} {r['win_rate']:>5.1f}% {r['total_pnl']:>8.1f}% {r['avg_pnl']:>6.2f}% {wlt:>12} {r['long_pnl']:>8.1f}% {r['short_pnl']:>8.1f}%")

    print("-"*100)
    print()
    print("Target frequency: 0.5-4 sig/day/coin")
    print("PROFITABLE: TotalPnL > 0")

    # Sort by total PnL
    sorted_results = sorted(results, key=lambda x: x['total_pnl'], reverse=True)
    print("\n=== TOP 5 BY PNL ===")
    for r in sorted_results[:5]:
        status = "OK" if r['total_pnl'] > 0 else "LOSS"
        freq_status = "OK" if r['sig/day/coin'] >= 0.5 else "LOW"
        print(f"  {r['name']}: PnL {r['total_pnl']:+.1f}%, WR {r['win_rate']:.1f}%, freq {r['sig/day/coin']:.2f} [{status}][{freq_status}]")

    # Find best viable strategy
    viable = [r for r in results if r['sig/day/coin'] >= 0.5 and r['total_pnl'] > 0]
    if viable:
        best = max(viable, key=lambda x: x['total_pnl'])
        print(f"\n*** BEST VIABLE: {best['name']} - {best['total_pnl']:.1f}% PnL, {best['sig/day/coin']:.2f} sig/day/coin ***")
    else:
        profitable = [r for r in results if r['total_pnl'] > 0]
        if profitable:
            best = max(profitable, key=lambda x: x['total_pnl'])
            print(f"\nBest profitable (low freq): {best['name']} - {best['total_pnl']:.1f}% PnL, {best['sig/day/coin']:.2f} sig/day/coin")
        else:
            print("\n!!! NO PROFITABLE STRATEGY FOUND !!!")

    return results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--examples":
        start = datetime(2024, 8, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 31, tzinfo=timezone.utc)
        symbols = [
            'ZECUSDT', 'SUIUSDT', 'ALICEUSDT', 'TAOUSDT', 'ENAUSDT',
            'UNIUSDT', 'ATOMUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
            'INJUSDT', 'TIAUSDT', 'SEIUSDT', 'JUPUSDT', 'STRKUSDT',
            'WLDUSDT', 'MKRUSDT', 'LDOUSDT'
        ]
        show_trade_examples(symbols, start, end)
    else:
        main()
