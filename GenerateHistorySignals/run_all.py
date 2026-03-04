# -*- coding: utf-8 -*-
"""
Run All Strategies - Generate signals and backtest all 5 strategies at once.

Usage:
    python run_all.py --start 2024-01-01 --end 2025-01-31 --symbols BTCUSDT,ETHUSDT,SOLUSDT
    python run_all.py --start 2024-01-01 --end 2025-01-31 --top 20
    python run_all.py --start 2024-01-01 --end 2025-01-31 --symbols BTCUSDT,ETHUSDT --sl 5 --tp 12

Options:
    --start         Start date (YYYY-MM-DD) [required]
    --end           End date (YYYY-MM-DD) [required]
    --symbols       Comma-separated symbols (e.g., BTCUSDT,ETHUSDT)
    --top           Use top N symbols by volume (default: 20)
    --sl            Stop Loss % (default: 4)
    --tp            Take Profit % (default: 10)
    --max-hold      Max hold days (default: 14)
    --output        Output directory (default: output)
    --save          Save signals to JSON files
"""

import sys
import io
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from hybrid_downloader import HybridHistoryDownloader
from strategies import StrategyConfig, list_strategies
from strategy_runner import StrategyRunner


# All available strategies
ALL_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']


def detect_market_regime(history: Dict[str, Any]) -> Dict[str, Any]:
    """Detect market regime from historical data."""
    # Try BTCUSDT first, then ETHUSDT, then any symbol with klines
    ref_symbol = None
    klines = None

    # Priority list: BTC, ETH, then any with klines
    priority_symbols = ['BTCUSDT', 'ETHUSDT']
    for sym in priority_symbols:
        if sym in history and history[sym].klines and len(history[sym].klines) >= 2:
            ref_symbol = sym
            klines = history[sym].klines
            break

    # Fallback: find any symbol with valid klines
    if not ref_symbol:
        for sym, data in history.items():
            if data.klines and len(data.klines) >= 2:
                ref_symbol = sym
                klines = data.klines
                break

    if not ref_symbol or not klines or len(klines) < 2:
        return {'regime': 'UNKNOWN', 'change_pct': 0, 'volatility': 0, 'ref_symbol': 'N/A'}

    # Price change
    first_close = float(klines[0].get('close', klines[0].get('c', 0)))
    last_close = float(klines[-1].get('close', klines[-1].get('c', 0)))
    change_pct = ((last_close - first_close) / first_close * 100) if first_close > 0 else 0

    # Volatility (avg daily range %)
    ranges = []
    for k in klines:
        high = float(k.get('high', k.get('h', 0)))
        low = float(k.get('low', k.get('l', 0)))
        if low > 0:
            ranges.append((high - low) / low * 100)
    volatility = sum(ranges) / len(ranges) if ranges else 0

    # Determine regime
    if change_pct > 15:
        regime = 'STRONG BULL'
    elif change_pct > 5:
        regime = 'BULL'
    elif change_pct < -15:
        regime = 'STRONG BEAR'
    elif change_pct < -5:
        regime = 'BEAR'
    else:
        regime = 'SIDEWAYS'

    # Add volatility qualifier
    if volatility > 8:
        regime += ' (HIGH VOL)'

    return {
        'regime': regime,
        'change_pct': change_pct,
        'volatility': volatility,
        'ref_symbol': ref_symbol
    }


