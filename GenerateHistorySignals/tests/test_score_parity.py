# -*- coding: utf-8 -*-
"""
Score Parity Tests - Accumulation score field matching.

Tests verify that accumulation_score fields match exactly between
BinanceFriend (reference) and GenerateHistorySignals (generated).
"""

import pytest
from typing import Dict, List, Tuple

from .comparator import SignalComparator


# =============================================================================
# ACCUMULATION SCORE FIELDS
# =============================================================================

SCORE_FIELDS = [
    "oi_growth",
    "oi_stability",
    "funding_cheap",
    "funding_gradient",
    "crowd_bearish",
    "crowd_bullish",
    "coordinated_buying",
    "volume_accumulation",
    "wash_trading_penalty",
    "extreme_funding_penalty",
    "orderbook_against_penalty",
    "total",
]


# =============================================================================
# HELPER: GET MATCHED PAIRS WITH SCORES
# =============================================================================

def get_matched_pairs_with_scores(
    references: List[dict],
    generated: List[dict],
    comparator: SignalComparator,
) -> List[Tuple[dict, dict, dict]]:
    """
    Get matched pairs with their scores.

    Returns list of tuples: (comparison_result, ref_signal, gen_signal)
    """
    result = comparator.compare_batch(
        references=references,
        generated=generated,
        timestamp_tolerance_sec=60,
    )

    # Build index of signals by signal_id
    ref_by_key = {}
    for sig in references:
        signal = sig.get("signal", {})
        key = (signal.get("symbol", ""), signal.get("timestamp", ""))
        ref_by_key[key] = sig

    gen_by_key = {}
    for sig in generated:
        signal = sig.get("signal", {})
        key = (signal.get("symbol", ""), signal.get("timestamp", ""))
        if key not in gen_by_key:
            gen_by_key[key] = sig

    pairs = []
    for comp_result in result.results:
        key = (comp_result.symbol, comp_result.timestamp)
        ref_sig = ref_by_key.get(key)
        gen_sig = gen_by_key.get(key)
        if ref_sig and gen_sig:
            pairs.append((comp_result, ref_sig, gen_sig))

    return pairs


# =============================================================================
# TEST: SCORE TOTAL EXACT MATCH
# =============================================================================

class TestScoreTotalParity:
    """Test that accumulation_score.total matches exactly."""

    def test_score_total_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """accumulation_score.total must match exactly for all pairs."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "accumulation_score.total":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"ACCUMULATION_SCORE.TOTAL MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Total mismatches:        {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nTOTAL MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} accumulation_score.total mismatches found."
        )


# =============================================================================
# TEST: OI_GROWTH EXACT MATCH
# =============================================================================

class TestOiGrowthParity:
    """Test that oi_growth matches exactly."""

    def test_oi_growth_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """accumulation_score.oi_growth must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "accumulation_score.oi_growth":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"OI_GROWTH MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  oi_growth mismatches:    {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nOI_GROWTH MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} oi_growth mismatches found."
        )


# =============================================================================
# TEST: FUNDING_CHEAP EXACT MATCH
# =============================================================================

class TestFundingCheapParity:
    """Test that funding_cheap matches exactly."""

    def test_funding_cheap_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """accumulation_score.funding_cheap must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "accumulation_score.funding_cheap":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"FUNDING_CHEAP MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:     {len(result.results):,}")
        print(f"  funding_cheap mismatches:  {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nFUNDING_CHEAP MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} funding_cheap mismatches found."
        )


# =============================================================================
# TEST: CROWD_BEARISH EXACT MATCH
# =============================================================================

class TestCrowdBearishParity:
    """Test that crowd_bearish matches exactly."""

    def test_crowd_bearish_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """accumulation_score.crowd_bearish must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "accumulation_score.crowd_bearish":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"CROWD_BEARISH MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:     {len(result.results):,}")
        print(f"  crowd_bearish mismatches:  {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nCROWD_BEARISH MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} crowd_bearish mismatches found."
        )


# =============================================================================
# TEST: CROWD_BULLISH EXACT MATCH
# =============================================================================

