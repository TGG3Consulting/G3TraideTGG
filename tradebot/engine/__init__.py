# -*- coding: utf-8 -*-
"""
Trade Engine - Исполнение торговых сигналов.

Заменяет backtest_signals() для LIVE торговли.

Компоненты:
- TradeEngine: исполнение сигналов (execute_signal)
- PositionManager: мониторинг позиций через WebSocket
- StateManager: сохранение/восстановление состояния при shutdown/startup
- RegimeFilter: динамический фильтр режима рынка (BTC/ALT/MIXED)
"""

from .trade_engine import TradeEngine
from .position_manager import PositionManager
from .state_manager import StateManager
from .metrics import MetricsTracker
from .regime_filter import RegimeFilter

__all__ = ["TradeEngine", "PositionManager", "StateManager", "MetricsTracker", "RegimeFilter"]
