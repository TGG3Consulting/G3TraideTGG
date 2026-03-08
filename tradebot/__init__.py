# -*- coding: utf-8 -*-
"""
TradeBot - Автоматическое исполнение торговых сигналов.

Архитектура:
- core/ - Ядро (exchange-agnostic модели)
- engine/ - Trade Engine (исполнение сигналов)
- adapters/ - Адаптеры бирж (Binance, Bybit, etc.)
- trade_app.py - Главный лаунчер (как run_all.py, но LIVE)

КЛЮЧЕВОЕ ОТЛИЧИЕ ОТ BACKTESTER:
- backtest_signals() → симуляция на истории
- trade_engine.execute_signal() → РЕАЛЬНЫЕ ордера на бирже

Usage:
    python -m tradebot.trade_app --testnet --symbols BTCUSDT,ETHUSDT
"""

__version__ = "0.2.0"

from .core import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    PositionStatus,
    TradeOrder,
    Position,
    ExchangeInterface,
)
from .engine import TradeEngine, PositionManager
from .adapters import BinanceFuturesAdapter

__all__ = [
    # Core
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "PositionSide",
    "PositionStatus",
    "TradeOrder",
    "Position",
    "ExchangeInterface",
    # Engine
    "TradeEngine",
    "PositionManager",
    # Adapters
    "BinanceFuturesAdapter",
]
