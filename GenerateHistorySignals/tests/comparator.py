# -*- coding: utf-8 -*-
"""
SignalComparator - Compares signals from BinanceFriend and GenerateHistorySignals.

Provides detailed mismatch reports to verify that historical signal generation
produces results identical to the production system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FieldMismatch:
    """A single field mismatch between reference and generated signal."""
    field: str
    expected: Any      # from reference (BinanceFriend)
    actual: Any        # from generated (GenerateHistorySignals)
    tolerance: float   # allowed tolerance
    delta: float       # actual deviation

    def __str__(self) -> str:
        if self.tolerance == 0:
            return f"{self.field}: expected={self.expected!r}, got={self.actual!r}"
        else:
            return (
                f"{self.field}: expected={self.expected}, got={self.actual}, "
                f"delta={self.delta:.6f} (tolerance={self.tolerance})"
            )


@dataclass
class ComparisonResult:
    """Result of comparing two signals."""
    reference_signal_id: str
    generated_signal_id: str
    symbol: str
    timestamp: str
    is_match: bool
    mismatches: List[FieldMismatch] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable comparison summary."""
        lines = [
            f"{'=' * 60}",
            f"Signal: {self.symbol} @ {self.timestamp}",
            f"Reference ID: {self.reference_signal_id}",
            f"Generated ID: {self.generated_signal_id}",
            f"Match: {'YES' if self.is_match else 'NO'}",
        ]

        if self.mismatches:
            lines.append(f"Mismatches ({len(self.mismatches)}):")
            for m in self.mismatches:
                lines.append(f"  - {m}")

        lines.append(f"{'=' * 60}")
        return "\n".join(lines)


