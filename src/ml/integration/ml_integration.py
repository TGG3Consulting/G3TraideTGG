# -*- coding: utf-8 -*-
"""
ML Integration for BinanceFriend.

Main entry point for ML-based signal optimization.
Integrates with existing system components.

Usage:
    integration = MLIntegration(futures_monitor, state_store)
    await integration.initialize()

    # Optimize a signal
    optimized = await integration.optimize_signal(signal)
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from src.ml.data import DataPreprocessor
from src.ml.features import FeatureEngineer
from src.ml.models import ModelEnsemble
from src.ml.optimization import SignalOptimizer
from src.ml.risk import PositionSizer, RiskManager
from src.ml.data.schemas import Direction, OptimizedSignal

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesMonitor
    from src.cross_exchange.state_store import StateStore
    from src.signals.models import TradeSignal


logger = structlog.get_logger(__name__)


class MLIntegration:
    """
    Main integration point for ML system.

    Provides:
    - Signal optimization using trained models
    - Real-time feature extraction
    - Position sizing recommendations
    - Risk management checks
    """

    def __init__(
        self,
        futures_monitor: Optional["FuturesMonitor"] = None,
        state_store: Optional["StateStore"] = None,
        model_dir: Optional[str] = None,
    ):
        """
        Initialize ML integration.

        Args:
            futures_monitor: FuturesMonitor instance for market data
            state_store: StateStore for cross-exchange data
            model_dir: Optional model directory (default from config)
        """
        self._config = settings.ml
        self._model_dir = model_dir or self._config.models.save_dir

        # Core components
        self._futures_monitor = futures_monitor
        self._state_store = state_store

        # ML components
        self._feature_engineer = FeatureEngineer(futures_monitor, state_store)
        self._preprocessor = DataPreprocessor()
        self._ensemble: Optional[ModelEnsemble] = None
        self._optimizer: Optional[SignalOptimizer] = None
        self._position_sizer = PositionSizer()
        self._risk_manager = RiskManager()

        # State
        self._is_initialized = False
        self._last_model_load: Optional[datetime] = None

        logger.info(
            "ml_integration_init",
            has_futures_monitor=futures_monitor is not None,
            has_state_store=state_store is not None,
            model_dir=self._model_dir,
        )

    @property
    def is_enabled(self) -> bool:
        """Whether ML optimization is enabled."""
        return self._config.enabled and self._is_initialized

    @property
    def is_ready(self) -> bool:
        """Whether ML system is ready for predictions."""
        return (
            self._is_initialized and
            self._ensemble is not None and
            self._ensemble.is_loaded
        )

    async def initialize(self) -> bool:
        """
        Initialize ML system.

        Loads models and prepares for predictions.

        Returns:
            True if initialization successful
        """
        if not self._config.enabled:
            logger.info("ml_disabled_in_config")
            return False

        try:
            # Check if models exist
            model_path = Path(self._model_dir)
            if not model_path.exists():
                logger.warning(
                    "model_dir_not_found",
                    path=str(model_path),
                )
                return False

            # Load models
            self._ensemble = ModelEnsemble()
            self._ensemble.load_models(str(model_path))

            # Load scalers
            scaler_path = model_path / "scalers.json"
            if scaler_path.exists():
                self._preprocessor.load_scalers(str(scaler_path))

            # Initialize optimizer
            self._optimizer = SignalOptimizer(ensemble=self._ensemble)

            self._is_initialized = True
            self._last_model_load = datetime.now(timezone.utc)

            logger.info(
                "ml_integration_initialized",
                model_dir=str(model_path),
            )
            return True

        except Exception as e:
            logger.error("ml_initialization_failed", error=str(e))
            return False

    async def optimize_signal(
        self,
        signal: "TradeSignal",
    ) -> Optional[OptimizedSignal]:
        """
        Optimize a trading signal using ML.

        Args:
            signal: Original TradeSignal

        Returns:
            OptimizedSignal or None if filtered
        """
        if not self.is_ready:
            logger.warning("ml_not_ready_for_optimization")
            return None

        try:
            # Extract features for the signal
            features = await self._extract_signal_features(signal)

            if features is None:
                logger.warning(
                    "feature_extraction_failed",
                    symbol=signal.symbol,
                )
                return None

            # Optimize
            optimized = self._optimizer.optimize(signal, features)

            if optimized is not None:
                logger.info(
                    "signal_optimized",
                    symbol=signal.symbol,
                    original_conf=signal.probability,
                    ml_conf=optimized.ml_confidence,
                    combined_conf=optimized.combined_confidence,
                )

            return optimized

        except Exception as e:
            logger.error(
                "signal_optimization_error",
                symbol=signal.symbol,
                error=str(e),
            )
            return None

    async def _extract_signal_features(
        self,
        signal: "TradeSignal",
    ) -> Optional[np.ndarray]:
        """Extract features for a signal."""
        # Get real-time feature vector
        feature_vector = self._feature_engineer.get_realtime_features(signal.symbol)

        if feature_vector is None:
            return None

        return feature_vector.features

    def get_position_size(
        self,
        optimized_signal: OptimizedSignal,
    ) -> float:
        """
        Get recommended position size for optimized signal.

        Args:
            optimized_signal: OptimizedSignal

        Returns:
            Position size as percentage of capital
        """
        return self._position_sizer.calculate_size(
            win_probability=optimized_signal.predicted_win_probability,
            risk_reward=optimized_signal.risk_reward_ratio,
            sl_pct=optimized_signal.optimized_sl_pct,
            current_drawdown=self._risk_manager.current_drawdown,
        )

    def can_trade(
        self,
        symbol: str,
        direction: int = 1,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if trading is allowed for a symbol.

        Args:
            symbol: Trading pair
            direction: 1 for long, -1 for short

        Returns:
            Tuple of (can_trade, reason_if_not)
        """
        return self._risk_manager.can_open_position(symbol, direction)

    def record_trade_result(
        self,
        symbol: str,
        pnl_pct: float,
        size_pct: Optional[float] = None,
    ) -> None:
        """
        Record a trade result for risk tracking.

        Args:
            symbol: Trading pair
            pnl_pct: Trade P&L percentage
            size_pct: Optional position size
        """
        self._risk_manager.record_trade_result(symbol, pnl_pct, size_pct)

    def get_stats(self) -> Dict:
        """Get ML system statistics."""
        stats = {
            "enabled": self._config.enabled,
            "initialized": self._is_initialized,
            "ready": self.is_ready,
            "last_model_load": (
                self._last_model_load.isoformat()
                if self._last_model_load else None
            ),
        }

        if self._optimizer:
            stats["optimizer"] = self._optimizer.get_stats()

        stats["risk"] = self._risk_manager.get_risk_summary()

        return stats

    async def reload_models(self) -> bool:
        """
        Reload models from disk.

        Useful after retraining.

        Returns:
            True if reload successful
        """
        logger.info("reloading_ml_models")

        try:
            self._ensemble = ModelEnsemble()
            self._ensemble.load_models(self._model_dir)

            scaler_path = Path(self._model_dir) / "scalers.json"
            if scaler_path.exists():
                self._preprocessor.load_scalers(str(scaler_path))

            self._optimizer = SignalOptimizer(ensemble=self._ensemble)
            self._last_model_load = datetime.now(timezone.utc)

            logger.info("ml_models_reloaded")
            return True

        except Exception as e:
            logger.error("model_reload_failed", error=str(e))
            return False


