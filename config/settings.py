# -*- coding: utf-8 -*-
"""
Unified Configuration Loader for BinanceFriend.

Загружает конфигурацию из config.yaml с fallback на дефолтные значения.
Дефолты = текущие захардкоженные значения из кода.

Usage:
    from config.settings import settings

    print(settings.spot.volume_spike_warning)  # 5.0
    print(settings.futures.oi_spike_critical)  # 30.0
"""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import structlog

try:
    import yaml
except ImportError:
    yaml = None

logger = structlog.get_logger(__name__)


# =============================================================================
# SPOT DETECTION CONFIG
# =============================================================================

@dataclass
class SpotDetectionConfig:
    """
    Spot detection thresholds.

    Source: src/screener/detection_engine.py
    """
    # Volume spikes (множитель от среднего)
    volume_spike_warning: float = 5.0      # 5x - ранний сигнал
    volume_spike_alert: float = 12.0       # 12x - активная фаза
    volume_spike_critical: float = 25.0    # 25x - экстремальный

    # Price velocity (% изменения цены)
    price_velocity_1m_alert: float = 5.0       # 5% за минуту
    price_velocity_5m_alert: float = 10.0      # 10% за 5 минут
    price_velocity_5m_critical: float = 25.0   # 25% за 5 минут

    # Order book imbalance (доля 0-1)
    imbalance_warning: float = 0.55    # 55% перекос
    imbalance_alert: float = 0.75      # 75% перекос

    # Wide spread thresholds (%)
    spread_warning: float = 2.0        # 2% спред
    spread_critical: float = 3.0       # 3% спред

    # Trade patterns
    wash_trade_threshold: float = 0.25       # 25% одинаковых размеров
    wash_trade_critical: float = 0.50        # 50% - likely wash trading
    coordinated_buy_threshold: float = 0.80  # 80% в одну сторону
    coordinated_extreme: float = 0.90        # 90% - very suspicious

    # Rapid fire trades (ms между трейдами)
    rapid_fire_alert_ms: float = 20.0       # < 20ms - bot activity
    rapid_fire_warning_ms: float = 50.0     # < 50ms - high frequency

    # Pump sequence thresholds
    pump_volume_multiplier: float = 10.0    # volume > 10x
    pump_price_change: float = 15.0         # price > 15%
    pump_imbalance: float = 0.50            # book > 50% imbalanced
    pump_one_sided: float = 0.80            # 80% buys/sells

    # Deduplication intervals (seconds)
    dedup_critical_sec: int = 30
    dedup_alert_sec: int = 30
    dedup_warning_sec: int = 30
    dedup_default_sec: int = 30

    # Minimum trades for pattern analysis
    min_trades_for_pattern: int = 20


# =============================================================================
# FUTURES DETECTION CONFIG
# =============================================================================

@dataclass
class FuturesDetectionConfig:
    """
    Futures detection thresholds (OI, Funding, L/S Ratio).

    Source: src/screener/futures_monitor.py
    """
    # Open Interest spikes (% изменения)
    oi_spike_warning: float = 10.0     # 10% рост OI за час
    oi_spike_alert: float = 20.0       # 20% рост OI за час
    oi_spike_critical: float = 30.0    # 30% рост OI за час

    # Open Interest drops (% изменения, отрицательные)
    oi_drop_warning: float = -8.0      # -8% падение OI
    oi_drop_alert: float = -12.0       # -12% падение OI

    # Funding Rate thresholds (в процентах: 0.05 = 0.05%)
    # Норма: -0.01% до +0.01%
    # Высокий: > 0.03% или < -0.02%
    funding_extreme_positive: float = 0.05    # 0.05% = очень дорогие лонги
    funding_extreme_negative: float = -0.03   # -0.03% = дорогие шорты

    # Funding Gradient (изменение за 3 периода, %)
    funding_gradient_threshold: float = 0.03  # 0.03% за 3 периода

    # Long/Short Ratio thresholds (% аккаунтов)
    ls_extremely_long: float = 70.0    # >70% лонгов = overcrowded
    ls_extremely_short: float = 55.0   # >55% шортов = squeeze setup

    # OI + Price Divergence (%)
    divergence_price_threshold: float = 1.0   # Цена изменилась > 1%
    divergence_oi_threshold: float = 2.0      # OI изменился > 2%

    # Accumulation Detector (%)
    accumulation_price_range: float = 2.0      # Цена в диапазоне < 2%
    accumulation_oi_growth: float = 5.0        # OI вырос > 5%
    accumulation_funding_min: float = -0.01    # Funding между -0.01%
    accumulation_funding_max: float = 0.01     # и +0.01%

    # Deduplication intervals (seconds)
    dedup_oi_sec: int = 30
    dedup_funding_sec: int = 30
    dedup_ls_sec: int = 30
    dedup_divergence_sec: int = 30
    dedup_accumulation_sec: int = 30

    # Update intervals (seconds)
    oi_update_interval_sec: int = 60         # OI каждую минуту
    funding_update_interval_sec: int = 300   # Funding каждые 5 мин
    ls_ratio_update_interval_sec: int = 300  # L/S ratio каждые 5 мин

    # Pump risk score weights
    pump_risk_oi_high: int = 30        # OI > 15%: +30
    pump_risk_oi_medium: int = 20      # OI > 10%: +20
    pump_risk_funding_neg: int = 25    # Funding negative: +25
    pump_risk_crowd_bearish: int = 30  # Crowd extremely short: +30
    pump_risk_recovery: int = 15       # OI recovering: +15


