# -*- coding: utf-8 -*-
"""
ML Integration with REAL Backtester.

Uses the working backtester/ module for REAL results.
NO random, NO fake simulations!

Usage:
    integration = MLBacktesterIntegration()
    results = integration.run_backtest("logs/signals.jsonl")
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import structlog

from backtester.config import BacktestConfig
from backtester.log_parser import LogParser
from backtester.data_loader import BinanceDataLoader
from backtester.position_simulator import PositionSimulator
from backtester.models import BacktestResult, ParsedSignal


logger = structlog.get_logger(__name__)


class MLBacktesterIntegration:
    """
    Integration between ML system and REAL backtester.

    Uses:
    - backtester.log_parser.LogParser for signal parsing
    - backtester.data_loader.BinanceDataLoader for REAL klines
    - backtester.position_simulator.PositionSimulator for REAL simulation

    NO random.random() anywhere!
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        """
        Initialize with backtester components.

        Args:
            config: Backtest configuration
        """
        self._config = config or BacktestConfig()
        self._parser = LogParser(self._config)
        self._simulator = PositionSimulator(self._config)

        logger.info("ml_backtester_integration_init")

    def run_backtest(
        self,
        signals_path: str = "logs/signals.jsonl",
        limit: Optional[int] = None,
    ) -> List[BacktestResult]:
        """
        Run backtest on signals using REAL historical data.

        Args:
            signals_path: Path to signals.jsonl
            limit: Optional limit on number of signals

        Returns:
            List of REAL BacktestResult (not fake!)
        """
        logger.info(
            "ml_backtest_start",
            signals_path=signals_path,
            limit=limit,
        )

        # 1. Parse signals from log
        signals = self._parser.parse_all_signals()

        if not signals:
            logger.warning("no_signals_found", path=signals_path)
            return []

        if limit:
            signals = signals[:limit]

        logger.info("signals_parsed", count=len(signals))

        # 2. Get unique symbols and time range
        symbols = self._get_unique_symbols(signals)
        start_time, end_time = self._parser.get_time_range()

        logger.info(
            "loading_klines",
            symbols_count=len(symbols),
            start=start_time.isoformat() if start_time else None,
            end=end_time.isoformat() if end_time else None,
        )

        # 3. Load REAL klines from Binance
        with BinanceDataLoader(self._config) as loader:
            all_klines = loader.load_all_symbols(symbols, start_time, end_time)

        logger.info(
            "klines_loaded",
            symbols_with_data=len(all_klines),
        )

        # 4. Simulate each signal with REAL data
        results = []

        for signal in signals:
            klines = all_klines.get(signal.symbol, [])

            if not klines:
                logger.debug(
                    "no_klines_for_symbol",
                    symbol=signal.symbol,
                )
                continue

            # REAL simulation - NO random!
            result = self._simulator.simulate(signal, klines)
            results.append(result)

        # Log summary
        filled = sum(1 for r in results if r.entry_filled)
        profitable = sum(1 for r in results if r.net_pnl > 0)

        logger.info(
            "ml_backtest_complete",
            total_signals=len(signals),
            simulated=len(results),
            filled=filled,
            profitable=profitable,
        )

        return results

    def _get_unique_symbols(self, signals: List[ParsedSignal]) -> Set[str]:
        """Get unique symbols from signals."""
        return {s.symbol for s in signals}

    def get_training_data(
        self,
        signals_path: str = "logs/signals.jsonl",
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """
        Get training data with REAL labels from backtest results.

        Args:
            signals_path: Path to signals
            limit: Optional limit

        Returns:
            List of dicts with signal features and REAL outcome labels
        """
        results = self.run_backtest(signals_path, limit)

        training_data = []

        for result in results:
            if not result.entry_filled:
                continue

            signal = result.signal

            # Features from signal
            features = {
                "symbol": signal.symbol,
                "direction": signal.direction.value,
                "probability": signal.probability,
                "risk_reward": signal.risk_reward,
                "stop_loss_pct": signal.stop_loss_pct,
                "tp1_pct": signal.tp1.percent,
                "tp2_pct": signal.tp2.percent,
                "tp3_pct": signal.tp3.percent,
                "signal_type": signal.signal_type,
            }

            # Add details if available
            if signal.details:
                features["oi_change_1h"] = signal.details.get("oi_change_1h", "0")
                features["funding"] = signal.details.get("funding", "0")

            # REAL labels from backtest result (NOT FAKE!)
            labels = {
                "label_profitable": float(result.net_pnl) > 0,
                "label_pnl_pct": float(result.net_pnl_percent),
                "label_exit_reason": result.exit_reason.value,
                "label_hold_hours": result.hold_time_hours,
                "label_tp1_hit": result.tp1_hit,
                "label_tp2_hit": result.tp2_hit,
                "label_tp3_hit": result.tp3_hit,
                "label_sl_hit": result.sl_hit,
            }

            training_data.append({**features, **labels})

        logger.info(
            "training_data_generated",
            samples=len(training_data),
        )

        return training_data
