# -*- coding: utf-8 -*-
"""
ManipBackTester - Конфигурация.
"""

from pathlib import Path
from dataclasses import dataclass
from decimal import Decimal

# Пути
PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
SIGNALS_FILE = LOGS_DIR / "signals.jsonl"
CACHE_DIR = PROJECT_ROOT / "backtester" / "cache" / "klines"
OUTPUT_DIR = PROJECT_ROOT / "backtester" / "output"
GENERATOR_CACHE_DIR = PROJECT_ROOT / "GenerateHistorySignals" / "cache"

# Binance API
BINANCE_FUTURES_URL = "https://fapi.binance.com"

# Rate limits
API_RATE_LIMIT_DELAY = 0.1  # секунд между запросами
MAX_KLINES_PER_REQUEST = 1500


@dataclass
class BacktestConfig:
    """Конфигурация бэктеста."""

    # Пути
    signals_file: Path = SIGNALS_FILE
    cache_dir: Path = CACHE_DIR
    output_dir: Path = OUTPUT_DIR
    generator_cache_dir: Path = GENERATOR_CACHE_DIR  # Кэш от GenerateHistorySignals

    # Интервал свечей
    kline_interval: str = "1m"  # 1 минута для точности

    # Комиссии Binance Futures (актуальные на 2026)
    maker_fee: Decimal = Decimal("0.0002")   # 0.02%
    taker_fee: Decimal = Decimal("0.0005")   # 0.05%

    # Funding rate (средний)
    avg_funding_rate: Decimal = Decimal("0.0001")  # 0.01% каждые 8 часов

    # Симуляция
    assume_limit_entry: bool = True   # Вход по лимитке (maker fee)
    assume_limit_tp: bool = True      # TP по лимитке (maker fee)
    assume_market_sl: bool = True     # SL по рынку (taker fee)

    # Запас времени
    data_padding_before_hours: int = 1   # Загрузить данные за час до сигнала
    data_padding_after_hours: int = 48   # Загрузить данные на 48 часов после

    # Параллелизм
    max_concurrent_downloads: int = 5

    # Вывод
    verbose: bool = True

    def __post_init__(self):
        """Создать директории если не существуют."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
