# -*- coding: utf-8 -*-
"""
OutputWriter - Writes signals to JSONL files.

Format is IDENTICAL to signal_logger.py from BinanceFriend.
This is critical - the backtester reads exactly this format.

Features:
- Max 200,000 signals per file (configurable)
- Automatic file rotation
- Flush after each write (no data loss on interrupt)
- JSONL format (one JSON object per line)
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from signals.models import TradeSignal, AccumulationScore


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class OutputWriter:
    """
    Writes TradeSignals to JSONL files.

    Format matches signal_logger.py from BinanceFriend exactly.
    Creates new file when current file reaches max_signals_per_file.
    """

    LOG_VERSION = "1.0"

    def __init__(
        self,
        output_dir: str = "output",
        max_signals_per_file: int = 200_000,
    ):
        """
        Initialize output writer.

        Args:
            output_dir: Directory to write files to
            max_signals_per_file: Maximum signals per file before rotation
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_signals_per_file = max_signals_per_file

        # State
        self._file_part = 1
        self._signals_in_current_file = 0
        self._total_written = 0
        self._current_file: Optional[Any] = None
        self._current_file_path: Optional[Path] = None
        self._session_timestamp: str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Open first file
        self._open_new_file()

    def _open_new_file(self) -> None:
        """Open a new output file."""
        # Close existing file if any
        if self._current_file:
            self._current_file.close()

        # Generate filename: signals_YYYYMMDD_HHMMSS_part{N}.jsonl
        filename = f"signals_{self._session_timestamp}_part{self._file_part}.jsonl"
        self._current_file_path = self.output_dir / filename

        self._current_file = open(self._current_file_path, "w", encoding="utf-8")
        self._signals_in_current_file = 0

        print(f"[OUTPUT] Created: {self._current_file_path}", flush=True)

    def write_signal(
        self,
        signal: TradeSignal,
        accumulation_score: AccumulationScore,
        futures_state=None,
        spot_state=None,
        trigger_detection: Optional[Dict] = None,
        config_snapshot: Optional[Dict] = None,
    ) -> None:
        """
        Write a signal to the current output file.

        Format matches signal_logger.py from BinanceFriend exactly.

        Args:
            signal: TradeSignal to write
            accumulation_score: AccumulationScore with breakdown
            futures_state: FuturesState (optional, for extra details)
            spot_state: SymbolState (optional, for extra details)
            trigger_detection: Detection that triggered the signal
            config_snapshot: Config used for signal generation
        """
        # Check if we need a new file
        if self._signals_in_current_file >= self.max_signals_per_file:
            self._file_part += 1
            self._open_new_file()

        # Build the log entry matching BinanceFriend format exactly
        log_entry = {
            "log_version": self.LOG_VERSION,
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "signal": self._format_signal(signal),
            "accumulation_score": accumulation_score.to_dict(),
        }

        # Add futures snapshot (matching signal_logger.py format)
        if futures_state:
            log_entry["futures_snapshot"] = self._format_futures_snapshot(futures_state)

        # Add spot snapshot (matching signal_logger.py format)
        if spot_state:
            log_entry["spot_snapshot"] = self._format_spot_snapshot(spot_state)

        # Add trigger detection
        if trigger_detection:
            log_entry["trigger_detection"] = trigger_detection

        # Add config
        if config_snapshot:
            log_entry["config"] = config_snapshot

        # Write as JSON line
        line = json.dumps(log_entry, cls=DecimalEncoder, ensure_ascii=False)
        self._current_file.write(line + "\n")
        self._current_file.flush()  # Flush immediately - no data loss on interrupt

        self._signals_in_current_file += 1
        self._total_written += 1

    def _format_signal(self, signal: TradeSignal) -> Dict[str, Any]:
        """Format signal dict matching BinanceFriend signal_logger.py exactly."""
        return {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "timestamp": signal.timestamp.isoformat() if isinstance(signal.timestamp, datetime) else signal.timestamp,
            "direction": signal.direction.value,
            "signal_type": signal.signal_type.value,
            "confidence": signal.confidence.value,
            "probability": signal.probability,
            "entry_zone": {
                "low": str(signal.entry_zone_low),
                "high": str(signal.entry_zone_high),
                "limit": str(signal.entry_limit),
            },
            "current_price": str(signal.current_price),
            "stop_loss": str(signal.stop_loss),
            "stop_loss_pct": signal.stop_loss_pct,
            "take_profits": [
                {
                    "label": tp.label,
                    "price": str(tp.price),
                    "percent": tp.percent,
                    "portion": tp.portion,
                }
                for tp in signal.take_profits
            ],
            "risk_reward": signal.risk_reward_ratio,
            "valid_hours": signal.valid_hours,
            "evidence": signal.evidence,
            "details": self._convert_decimals(signal.details),
            "scenarios": signal.scenarios,
            "trigger_detections": signal.trigger_detections,
        }

    def _format_futures_snapshot(self, state) -> Dict[str, Any]:
        """Format futures snapshot matching signal_logger.py format exactly."""
        snapshot = {
            "symbol": state.symbol,
            "last_update": state.timestamp.isoformat() if state.timestamp else None,

            # OI
            "oi": None,
            "oi_changes": {
                "1m_pct": float(state.oi_change_1m_pct),
                "5m_pct": float(state.oi_change_5m_pct),
                "1h_pct": float(state.oi_change_1h_pct),
            },

            # Funding
            "funding": None,

            # Long/Short ratio
            "ls_ratio": None,

            # Price changes
            "price_changes": {
                "5m_pct": float(state.price_change_5m_pct),
                "1h_pct": float(state.price_change_1h_pct),
            },

            # History arrays
            "oi_history": [],
            "funding_history": [],
        }

        # Current OI
        if state.current_oi:
            snapshot["oi"] = {
                "value": float(state.current_oi.open_interest),
                "value_usd": float(state.current_oi.open_interest_usd),
                "timestamp": state.current_oi.timestamp.isoformat(),
            }

        # Current funding
        if state.current_funding:
            f = state.current_funding
            snapshot["funding"] = {
                "rate": float(f.funding_rate),
                "rate_pct": float(f.funding_rate_percent),
                "mark_price": float(f.mark_price),
                "funding_time": f.funding_time,
            }

        # Current L/S ratio
        if state.current_ls_ratio:
            ls = state.current_ls_ratio
            snapshot["ls_ratio"] = {
                "long_account_pct": float(ls.long_account_pct),
                "short_account_pct": float(ls.short_account_pct),
                "long_short_ratio": float(ls.long_short_ratio),
                "timestamp": ls.timestamp.isoformat(),
            }

        # OI History
        for oi in getattr(state, 'oi_history', []):
            snapshot["oi_history"].append({
                "value": float(oi.open_interest),
                "timestamp": oi.timestamp.isoformat(),
            })

        # Funding History
        for f in getattr(state, 'funding_history', []):
            snapshot["funding_history"].append({
                "rate_pct": float(f.funding_rate_percent),
                "timestamp": f.funding_time if isinstance(f.funding_time, str) else f.funding_time.isoformat(),
            })

        return snapshot

    def _format_spot_snapshot(self, state) -> Dict[str, Any]:
        """Format spot snapshot matching signal_logger.py format exactly."""
        snapshot = {
            "symbol": state.symbol,
            "last_update": state.timestamp.isoformat() if state.timestamp else None,

            # Prices
            "price": {
                "last": float(state.last_price),
                "bid": float(state.best_bid),
                "ask": float(state.best_ask),
                "mid": float(state.mid_price),
                "spread_pct": float(state.spread_pct),
            },

            # Price changes
            "price_changes": {
                "1m_pct": float(state.price_change_1m_pct),
                "5m_pct": float(state.price_change_5m_pct),
                "1h_pct": float(state.price_change_1h_pct),
            },

            # Volumes
            "volume": {
                "1m": float(state.volume_1m),
                "5m": float(state.volume_5m),
                "1h": float(state.volume_1h),
                "avg_1h": float(state.avg_volume_1h),
                "spike_ratio": float(state.volume_spike_ratio),
            },

            # Orderbook
            "orderbook": {
                "bid_volume_20": float(state.bid_volume_20),
                "ask_volume_20": float(state.ask_volume_20),
                "imbalance": float(state.book_imbalance),
            },

            # Trades
            "trades": {
                "count_1m": state.trade_count_1m,
                "count_5m": state.trade_count_5m,
                "buy_ratio_5m": float(state.buy_ratio_5m) if state.buy_ratio_5m else None,
            },

            # Price history (last 60 close values)
            "price_history": [float(p) for p in getattr(state, 'price_history', [])[-60:]],
        }

        return snapshot

    def _convert_decimals(self, obj: Any) -> Any:
        """Recursively convert Decimal to string in dict/list."""
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, dict):
            return {k: self._convert_decimals(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._convert_decimals(v) for v in obj]
        return obj

    def close(self) -> None:
        """Close current file and finalize."""
        if self._current_file:
            self._current_file.close()
            self._current_file = None

        print(f"\n[OUTPUT] Finalized:", flush=True)
        print(f"  Total signals written: {self._total_written}", flush=True)
        print(f"  Total files: {self._file_part}", flush=True)
        print(f"  Output directory: {self.output_dir}", flush=True)

    @property
    def total_written(self) -> int:
        """Total number of signals written across all files."""
        return self._total_written

    @property
    def current_file_path(self) -> Optional[Path]:
        """Path to current output file."""
        return self._current_file_path

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


# =============================================================================
# JSONL READER (for verification and backtesting)
# =============================================================================

class OutputReader:
    """Reads signals from JSONL files (for verification)."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)

    def read_all_signals(self) -> List[Dict]:
        """Read all signals from all JSONL files in output dir."""
        signals = []

        for file_path in sorted(self.output_dir.glob("*.jsonl")):
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            signals.append(entry)
                        except json.JSONDecodeError:
                            continue

        return signals

    def count_signals(self) -> Dict[str, int]:
        """Count signals per file."""
        counts = {}

        for file_path in sorted(self.output_dir.glob("*.jsonl")):
            count = 0
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
            counts[file_path.name] = count

        return counts

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about output files."""
        entries = self.read_all_signals()

        if not entries:
            return {"total": 0}

        # Extract signals
        signals = [e.get("signal", {}) for e in entries]

        # Count by direction
        directions = {}
        for s in signals:
            d = s.get("direction", "UNKNOWN")
            directions[d] = directions.get(d, 0) + 1

        # Count by symbol
        symbols = {}
        for s in signals:
            sym = s.get("symbol", "UNKNOWN")
            symbols[sym] = symbols.get(sym, 0) + 1

        # Score distribution
        scores = [e.get("accumulation_score", {}).get("total", 0) for e in entries]
        avg_score = sum(scores) / len(scores) if scores else 0

        return {
            "total": len(entries),
            "by_direction": directions,
            "by_symbol_top20": dict(sorted(symbols.items(), key=lambda x: -x[1])[:20]),
            "avg_accumulation_score": round(avg_score, 1),
            "files": self.count_signals(),
        }


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from datetime import datetime
    from decimal import Decimal

    from signals.models import (
        SignalDirection,
        SignalConfidence,
        SignalType,
        TakeProfit,
        TradeSignal,
        AccumulationScore,
    )

    print("OutputWriter - Test Run")
    print("=" * 60)

    # Create test writer
    writer = OutputWriter(output_dir="test_output", max_signals_per_file=5)

    # Create test accumulation score
    acc_score = AccumulationScore(
        oi_growth=15,
        oi_stability=5,
        funding_cheap=10,
        funding_gradient=5,
        crowd_bearish=15,
        coordinated_buying=10,
        volume_accumulation=5,
        spot_bid_pressure=5,
    )

    print(f"Test accumulation score total: {acc_score.total}")

    # Create test signals
    for i in range(12):  # Will create 3 files (5+5+2)
        signal = TradeSignal(
            signal_id=f"TEST-{i:06d}",
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            direction=SignalDirection.LONG,
            signal_type=SignalType.ACCUMULATION,
            confidence=SignalConfidence.HIGH,
            probability=75,
            entry_zone_low=Decimal("50000"),
            entry_zone_high=Decimal("50500"),
            entry_limit=Decimal("50250"),
            current_price=Decimal("50300"),
            stop_loss=Decimal("48000"),
            stop_loss_pct=4.5,
            take_profits=[
                TakeProfit(price=Decimal("52000"), percent=3.5, portion=35, label="TP1"),
                TakeProfit(price=Decimal("55000"), percent=9.5, portion=40, label="TP2"),
                TakeProfit(price=Decimal("60000"), percent=19.4, portion=25, label="TP3"),
            ],
            risk_reward_ratio=2.5,
            evidence=["OI grew +5%", "Funding negative"],
            details={"test_index": i},
            trigger_detections=["VOLUME_SPIKE_HIGH"],
        )
        writer.write_signal(signal, acc_score)

    writer.close()

    print(f"\nTotal written: {writer.total_written}")

    # Read back and verify
    print("\n" + "=" * 60)
    print("Verification:")
    reader = OutputReader(output_dir="test_output")
    stats = reader.get_stats()
    print(f"Stats: {json.dumps(stats, indent=2)}")

    # Show sample entry
    entries = reader.read_all_signals()
    if entries:
        print("\nSample entry (first):")
        print(json.dumps(entries[0], indent=2, cls=DecimalEncoder))