def run_all_strategies(
    symbols: List[str],
    start: datetime,
    end: datetime,
    sl_pct: float = 4.0,
    tp_pct: float = 10.0,
    max_hold_days: int = 14,
    dedup_days: int = 14,
    position_mode: str = "single",
    order_size_usd: float = 100.0,
    taker_fee_pct: float = 0.05,
    output_dir: str = "output",
    save_signals: bool = False,
    export_xlsx: bool = False,
    data_interval: str = "daily",
    daily_max_dd: float = 5.0,
    monthly_max_dd: float = 20.0,
    use_ml: bool = False,
    ml_model_dir: str = "models",
    dynamic_size_enabled: bool = False,
    normal_size: float = 100.0,
    protected_size: float = 1.0,
    month_off_dd: Optional[float] = None,
    month_off_pnl: Optional[float] = None,
    day_off_dd: Optional[float] = None,
    day_off_pnl: Optional[float] = None,
    coin_regime_enabled: bool = False,
    coin_regime_lookback: int = 14,
    vol_filter_enabled: bool = False,
    vol_filter_low: float = 3.0,
    vol_filter_high: float = 15.0,
) -> List[Dict[str, Any]]:
    """
    Run all strategies on given symbols and date range.

    Returns:
        List of result dicts for each strategy
    """
    # Download data once
    print("=" * 80)
    print("RUN ALL STRATEGIES")
    print("=" * 80)
    print(f"Period:      {start.strftime('%Y-%m-%d')} -> {end.strftime('%Y-%m-%d')}")
    print(f"Symbols:     {len(symbols)}")
    print(f"Data:        {data_interval}")
    print(f"SL/TP:       {sl_pct}% / {tp_pct}%")
    print(f"Max Hold:    {max_hold_days} days")
    print(f"Dedup Days:  {dedup_days}")
    print(f"Order Size:  ${order_size_usd:.0f}")
    print(f"Taker Fee:   {taker_fee_pct}%")
    print(f"Position:    {position_mode}")
    print(f"Daily MaxDD: {daily_max_dd}%")
    print(f"Month MaxDD: {monthly_max_dd}%")
    print(f"Output Dir:  {output_dir}")
    print(f"Save Signals:{' Yes' if save_signals else ' No'}")
    print(f"Export XLSX: {'Yes' if export_xlsx else 'No'}")
    print(f"ML Filter:   {'ENABLED' if use_ml else 'Disabled'}")
    if use_ml:
        print(f"ML Models:   {ml_model_dir}")
    print(f"Dynamic Size:{'ENABLED' if dynamic_size_enabled else 'Disabled'}")
    if dynamic_size_enabled:
        print(f"  Normal:    ${normal_size:.0f} (after WIN)")
        print(f"  Protected: ${protected_size:.0f} (after LOSS)")
    if month_off_dd is not None or month_off_pnl is not None:
        print(f"Month OFF:   DD>{month_off_dd}% PnL<{month_off_pnl}%")
    if day_off_dd is not None or day_off_pnl is not None:
        print(f"Day OFF:     DD>{day_off_dd}% PnL<{day_off_pnl}%")
    print(f"Coin Regime: {'ENABLED (' + str(coin_regime_lookback) + 'd lookback)' if coin_regime_enabled else 'Disabled'}")
    print(f"Vol Filter:  {'ENABLED (low=' + str(vol_filter_low) + '%, high=' + str(vol_filter_high) + '%)' if vol_filter_enabled else 'Disabled'}")
    print(f"Strategies:  {len(ALL_STRATEGIES)}")
    print("=" * 80)
    print()

    print("[1/3] Downloading historical data...")
    print()

    downloader = HybridHistoryDownloader(
        cache_dir='cache',
        coinalyze_api_key='adb282f9-7e9e-4b6c-a669-b01c0304d506',
        data_interval=data_interval
    )

    history = downloader.download_with_coinalyze_backfill(symbols, start, end)

    # Detect market regime
    market_regime = detect_market_regime(history)
    print(f"Market Regime: {market_regime['regime']} ({market_regime['change_pct']:+.1f}% change, {market_regime['volatility']:.1f}% vol)")

    print()
    print("[2/3] Running all strategies...")
    print()

    results = []

    for i, strat_name in enumerate(ALL_STRATEGIES, 1):
        print(f"  [{i}/{len(ALL_STRATEGIES)}] {strat_name}...", end=" ", flush=True)

        # Build config
        config = StrategyConfig(
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            max_hold_days=max_hold_days,
            lookback=7,
            params={
                "ls_extreme": 0.65,
                "momentum_threshold": 5.0,
                "oversold_threshold": -10.0,
                "overbought_threshold": 15.0,
                "crowd_bearish": 0.55,
                "crowd_bullish": 0.60,
                "ls_confirm": 0.60,
            }
        )

        # Create runner
        runner = StrategyRunner(
            strategy_name=strat_name,
            config=config,
            output_dir=output_dir,
            use_ml=use_ml,
            ml_model_dir=ml_model_dir,
        )

        # Generate signals (suppress output)
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            signals = runner.generate_signals(history, symbols, dedup_days=dedup_days)
            result = runner.backtest_signals(
                signals, history,
                max_hold_days=max_hold_days,
                order_size_usd=order_size_usd,
                taker_fee_pct=taker_fee_pct,
                position_mode=position_mode,
                daily_max_dd=daily_max_dd,
                monthly_max_dd=monthly_max_dd,
                dynamic_size_enabled=dynamic_size_enabled,
                normal_size=normal_size,
                protected_size=protected_size,
                month_off_dd=month_off_dd,
                month_off_pnl=month_off_pnl,
                day_off_dd=day_off_dd,
                day_off_pnl=day_off_pnl,
                coin_regime_enabled=coin_regime_enabled,
                coin_regime_lookback=coin_regime_lookback,
                vol_filter_enabled=vol_filter_enabled,
                vol_filter_low=vol_filter_low,
                vol_filter_high=vol_filter_high,
            )
        except Exception as e:
            sys.stdout = old_stdout
            import traceback
            print(f"\n[ERROR] Strategy {strat_name} failed: {e}", flush=True)
            traceback.print_exc()
            raise
        finally:
            sys.stdout = old_stdout

        # Save signals if requested
        if save_signals and signals:
            runner.write_signals_json(signals)

        print(f"[DEBUG] export_xlsx={export_xlsx}, trades={len(result.trades)}", flush=True)

        # Export to XLSX if requested
        if export_xlsx and result.trades:
            print("[DEBUG] Starting XLSX export...", flush=True)
            xlsx_path = runner.export_to_xlsx(
                result=result,
                history=history,
                order_size_usd=order_size_usd,
                start_date=start,
                end_date=end,
                market_regime=market_regime,
            )
            print(f"[DEBUG] XLSX done: {xlsx_path}", flush=True)
            print(f"XLSX: {xlsx_path}", end=" ", flush=True)

        # Store result
        results.append({
            'name': strat_name,
            'signals': result.total_signals,
            'trades': result.total_trades,
            'skipped_liquidity': result.skipped_liquidity,
            'skipped_position': result.skipped_position,
            'skipped_daily_limit': result.skipped_daily_limit,
            'skipped_monthly_limit': result.skipped_monthly_limit,
            'skipped_month_filter': result.skipped_month_filter,
            'skipped_day_filter': result.skipped_day_filter,
            'skipped_regime': result.skipped_regime,
            'regime_dynamic': result.regime_dynamic_count,
            'wins': result.wins,
            'losses': result.losses,
            'timeouts': result.timeouts,
            'win_rate': result.win_rate,
            'total_pnl': result.total_pnl,
            'avg_pnl': result.avg_pnl,
            'long_pnl': result.long_pnl,
            'short_pnl': result.short_pnl,
            'total_fees': result.total_fees_pct,
            'max_drawdown': result.max_drawdown,
            'calmar_ratio': result.calmar_ratio,
            'avg_hold_win': result.avg_hold_win,
            'avg_hold_loss': result.avg_hold_loss,
            'avg_hold_timeout': result.avg_hold_timeout,
            'days_stopped': result.days_stopped,
            'monthly_stopped': result.monthly_stopped,
            'ml_passed': runner.ml_passed_count if use_ml else 0,
            'ml_filtered': runner.ml_filtered_count if use_ml else 0,
        })

        status = "PROFIT" if result.total_pnl > 0 else "LOSS"
        print(f"{result.total_signals} signals, {result.total_pnl:+.1f}% PnL [{status}]")

    return results, market_regime