# =============================================================================
# VULNERABILITY FILTER CONFIG
# =============================================================================

@dataclass
class FilterConfig:
    """
    Vulnerability filter thresholds.

    Source: src/screener/vulnerability_filter.py
    """
    # Volume thresholds (USD)
    max_volume_usd: float = 1_000_000.0    # Макс объём $1M
    min_volume_usd: float = 1_000.0        # Мин объём $1K

    # Order book depth (USD to move price 2%)
    max_depth_usd: float = 50_000.0        # Макс глубина $50K

    # Spread threshold (%)
    min_spread_pct: float = 0.3            # Мин спред 0.3%

    # Trade count thresholds
    max_trade_count: int = 50_000          # Макс трейдов в день
    min_trade_count: int = 100             # Мин трейдов

    # Depth vulnerability levels (USD)
    depth_critical: float = 5_000.0        # < $5K = CRITICAL
    depth_high: float = 20_000.0           # < $20K = HIGH
    depth_medium: float = 50_000.0         # < $50K = MEDIUM

    # Spread vulnerability levels (%)
    spread_critical: float = 2.0           # > 2% = adds to HIGH
    spread_high: float = 1.0               # > 1% = adds to MEDIUM
    spread_elevated: float = 0.5           # > 0.5% = adds to LOW

    # Volume vulnerability levels (USD)
    volume_very_low: float = 50_000.0      # < $50K = HIGH
    volume_low: float = 200_000.0          # < $200K = MEDIUM

    # Trade count vulnerability levels
    trades_few: int = 1_000                # < 1000 = MEDIUM
    trades_low: int = 5_000                # < 5000 = LOW

    # Minimum vulnerability level to include
    min_vulnerability_level: int = 2       # MEDIUM = 2

    # Excluded base assets (top coins, stablecoins, wrapped)
    excluded_base_assets: Tuple[str, ...] = (
        # Top coins
        "BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE", "DOT", "MATIC",
        "SHIB", "LTC", "AVAX", "LINK", "TRX", "ATOM", "XMR", "ETC", "BCH",
        "XLM", "ALGO", "VET", "FIL", "ICP", "HBAR", "APT", "ARB", "OP",
        # Stablecoins
        "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "USDD",
        # Wrapped
        "WBTC", "WETH", "WBNB",
    )


# =============================================================================
# WEBSOCKET CONFIG
# =============================================================================

@dataclass
class WebSocketConfig:
    """
    WebSocket and REST API settings.

    Source: src/screener/realtime_monitor.py
    """
    # URLs
    ws_url: str = "wss://stream.binance.com:9443/stream"
    rest_url: str = "https://api.binance.com"
    futures_url: str = "https://fapi.binance.com"

    # Connection limits
    max_streams_per_connection: int = 200  # Лимит Binance

    # Reconnection settings
    reconnect_delay_sec: int = 5
    max_reconnect_attempts: int = 10

    # Ping/pong
    ping_interval_sec: int = 20
    ping_timeout_sec: int = 10
    close_timeout_sec: int = 5

    # Baseline warmup
    warmup_klines: int = 60          # 60 минутных свечей = 1 час истории
    warmup_concurrent: int = 10      # Параллельные запросы


# =============================================================================
# SCREENER CONFIG
# =============================================================================

@dataclass
class ScreenerConfig:
    """
    Main screener settings.

    Source: src/screener/screener.py
    """
    # Scan intervals
    rescan_interval_sec: int = 300       # 5 минут между сканами

    # Symbol limits
    max_monitored_symbols: int = 100     # Максимум пар для мониторинга


# =============================================================================
# TELEGRAM CONFIG
# =============================================================================

@dataclass
class TelegramConfigSettings:
    """
    Telegram notification settings.

    Source: src/screener/telegram_notifier.py
    Note: Credentials loaded from config/telegram.json
    """
    # Minimum severity to send
    min_severity: str = "WARNING"  # INFO, WARNING, ALERT, CRITICAL

    # Rate limiting (Telegram limit: 30 msg/sec)
    max_messages_per_minute: int = 20

    # Send startup/shutdown messages
    send_startup_message: bool = True
    send_shutdown_message: bool = True


