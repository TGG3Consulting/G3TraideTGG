# -*- coding: utf-8 -*-
"""
Pytest fixtures for GenerateHistorySignals tests.

Provides fixtures for loading reference (BinanceFriend) and generated signals.
"""

import json
import os
from pathlib import Path
from typing import List

import pytest

from .comparator import SignalComparator, load_jsonl, load_all_jsonl_from_dir


# =============================================================================
# PATH FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def binancefriend_signals_dir() -> Path:
    """
    Path to BinanceFriend signals directory.

    Checks (in order):
    1. BINFRIEND_LOGS_DIR environment variable
    2. ../logs/ relative to GenerateHistorySignals
    3. ../../logs/ relative to tests directory

    Skips tests if not found.
    """
    # Check environment variable first
    env_path = os.environ.get("BINFRIEND_LOGS_DIR")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    # Check relative to GenerateHistorySignals
    tests_dir = Path(__file__).parent
    project_dir = tests_dir.parent

    candidates = [
        project_dir / "logs",
        project_dir.parent / "logs",
        project_dir.parent / "src" / "logs",
        Path("G:/BinanceFriend/logs"),
    ]

    for candidate in candidates:
        if candidate.exists():
            # Check if it has signals.jsonl or signals*.jsonl
            if (candidate / "signals.jsonl").exists():
                return candidate
            if list(candidate.glob("signals*.jsonl")):
                return candidate

    pytest.skip(
        "BinanceFriend logs not found. "
        "Set BINFRIEND_LOGS_DIR env var or place logs in expected location."
    )


@pytest.fixture(scope="session")
def generated_signals_dir() -> Path:
    """
    Path to GenerateHistorySignals output directory.

    Checks (in order):
    1. GENERATED_SIGNALS_DIR environment variable
    2. ./output/ relative to GenerateHistorySignals
    3. ../output/ relative to tests directory

    Skips tests if not found.
    """
    # Check environment variable first
    env_path = os.environ.get("GENERATED_SIGNALS_DIR")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    # Check relative to project
    tests_dir = Path(__file__).parent
    project_dir = tests_dir.parent

    candidates = [
        project_dir / "output",
        tests_dir / "output",
        Path("G:/BinanceFriend/GenerateHistorySignals/output"),
    ]

    for candidate in candidates:
        if candidate.exists():
            # Check if it has any .jsonl files
            if list(candidate.glob("*.jsonl")):
                return candidate

    pytest.skip(
        "Generated signals not found. "
        "Set GENERATED_SIGNALS_DIR env var or run main.py first to generate signals."
    )


# =============================================================================
# SIGNAL LOADING FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def load_reference_signals(binancefriend_signals_dir: Path) -> List[dict]:
    """
    Load all reference signals from BinanceFriend logs.

    Returns list of signal records (dicts from JSONL).
    """
    signals = []

    # Try signals.jsonl first
    main_file = binancefriend_signals_dir / "signals.jsonl"
    if main_file.exists():
        signals.extend(load_jsonl(str(main_file)))

    # Also load any signals_*.jsonl files
    for file_path in sorted(binancefriend_signals_dir.glob("signals_*.jsonl")):
        signals.extend(load_jsonl(str(file_path)))

    if not signals:
        pytest.skip(f"No signals found in {binancefriend_signals_dir}")

    return signals


@pytest.fixture(scope="session")
def load_generated_signals(generated_signals_dir: Path) -> List[dict]:
    """
    Load all generated signals from GenerateHistorySignals output.

    Returns list of signal records (dicts from JSONL).
    """
    signals = load_all_jsonl_from_dir(str(generated_signals_dir), "*.jsonl")

    if not signals:
        pytest.skip(f"No signals found in {generated_signals_dir}")

    return signals


# =============================================================================
# COMPARATOR FIXTURE
# =============================================================================

@pytest.fixture
def comparator() -> SignalComparator:
    """Get a SignalComparator instance."""
    return SignalComparator()


# =============================================================================
# SAMPLE DATA FIXTURES (for unit tests)
# =============================================================================

@pytest.fixture
def sample_reference_signal() -> dict:
    """Sample reference signal for unit testing."""
    return {
        "log_version": "1.0",
        "logged_at": "2024-01-15T10:30:00+00:00",
        "signal": {
            "signal_id": "SIG-BTCUSD-ref12345",
            "symbol": "BTCUSDT",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "direction": "LONG",
            "signal_type": "НАКОПЛЕНИЕ",
            "confidence": "ВЫСОКАЯ",
            "probability": 75,
            "entry_zone": {
                "low": "42000.00",
                "high": "42500.00",
                "limit": "42250.00",
            },
            "current_price": "42300.00",
            "stop_loss": "40000.00",
            "stop_loss_pct": 5.3,
            "take_profits": [
                {"label": "TP1", "price": "44000.00", "percent": 4.1, "portion": 35},
                {"label": "TP2", "price": "46000.00", "percent": 8.9, "portion": 40},
                {"label": "TP3", "price": "50000.00", "percent": 18.3, "portion": 25},
            ],
            "risk_reward": 2.45,
            "valid_hours": 24,
            "evidence": ["OI grew +5%", "Funding negative"],
            "details": {"test": True},
            "scenarios": {},
            "trigger_detections": ["VOLUME_SPIKE_HIGH"],
        },
        "accumulation_score": {
            "oi_growth": 15,
            "oi_stability": 5,
            "funding_cheap": 10,
            "funding_gradient": 5,
            "crowd_bearish": 15,
            "crowd_bullish": 0,
            "coordinated_buying": 10,
            "volume_accumulation": 5,
            "wash_trading_penalty": 0,
            "extreme_funding_penalty": 0,
            "orderbook_against_penalty": 0,
            "total": 75,
        },
    }


