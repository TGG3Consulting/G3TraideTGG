#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BinanceFriend - Quick Start Script

Запуск:
    python run.py
    python run.py --max-symbols 50
    python run.py --help
"""

import sys
from pathlib import Path

# Добавить src в путь
sys.path.insert(0, str(Path(__file__).parent / "src"))

from screener.main import run

if __name__ == "__main__":
    run()