# =============================================================================
# ALERT DISPATCHER CONFIG
# =============================================================================

@dataclass
class DispatcherConfig:
    """
    Alert dispatcher settings.

    Source: src/screener/alert_dispatcher.py
    """
    # Minimum severity to dispatch
    min_severity: str = "WARNING"  # INFO, WARNING, ALERT, CRITICAL

    # Batching
    batch_size: int = 10
    batch_interval_sec: int = 5

    # Retry settings
    max_retries: int = 3
    retry_delay_sec: int = 2

    # Local logging
    log_to_file: bool = True
    log_file_path: str = "logs/alerts.jsonl"


# =============================================================================
# RATE LIMIT CONFIG
# =============================================================================

@dataclass
class RateLimitConfig:
    """
    Rate limiting settings for API requests.

    Source: src/screener/futures_monitor.py, vulnerability_filter.py
    """
    # Futures API
    futures_max_concurrent: int = 5
    futures_request_delay_sec: float = 0.2   # 200ms между запросами

    # Spot API (vulnerability filter)
    spot_max_concurrent: int = 3             # Уменьшено с 10 до 3
    spot_request_delay_sec: float = 0.3      # 300ms между запросами

    # General timeouts
    request_timeout_sec: int = 30
    short_timeout_sec: int = 10
    long_timeout_sec: int = 15


# =============================================================================
# HISTORY LOADER CONFIG
# =============================================================================

@dataclass
class HistoryKlinesConfig:
    """Klines history loading settings."""
    enabled: bool = True
    hours: int = 2           # Сколько часов истории загружать
    interval: str = "1m"     # Интервал свечей


@dataclass
class HistoryFundingConfig:
    """Funding rate history loading settings."""
    enabled: bool = True
    hours: int = 72          # 72 часа = 9 записей (8h интервал)


@dataclass
class HistoryOIConfig:
    """Open Interest history loading settings."""
    enabled: bool = True
    hours: int = 2           # Сколько часов истории загружать
    period: str = "5m"       # Период данных OI


@dataclass
class HistoryTradesConfig:
    """Aggregated trades history loading settings."""
    enabled: bool = False    # По умолчанию выключено (много данных)
    minutes: int = 30        # Сколько минут истории


@dataclass
class HistoryCrossExchangeConfig:
    """Cross-exchange history loading settings."""
    enabled: bool = True
    exchanges: List[str] = field(default_factory=lambda: ["binance", "bybit", "okx"])
    hours: int = 1           # Сколько часов истории для каждой биржи


@dataclass
class HistoryConfig:
    """
    Historical data loading configuration.

    Source: src/screener/history_loader.py
    Design: HISTORY_LOADER_DESIGN.md
    """
    enabled: bool = True
    parallel_requests: int = 10       # Параллельность загрузки
    rate_limit_delay_ms: int = 100    # Задержка между запросами
    request_timeout_sec: int = 15     # Таймаут запроса

    # Sub-configs
    klines: HistoryKlinesConfig = field(default_factory=HistoryKlinesConfig)
    funding: HistoryFundingConfig = field(default_factory=HistoryFundingConfig)
    oi: HistoryOIConfig = field(default_factory=HistoryOIConfig)
    trades: HistoryTradesConfig = field(default_factory=HistoryTradesConfig)
    cross_exchange: HistoryCrossExchangeConfig = field(default_factory=HistoryCrossExchangeConfig)


# =============================================================================
# SIGNALS CONFIG
# =============================================================================

@dataclass
class SignalsConfig:
    """
    Trading signal generation configuration.

    Source: src/signals/models.py, src/signals/signal_generator.py
    """
    enabled: bool = True

    # Минимальные пороги для генерации сигнала
    min_accumulation_score: int = 65   # Мин скор накопления
    min_probability: int = 60          # Мин вероятность успеха

    # Уровни уверенности
    confidence_low: int = 50           # <50 = LOW
    confidence_medium: int = 65        # 50-65 = MEDIUM
    confidence_high: int = 80          # 65-80 = HIGH
    confidence_very_high: int = 90     # >90 = VERY HIGH

    # Риск-менеджмент
    default_sl_pct: float = 7.0        # Дефолтный стоп-лосс %
    min_risk_reward: float = 2.0       # Минимальный R:R для сигнала

    # Take Profit уровни (множители от риска)
    tp1_ratio: float = 1.5             # TP1 = 1.5x риск
    tp2_ratio: float = 3.0             # TP2 = 3x риск
    tp3_ratio: float = 5.0             # TP3 = 5x риск

    # Распределение закрытия по TP
    tp1_portion: int = 30              # 30% на TP1
    tp2_portion: int = 40              # 40% на TP2
    tp3_portion: int = 30              # 30% на TP3

    # Время действия сигнала
    default_valid_hours: int = 24

    # OI score factors
    oi_growth_min: float = 5.0         # Мин рост OI для скора
    oi_growth_strong: float = 15.0     # Сильный рост OI

    # Funding score factors
    funding_cheap_threshold: float = -0.01    # Funding < -0.01% = дешёвые лонги
    funding_extreme_threshold: float = 0.05   # Funding > 0.05% = поздно входить

    # Crowd sentiment factors
    crowd_short_threshold: float = 55.0       # >55% шортов = contrarian signal
    crowd_extreme_short: float = 60.0         # >60% шортов = strong signal


