# -*- coding: utf-8 -*-
"""
Configuration for GenerateHistorySignals.
"""

from dataclasses import dataclass


@dataclass
class AppConfig:
    """Application configuration."""

    # Binance API
    base_url: str = "https://fapi.binance.com"
    api_rate_limit_delay: float = 0.1
    max_klines_per_request: int = 1500
    max_records_per_request: int = 500

    # Coinalyze API (for historical OI/LS data older than 30 days)
    # Get free API key at https://coinalyze.net/
    coinalyze_api_key: str = "adb282f9-7e9e-4b6c-a669-b01c0304d506"  # Or set COINALYZE_API_KEY env var

    # Скачивание
    cache_dir: str = "cache"
    symbols_limit: int = 500

    # Генерация сигналов
    kline_interval: str = "1m"
    signal_step_minutes: int = 1440  # шаг = 1 день (дневной таймфрейм)

    # Пороги для сигналов (сбалансированы с cooldown 24h)
    min_accumulation_score: int = 45  # с cooldown 24h это не создаёт шум
    min_probability: int = 45         # базовый порог
    min_risk_reward: float = 1.5      # честный порог без хака TP

    # Вывод
    output_dir: str = "output"
    max_signals_per_file: int = 200000

    # Детекция триггеров
    volume_spike_threshold: float = 1.5
    buy_ratio_threshold: float = 0.7  # Было 0.6, ужесточено (COORDINATED_BUYING убыточен)
    oi_spike_threshold: float = 2.5   # Было 3.0, снижено (OI_SPIKE прибылен, нужно больше сигналов)
    price_momentum_threshold: float = 2.0
    max_volume_spike: float = 2.0     # Spike > 2.0 = FOMO, пропускаем

    @classmethod
    def default(cls) -> "AppConfig":
        """Create default config."""
        return cls()

    @classmethod
    def for_backtesting(cls) -> "AppConfig":
        """Config for maximum signal generation (backtesting dataset)."""
        return cls(
            min_accumulation_score=50,
            min_probability=50,
            min_risk_reward=1.5,
            volume_spike_threshold=1.3,
            buy_ratio_threshold=0.55,
        )
