# -*- coding: utf-8 -*-
"""
ML Optimization module - signal optimization.

Components:
- SignalOptimizer: Applies ML predictions to optimize trading signals
"""

from .optimizer import SignalOptimizer
from .param_calculator import OptimalParamCalculator, OptimalParams

__all__ = [
    "SignalOptimizer",
    "OptimalParamCalculator",
    "OptimalParams",
]