# =============================================================================
# ML CONFIG
# =============================================================================

@dataclass
class MLDataConfig:
    """ML data collection settings."""
    history_days: int = 90
    min_samples_per_symbol: int = 1000
    kline_intervals: List[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h"])
    min_volume_usd_24h: float = 10_000_000
    min_oi_usd: float = 5_000_000
    request_delay_ms: int = 100
    max_concurrent_requests: int = 5


@dataclass
class MLFeaturesConfig:
    """ML feature engineering settings."""
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

    # Feature selection
    normalize: bool = True
    scaler_type: str = "robust"
    max_features: int = 100
    min_importance: float = 0.01


@dataclass
class MLModelsConfig:
    """ML models settings."""
    save_dir: str = "models/ml"

    # Common params
    model_type: str = "lightgbm"
    n_estimators: int = 500
    max_depth: int = 8
    learning_rate: float = 0.05
    min_samples_leaf: int = 50

    # Confidence calibration
    calibration_method: str = "isotonic"
    cv_folds: int = 5


@dataclass
class MLTrainingConfig:
    """ML training settings."""
    test_size: float = 0.2
    validation_size: float = 0.1
    shuffle: bool = False  # Time series - no shuffle!

    cv_type: str = "time_series"
    cv_folds: int = 5
    early_stopping_rounds: int = 50

    retrain_interval_days: int = 7
    min_new_samples: int = 500


@dataclass
class MLOptimizationConfig:
    """ML signal optimization settings."""
    min_confidence: float = 0.6

    max_sl_adjustment_pct: float = 30.0
    max_tp_adjustment_pct: float = 50.0
    max_entry_adjustment_pct: float = 5.0

    min_predicted_winrate: float = 0.55
    min_predicted_rr: float = 1.5

    symbol_cooldown_minutes: int = 15


@dataclass
class MLRiskConfig:
    """ML risk management settings."""
    position_sizing_method: str = "kelly"

    kelly_fraction: float = 0.25
    max_position_pct: float = 5.0
    min_position_pct: float = 0.5

    max_daily_trades: int = 20
    max_open_positions: int = 5
    max_daily_loss_pct: float = 10.0
    max_drawdown_pct: float = 20.0

    max_correlated_positions: int = 3
    correlation_threshold: float = 0.7


@dataclass
class MLMetricsConfig:
    """ML metrics and evaluation settings."""
    primary_metric: str = "sharpe_ratio"

    min_sharpe_ratio: float = 1.0
    min_profit_factor: float = 1.3
    min_win_rate: float = 0.5
    max_max_drawdown: float = 0.25

    track_metrics: List[str] = field(default_factory=lambda: [
        "win_rate", "profit_factor", "sharpe_ratio",
        "max_drawdown", "avg_trade_pnl", "trades_per_day"
    ])


@dataclass
class MLConfig:
    """
    Main ML configuration.

    Source: config/ml_config.yaml
    """
    enabled: bool = False  # Enable after model training

    data: MLDataConfig = field(default_factory=MLDataConfig)
    features: MLFeaturesConfig = field(default_factory=MLFeaturesConfig)
    models: MLModelsConfig = field(default_factory=MLModelsConfig)
    training: MLTrainingConfig = field(default_factory=MLTrainingConfig)
    optimization: MLOptimizationConfig = field(default_factory=MLOptimizationConfig)
    risk: MLRiskConfig = field(default_factory=MLRiskConfig)
    metrics: MLMetricsConfig = field(default_factory=MLMetricsConfig)


# =============================================================================
# LOGGING CONFIG
# =============================================================================

@dataclass
class LoggingConfig:
    """
    Logging settings.

    Source: config/config.yaml (reference)
    """
    level: str = "INFO"          # DEBUG, INFO, WARNING, ERROR
    format: str = "console"      # console, json
    file_path: str = "logs/screener.log"
    max_size_mb: int = 100
    backup_count: int = 5


# =============================================================================
# EXCHANGE CONFIG
# =============================================================================

@dataclass
class ExchangeRateLimitConfig:
    """Rate limit settings for a single exchange."""
    requests_per_second: int = 10
    requests_per_minute: int = 600
    weight_per_minute: int = 1200
    ws_connections_max: int = 5
    ws_streams_per_connection: int = 200


@dataclass
class SingleExchangeConfig:
    """Configuration for a single exchange."""
    enabled: bool = True
    type: str = "CEX"  # CEX or DEX

    # Endpoints
    ws_url: Optional[str] = None
    ws_futures_url: Optional[str] = None
    rest_url: Optional[str] = None
    rest_futures_url: Optional[str] = None

    # Rate limits
    rate_limit: ExchangeRateLimitConfig = field(
        default_factory=ExchangeRateLimitConfig
    )


@dataclass
class CrossExchangePriceDivergenceConfig:
    """Price divergence detection thresholds."""
    threshold_low: float = 0.1
    threshold_medium: float = 0.3
    threshold_high: float = 0.5
    threshold_critical: float = 1.0


@dataclass
class CrossExchangeFundingArbitrageConfig:
    """Funding arbitrage detection thresholds."""
    threshold_low: float = 0.01
    threshold_medium: float = 0.03
    threshold_high: float = 0.05
    threshold_critical: float = 0.1


@dataclass
class CrossExchangeOIDivergenceConfig:
    """OI divergence detection thresholds."""
    threshold_low: float = 5.0
    threshold_medium: float = 10.0
    threshold_high: float = 20.0
    threshold_critical: float = 30.0


@dataclass
class CrossExchangeVolumeCorrelationConfig:
    """Volume correlation (wash trading) detection."""
    suspicious_threshold: float = 0.95
    min_data_points: int = 60


@dataclass
class CrossExchangeLeadLagConfig:
    """Lead-lag detection settings."""
    min_lag_seconds: float = 0.5
    max_lag_seconds: float = 30.0
    min_samples: int = 10


@dataclass
class CrossExchangeGeneralConfig:
    """General cross-exchange settings."""
    min_exchanges: int = 2
    max_data_age_sec: int = 60
    check_interval_sec: int = 5


@dataclass
class CrossExchangeLiquidityHuntConfig:
    """CX-005: Liquidity Hunt detection settings."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Price movement thresholds
    price_drop_threshold: float = 0.02
    price_spike_threshold: float = 0.02

    # Recovery detection
    recovery_window_sec: int = 300
    recovery_threshold: float = 0.5

    # Alert thresholds
    warning_threshold: float = 0.02
    alert_threshold: float = 0.03
    critical_threshold: float = 0.05

    # Orderbook imbalance
    imbalance_threshold: float = 0.7


@dataclass
class CrossExchangeSpoofingCrossConfig:
    """CX-006: Cross-Exchange Spoofing detection settings."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Orderbook imbalance threshold
    imbalance_threshold: float = 0.80

    # Volume spike threshold
    volume_spike_threshold: float = 5.0

    # Wall lifetime
    wall_lifetime_sec: int = 30

    # Alert thresholds (confidence score)
    warning_threshold: float = 0.5
    alert_threshold: float = 0.7
    critical_threshold: float = 0.85


@dataclass
class CrossExchangeOrchestratorConfig:
    """Detector orchestrator settings."""
    # Enable/disable individual detectors
    enable_price_divergence: bool = True
    enable_volume_correlation: bool = True
    enable_funding_arbitrage: bool = True
    enable_oi_migration: bool = True
    enable_liquidity_hunt: bool = True
    enable_spoofing_cross: bool = True

    # Analysis settings
    parallel_analysis: bool = True
    max_concurrent_symbols: int = 50

    # Result filtering
    min_severity: str = "WARNING"


@dataclass
class CrossExchangeConfig:
    """Cross-exchange detection configuration."""
    price_divergence: CrossExchangePriceDivergenceConfig = field(
        default_factory=CrossExchangePriceDivergenceConfig
    )
    funding_arbitrage: CrossExchangeFundingArbitrageConfig = field(
        default_factory=CrossExchangeFundingArbitrageConfig
    )
    oi_divergence: CrossExchangeOIDivergenceConfig = field(
        default_factory=CrossExchangeOIDivergenceConfig
    )
    volume_correlation: CrossExchangeVolumeCorrelationConfig = field(
        default_factory=CrossExchangeVolumeCorrelationConfig
    )
    lead_lag: CrossExchangeLeadLagConfig = field(
        default_factory=CrossExchangeLeadLagConfig
    )
    liquidity_hunt: CrossExchangeLiquidityHuntConfig = field(
        default_factory=CrossExchangeLiquidityHuntConfig
    )
    spoofing_cross: CrossExchangeSpoofingCrossConfig = field(
        default_factory=CrossExchangeSpoofingCrossConfig
    )
    orchestrator: CrossExchangeOrchestratorConfig = field(
        default_factory=CrossExchangeOrchestratorConfig
    )
    general: CrossExchangeGeneralConfig = field(
        default_factory=CrossExchangeGeneralConfig
    )


@dataclass
class ExchangesConfig:
    """Configuration for all exchanges."""
    binance: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    bybit: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    okx: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    bitget: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    gate: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    mexc: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    kucoin: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    bingx: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    htx: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    bitmart: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    hyperliquid: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    asterdex: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)
    lighter: SingleExchangeConfig = field(default_factory=SingleExchangeConfig)

    def get(self, name: str) -> Optional[SingleExchangeConfig]:
        """Get exchange config by name."""
        return getattr(self, name.lower(), None)

    def enabled_exchanges(self) -> List[str]:
        """Get list of enabled exchange names."""
        return [
            name for name in [
                "binance", "bybit", "okx", "bitget", "gate",
                "mexc", "kucoin", "bingx", "htx", "bitmart",
                "hyperliquid", "asterdex", "lighter"
            ]
            if getattr(self, name).enabled
        ]


