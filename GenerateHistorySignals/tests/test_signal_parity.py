# -*- coding: utf-8 -*-
"""
Signal Parity Tests - General signal matching between BinanceFriend and GenerateHistorySignals.

Tests verify that generated signals match reference signals in:
- Match rate (>= 95%)
- Direction, confidence, probability
- Signal type, valid_hours
- Per-symbol match rates
- LONG/SHORT ratio parity
"""

import pytest
from typing import Dict, List, Tuple

from .comparator import SignalComparator


# =============================================================================
# TEST: SIGNALS EXIST IN BOTH SOURCES
# =============================================================================

class TestSignalsExist:
    """Verify both reference and generated signals exist."""

    def test_reference_signals_not_empty(self, load_reference_signals: List[dict]):
        """Reference signals (BinanceFriend) must not be empty."""
        assert len(load_reference_signals) > 0, (
            "FAIL: No reference signals found in BinanceFriend logs.\n"
            "Check BINFRIEND_LOGS_DIR environment variable or logs/ directory."
        )
        print(f"\n[OK] Reference signals: {len(load_reference_signals):,}")

    def test_generated_signals_not_empty(self, load_generated_signals: List[dict]):
        """Generated signals (GenerateHistorySignals) must not be empty."""
        assert len(load_generated_signals) > 0, (
            "FAIL: No generated signals found.\n"
            "Run main.py first to generate signals, or check GENERATED_SIGNALS_DIR."
        )
        print(f"\n[OK] Generated signals: {len(load_generated_signals):,}")

    def test_signals_exist_in_both(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
    ):
        """Both reference and generated signals must exist."""
        ref_count = len(load_reference_signals)
        gen_count = len(load_generated_signals)

        print(f"\n{'=' * 60}")
        print(f"SIGNAL COUNTS")
        print(f"{'=' * 60}")
        print(f"  Reference (BinanceFriend):     {ref_count:,}")
        print(f"  Generated (GenerateHistory):   {gen_count:,}")
        print(f"  Ratio (gen/ref):               {gen_count/ref_count:.2%}" if ref_count > 0 else "")
        print(f"{'=' * 60}")

        assert ref_count > 0, "Reference signals are empty"
        assert gen_count > 0, "Generated signals are empty"


# =============================================================================
# TEST: MATCH RATE
# =============================================================================

class TestMatchRate:
    """Test overall match rate between reference and generated signals."""

    MINIMUM_MATCH_RATE = 0.95  # 95%

    def test_match_rate_above_threshold(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """At least 95% of reference signals must find a matching generated signal."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        match_rate = result.match_rate / 100  # Convert to 0-1

        print(f"\n{'=' * 60}")
        print(f"MATCH RATE TEST")
        print(f"{'=' * 60}")
        print(f"  Total reference:    {result.total_reference:,}")
        print(f"  Total generated:    {result.total_generated:,}")
        print(f"  Matched pairs:      {result.matched:,}")
        print(f"  Match rate:         {match_rate:.2%}")
        print(f"  Required minimum:   {self.MINIMUM_MATCH_RATE:.2%}")
        print(f"{'=' * 60}")

        if match_rate < self.MINIMUM_MATCH_RATE:
            print(f"\nUNMATCHED REFERENCE SIGNALS (first 20):")
            for sig_id in result.unmatched_reference_ids[:20]:
                print(f"  - {sig_id}")

            if len(result.unmatched_reference_ids) > 20:
                print(f"  ... and {len(result.unmatched_reference_ids) - 20} more")

        assert match_rate >= self.MINIMUM_MATCH_RATE, (
            f"FAIL: Match rate {match_rate:.2%} is below threshold {self.MINIMUM_MATCH_RATE:.2%}.\n"
            f"Unmatched: {result.unmatched_reference} reference signals have no matching generated signal."
        )


# =============================================================================
# TEST: DIRECTION MISMATCH
# =============================================================================

class TestDirectionParity:
    """Test that signal direction matches exactly."""

    def test_no_direction_mismatch(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """Direction (LONG/SHORT) must match exactly for all paired signals."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Find direction mismatches
        direction_mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.direction":
                    direction_mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "ref_id": comp_result.reference_signal_id,
                        "gen_id": comp_result.generated_signal_id,
                    })

        print(f"\n{'=' * 60}")
        print(f"DIRECTION MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Direction mismatches:    {len(direction_mismatches)}")
        print(f"{'=' * 60}")

        if direction_mismatches:
            print(f"\nDIRECTION MISMATCHES (ALL):")
            for m in direction_mismatches:
                print(f"\n  Symbol:    {m['symbol']}")
                print(f"  Timestamp: {m['timestamp']}")
                print(f"  Expected:  {m['expected']}")
                print(f"  Actual:    {m['actual']}")
                print(f"  Ref ID:    {m['ref_id']}")
                print(f"  Gen ID:    {m['gen_id']}")

        assert len(direction_mismatches) == 0, (
            f"CRITICAL FAIL: {len(direction_mismatches)} direction mismatches found.\n"
            f"Direction must match exactly (LONG/SHORT)."
        )


