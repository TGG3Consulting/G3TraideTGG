# -*- coding: utf-8 -*-
"""
BinanceFriend - Manipulation Detection Screener

Система обнаружения манипуляций на криптовалютных рынках.
Сканирует все пары Binance, выявляет уязвимые к манипуляциям,
и детектирует подозрительную активность в реальном времени.
"""

__version__ = "2.0.0"
__author__ = "BinanceFriend Team"

# Changelog v2.0.0:
# - Оптимизированные пороги детекции (5x/12x/25x volume, 5%/10%/25% price)
# - Динамический dedup по severity (15s CRITICAL, 30s ALERT, 60s WARNING)
# - Прогрев baseline при старте (fetch 1h klines)
# - Асинхронные callbacks (не блокируют websocket)
# - НОВОЕ: FuturesMonitor - Open Interest, Funding Rate, Long/Short Ratio
# - НОВОЕ: Whale Accumulation Detection (OI spike + stable price)
# - НОВОЕ: Pre-Pump Setup Detection (negative funding + OI growth)
# - НОВОЕ: Short Squeeze Alert (extreme short positioning)
# - НОВОЕ: Корреляция Spot + Futures детекций

from .models import (
    SymbolStats,
    VulnerableSymbol,
    VulnerabilityLevel,
    SymbolState,
    Trade,
    Detection,
    AlertSeverity,
)
from .universe_scanner import UniverseScanner
from .vulnerability_filter import VulnerabilityFilter
from .realtime_monitor import RealTimeMonitor
from .detection_engine import DetectionEngine
from .alert_dispatcher import AlertDispatcher, AlertConfig
from .telegram_notifier import TelegramNotifier, TelegramConfig
from .futures_monitor import FuturesMonitor, FuturesDetection, FuturesState
from .screener import ManipulationScreener

__all__ = [
    # Models
    "SymbolStats",
    "VulnerableSymbol",
    "VulnerabilityLevel",
    "SymbolState",
    "Trade",
    "Detection",
    "AlertSeverity",
    # Components
    "UniverseScanner",
    "VulnerabilityFilter",
    "RealTimeMonitor",
    "DetectionEngine",
    "AlertDispatcher",
    "AlertConfig",
    "TelegramNotifier",
    "TelegramConfig",
    # Futures (Open Interest, Funding, L/S Ratio)
    "FuturesMonitor",
    "FuturesDetection",
    "FuturesState",
    # Main
    "ManipulationScreener",
]