@pytest.fixture
def sample_generated_signal_matching() -> dict:
    """Sample generated signal that matches reference."""
    return {
        "log_version": "1.0",
        "logged_at": "2024-01-15T10:30:05+00:00",  # 5 seconds later - OK
        "signal": {
            "signal_id": "SIG-BTCUSD-gen67890",  # Different ID - OK
            "symbol": "BTCUSDT",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "direction": "LONG",
            "signal_type": "НАКОПЛЕНИЕ",
            "confidence": "ВЫСОКАЯ",
            "probability": 75,
            "entry_zone": {
                "low": "42000.00",
                "high": "42500.00",
                "limit": "42250.00",
            },
            "current_price": "42300.00",
            "stop_loss": "40000.00",
            "stop_loss_pct": 5.3,
            "take_profits": [
                {"label": "TP1", "price": "44000.00", "percent": 4.1, "portion": 35},
                {"label": "TP2", "price": "46000.00", "percent": 8.9, "portion": 40},
                {"label": "TP3", "price": "50000.00", "percent": 18.3, "portion": 25},
            ],
            "risk_reward": 2.45,
            "valid_hours": 24,
            "evidence": ["Different evidence text"],  # Ignored
            "details": {"different": "details"},       # Ignored
            "scenarios": {},
            "trigger_detections": [],
        },
        "accumulation_score": {
            "oi_growth": 15,
            "oi_stability": 5,
            "funding_cheap": 10,
            "funding_gradient": 5,
            "crowd_bearish": 15,
            "crowd_bullish": 0,
            "coordinated_buying": 10,
            "volume_accumulation": 5,
            "wash_trading_penalty": 0,
            "extreme_funding_penalty": 0,
            "orderbook_against_penalty": 0,
            "total": 75,
        },
    }


@pytest.fixture
def sample_generated_signal_mismatching() -> dict:
    """Sample generated signal with mismatches."""
    return {
        "log_version": "1.0",
        "logged_at": "2024-01-15T10:30:05+00:00",
        "signal": {
            "signal_id": "SIG-BTCUSD-gen99999",
            "symbol": "BTCUSDT",
            "timestamp": "2024-01-15T10:30:00+00:00",
            "direction": "SHORT",              # MISMATCH: should be LONG
            "signal_type": "НАКОПЛЕНИЕ",
            "confidence": "СРЕДНЯЯ",            # MISMATCH: should be ВЫСОКАЯ
            "probability": 65,                  # MISMATCH: should be 75
            "entry_zone": {
                "low": "42000.00",
                "high": "42500.00",
                "limit": "42250.00",
            },
            "current_price": "42300.00",
            "stop_loss": "40000.00",
            "stop_loss_pct": 5.3,
            "take_profits": [
                {"label": "TP1", "price": "44000.00", "percent": 4.1, "portion": 35},
                {"label": "TP2", "price": "46000.00", "percent": 8.9, "portion": 40},
                {"label": "TP3", "price": "50000.00", "percent": 18.3, "portion": 25},
            ],
            "risk_reward": 2.45,
            "valid_hours": 24,
            "evidence": [],
            "details": {},
            "scenarios": {},
            "trigger_detections": [],
        },
        "accumulation_score": {
            "oi_growth": 10,                    # MISMATCH: should be 15
            "oi_stability": 5,
            "funding_cheap": 10,
            "funding_gradient": 5,
            "crowd_bearish": 15,
            "crowd_bullish": 0,
            "coordinated_buying": 10,
            "volume_accumulation": 5,
            "wash_trading_penalty": 0,
            "extreme_funding_penalty": 0,
            "orderbook_against_penalty": 0,
            "total": 70,                        # MISMATCH: should be 75
        },
    }


# =============================================================================
# FILTERING FIXTURES
# =============================================================================

@pytest.fixture
def filter_signals_by_symbol():
    """Factory fixture to filter signals by symbol."""
    def _filter(signals: List[dict], symbol: str) -> List[dict]:
        return [
            s for s in signals
            if s.get("signal", {}).get("symbol") == symbol
        ]
    return _filter


@pytest.fixture
def filter_signals_by_date_range():
    """Factory fixture to filter signals by date range."""
    from datetime import datetime

    def _filter(
        signals: List[dict],
        start_date: str,
        end_date: str,
    ) -> List[dict]:
        start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

        result = []
        for s in signals:
            ts_str = s.get("signal", {}).get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if start <= ts <= end:
                    result.append(s)
            except Exception:
                continue

        return result

    return _filter


# =============================================================================
# REPORTING FIXTURES
# =============================================================================

@pytest.fixture
def report_comparison_results(tmp_path):
    """Factory fixture to save comparison results to file."""
    def _report(result, filename: str = "comparison_report.txt"):
        report_path = tmp_path / filename

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"Total Reference: {result.total_reference}\n")
            f.write(f"Total Generated: {result.total_generated}\n")
            f.write(f"Matched: {result.matched}\n")
            f.write(f"Perfect Matches: {result.perfect_matches}\n")
            f.write(f"Partial Matches: {result.partial_matches}\n")
            f.write(f"\nMismatches:\n")

            for r in result.results:
                if not r.is_match:
                    f.write(f"\n{r.symbol} @ {r.timestamp}:\n")
                    for m in r.mismatches:
                        f.write(f"  - {m}\n")

        return report_path

    return _report