class MLService:
    """
    Background service for ML operations.

    Handles:
    - Periodic model reloading
    - Batch signal processing
    - Metrics collection
    """

    def __init__(
        self,
        integration: MLIntegration,
        reload_interval_hours: int = 24,
    ):
        """
        Initialize ML service.

        Args:
            integration: MLIntegration instance
            reload_interval_hours: Hours between model reloads
        """
        self._integration = integration
        self._reload_interval = reload_interval_hours * 3600

        self._running = False
        self._task: Optional[asyncio.Task] = None

        logger.info(
            "ml_service_init",
            reload_interval_hours=reload_interval_hours,
        )

    async def start(self) -> None:
        """Start the ML service."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

        logger.info("ml_service_started")

    async def stop(self) -> None:
        """Stop the ML service."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("ml_service_stopped")

    async def _run_loop(self) -> None:
        """Main service loop."""
        while self._running:
            try:
                # Check if models need reloading
                if self._integration._last_model_load:
                    age = (
                        datetime.now(timezone.utc) -
                        self._integration._last_model_load
                    ).total_seconds()

                    if age >= self._reload_interval:
                        await self._integration.reload_models()

                # Sleep
                await asyncio.sleep(3600)  # Check hourly

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ml_service_error", error=str(e))
                await asyncio.sleep(60)