def print_results_table(results: List[Dict[str, Any]], start: datetime, end: datetime, symbols_count: int, market_regime: Dict[str, Any] = None):
    """Print formatted results table."""
    days = (end - start).days

    print()
    print("=" * 120)
    print("RESULTS - ALL STRATEGIES (HONEST BACKTEST, NO LOOK-AHEAD BIAS)")
    print("=" * 120)
    print(f"Period: {start.strftime('%Y-%m-%d')} -> {end.strftime('%Y-%m-%d')} ({days} days)")
    print(f"Symbols: {symbols_count}")
    if market_regime:
        print(f"Market: {market_regime['regime']} | {market_regime.get('ref_symbol', 'N/A')}: {market_regime['change_pct']:+.1f}% | Volatility: {market_regime['volatility']:.1f}%")
    print("=" * 120)
    print()
    print(f"{'Strategy':<14} {'Sigs':>6} {'Trds':>5} {'WinR':>6} {'NetPnL':>8} {'MaxDD':>7} {'Calmar':>7} {'Fees':>6} {'W/L/T':>10} {'HoldW':>6}")
    print("-" * 120)

    # Sort by total PnL
    sorted_results = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

    for r in sorted_results:
        wlt = f"{r['wins']}/{r['losses']}/{r['timeouts']}"
        status = "PROFIT" if r['total_pnl'] > 0 else "LOSS"

        print(f"{r['name']:<14} {r['signals']:>6} {r['trades']:>5} {r['win_rate']:>5.1f}% {r['total_pnl']:>+7.1f}% {r['max_drawdown']:>6.1f}% {r['calmar_ratio']:>7.2f} {r['total_fees']:>5.1f}% {wlt:>10} {r['avg_hold_win']:>5.1f}d  [{status}]")

    print("-" * 120)
    print()

    # Skipped signals summary
    total_skipped_liq = sum(r.get('skipped_liquidity', 0) for r in results)
    total_skipped_pos = sum(r.get('skipped_position', 0) for r in results)
    total_skipped_daily = sum(r.get('skipped_daily_limit', 0) for r in results)
    total_skipped_monthly = sum(r.get('skipped_monthly_limit', 0) for r in results)
    total_skipped_month_filter = sum(r.get('skipped_month_filter', 0) for r in results)
    total_skipped_day_filter = sum(r.get('skipped_day_filter', 0) for r in results)
    total_skipped_regime = sum(r.get('skipped_regime', 0) for r in results)
    total_regime_dynamic = sum(r.get('regime_dynamic', 0) for r in results)
    total_days_stopped = sum(r.get('days_stopped', 0) for r in results)
    any_monthly_stopped = any(r.get('monthly_stopped', False) for r in results)

    # ML stats
    total_ml_filtered = sum(r.get('ml_filtered', 0) for r in results)
    total_ml_passed = sum(r.get('ml_passed', 0) for r in results)

    has_skips = (total_skipped_liq > 0 or total_skipped_pos > 0 or total_skipped_daily > 0 or
                 total_skipped_monthly > 0 or total_skipped_month_filter > 0 or
                 total_skipped_day_filter > 0 or total_skipped_regime > 0 or total_ml_filtered > 0)
    if has_skips:
        print("Skip Summary:")
        if total_ml_filtered > 0:
            print(f"  - ML Filtered: {total_ml_filtered} (passed: {total_ml_passed})")
        if total_skipped_month_filter > 0:
            print(f"  - Month filter: {total_skipped_month_filter}")
        if total_skipped_day_filter > 0:
            print(f"  - Day filter: {total_skipped_day_filter}")
        if total_skipped_regime > 0:
            print(f"  - Coin regime OFF: {total_skipped_regime}")
        if total_regime_dynamic > 0:
            print(f"  - Coin regime DYN: {total_regime_dynamic} (reduced size)")
        if total_skipped_liq > 0:
            print(f"  - Low liquidity: {total_skipped_liq}")
        if total_skipped_pos > 0:
            print(f"  - Position open: {total_skipped_pos}")
        if total_skipped_daily > 0:
            print(f"  - Daily MaxDD limit: {total_skipped_daily} ({total_days_stopped} days stopped)")
        if total_skipped_monthly > 0:
            print(f"  - Monthly MaxDD limit: {total_skipped_monthly}")
        if any_monthly_stopped:
            print(f"  [!] Some strategies hit monthly limit and stopped trading")
        print()

    # Summary
    profitable = [r for r in results if r['total_pnl'] > 0]
    print(f"Profitable strategies: {len(profitable)}/{len(results)}")

    if profitable:
        best = max(profitable, key=lambda x: x['total_pnl'])
        print(f"BEST: {best['name']} with {best['total_pnl']:+.1f}% Net PnL, {best['win_rate']:.1f}% Win Rate, {best['max_drawdown']:.1f}% MaxDD")

    # Direction analysis
    print()
    print("Direction Analysis:")
    for r in sorted_results[:3]:  # Top 3
        if r['total_pnl'] > 0:
            dominant = "SHORT" if r['short_pnl'] > r['long_pnl'] else "LONG"
            print(f"  {r['name']}: {dominant} dominant ({r['short_pnl']:+.0f}% SHORT / {r['long_pnl']:+.0f}% LONG)")

    print()
    print("=" * 120)


