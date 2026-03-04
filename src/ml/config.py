# -*- coding: utf-8 -*-
"""
ML Configuration dataclass.

Provides typed configuration for the ML system.
All values come from config/ml_config.yaml via settings.

Usage:
    from src.ml.config import MLConfig
    config = MLConfig.from_settings()
    print(config.min_confidence)
"""

from dataclasses import dataclass, field
from typing import List, Optional

import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


@dataclass
class DataConfig:
    """Data collection configuration."""

    history_days: int = 90
    min_samples_per_symbol: int = 1000
    kline_intervals: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h"])
    min_volume_usd_24h: float = 10_000_000
    min_oi_usd: float = 5_000_000
    request_delay_ms: int = 100
    max_concurrent_requests: int = 5


@dataclass
class FeaturesConfig:
    """Feature engineering configuration."""

    # Technical indicators
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    ema_periods: List[int] = field(default_factory=lambda: [9, 21, 50, 200])

    # Aggregation windows
    windows_minutes: List[int] = field(default_factory=lambda: [1, 5, 15, 60, 240])

    # Normalization
    normalize: bool = True
    scaler_type: str = "robust"

    # Feature selection
    max_features: int = 100
    min_importance: float = 0.01


@dataclass
class ModelsConfig:
    """Model configuration."""

    save_dir: str = "models/ml"
    reload_interval_hours: int = 24

    # Direction classifier
    direction_n_estimators: int = 500
    direction_max_depth: int = 8
    direction_learning_rate: float = 0.05

    # Level regressors
    levels_n_estimators: int = 300
    levels_max_depth: int = 6
    levels_learning_rate: float = 0.05

    # Lifetime regressor
    lifetime_n_estimators: int = 200
    lifetime_max_depth: int = 5
    lifetime_learning_rate: float = 0.05

    # Calibration
    calibration_method: str = "isotonic"
    cv_folds: int = 5


@dataclass
class TrainingConfig:
    """Training configuration."""

    test_size: float = 0.2
    validation_size: float = 0.1
    shuffle: bool = False  # NEVER shuffle time series!

    cv_type: str = "time_series"
    cv_folds: int = 5

    early_stopping_rounds: int = 50

    retrain_interval_days: int = 7
    min_new_samples: int = 500

    # Minimum data requirements
    min_train_samples: int = 500
    min_test_samples: int = 100
    min_history_days: int = 90


@dataclass
class OptimizationConfig:
    """Signal optimization configuration."""

    min_confidence: float = 0.6
    max_sl_adjustment_pct: float = 30.0
    max_tp_adjustment_pct: float = 50.0
    max_entry_adjustment_pct: float = 5.0

    min_predicted_winrate: float = 0.55
    min_predicted_rr: float = 1.5

    symbol_cooldown_minutes: int = 15


@dataclass
class RiskConfig:
    """Risk management configuration."""

    # Position sizing
    position_sizing_method: str = "kelly"
    kelly_fraction: float = 0.25
    max_position_pct: float = 5.0
    min_position_pct: float = 0.5

    # Limits
    max_daily_trades: int = 20
    max_open_positions: int = 5
    max_daily_loss_pct: float = 10.0
    max_drawdown_pct: float = 20.0

    # Correlation
    max_correlated_positions: int = 3
    correlation_threshold: float = 0.7


@dataclass
class MetricsConfig:
    """Metrics and evaluation configuration."""

    primary_metric: str = "sharpe_ratio"

    # Validation thresholds
    min_sharpe_ratio: float = 1.0
    min_profit_factor: float = 1.3
    min_win_rate: float = 0.5
    max_max_drawdown: float = 0.25

    # Baseline comparison
    min_improvement_vs_baseline_pct: float = 10.0

    # Overfitting detection
    max_train_test_gap_pct: float = 15.0


@dataclass
class MonitoringConfig:
    """Production monitoring configuration."""

    baseline_accuracy: float = 0.55
    baseline_sharpe: float = 0.5

    drift_check_window: int = 100
    max_accuracy_drop: float = 0.15

    max_price_change_24h_pct: float = 30.0
    max_volume_spike_ratio: float = 10.0
    extreme_funding_rate: float = 0.1

    consecutive_losses_alert: int = 5
    daily_loss_alert_pct: float = 5.0