# =============================================================================
# TEST: CONFIDENCE MISMATCH
# =============================================================================

class TestConfidenceParity:
    """Test that confidence level matches exactly."""

    def test_no_confidence_mismatch(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """Confidence must match exactly for all paired signals."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        confidence_mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.confidence":
                    confidence_mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"CONFIDENCE MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Confidence mismatches:   {len(confidence_mismatches)}")
        print(f"{'=' * 60}")

        if confidence_mismatches:
            print(f"\nCONFIDENCE MISMATCHES (first 20):")
            for m in confidence_mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(confidence_mismatches) == 0, (
            f"FAIL: {len(confidence_mismatches)} confidence mismatches found."
        )


# =============================================================================
# TEST: PROBABILITY
# =============================================================================

class TestProbabilityParity:
    """Test that probability matches exactly (integer)."""

    def test_probability_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """Probability must match exactly (tolerance = 0)."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        probability_mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.probability":
                    probability_mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"PROBABILITY MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:     {len(result.results):,}")
        print(f"  Probability mismatches:    {len(probability_mismatches)}")
        print(f"{'=' * 60}")

        if probability_mismatches:
            print(f"\nPROBABILITY MISMATCHES (first 20):")
            for m in probability_mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(probability_mismatches) == 0, (
            f"FAIL: {len(probability_mismatches)} probability mismatches found.\n"
            f"Probability is an integer and must match exactly."
        )


# =============================================================================
# TEST: VALID_HOURS
# =============================================================================

class TestValidHoursParity:
    """Test that valid_hours matches exactly."""

    def test_valid_hours_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """valid_hours must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.valid_hours":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"VALID_HOURS MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  valid_hours mismatches:  {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nVALID_HOURS MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} valid_hours mismatches found."
        )


# =============================================================================
# TEST: SIGNAL_TYPE
# =============================================================================

class TestSignalTypeParity:
    """Test that signal_type matches exactly."""

    def test_signal_type_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """signal_type must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.signal_type":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"SIGNAL_TYPE MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:    {len(result.results):,}")
        print(f"  signal_type mismatches:   {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nSIGNAL_TYPE MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']!r}, got={m['actual']!r}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} signal_type mismatches found."
        )


# =============================================================================
# TEST: PER-SYMBOL MATCH RATE
# =============================================================================