def main():
    parser = argparse.ArgumentParser(
        description="Run all 5 strategies and compare results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_all.py --start 2024-01-01 --end 2025-01-31 --symbols BTCUSDT,ETHUSDT,SOLUSDT
  python run_all.py --start 2024-08-01 --end 2025-01-31 --top 20 --sl 4 --tp 10
  python run_all.py --start 2024-01-01 --end 2024-12-31 --symbols DOGEUSDT,SOLUSDT --save
        """
    )

    parser.add_argument("--start", type=str, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    parser.add_argument("--top", type=int, default=20, help="Top N symbols by volume (if --symbols not provided)")
    parser.add_argument("--sl", type=float, default=4.0, help="Stop Loss %% (default: 4)")
    parser.add_argument("--tp", type=float, default=10.0, help="Take Profit %% (default: 10)")
    parser.add_argument("--max-hold", type=int, default=14, help="Max hold days (default: 14)")
    parser.add_argument("--dedup-days", type=int, default=3, help="Chain grouping threshold days (default: 3)")
    parser.add_argument("--position-mode", type=str, default="single", choices=["single", "direction", "multi"],
                        help="Position mode: single (1 per coin), direction (1 per direction), multi (default: single)")
    parser.add_argument("--order-size", type=float, default=100.0, help="Order size in USDT (default: 100)")
    parser.add_argument("--taker-fee", type=float, default=0.05, help="Taker fee %% per side (default: 0.05)")
    parser.add_argument("--data-interval", type=str, default="daily", choices=["daily", "5m", "1m"],
                        help="Data granularity: daily (1d candles), 5m, or 1m (default: daily)")
    parser.add_argument("--daily-max-dd", type=float, default=5.0,
                        help="Daily max drawdown %% - stop new trades for day if hit (default: 5)")
    parser.add_argument("--monthly-max-dd", type=float, default=20.0,
                        help="Monthly max drawdown %% - stop all trading if hit (default: 20)")
    parser.add_argument("--output", type=str, default=r"G:\BinanceFriend\outputNEWARCH", help="Output directory")
    parser.add_argument("--no-save", action="store_true", help="Disable saving signals to JSON")
    parser.add_argument("--no-xlsx", action="store_true", help="Disable XLSX export")
    parser.add_argument("--ml", action="store_true", help="Enable ML filtering of signals")
    parser.add_argument("--ml-model-dir", type=str, default="models", help="Directory with ML models")
    parser.add_argument("--dynamic-size", action="store_true", help="Enable dynamic order sizing (1$ after LOSS, normal after WIN)")
    parser.add_argument("--normal-size", type=float, default=100.0, help="Order size after WIN (default: 100)")
    parser.add_argument("--protected-size", type=float, default=1.0, help="Order size after LOSS (default: 1)")
    parser.add_argument("--month-off-dd", type=float, default=None, help="Skip months where MaxDD > X%% (e.g., 50)")
    parser.add_argument("--month-off-pnl", type=float, default=None, help="Skip months where PnL < X%% (e.g., -20)")
    parser.add_argument("--day-off-dd", type=float, default=None, help="Skip days where MaxDD > X%% (e.g., 40)")
    parser.add_argument("--day-off-pnl", type=float, default=None, help="Skip days where PnL < X%% (e.g., -10)")
    parser.add_argument("--coin-regime", action="store_true", help="Enable COIN REGIME filter (based on 14d price change)")
    parser.add_argument("--coin-regime-lookback", type=int, default=14, help="Lookback days for coin regime calculation (default: 14)")
    parser.add_argument("--vol-filter", action="store_true", help="Enable VOLATILITY filter (skip momentum in low vol, reduce mean_rev in high vol)")
    parser.add_argument("--vol-filter-low", type=float, default=3.0, help="Low volatility threshold %% (default: 3.0)")
    parser.add_argument("--vol-filter-high", type=float, default=15.0, help="High volatility threshold %% (default: 15.0)")

    args = parser.parse_args()

    # Parse dates
    try:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    except ValueError as e:
        print(f"[ERROR] Invalid date format: {e}")
        return 1

    # Parse symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        # Get top N symbols
        print(f"Fetching top {args.top} symbols by volume...")
        downloader = HybridHistoryDownloader(cache_dir='cache')
        symbols = downloader.get_active_symbols(top_n=args.top)
        print(f"Selected: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}")
        print()

    if not symbols:
        print("[ERROR] No symbols provided")
        return 1

    # Run all strategies
    results = run_all_strategies(
        symbols=symbols,
        start=start,
        end=end,
        sl_pct=args.sl,
        tp_pct=args.tp,
        max_hold_days=args.max_hold,
        dedup_days=args.dedup_days,
        position_mode=args.position_mode,
        order_size_usd=args.order_size,
        taker_fee_pct=args.taker_fee,
        output_dir=args.output,
        save_signals=not args.no_save,
        export_xlsx=not args.no_xlsx,
        data_interval=args.data_interval,
        daily_max_dd=args.daily_max_dd,
        monthly_max_dd=args.monthly_max_dd,
        use_ml=args.ml,
        ml_model_dir=args.ml_model_dir,
        dynamic_size_enabled=args.dynamic_size,
        normal_size=args.normal_size,
        protected_size=args.protected_size,
        month_off_dd=args.month_off_dd,
        month_off_pnl=args.month_off_pnl,
        day_off_dd=args.day_off_dd,
        day_off_pnl=args.day_off_pnl,
        coin_regime_enabled=args.coin_regime,
        coin_regime_lookback=args.coin_regime_lookback,
        vol_filter_enabled=args.vol_filter,
        vol_filter_low=args.vol_filter_low,
        vol_filter_high=args.vol_filter_high,
    )

    # Print results
    print()
    print("[3/3] Results:")
    results, market_regime = results
    print_results_table(results, start, end, len(symbols), market_regime)

    return 0


if __name__ == "__main__":
    sys.exit(main())