# =============================================================================
# SETTINGS LOADER
# =============================================================================

class Settings:
    """
    Unified configuration loader.

    Loads from config.yaml with fallback to hardcoded defaults.
    Defaults = current values from code (not from outdated yaml).

    Usage:
        from config.settings import settings

        # Access config values
        threshold = settings.spot.volume_spike_warning

        # Reload from file
        settings.reload()
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self._config_path = Path(config_path)
        self._raw: dict = {}

        # Initialize all config sections with defaults
        self.spot = SpotDetectionConfig()
        self.futures = FuturesDetectionConfig()
        self.filter = FilterConfig()
        self.websocket = WebSocketConfig()
        self.screener = ScreenerConfig()
        self.telegram = TelegramConfigSettings()
        self.dispatcher = DispatcherConfig()
        self.rate_limit = RateLimitConfig()
        self.logging = LoggingConfig()
        self.history = HistoryConfig()
        self.signals = SignalsConfig()

        # Cross-exchange config
        self.exchanges = ExchangesConfig()
        self.cross_exchange = CrossExchangeConfig()

        # ML config
        self.ml = MLConfig()

        # Load overrides from file
        self._load()
        self._load_ml_config()

    def _load(self):
        """Load configuration from YAML file."""
        if yaml is None:
            logger.warning("pyyaml_not_installed", message="Using defaults only")
            return

        if not self._config_path.exists():
            logger.info(
                "config_file_not_found",
                path=str(self._config_path),
                message="Using defaults"
            )
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._raw = yaml.safe_load(f) or {}

            self._apply_overrides()
            logger.info(
                "config_loaded",
                path=str(self._config_path),
                sections=list(self._raw.keys())
            )
        except Exception as e:
            logger.error(
                "config_load_error",
                path=str(self._config_path),
                error=str(e)
            )

    def _apply_overrides(self):
        """Apply values from yaml over defaults."""
        section_map = {
            "spot": self.spot,
            "spot_detection": self.spot,
            "detection": self.spot,  # Legacy key from old config
            "futures": self.futures,
            "futures_detection": self.futures,
            "filter": self.filter,
            "vulnerability_filter": self.filter,
            "websocket": self.websocket,
            "ws": self.websocket,
            "screener": self.screener,
            "telegram": self.telegram,
            "alerts": self.dispatcher,  # Legacy key
            "dispatcher": self.dispatcher,
            "rate_limit": self.rate_limit,
            "logging": self.logging,
            "signals": self.signals,
        }

        for yaml_key, config_obj in section_map.items():
            if yaml_key in self._raw and isinstance(self._raw[yaml_key], dict):
                self._apply_section(config_obj, self._raw[yaml_key])

        # Handle exchanges config separately (nested structure)
        if "exchanges" in self._raw and isinstance(self._raw["exchanges"], dict):
            self._apply_exchanges_config(self._raw["exchanges"])

        # Handle cross_exchange config
        if "cross_exchange" in self._raw and isinstance(self._raw["cross_exchange"], dict):
            self._apply_cross_exchange_config(self._raw["cross_exchange"])

        # Handle history config
        if "history" in self._raw and isinstance(self._raw["history"], dict):
            self._apply_history_config(self._raw["history"])

    def _apply_exchanges_config(self, exchanges_yaml: dict):
        """Apply exchanges configuration from yaml."""
        for exchange_name, exchange_data in exchanges_yaml.items():
            if not isinstance(exchange_data, dict):
                continue

            # Get or create exchange config
            exchange_config = getattr(self.exchanges, exchange_name.lower(), None)
            if exchange_config is None:
                continue

            # Apply basic fields
            for key in ["enabled", "type", "ws_url", "ws_futures_url",
                        "rest_url", "rest_futures_url"]:
                if key in exchange_data:
                    setattr(exchange_config, key, exchange_data[key])

            # Apply rate_limit nested config
            if "rate_limit" in exchange_data and isinstance(exchange_data["rate_limit"], dict):
                rate_limit_data = exchange_data["rate_limit"]
                for key, value in rate_limit_data.items():
                    if hasattr(exchange_config.rate_limit, key):
                        setattr(exchange_config.rate_limit, key, value)

    def _apply_cross_exchange_config(self, cross_exchange_yaml: dict):
        """Apply cross-exchange detection configuration from yaml."""
        sub_configs = {
            "price_divergence": self.cross_exchange.price_divergence,
            "funding_arbitrage": self.cross_exchange.funding_arbitrage,
            "oi_divergence": self.cross_exchange.oi_divergence,
            "volume_correlation": self.cross_exchange.volume_correlation,
            "lead_lag": self.cross_exchange.lead_lag,
            "liquidity_hunt": self.cross_exchange.liquidity_hunt,
            "spoofing_cross": self.cross_exchange.spoofing_cross,
            "orchestrator": self.cross_exchange.orchestrator,
            "general": self.cross_exchange.general,
        }

        for section_name, config_obj in sub_configs.items():
            if section_name in cross_exchange_yaml:
                section_data = cross_exchange_yaml[section_name]
                if isinstance(section_data, dict):
                    self._apply_section(config_obj, section_data)

    def _apply_history_config(self, history_yaml: dict):
        """Apply history loading configuration from yaml."""
        # Apply top-level history settings
        for key in ["enabled", "parallel_requests", "rate_limit_delay_ms", "request_timeout_sec"]:
            if key in history_yaml:
                setattr(self.history, key, history_yaml[key])

        # Apply sub-configs
        sub_configs = {
            "klines": self.history.klines,
            "funding": self.history.funding,
            "oi": self.history.oi,
            "trades": self.history.trades,
            "cross_exchange": self.history.cross_exchange,
        }

        for section_name, config_obj in sub_configs.items():
            if section_name in history_yaml:
                section_data = history_yaml[section_name]
                if isinstance(section_data, dict):
                    self._apply_section(config_obj, section_data)

    def _apply_section(self, config_obj: Any, yaml_section: dict):
        """Apply yaml values to a config dataclass."""
        for key, value in yaml_section.items():
            # Normalize key (yaml might use different naming)
            normalized_key = key.lower().replace("-", "_")

            if hasattr(config_obj, normalized_key):
                try:
                    # Get expected type from current value
                    current_value = getattr(config_obj, normalized_key)

                    # Convert if needed
                    if isinstance(current_value, float) and isinstance(value, (int, float)):
                        value = float(value)
                    elif isinstance(current_value, int) and isinstance(value, (int, float)):
                        value = int(value)
                    elif isinstance(current_value, tuple) and isinstance(value, list):
                        value = tuple(value)

                    setattr(config_obj, normalized_key, value)
                    logger.debug(
                        "config_override",
                        key=normalized_key,
                        value=value
                    )
                except Exception as e:
                    logger.warning(
                        "config_override_failed",
                        key=normalized_key,
                        error=str(e)
                    )

    def _load_ml_config(self):
        """Load ML configuration from ml_config.yaml."""
        if yaml is None:
            return

        ml_config_path = Path("config/ml_config.yaml")
        if not ml_config_path.exists():
            logger.debug("ml_config_not_found", path=str(ml_config_path))
            return

        try:
            with open(ml_config_path, "r", encoding="utf-8") as f:
                ml_raw = yaml.safe_load(f) or {}

            ml_section = ml_raw.get("ml", {})
            if not ml_section:
                return

            # Apply top-level ml settings
            if "enabled" in ml_section:
                self.ml.enabled = ml_section["enabled"]

            # Apply sub-configs
            sub_configs = {
                "data": self.ml.data,
                "features": self.ml.features,
                "models": self.ml.models,
                "training": self.ml.training,
                "optimization": self.ml.optimization,
                "risk": self.ml.risk,
                "metrics": self.ml.metrics,
            }

            for section_name, config_obj in sub_configs.items():
                if section_name in ml_section:
                    section_data = ml_section[section_name]
                    if isinstance(section_data, dict):
                        self._apply_section(config_obj, section_data)

            logger.info("ml_config_loaded", path=str(ml_config_path))

        except Exception as e:
            logger.error("ml_config_load_error", error=str(e))

    def reload(self):
        """Reload configuration from file."""
        # Reset to defaults
        self.spot = SpotDetectionConfig()
        self.futures = FuturesDetectionConfig()
        self.filter = FilterConfig()
        self.websocket = WebSocketConfig()
        self.screener = ScreenerConfig()
        self.telegram = TelegramConfigSettings()
        self.dispatcher = DispatcherConfig()
        self.rate_limit = RateLimitConfig()
        self.logging = LoggingConfig()
        self.history = HistoryConfig()
        self.signals = SignalsConfig()
        self.exchanges = ExchangesConfig()
        self.cross_exchange = CrossExchangeConfig()
        self.ml = MLConfig()

        # Reload from file
        self._load()
        self._load_ml_config()

    def get_raw(self, section: Optional[str] = None) -> dict:
        """Get raw YAML data."""
        if section:
            return self._raw.get(section, {})
        return self._raw.copy()

    def to_dict(self) -> dict:
        """Export all settings as dictionary."""
        from dataclasses import asdict
        return {
            "spot": asdict(self.spot),
            "futures": asdict(self.futures),
            "filter": {
                **asdict(self.filter),
                "excluded_base_assets": list(self.filter.excluded_base_assets),
            },
            "websocket": asdict(self.websocket),
            "screener": asdict(self.screener),
            "telegram": asdict(self.telegram),
            "dispatcher": asdict(self.dispatcher),
            "rate_limit": asdict(self.rate_limit),
            "logging": asdict(self.logging),
            "history": asdict(self.history),
            "exchanges": asdict(self.exchanges),
            "cross_exchange": asdict(self.cross_exchange),
        }

    def print_summary(self):
        """Print configuration summary."""
        print("\n" + "=" * 60)
        print("BINANCEFRIEND CONFIGURATION")
        print("=" * 60)

        print("\n[SPOT DETECTION]")
        print(f"  Volume spikes: {self.spot.volume_spike_warning}x / "
              f"{self.spot.volume_spike_alert}x / {self.spot.volume_spike_critical}x")
        print(f"  Price velocity: {self.spot.price_velocity_1m_alert}% (1m) / "
              f"{self.spot.price_velocity_5m_alert}% (5m)")
        print(f"  Dedup: {self.spot.dedup_critical_sec}s / "
              f"{self.spot.dedup_alert_sec}s / {self.spot.dedup_warning_sec}s")

        print("\n[FUTURES DETECTION]")
        print(f"  OI spikes: {self.futures.oi_spike_warning}% / "
              f"{self.futures.oi_spike_alert}% / {self.futures.oi_spike_critical}%")
        print(f"  Funding extreme: +{self.futures.funding_extreme_positive}% / "
              f"{self.futures.funding_extreme_negative}%")
        print(f"  L/S extreme: {self.futures.ls_extremely_long}% long / "
              f"{self.futures.ls_extremely_short}% short")

        print("\n[VULNERABILITY FILTER]")
        print(f"  Volume: ${self.filter.min_volume_usd:,.0f} - "
              f"${self.filter.max_volume_usd:,.0f}")
        print(f"  Depth: < ${self.filter.max_depth_usd:,.0f}")
        print(f"  Excluded: {len(self.filter.excluded_base_assets)} assets")

        print("\n[SCREENER]")
        print(f"  Rescan interval: {self.screener.rescan_interval_sec}s")
        print(f"  Max symbols: {self.screener.max_monitored_symbols}")

        print("=" * 60 + "\n")


# =============================================================================
# GLOBAL SINGLETON
# =============================================================================

# Determine config path relative to this file or project root
_config_dir = Path(__file__).parent
_config_file = _config_dir / "config.yaml"

# Fallback to project root config
if not _config_file.exists():
    _project_root = _config_dir.parent
    _config_file = _project_root / "config" / "config.yaml"

settings = Settings(str(_config_file))


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def get_spot_config() -> SpotDetectionConfig:
    """Get spot detection config."""
    return settings.spot


def get_futures_config() -> FuturesDetectionConfig:
    """Get futures detection config."""
    return settings.futures


def get_filter_config() -> FilterConfig:
    """Get vulnerability filter config."""
    return settings.filter


def get_websocket_config() -> WebSocketConfig:
    """Get WebSocket config."""
    return settings.websocket


def reload_settings():
    """Reload settings from file."""
    settings.reload()


def get_exchanges_config() -> ExchangesConfig:
    """Get exchanges config."""
    return settings.exchanges


def get_cross_exchange_config() -> CrossExchangeConfig:
    """Get cross-exchange detection config."""
    return settings.cross_exchange


def get_history_config() -> HistoryConfig:
    """Get history loading config."""
    return settings.history


def get_exchange(name: str) -> Optional[SingleExchangeConfig]:
    """Get config for specific exchange."""
    return settings.exchanges.get(name)


def get_signals_config() -> SignalsConfig:
    """Get trading signals config."""
    return settings.signals


# =============================================================================
# CLI TEST
# =============================================================================

if __name__ == "__main__":
    print("Testing settings loader...\n")

    # Print summary
    settings.print_summary()

    # Count parameters
    from dataclasses import fields

    total_params = 0
    for section_name in ["spot", "futures", "filter", "websocket",
                         "screener", "telegram", "dispatcher", "rate_limit", "logging"]:
        section = getattr(settings, section_name)
        param_count = len(fields(section))
        print(f"{section_name}: {param_count} parameters")
        total_params += param_count

    print(f"\nTOTAL: {total_params} parameters")

    # Verify no syntax errors
    print("\nSyntax check: OK")