class TestPerSymbolMatchRate:
    """Test match rate for each symbol individually."""

    MINIMUM_PER_SYMBOL_RATE = 0.90  # 90%

    def test_per_symbol_match_rate(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """Each symbol must have at least 90% match rate."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Count signals per symbol in reference
        ref_by_symbol: Dict[str, int] = {}
        for sig in load_reference_signals:
            symbol = sig.get("signal", {}).get("symbol", "UNKNOWN")
            ref_by_symbol[symbol] = ref_by_symbol.get(symbol, 0) + 1

        # Count signals per symbol in generated
        gen_by_symbol: Dict[str, int] = {}
        for sig in load_generated_signals:
            symbol = sig.get("signal", {}).get("symbol", "UNKNOWN")
            gen_by_symbol[symbol] = gen_by_symbol.get(symbol, 0) + 1

        # Count matched per symbol
        matched_by_symbol: Dict[str, int] = {}
        for comp_result in result.results:
            symbol = comp_result.symbol
            matched_by_symbol[symbol] = matched_by_symbol.get(symbol, 0) + 1

        # Build table
        all_symbols = sorted(set(ref_by_symbol.keys()) | set(gen_by_symbol.keys()))
        failing_symbols = []

        print(f"\n{'=' * 80}")
        print(f"PER-SYMBOL MATCH RATE")
        print(f"{'=' * 80}")
        print(f"{'Symbol':<12} {'Ref':>8} {'Gen':>8} {'Matched':>8} {'Rate':>10}")
        print(f"{'-' * 80}")

        for symbol in all_symbols:
            ref_count = ref_by_symbol.get(symbol, 0)
            gen_count = gen_by_symbol.get(symbol, 0)
            matched = matched_by_symbol.get(symbol, 0)
            rate = matched / ref_count if ref_count > 0 else 0.0

            status = "OK" if rate >= self.MINIMUM_PER_SYMBOL_RATE else "FAIL"
            print(f"{symbol:<12} {ref_count:>8} {gen_count:>8} {matched:>8} {rate:>9.1%} {status}")

            if rate < self.MINIMUM_PER_SYMBOL_RATE and ref_count > 0:
                failing_symbols.append({
                    "symbol": symbol,
                    "ref_count": ref_count,
                    "gen_count": gen_count,
                    "matched": matched,
                    "rate": rate,
                })

        print(f"{'=' * 80}")

        assert len(failing_symbols) == 0, (
            f"FAIL: {len(failing_symbols)} symbols have match rate below {self.MINIMUM_PER_SYMBOL_RATE:.0%}:\n"
            + "\n".join(f"  {s['symbol']}: {s['rate']:.1%}" for s in failing_symbols)
        )


# =============================================================================
# TEST: LONG/SHORT RATIO PARITY
# =============================================================================

class TestLongShortRatioParity:
    """Test that LONG/SHORT ratio is similar between reference and generated."""

    MAX_RATIO_DIFFERENCE_PP = 5  # 5 percentage points

    def test_long_short_ratio_parity(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
    ):
        """LONG/SHORT ratio difference must not exceed 5 percentage points."""
        # Count LONG/SHORT in reference
        ref_long = 0
        ref_short = 0
        for sig in load_reference_signals:
            direction = sig.get("signal", {}).get("direction", "")
            if direction == "LONG":
                ref_long += 1
            elif direction == "SHORT":
                ref_short += 1

        ref_total = ref_long + ref_short
        ref_long_pct = (ref_long / ref_total * 100) if ref_total > 0 else 0

        # Count LONG/SHORT in generated
        gen_long = 0
        gen_short = 0
        for sig in load_generated_signals:
            direction = sig.get("signal", {}).get("direction", "")
            if direction == "LONG":
                gen_long += 1
            elif direction == "SHORT":
                gen_short += 1

        gen_total = gen_long + gen_short
        gen_long_pct = (gen_long / gen_total * 100) if gen_total > 0 else 0

        # Calculate difference
        diff_pp = abs(ref_long_pct - gen_long_pct)

        print(f"\n{'=' * 60}")
        print(f"LONG/SHORT RATIO PARITY TEST")
        print(f"{'=' * 60}")
        print(f"  Reference:")
        print(f"    LONG:  {ref_long:,} ({ref_long_pct:.1f}%)")
        print(f"    SHORT: {ref_short:,} ({100 - ref_long_pct:.1f}%)")
        print(f"  Generated:")
        print(f"    LONG:  {gen_long:,} ({gen_long_pct:.1f}%)")
        print(f"    SHORT: {gen_short:,} ({100 - gen_long_pct:.1f}%)")
        print(f"  Difference: {diff_pp:.1f} percentage points")
        print(f"  Max allowed: {self.MAX_RATIO_DIFFERENCE_PP} percentage points")
        print(f"{'=' * 60}")

        assert diff_pp <= self.MAX_RATIO_DIFFERENCE_PP, (
            f"FAIL: LONG/SHORT ratio difference is {diff_pp:.1f}pp, "
            f"exceeds maximum {self.MAX_RATIO_DIFFERENCE_PP}pp.\n"
            f"Reference: {ref_long_pct:.1f}% LONG, Generated: {gen_long_pct:.1f}% LONG"
        )
