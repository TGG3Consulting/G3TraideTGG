# -*- coding: utf-8 -*-
"""
Entry point for running as module.

Usage:
    python -m src.ml.feature_selection backtester/output/backtest_YYYYMMDD_HHMMSS.xlsx
"""

from .run_ga import main

if __name__ == "__main__":
    main()
