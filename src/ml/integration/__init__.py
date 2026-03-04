# -*- coding: utf-8 -*-
"""
ML Integration module - integration with BinanceFriend.

Components:
- MLIntegration: Main entry point for ML signal optimization
- MLService: Background service for periodic operations
- MLBacktesterIntegration: Integration with REAL backtester (no random!)
"""

from .ml_integration import MLIntegration, MLService
from .backtester_integration import MLBacktesterIntegration

__all__ = [
    "MLIntegration",
    "MLService",
    "MLBacktesterIntegration",
]
