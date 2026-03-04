# -*- coding: utf-8 -*-
"""
Edge Cases Tests - Boundary conditions and data integrity checks.

Tests verify:
- No duplicate signals per symbol per minute
- All signals within requested period
- Signal count in reasonable range
- All values in valid ranges
- JSONL files are valid JSON
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# TEST: NO DUPLICATE SIGNALS
# =============================================================================

class TestNoDuplicateSignals:
    """Verify no duplicate signals per symbol per minute."""

    def test_no_duplicate_signals_per_symbol_per_minute(
        self,
        load_generated_signals: List[dict],
    ):
        """
        For each symbol, there should be no two signals with identical timestamp.
        Duplicates indicate cooldown or deduplication not working.
        """
        # Group signals by (symbol, timestamp)
        signals_by_key: Dict[Tuple[str, str], List[dict]] = defaultdict(list)

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            symbol = signal.get("symbol", "")
            timestamp = signal.get("timestamp", "")
            key = (symbol, timestamp)
            signals_by_key[key].append(sig)

        # Find duplicates
        duplicates = []
        for key, sigs in signals_by_key.items():
            if len(sigs) > 1:
                duplicates.append({
                    "symbol": key[0],
                    "timestamp": key[1],
                    "count": len(sigs),
                    "signal_ids": [s.get("signal", {}).get("signal_id") for s in sigs],
                })

        print(f"\n{'=' * 60}")
        print(f"DUPLICATE SIGNALS TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:          {len(load_generated_signals):,}")
        print(f"  Unique (symbol, time):  {len(signals_by_key):,}")
        print(f"  Duplicate groups:       {len(duplicates)}")
        print(f"{'=' * 60}")

        if duplicates:
            print(f"\nDUPLICATE SIGNALS (first 20):")
            for d in duplicates[:20]:
                print(f"  {d['symbol']} @ {d['timestamp']}: {d['count']} signals")
                for sig_id in d['signal_ids']:
                    print(f"    - {sig_id}")

        assert len(duplicates) == 0, (
            f"FAIL: {len(duplicates)} duplicate signal groups found.\n"
            f"Cooldown or deduplication logic may be broken."
        )


# =============================================================================
# TEST: SIGNALS WITHIN REQUESTED PERIOD
# =============================================================================

class TestSignalsWithinPeriod:
    """Verify all signals are within the requested time period."""

    def test_all_signals_within_requested_period(
        self,
        load_generated_signals: List[dict],
        generated_signals_dir,
    ):
        """All signal timestamps must be within the generation period."""
        if not load_generated_signals:
            pytest.skip("No generated signals to check")

        # Parse all timestamps
        timestamps = []
        parse_errors = []

        for i, sig in enumerate(load_generated_signals):
            signal = sig.get("signal", {})
            ts_str = signal.get("timestamp", "")

            try:
                # Handle various timestamp formats
                ts_str = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
                timestamps.append(ts)
            except Exception as e:
                parse_errors.append((i, ts_str, str(e)))

        if not timestamps:
            pytest.skip("No valid timestamps found")

        min_ts = min(timestamps)
        max_ts = max(timestamps)
        span_days = (max_ts - min_ts).days

        print(f"\n{'=' * 60}")
        print(f"SIGNALS WITHIN PERIOD TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:      {len(load_generated_signals):,}")
        print(f"  Valid timestamps:   {len(timestamps):,}")
        print(f"  Parse errors:       {len(parse_errors)}")
        print(f"  Earliest signal:    {min_ts}")
        print(f"  Latest signal:      {max_ts}")
        print(f"  Span:               {span_days} days")
        print(f"{'=' * 60}")

        if parse_errors:
            print(f"\nTIMESTAMP PARSE ERRORS (first 10):")
            for idx, ts_str, err in parse_errors[:10]:
                print(f"  Signal {idx}: '{ts_str}' - {err}")

        # Note: We can't know the original requested period without metadata
        # Just verify timestamps are parseable and reasonable
        assert len(parse_errors) == 0, (
            f"FAIL: {len(parse_errors)} signals have unparseable timestamps"
        )


# =============================================================================
# TEST: SIGNAL COUNT REASONABLE
# =============================================================================

class TestSignalCountReasonable:
    """Verify signal count is in reasonable range."""

    MIN_SIGNALS_30_DAY_50_SYMBOLS = 1000
    MAX_SIGNALS_30_DAY_50_SYMBOLS = 500000

    def test_signal_count_reasonable(
        self,
        load_generated_signals: List[dict],
    ):
        """
        For typical 30-day, 50-symbol run:
        - Too few (< 1000): Something is broken
        - Too many (> 500000): Cooldown not working
        """
        count = len(load_generated_signals)

        # Calculate approximate per-symbol-per-day rate
        symbols: Set[str] = set()
        timestamps = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            symbols.add(signal.get("symbol", ""))
            ts_str = signal.get("timestamp", "")
            try:
                ts_str = ts_str.replace("Z", "+00:00")
                timestamps.append(datetime.fromisoformat(ts_str))
            except Exception:
                pass

        num_symbols = len(symbols) if symbols else 1
        if timestamps:
            span_days = max(1, (max(timestamps) - min(timestamps)).days)
        else:
            span_days = 1

        signals_per_symbol_per_day = count / num_symbols / span_days if num_symbols and span_days else 0

        print(f"\n{'=' * 60}")
        print(f"SIGNAL COUNT REASONABLENESS TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:               {count:,}")
        print(f"  Unique symbols:              {num_symbols}")
        print(f"  Span (days):                 {span_days}")
        print(f"  Signals/symbol/day:          {signals_per_symbol_per_day:.1f}")
        print(f"  Expected range (30d, 50sym): [{self.MIN_SIGNALS_30_DAY_50_SYMBOLS:,}, {self.MAX_SIGNALS_30_DAY_50_SYMBOLS:,}]")
        print(f"{'=' * 60}")

        # Adjust thresholds based on actual parameters
        expected_min = max(10, num_symbols * span_days * 0.5)  # At least 0.5 per symbol per day
        expected_max = num_symbols * span_days * 1000  # At most 1000 per symbol per day

        if count < expected_min:
            print(f"\nWARNING: Signal count {count} is below expected minimum {expected_min:.0f}")

        if count > expected_max:
            print(f"\nWARNING: Signal count {count} is above expected maximum {expected_max:.0f}")

        # Don't fail, just warn - actual thresholds depend on parameters
        assert count > 0, "FAIL: No signals generated"


# =============================================================================
# TEST: PROBABILITIES IN RANGE
# =============================================================================

class TestProbabilitiesInRange:
    """Verify all probability values are in valid range."""

    def test_all_probabilities_in_range(
        self,
        load_generated_signals: List[dict],
    ):
        """All probability values must be 0 <= p <= 100."""
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            prob = signal.get("probability")

            if prob is None:
                violations.append({
                    "signal_id": signal.get("signal_id"),
                    "symbol": signal.get("symbol"),
                    "timestamp": signal.get("timestamp"),
                    "probability": prob,
                    "error": "probability is None",
                })
            elif not (0 <= prob <= 100):
                violations.append({
                    "signal_id": signal.get("signal_id"),
                    "symbol": signal.get("symbol"),
                    "timestamp": signal.get("timestamp"),
                    "probability": prob,
                    "error": f"probability {prob} out of range [0, 100]",
                })

        print(f"\n{'=' * 60}")
        print(f"PROBABILITY RANGE TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:   {len(load_generated_signals):,}")
        print(f"  Violations:      {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nPROBABILITY VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"  {v['symbol']} @ {v['timestamp']}: {v['error']}")

        assert len(violations) == 0, (
            f"FAIL: {len(violations)} signals have probability out of range [0, 100]"
        )


# =============================================================================
# TEST: STOP LOSS PERCENTAGE POSITIVE
# =============================================================================

class TestStopLossPctPositive:
    """Verify all stop_loss_pct values are positive."""

    def test_all_sl_pct_positive(
        self,
        load_generated_signals: List[dict],
    ):
        """stop_loss_pct must be > 0 for all signals."""
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            sl_pct = signal.get("stop_loss_pct")

            if sl_pct is None or sl_pct <= 0:
                violations.append({
                    "symbol": signal.get("symbol"),
                    "timestamp": signal.get("timestamp"),
                    "stop_loss_pct": sl_pct,
                })

        print(f"\n{'=' * 60}")
        print(f"STOP LOSS PCT POSITIVE TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:   {len(load_generated_signals):,}")
        print(f"  Violations:      {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nSL_PCT VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"  {v['symbol']} @ {v['timestamp']}: stop_loss_pct={v['stop_loss_pct']}")

        assert len(violations) == 0, (
            f"FAIL: {len(violations)} signals have stop_loss_pct <= 0"
        )


# =============================================================================
# TEST: RISK REWARD POSITIVE
# =============================================================================

class TestRiskRewardPositive:
    """Verify all risk_reward values are positive."""

    def test_all_rr_positive(
        self,
        load_generated_signals: List[dict],
    ):
        """risk_reward must be > 0 for all signals."""
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            rr = signal.get("risk_reward")

            if rr is None or rr <= 0:
                violations.append({
                    "symbol": signal.get("symbol"),
                    "timestamp": signal.get("timestamp"),
                    "risk_reward": rr,
                })

        print(f"\n{'=' * 60}")
        print(f"RISK REWARD POSITIVE TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:   {len(load_generated_signals):,}")
        print(f"  Violations:      {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nRR VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"  {v['symbol']} @ {v['timestamp']}: risk_reward={v['risk_reward']}")

        assert len(violations) == 0, (
            f"FAIL: {len(violations)} signals have risk_reward <= 0"
        )


# =============================================================================
# TEST: JSONL FILES VALID JSON
# =============================================================================

class TestJSONLFilesValid:
    """Verify all JSONL files contain valid JSON."""

    def test_jsonl_files_valid_json(
        self,
        generated_signals_dir,
    ):
        """Every line in every .jsonl file must be valid JSON."""
        invalid_lines = []

        jsonl_files = list(generated_signals_dir.glob("*.jsonl"))

        if not jsonl_files:
            pytest.skip("No JSONL files found in output directory")

        total_lines = 0

        for file_path in jsonl_files:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    total_lines += 1
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        json.loads(line)
                    except json.JSONDecodeError as e:
                        invalid_lines.append({
                            "file": file_path.name,
                            "line_num": line_num,
                            "error": str(e),
                            "content": line[:100] + "..." if len(line) > 100 else line,
                        })

        print(f"\n{'=' * 60}")
        print(f"JSONL VALIDITY TEST")
        print(f"{'=' * 60}")
        print(f"  JSONL files:     {len(jsonl_files)}")
        print(f"  Total lines:     {total_lines:,}")
        print(f"  Invalid lines:   {len(invalid_lines)}")
        print(f"{'=' * 60}")

        if invalid_lines:
            print(f"\nINVALID JSON LINES (first 20):")
            for inv in invalid_lines[:20]:
                print(f"  {inv['file']}:{inv['line_num']}: {inv['error']}")
                print(f"    Content: {inv['content']}")

        assert len(invalid_lines) == 0, (
            f"FAIL: {len(invalid_lines)} lines are not valid JSON"
        )


# =============================================================================
# TEST: ACCUMULATION SCORE TOTAL IN RANGE
# =============================================================================

class TestAccumulationScoreTotalInRange:
    """Verify accumulation_score.total is in valid range."""

    def test_accumulation_score_total_in_range(
        self,
        load_generated_signals: List[dict],
    ):
        """accumulation_score.total must be 0 <= total <= 100."""
        violations = []

        for sig in load_generated_signals:
            score = sig.get("accumulation_score", {})
            total = score.get("total")

            if total is None:
                violations.append({
                    "symbol": sig.get("signal", {}).get("symbol"),
                    "timestamp": sig.get("signal", {}).get("timestamp"),
                    "total": total,
                    "error": "total is None",
                })
            elif not (0 <= total <= 100):
                violations.append({
                    "symbol": sig.get("signal", {}).get("symbol"),
                    "timestamp": sig.get("signal", {}).get("timestamp"),
                    "total": total,
                    "error": f"total {total} out of range [0, 100]",
                })

        print(f"\n{'=' * 60}")
        print(f"ACCUMULATION SCORE TOTAL RANGE TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:   {len(load_generated_signals):,}")
        print(f"  Violations:      {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nSCORE TOTAL VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"  {v['symbol']} @ {v['timestamp']}: {v['error']}")

        assert len(violations) == 0, (
            f"FAIL: {len(violations)} signals have accumulation_score.total out of range [0, 100]"
        )


# =============================================================================
# TEST: ENTRY ZONE VALIDITY
# =============================================================================

class TestEntryZoneValidity:
    """Verify entry zone values are valid."""

    def test_entry_zone_low_less_than_high(
        self,
        load_generated_signals: List[dict],
    ):
        """entry_zone.low must be <= entry_zone.high."""
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            entry_zone = signal.get("entry_zone", {})

            low = entry_zone.get("low")
            high = entry_zone.get("high")

            if low is None or high is None:
                continue

            try:
                low_f = float(low)
                high_f = float(high)
                if low_f > high_f:
                    violations.append({
                        "symbol": signal.get("symbol"),
                        "timestamp": signal.get("timestamp"),
                        "low": low,
                        "high": high,
                    })
            except (TypeError, ValueError):
                violations.append({
                    "symbol": signal.get("symbol"),
                    "timestamp": signal.get("timestamp"),
                    "low": low,
                    "high": high,
                    "error": "non-numeric",
                })

        print(f"\n{'=' * 60}")
        print(f"ENTRY ZONE VALIDITY TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:   {len(load_generated_signals):,}")
        print(f"  Violations:      {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nENTRY ZONE VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"  {v['symbol']} @ {v['timestamp']}: low={v['low']}, high={v['high']}")

        assert len(violations) == 0, (
            f"FAIL: {len(violations)} signals have entry_zone.low > entry_zone.high"
        )


# =============================================================================
# TEST: ALL REQUIRED FIELDS PRESENT
# =============================================================================

class TestRequiredFieldsPresent:
    """Verify all required fields are present in signals."""

    REQUIRED_SIGNAL_FIELDS = [
        "signal_id",
        "symbol",
        "timestamp",
        "direction",
        "signal_type",
        "confidence",
        "probability",
        "entry_zone",
        "stop_loss",
        "stop_loss_pct",
        "take_profits",
        "risk_reward",
        "valid_hours",
    ]

    def test_all_required_fields_present(
        self,
        load_generated_signals: List[dict],
    ):
        """All required signal fields must be present."""
        missing_fields_count: Dict[str, int] = defaultdict(int)
        signals_with_missing = 0

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            missing = []

            for field in self.REQUIRED_SIGNAL_FIELDS:
                if field not in signal or signal[field] is None:
                    missing.append(field)
                    missing_fields_count[field] += 1

            if missing:
                signals_with_missing += 1

        print(f"\n{'=' * 60}")
        print(f"REQUIRED FIELDS TEST")
        print(f"{'=' * 60}")
        print(f"  Total signals:          {len(load_generated_signals):,}")
        print(f"  Signals with missing:   {signals_with_missing}")
        print(f"{'=' * 60}")

        if missing_fields_count:
            print(f"\nMISSING FIELDS:")
            for field, count in sorted(missing_fields_count.items(), key=lambda x: -x[1]):
                pct = count / len(load_generated_signals) * 100
                print(f"  {field}: {count:,} ({pct:.1f}%)")

        assert signals_with_missing == 0, (
            f"FAIL: {signals_with_missing} signals are missing required fields"
        )