class TestCrowdBullishParity:
    """Test that crowd_bullish matches exactly."""

    def test_crowd_bullish_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """accumulation_score.crowd_bullish must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "accumulation_score.crowd_bullish":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    })

        print(f"\n{'=' * 60}")
        print(f"CROWD_BULLISH MISMATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:     {len(result.results):,}")
        print(f"  crowd_bullish mismatches:  {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nCROWD_BULLISH MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} crowd_bullish mismatches found."
        )


# =============================================================================
# TEST: SCORE COMPONENTS SUM EQUALS TOTAL
# =============================================================================

class TestScoreComponentsConsistency:
    """Test internal consistency of score components."""

    # Components that should sum to total (positive)
    POSITIVE_COMPONENTS = [
        "oi_growth",
        "oi_stability",
        "funding_cheap",
        "funding_gradient",
        "crowd_bearish",
        "crowd_bullish",
        "coordinated_buying",
        "volume_accumulation",
    ]

    # Penalties (subtracted from total)
    PENALTY_COMPONENTS = [
        "wash_trading_penalty",
        "extreme_funding_penalty",
        "orderbook_against_penalty",
    ]

    def test_score_components_sum_equals_total(
        self,
        load_generated_signals: List[dict],
    ):
        """Sum of score components must equal total for all generated signals."""
        inconsistent = []

        for sig in load_generated_signals:
            score = sig.get("accumulation_score", {})
            if not score:
                continue

            # Calculate sum
            positive_sum = sum(score.get(c, 0) for c in self.POSITIVE_COMPONENTS)
            penalty_sum = sum(score.get(c, 0) for c in self.PENALTY_COMPONENTS)
            calculated_total = positive_sum - penalty_sum

            actual_total = score.get("total", 0)

            if calculated_total != actual_total:
                signal = sig.get("signal", {})
                inconsistent.append({
                    "symbol": signal.get("symbol", "N/A"),
                    "timestamp": signal.get("timestamp", "N/A"),
                    "calculated": calculated_total,
                    "actual": actual_total,
                    "diff": actual_total - calculated_total,
                })

        print(f"\n{'=' * 60}")
        print(f"SCORE COMPONENTS CONSISTENCY TEST")
        print(f"{'=' * 60}")
        print(f"  Generated signals checked:   {len(load_generated_signals):,}")
        print(f"  Inconsistent totals:         {len(inconsistent)}")
        print(f"{'=' * 60}")

        if inconsistent:
            print(f"\nINCONSISTENT TOTALS (first 20):")
            for m in inconsistent[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: calculated={m['calculated']}, actual={m['actual']}, diff={m['diff']}")

        assert len(inconsistent) == 0, (
            f"FAIL: {len(inconsistent)} signals have inconsistent total.\n"
            f"Sum of components does not equal total field."
        )


# =============================================================================
# TEST: ALL SCORE FIELDS MATCH (META-TEST)
# =============================================================================

class TestAllScoreFieldsMatch:
    """Meta-test: check all score fields and report mismatch matrix."""

    MAX_MISMATCH_RATE = 0.01  # 1%

    def test_all_score_fields_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """All score fields should match with < 1% mismatch rate."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Count mismatches per field
        field_mismatches: Dict[str, int] = {f: 0 for f in SCORE_FIELDS}

        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                # Extract field name from path like "accumulation_score.oi_growth"
                if mismatch.field.startswith("accumulation_score."):
                    field_name = mismatch.field.replace("accumulation_score.", "")
                    if field_name in field_mismatches:
                        field_mismatches[field_name] += 1

        total_pairs = len(result.results)
        failing_fields = []

        print(f"\n{'=' * 70}")
        print(f"SCORE FIELD MISMATCH MATRIX")
        print(f"{'=' * 70}")
        print(f"  Total matched pairs: {total_pairs:,}")
        print(f"")
        print(f"  {'Field':<30} {'Mismatches':>12} {'Rate':>10} {'Status':>10}")
        print(f"  {'-' * 66}")

        for field in SCORE_FIELDS:
            count = field_mismatches[field]
            rate = count / total_pairs if total_pairs > 0 else 0
            status = "OK" if rate == 0 else ("WARN" if rate <= self.MAX_MISMATCH_RATE else "FAIL")

            print(f"  {field:<30} {count:>12,} {rate:>9.2%} {status:>10}")

            if rate > 0:
                if rate > self.MAX_MISMATCH_RATE:
                    failing_fields.append((field, count, rate))

        print(f"{'=' * 70}")

        # Warn about any mismatches
        warning_fields = [f for f in SCORE_FIELDS if field_mismatches[f] > 0]
        if warning_fields:
            print(f"\nWARNING: {len(warning_fields)} fields have mismatches:")
            for f in warning_fields:
                print(f"  - {f}: {field_mismatches[f]} mismatches")

        assert len(failing_fields) == 0, (
            f"FAIL: {len(failing_fields)} fields have mismatch rate > {self.MAX_MISMATCH_RATE:.0%}:\n"
            + "\n".join(f"  {f}: {c} mismatches ({r:.2%})" for f, c, r in failing_fields)
        )


# =============================================================================
# TEST: INDIVIDUAL SCORE COMPONENTS (ADDITIONAL)
# =============================================================================

class TestAdditionalScoreComponents:
    """Additional tests for remaining score components."""

    def test_oi_stability_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """oi_stability must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.oi_stability"
        ]

        print(f"\n[oi_stability] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} oi_stability mismatches"

    def test_funding_gradient_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """funding_gradient must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.funding_gradient"
        ]

        print(f"\n[funding_gradient] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} funding_gradient mismatches"

    def test_coordinated_buying_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """coordinated_buying must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.coordinated_buying"
        ]

        print(f"\n[coordinated_buying] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} coordinated_buying mismatches"

    def test_volume_accumulation_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """volume_accumulation must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.volume_accumulation"
        ]

        print(f"\n[volume_accumulation] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} volume_accumulation mismatches"

    def test_wash_trading_penalty_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """wash_trading_penalty must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.wash_trading_penalty"
        ]

        print(f"\n[wash_trading_penalty] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} wash_trading_penalty mismatches"

    def test_extreme_funding_penalty_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """extreme_funding_penalty must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.extreme_funding_penalty"
        ]

        print(f"\n[extreme_funding_penalty] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} extreme_funding_penalty mismatches"

    def test_orderbook_against_penalty_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """orderbook_against_penalty must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "accumulation_score.orderbook_against_penalty"
        ]

        print(f"\n[orderbook_against_penalty] Mismatches: {len(mismatches)}")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} orderbook_against_penalty mismatches"