@dataclass
class BatchComparisonResult:
    """Result of comparing batches of signals."""
    total_reference: int
    total_generated: int
    matched: int                          # pairs found
    unmatched_reference: int              # in reference but not in generated
    unmatched_generated: int              # in generated but not in reference
    perfect_matches: int                  # matched AND all fields equal
    partial_matches: int                  # matched BUT has mismatches
    results: List[ComparisonResult] = field(default_factory=list)

    # Lists of unmatched signal IDs
    unmatched_reference_ids: List[str] = field(default_factory=list)
    unmatched_generated_ids: List[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        """Percentage of reference signals that were matched."""
        if self.total_reference == 0:
            return 0.0
        return (self.matched / self.total_reference) * 100

    @property
    def perfect_match_rate(self) -> float:
        """Percentage of matched signals that are perfect matches."""
        if self.matched == 0:
            return 0.0
        return (self.perfect_matches / self.matched) * 100

    def print_report(self) -> None:
        """Print full comparison report."""
        print("\n" + "=" * 70)
        print("SIGNAL COMPARISON REPORT")
        print("=" * 70)

        print(f"\nOVERVIEW:")
        print(f"  Reference signals (BinanceFriend):    {self.total_reference:,}")
        print(f"  Generated signals (GenerateHistory):  {self.total_generated:,}")

        print(f"\nMATCHING:")
        print(f"  Matched pairs:          {self.matched:,} ({self.match_rate:.1f}%)")
        print(f"  Perfect matches:        {self.perfect_matches:,} ({self.perfect_match_rate:.1f}% of matched)")
        print(f"  Partial matches:        {self.partial_matches:,}")

        print(f"\nUNMATCHED:")
        print(f"  Missing in generated:   {self.unmatched_reference:,}")
        print(f"  Extra in generated:     {self.unmatched_generated:,}")

        # Show sample mismatches
        partial = [r for r in self.results if not r.is_match]
        if partial:
            print(f"\nSAMPLE MISMATCHES (first 5):")
            for result in partial[:5]:
                print(f"\n  {result.symbol} @ {result.timestamp}:")
                for m in result.mismatches[:3]:
                    print(f"    - {m}")
                if len(result.mismatches) > 3:
                    print(f"    ... and {len(result.mismatches) - 3} more")

        # Show sample unmatched
        if self.unmatched_reference_ids:
            print(f"\nSAMPLE MISSING IN GENERATED (first 5):")
            for sig_id in self.unmatched_reference_ids[:5]:
                print(f"  - {sig_id}")

        if self.unmatched_generated_ids:
            print(f"\nSAMPLE EXTRA IN GENERATED (first 5):")
            for sig_id in self.unmatched_generated_ids[:5]:
                print(f"  - {sig_id}")

        print("\n" + "=" * 70)


# =============================================================================
# SIGNAL COMPARATOR
# =============================================================================

class SignalComparator:
    """
    Compares signals from BinanceFriend (reference) and GenerateHistorySignals (generated).

    Supports field-level comparison with configurable tolerances.
    """

    # Fields to compare strictly (exact match, tolerance=0)
    STRICT_FIELDS = [
        ("signal.symbol", 0),
        ("signal.direction", 0),
        ("signal.confidence", 0),
        ("signal.signal_type", 0),
        ("signal.valid_hours", 0),
        ("signal.probability", 0),
        # Accumulation score components
        ("accumulation_score.oi_growth", 0),
        ("accumulation_score.oi_stability", 0),
        ("accumulation_score.funding_cheap", 0),
        ("accumulation_score.funding_gradient", 0),
        ("accumulation_score.crowd_bearish", 0),
        ("accumulation_score.crowd_bullish", 0),
        ("accumulation_score.coordinated_buying", 0),
        ("accumulation_score.volume_accumulation", 0),
        ("accumulation_score.wash_trading_penalty", 0),
        ("accumulation_score.extreme_funding_penalty", 0),
        ("accumulation_score.orderbook_against_penalty", 0),
        ("accumulation_score.total", 0),
    ]

    # Fields to compare with percentage tolerance (relative to value)
    PERCENT_TOLERANCE_FIELDS = [
        ("signal.entry_zone.limit", 0.0001),    # ±0.01%
        ("signal.stop_loss", 0.0001),           # ±0.01%
        ("signal.take_profits[0].price", 0.0001),
        ("signal.take_profits[1].price", 0.0001),
        ("signal.take_profits[2].price", 0.0001),
    ]

    # Fields to compare with absolute tolerance
    ABSOLUTE_TOLERANCE_FIELDS = [
        ("signal.stop_loss_pct", 0.1),          # ±0.1
        ("signal.take_profits[0].percent", 0.1),
        ("signal.take_profits[1].percent", 0.1),
        ("signal.take_profits[2].percent", 0.1),
        ("signal.risk_reward", 0.01),           # ±0.01
    ]

    # Fields to ignore (not compared)
    IGNORE_FIELDS = {
        "signal.signal_id",
        "signal.timestamp",
        "logged_at",
        "log_version",
        "signal.evidence",
        "signal.details",
        "signal.scenarios",
        "signal.links",
        "signal.trigger_detections",
        "market_context",
    }

    def __init__(self):
        pass

    def compare(
        self,
        reference: dict,
        generated: dict,
    ) -> ComparisonResult:
        """
        Compare two signal records from JSONL.

        Args:
            reference: Signal record from BinanceFriend (reference)
            generated: Signal record from GenerateHistorySignals

        Returns:
            ComparisonResult with detailed mismatch information
        """
        ref_signal = reference.get("signal", {})
        gen_signal = generated.get("signal", {})
        ref_score = reference.get("accumulation_score", {})
        gen_score = generated.get("accumulation_score", {})

        mismatches = []

        # Check strict fields
        for field_path, _ in self.STRICT_FIELDS:
            ref_val = self._get_nested(reference, field_path)
            gen_val = self._get_nested(generated, field_path)

            if ref_val != gen_val:
                mismatches.append(FieldMismatch(
                    field=field_path,
                    expected=ref_val,
                    actual=gen_val,
                    tolerance=0,
                    delta=0,
                ))

        # Check fields with percentage tolerance
        for field_path, tolerance in self.PERCENT_TOLERANCE_FIELDS:
            ref_val = self._get_nested(reference, field_path)
            gen_val = self._get_nested(generated, field_path)

            if ref_val is None or gen_val is None:
                if ref_val != gen_val:
                    mismatches.append(FieldMismatch(
                        field=field_path,
                        expected=ref_val,
                        actual=gen_val,
                        tolerance=tolerance,
                        delta=0,
                    ))
                continue

            ref_num = self._to_float(ref_val)
            gen_num = self._to_float(gen_val)

            if ref_num == 0:
                delta = abs(gen_num)
            else:
                delta = abs(gen_num - ref_num) / abs(ref_num)

            if delta > tolerance:
                mismatches.append(FieldMismatch(
                    field=field_path,
                    expected=ref_val,
                    actual=gen_val,
                    tolerance=tolerance,
                    delta=delta,
                ))

        # Check fields with absolute tolerance
        for field_path, tolerance in self.ABSOLUTE_TOLERANCE_FIELDS:
            ref_val = self._get_nested(reference, field_path)
            gen_val = self._get_nested(generated, field_path)

            if ref_val is None or gen_val is None:
                if ref_val != gen_val:
                    mismatches.append(FieldMismatch(
                        field=field_path,
                        expected=ref_val,
                        actual=gen_val,
                        tolerance=tolerance,
                        delta=0,
                    ))
                continue

            ref_num = self._to_float(ref_val)
            gen_num = self._to_float(gen_val)
            delta = abs(gen_num - ref_num)

            if delta > tolerance:
                mismatches.append(FieldMismatch(
                    field=field_path,
                    expected=ref_val,
                    actual=gen_val,
                    tolerance=tolerance,
                    delta=delta,
                ))

        return ComparisonResult(
            reference_signal_id=ref_signal.get("signal_id", "N/A"),
            generated_signal_id=gen_signal.get("signal_id", "N/A"),
            symbol=ref_signal.get("symbol", "N/A"),
            timestamp=ref_signal.get("timestamp", "N/A"),
            is_match=len(mismatches) == 0,
            mismatches=mismatches,
        )

    def compare_batch(
        self,
        references: List[dict],
        generated: List[dict],
        match_by: str = "symbol+timestamp",
        timestamp_tolerance_sec: int = 60,
    ) -> BatchComparisonResult:
        """
        Compare batches of signals.

        Matches signals by symbol and closest timestamp (within tolerance).

        Args:
            references: List of reference signals from BinanceFriend
            generated: List of generated signals from GenerateHistorySignals
            match_by: Matching strategy ("symbol+timestamp")
            timestamp_tolerance_sec: Max time difference for matching (seconds)

        Returns:
            BatchComparisonResult with aggregate statistics
        """
        # Build index of generated signals by symbol
        gen_by_symbol: Dict[str, List[Tuple[datetime, dict]]] = {}
        for g in generated:
            signal = g.get("signal", {})
            symbol = signal.get("symbol", "")
            ts_str = signal.get("timestamp", "")

            try:
                ts = self._parse_timestamp(ts_str)
            except Exception:
                continue

            if symbol not in gen_by_symbol:
                gen_by_symbol[symbol] = []
            gen_by_symbol[symbol].append((ts, g))

        # Sort by timestamp for binary search
        for symbol in gen_by_symbol:
            gen_by_symbol[symbol].sort(key=lambda x: x[0])

        # Track which generated signals were matched
        matched_generated: set = set()
        results: List[ComparisonResult] = []

        unmatched_reference_ids = []

        for ref in references:
            ref_signal = ref.get("signal", {})
            symbol = ref_signal.get("symbol", "")
            ts_str = ref_signal.get("timestamp", "")
            ref_id = ref_signal.get("signal_id", "N/A")

            try:
                ref_ts = self._parse_timestamp(ts_str)
            except Exception:
                unmatched_reference_ids.append(ref_id)
                continue

            # Find matching generated signal
            match = self._find_closest_match(
                gen_by_symbol.get(symbol, []),
                ref_ts,
                timestamp_tolerance_sec,
                matched_generated,
            )

            if match is None:
                unmatched_reference_ids.append(ref_id)
                continue

            gen_ts, gen_dict = match
            gen_id = gen_dict.get("signal", {}).get("signal_id", "N/A")
            matched_generated.add(id(gen_dict))

            # Compare
            result = self.compare(ref, gen_dict)
            results.append(result)

        # Find unmatched generated
        unmatched_generated_ids = []
        for g in generated:
            if id(g) not in matched_generated:
                gen_id = g.get("signal", {}).get("signal_id", "N/A")
                unmatched_generated_ids.append(gen_id)

        # Calculate stats
        perfect = sum(1 for r in results if r.is_match)
        partial = len(results) - perfect

        return BatchComparisonResult(
            total_reference=len(references),
            total_generated=len(generated),
            matched=len(results),
            unmatched_reference=len(unmatched_reference_ids),
            unmatched_generated=len(unmatched_generated_ids),
            perfect_matches=perfect,
            partial_matches=partial,
            results=results,
            unmatched_reference_ids=unmatched_reference_ids,
            unmatched_generated_ids=unmatched_generated_ids,
        )

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_nested(self, obj: dict, path: str) -> Any:
        """
        Get nested value by dot-separated path.

        Supports array indexing: "take_profits[0].price"
        """
        parts = path.replace("]", "").replace("[", ".").split(".")
        current = obj

        for part in parts:
            if current is None:
                return None

            if part.isdigit():
                # Array index
                idx = int(part)
                if isinstance(current, list) and idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                # Dict key
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None

        return current

    def _to_float(self, value: Any) -> float:
        """Convert value to float for comparison."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse ISO timestamp string."""
        # Handle various formats
        formats = [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ]

        # Remove trailing Z
        ts_str = ts_str.replace("Z", "+00:00")

        for fmt in formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue

        raise ValueError(f"Cannot parse timestamp: {ts_str}")

    def _find_closest_match(
        self,
        candidates: List[Tuple[datetime, dict]],
        target_ts: datetime,
        tolerance_sec: int,
        already_matched: set,
    ) -> Optional[Tuple[datetime, dict]]:
        """
        Find the closest matching signal by timestamp.

        Returns None if no match within tolerance.
        """
        if not candidates:
            return None

        best_match = None
        best_delta = float("inf")

        for ts, gen_dict in candidates:
            if id(gen_dict) in already_matched:
                continue

            # Make timestamps comparable (handle naive vs aware)
            try:
                if ts.tzinfo is None and target_ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=target_ts.tzinfo)
                elif ts.tzinfo is not None and target_ts.tzinfo is None:
                    target_ts = target_ts.replace(tzinfo=ts.tzinfo)

                delta = abs((ts - target_ts).total_seconds())
            except Exception:
                continue

            if delta <= tolerance_sec and delta < best_delta:
                best_delta = delta
                best_match = (ts, gen_dict)

        return best_match


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_jsonl(file_path: str) -> List[dict]:
    """Load all records from a JSONL file."""
    import json
    from pathlib import Path

    records = []
    path = Path(file_path)

    if not path.exists():
        return records

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return records


def load_all_jsonl_from_dir(dir_path: str, pattern: str = "*.jsonl") -> List[dict]:
    """Load all records from all JSONL files in a directory."""
    from pathlib import Path

    records = []
    path = Path(dir_path)

    if not path.exists():
        return records

    for file_path in sorted(path.glob(pattern)):
        records.extend(load_jsonl(str(file_path)))

    return records