@dataclass
class MLConfig:
    """
    Main ML configuration dataclass.

    Aggregates all ML-related configuration in a typed structure.
    """

    enabled: bool = False

    data: DataConfig = field(default_factory=DataConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    @classmethod
    def from_settings(cls) -> "MLConfig":
        """
        Create MLConfig from global settings.

        Reads from config/ml_config.yaml via settings.ml.
        """
        try:
            ml = settings.ml

            config = cls(
                enabled=ml.enabled,
                data=DataConfig(
                    history_days=ml.data.history_days,
                    min_samples_per_symbol=ml.data.min_samples_per_symbol,
                    kline_intervals=list(ml.data.kline_intervals),
                    min_volume_usd_24h=ml.data.min_volume_usd_24h,
                    min_oi_usd=ml.data.min_oi_usd,
                    request_delay_ms=ml.data.request_delay_ms,
                    max_concurrent_requests=ml.data.max_concurrent_requests,
                ),
                features=FeaturesConfig(
                    rsi_period=ml.features.technical.rsi_period,
                    macd_fast=ml.features.technical.macd_fast,
                    macd_slow=ml.features.technical.macd_slow,
                    macd_signal=ml.features.technical.macd_signal,
                    bb_period=ml.features.technical.bb_period,
                    bb_std=ml.features.technical.bb_std,
                    atr_period=ml.features.technical.atr_period,
                    ema_periods=list(ml.features.technical.ema_periods),
                    windows_minutes=list(ml.features.windows_minutes),
                    normalize=ml.features.normalize,
                    scaler_type=ml.features.scaler_type,
                    max_features=ml.features.max_features,
                    min_importance=ml.features.min_importance,
                ),
                models=ModelsConfig(
                    save_dir=ml.models.save_dir,
                    reload_interval_hours=ml.models.reload_interval_hours,
                    direction_n_estimators=ml.models.direction.n_estimators,
                    direction_max_depth=ml.models.direction.max_depth,
                    direction_learning_rate=ml.models.direction.learning_rate,
                    levels_n_estimators=ml.models.levels.n_estimators,
                    levels_max_depth=ml.models.levels.max_depth,
                    levels_learning_rate=ml.models.levels.learning_rate,
                    lifetime_n_estimators=ml.models.lifetime.n_estimators,
                    lifetime_max_depth=ml.models.lifetime.max_depth,
                    lifetime_learning_rate=ml.models.lifetime.learning_rate,
                    calibration_method=ml.models.confidence.calibration_method,
                    cv_folds=ml.models.confidence.cv_folds,
                ),
                training=TrainingConfig(
                    test_size=ml.training.test_size,
                    validation_size=ml.training.validation_size,
                    shuffle=ml.training.shuffle,
                    cv_type=ml.training.cv_type,
                    cv_folds=ml.training.cv_folds,
                    early_stopping_rounds=ml.training.early_stopping_rounds,
                    retrain_interval_days=ml.training.retrain_interval_days,
                    min_new_samples=ml.training.min_new_samples,
                ),
                optimization=OptimizationConfig(
                    min_confidence=ml.optimization.min_confidence,
                    max_sl_adjustment_pct=ml.optimization.max_sl_adjustment_pct,
                    max_tp_adjustment_pct=ml.optimization.max_tp_adjustment_pct,
                    max_entry_adjustment_pct=ml.optimization.max_entry_adjustment_pct,
                    min_predicted_winrate=ml.optimization.min_predicted_winrate,
                    min_predicted_rr=ml.optimization.min_predicted_rr,
                    symbol_cooldown_minutes=ml.optimization.symbol_cooldown_minutes,
                ),
                risk=RiskConfig(
                    position_sizing_method=ml.risk.position_sizing_method,
                    kelly_fraction=ml.risk.kelly_fraction,
                    max_position_pct=ml.risk.max_position_pct,
                    min_position_pct=ml.risk.min_position_pct,
                    max_daily_trades=ml.risk.max_daily_trades,
                    max_open_positions=ml.risk.max_open_positions,
                    max_daily_loss_pct=ml.risk.max_daily_loss_pct,
                    max_drawdown_pct=ml.risk.max_drawdown_pct,
                    max_correlated_positions=ml.risk.max_correlated_positions,
                    correlation_threshold=ml.risk.correlation_threshold,
                ),
                metrics=MetricsConfig(
                    primary_metric=ml.metrics.primary_metric,
                    min_sharpe_ratio=ml.metrics.min_sharpe_ratio,
                    min_profit_factor=ml.metrics.min_profit_factor,
                    min_win_rate=ml.metrics.min_win_rate,
                    max_max_drawdown=ml.metrics.max_max_drawdown,
                ),
                monitoring=MonitoringConfig(
                    baseline_accuracy=ml.monitoring.baseline_accuracy,
                    baseline_sharpe=ml.monitoring.baseline_sharpe,
                    drift_check_window=ml.monitoring.drift_check_window,
                    max_accuracy_drop=ml.monitoring.max_accuracy_drop,
                    max_price_change_24h_pct=ml.monitoring.max_price_change_24h_pct,
                    max_volume_spike_ratio=ml.monitoring.max_volume_spike_ratio,
                    extreme_funding_rate=ml.monitoring.extreme_funding_rate,
                    consecutive_losses_alert=ml.monitoring.consecutive_losses_alert,
                    daily_loss_alert_pct=ml.monitoring.daily_loss_alert_pct,
                ),
            )

            logger.info("ml_config_loaded_from_settings")
            return config

        except Exception as e:
            logger.warning(
                "failed_to_load_ml_config_using_defaults",
                error=str(e),
            )
            return cls()

    def validate(self) -> List[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Training validation
        if self.training.shuffle:
            errors.append("training.shuffle must be False for time series data")

        if self.training.min_train_samples < 100:
            errors.append("training.min_train_samples must be >= 100")

        # Risk validation
        if self.risk.kelly_fraction > 0.5:
            errors.append("risk.kelly_fraction > 0.5 is too aggressive")

        if self.risk.max_position_pct > 10:
            errors.append("risk.max_position_pct > 10% is too risky")

        # Optimization validation
        if self.optimization.min_confidence < 0.5:
            errors.append("optimization.min_confidence should be >= 0.5")

        return errors
