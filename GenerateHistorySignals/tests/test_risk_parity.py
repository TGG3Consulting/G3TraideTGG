# -*- coding: utf-8 -*-
"""
Risk Parity Tests - Entry, Stop Loss, and Take Profit level matching.

Tests verify that risk parameters match between BinanceFriend (reference)
and GenerateHistorySignals (generated):
- Entry zone (limit price)
- Stop loss (price and percentage)
- Take profits (prices, percentages, portions)
- Risk:reward ratio
- Directional correctness (SL/TP on correct side of entry)
"""

import pytest
from typing import Dict, List, Tuple

from .comparator import SignalComparator


# =============================================================================
# TOLERANCES
# =============================================================================

PRICE_TOLERANCE_PCT = 0.0001    # 0.01% for prices
PERCENT_TOLERANCE_ABS = 0.1    # ±0.1 for percentages
RISK_REWARD_TOLERANCE = 0.01   # ±0.01 for R:R


# =============================================================================
# HELPER: GET FIELD VALUE
# =============================================================================

def get_nested(obj: dict, path: str):
    """Get nested value by dot-separated path with array indexing."""
    parts = path.replace("]", "").replace("[", ".").split(".")
    current = obj

    for part in parts:
        if current is None:
            return None
        if part.isdigit():
            idx = int(part)
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None

    return current


def to_float(value) -> float:
    """Convert value to float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


# =============================================================================
# TEST: ENTRY LIMIT WITHIN TOLERANCE
# =============================================================================

class TestEntryLimitParity:
    """Test entry_zone.limit price matching."""

    def test_entry_limit_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """entry_zone.limit must match within 0.01% tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Find mismatches for entry_zone.limit
        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.entry_zone.limit":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "delta": mismatch.delta,
                        "tolerance": mismatch.tolerance,
                    })

        print(f"\n{'=' * 60}")
        print(f"ENTRY_ZONE.LIMIT TOLERANCE TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Tolerance:               {PRICE_TOLERANCE_PCT:.4%}")
        print(f"  Mismatches:              {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nENTRY LIMIT MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}:")
                print(f"    expected={m['expected']}, got={m['actual']}, delta={m['delta']:.6f}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} entry_zone.limit mismatches exceed {PRICE_TOLERANCE_PCT:.4%} tolerance."
        )


# =============================================================================
# TEST: STOP LOSS WITHIN TOLERANCE
# =============================================================================

class TestStopLossParity:
    """Test stop_loss price matching."""

    def test_stop_loss_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """stop_loss must match within 0.01% tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.stop_loss":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "delta": mismatch.delta,
                    })

        print(f"\n{'=' * 60}")
        print(f"STOP_LOSS TOLERANCE TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Tolerance:               {PRICE_TOLERANCE_PCT:.4%}")
        print(f"  Mismatches:              {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nSTOP_LOSS MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}:")
                print(f"    expected={m['expected']}, got={m['actual']}, delta={m['delta']:.6f}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} stop_loss mismatches exceed {PRICE_TOLERANCE_PCT:.4%} tolerance."
        )


# =============================================================================
# TEST: STOP LOSS PERCENTAGE WITHIN TOLERANCE
# =============================================================================

class TestStopLossPctParity:
    """Test stop_loss_pct matching."""

    def test_stop_loss_pct_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """stop_loss_pct must match within ±0.1 absolute tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = []
        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                if mismatch.field == "signal.stop_loss_pct":
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "delta": mismatch.delta,
                    })

        print(f"\n{'=' * 60}")
        print(f"STOP_LOSS_PCT TOLERANCE TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {len(result.results):,}")
        print(f"  Tolerance:               ±{PERCENT_TOLERANCE_ABS}")
        print(f"  Mismatches:              {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nSTOP_LOSS_PCT MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']}, got={m['actual']}, delta={m['delta']:.4f}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} stop_loss_pct mismatches exceed ±{PERCENT_TOLERANCE_ABS} tolerance."
        )


# =============================================================================
# TEST: TAKE PROFIT PRICES WITHIN TOLERANCE
# =============================================================================

class TestTakeProfitPricesParity:
    """Test take_profits price matching."""

    def test_tp1_price_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[0].price must match within 0.01% tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[0].price"
        ]

        print(f"\n[TP1 price] Mismatches: {len(mismatches)} / {len(result.results)} pairs")

        if mismatches:
            for m in mismatches[:10]:
                print(f"  delta={m.delta:.6f}, expected={m.expected}, got={m.actual}")

        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP1 price mismatches"

    def test_tp2_price_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[1].price must match within 0.01% tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[1].price"
        ]

        print(f"\n[TP2 price] Mismatches: {len(mismatches)} / {len(result.results)} pairs")

        if mismatches:
            for m in mismatches[:10]:
                print(f"  delta={m.delta:.6f}, expected={m.expected}, got={m.actual}")

        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP2 price mismatches"

    def test_tp3_price_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[2].price must match within 0.01% tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[2].price"
        ]

        print(f"\n[TP3 price] Mismatches: {len(mismatches)} / {len(result.results)} pairs")

        if mismatches:
            for m in mismatches[:10]:
                print(f"  delta={m.delta:.6f}, expected={m.expected}, got={m.actual}")

        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP3 price mismatches"


# =============================================================================
# TEST: TAKE PROFIT PERCENTAGES WITHIN TOLERANCE
# =============================================================================

class TestTakeProfitPercentsParity:
    """Test take_profits percent matching."""

    def test_tp1_percent_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[0].percent must match within ±0.1 tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[0].percent"
        ]

        print(f"\n[TP1 percent] Mismatches: {len(mismatches)} / {len(result.results)} pairs")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP1 percent mismatches"

    def test_tp2_percent_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[1].percent must match within ±0.1 tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[1].percent"
        ]

        print(f"\n[TP2 percent] Mismatches: {len(mismatches)} / {len(result.results)} pairs")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP2 percent mismatches"

    def test_tp3_percent_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits[2].percent must match within ±0.1 tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        mismatches = [
            m for r in result.results for m in r.mismatches
            if m.field == "signal.take_profits[2].percent"
        ]

        print(f"\n[TP3 percent] Mismatches: {len(mismatches)} / {len(result.results)} pairs")
        assert len(mismatches) == 0, f"FAIL: {len(mismatches)} TP3 percent mismatches"


# =============================================================================
# TEST: TAKE PROFIT PORTIONS EXACT MATCH
# =============================================================================

class TestTakeProfitPortionsParity:
    """Test take_profits portion matching (exact integers)."""

    def test_tp_portions_exact_match(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """take_profits portions (35/40/25) must match exactly."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Build index for matching
        gen_by_key = {}
        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            key = (signal.get("symbol", ""), signal.get("timestamp", ""))
            if key not in gen_by_key:
                gen_by_key[key] = sig

        ref_by_key = {}
        for sig in load_reference_signals:
            signal = sig.get("signal", {})
            key = (signal.get("symbol", ""), signal.get("timestamp", ""))
            ref_by_key[key] = sig

        mismatches = []
        checked = 0

        for comp_result in result.results:
            key = (comp_result.symbol, comp_result.timestamp)
            ref_sig = ref_by_key.get(key)
            gen_sig = gen_by_key.get(key)

            if not ref_sig or not gen_sig:
                continue

            ref_tps = ref_sig.get("signal", {}).get("take_profits", [])
            gen_tps = gen_sig.get("signal", {}).get("take_profits", [])

            checked += 1

            for i in range(3):
                ref_portion = ref_tps[i].get("portion") if i < len(ref_tps) else None
                gen_portion = gen_tps[i].get("portion") if i < len(gen_tps) else None

                if ref_portion != gen_portion:
                    mismatches.append({
                        "symbol": comp_result.symbol,
                        "timestamp": comp_result.timestamp,
                        "tp_index": i + 1,
                        "expected": ref_portion,
                        "actual": gen_portion,
                    })

        print(f"\n{'=' * 60}")
        print(f"TP PORTIONS EXACT MATCH TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {checked:,}")
        print(f"  Portion mismatches:      {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nPORTION MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']} TP{m['tp_index']}: expected={m['expected']}, got={m['actual']}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} TP portion mismatches found.\n"
            f"Portions must be exact integers (e.g., 35/40/25)."
        )


# =============================================================================
# TEST: RISK:REWARD WITHIN TOLERANCE
# =============================================================================

class TestRiskRewardParity:
    """Test risk_reward ratio matching."""

    def test_risk_reward_within_tolerance(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """risk_reward must match within ±0.01 tolerance."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # risk_reward is not in comparator's fields by default, check manually
        gen_by_key = {}
        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            key = (signal.get("symbol", ""), signal.get("timestamp", ""))
            if key not in gen_by_key:
                gen_by_key[key] = sig

        ref_by_key = {}
        for sig in load_reference_signals:
            signal = sig.get("signal", {})
            key = (signal.get("symbol", ""), signal.get("timestamp", ""))
            ref_by_key[key] = sig

        mismatches = []
        checked = 0

        for comp_result in result.results:
            key = (comp_result.symbol, comp_result.timestamp)
            ref_sig = ref_by_key.get(key)
            gen_sig = gen_by_key.get(key)

            if not ref_sig or not gen_sig:
                continue

            ref_rr = to_float(ref_sig.get("signal", {}).get("risk_reward"))
            gen_rr = to_float(gen_sig.get("signal", {}).get("risk_reward"))

            checked += 1
            delta = abs(ref_rr - gen_rr)

            if delta > RISK_REWARD_TOLERANCE:
                mismatches.append({
                    "symbol": comp_result.symbol,
                    "timestamp": comp_result.timestamp,
                    "expected": ref_rr,
                    "actual": gen_rr,
                    "delta": delta,
                })

        print(f"\n{'=' * 60}")
        print(f"RISK:REWARD TOLERANCE TEST")
        print(f"{'=' * 60}")
        print(f"  Matched pairs checked:   {checked:,}")
        print(f"  Tolerance:               ±{RISK_REWARD_TOLERANCE}")
        print(f"  Mismatches:              {len(mismatches)}")
        print(f"{'=' * 60}")

        if mismatches:
            print(f"\nRISK:REWARD MISMATCHES (first 20):")
            for m in mismatches[:20]:
                print(f"  {m['symbol']} @ {m['timestamp']}: expected={m['expected']:.2f}, got={m['actual']:.2f}, delta={m['delta']:.4f}")

        assert len(mismatches) == 0, (
            f"FAIL: {len(mismatches)} risk_reward mismatches exceed ±{RISK_REWARD_TOLERANCE} tolerance."
        )


# =============================================================================
# TEST: STOP LOSS DIRECTION CORRECTNESS
# =============================================================================

class TestStopLossDirectionCorrectness:
    """Test that stop loss is on the correct side of entry."""

    def test_sl_direction_correct(
        self,
        load_generated_signals: List[dict],
    ):
        """
        For LONG: stop_loss < entry_limit (SL below entry)
        For SHORT: stop_loss > entry_limit (SL above entry)
        """
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            direction = signal.get("direction", "")
            entry_limit = to_float(signal.get("entry_zone", {}).get("limit"))
            stop_loss = to_float(signal.get("stop_loss"))

            if entry_limit == 0 or stop_loss == 0:
                continue

            is_valid = True
            if direction == "LONG":
                is_valid = stop_loss < entry_limit
            elif direction == "SHORT":
                is_valid = stop_loss > entry_limit

            if not is_valid:
                violations.append({
                    "symbol": signal.get("symbol", "N/A"),
                    "timestamp": signal.get("timestamp", "N/A"),
                    "direction": direction,
                    "entry_limit": entry_limit,
                    "stop_loss": stop_loss,
                })

        print(f"\n{'=' * 60}")
        print(f"STOP LOSS DIRECTION CORRECTNESS TEST")
        print(f"{'=' * 60}")
        print(f"  Generated signals checked:   {len(load_generated_signals):,}")
        print(f"  Violations:                  {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nSL DIRECTION VIOLATIONS (ALL):")
            for v in violations:
                print(f"  {v['symbol']} @ {v['timestamp']}:")
                print(f"    Direction: {v['direction']}")
                print(f"    Entry:     {v['entry_limit']}")
                print(f"    SL:        {v['stop_loss']}")
                if v['direction'] == "LONG":
                    print(f"    ERROR: SL should be < Entry for LONG")
                else:
                    print(f"    ERROR: SL should be > Entry for SHORT")

        assert len(violations) == 0, (
            f"CRITICAL FAIL: {len(violations)} signals have stop loss on wrong side of entry.\n"
            f"LONG: SL must be below entry. SHORT: SL must be above entry."
        )


# =============================================================================
# TEST: TAKE PROFIT DIRECTION CORRECTNESS
# =============================================================================

class TestTakeProfitDirectionCorrectness:
    """Test that take profits are on the correct side and in correct order."""

    def test_tp_direction_correct(
        self,
        load_generated_signals: List[dict],
    ):
        """
        For LONG: tp1 < tp2 < tp3, all > entry_limit
        For SHORT: tp1 > tp2 > tp3, all < entry_limit
        """
        violations = []

        for sig in load_generated_signals:
            signal = sig.get("signal", {})
            direction = signal.get("direction", "")
            entry_limit = to_float(signal.get("entry_zone", {}).get("limit"))
            take_profits = signal.get("take_profits", [])

            if entry_limit == 0 or len(take_profits) < 3:
                continue

            tp1 = to_float(take_profits[0].get("price"))
            tp2 = to_float(take_profits[1].get("price"))
            tp3 = to_float(take_profits[2].get("price"))

            errors = []

            if direction == "LONG":
                # All TPs should be above entry
                if tp1 <= entry_limit:
                    errors.append(f"TP1 ({tp1}) should be > entry ({entry_limit})")
                if tp2 <= entry_limit:
                    errors.append(f"TP2 ({tp2}) should be > entry ({entry_limit})")
                if tp3 <= entry_limit:
                    errors.append(f"TP3 ({tp3}) should be > entry ({entry_limit})")
                # TPs should be in ascending order
                if not (tp1 < tp2 < tp3):
                    errors.append(f"TPs should be ascending: TP1={tp1} < TP2={tp2} < TP3={tp3}")

            elif direction == "SHORT":
                # All TPs should be below entry
                if tp1 >= entry_limit:
                    errors.append(f"TP1 ({tp1}) should be < entry ({entry_limit})")
                if tp2 >= entry_limit:
                    errors.append(f"TP2 ({tp2}) should be < entry ({entry_limit})")
                if tp3 >= entry_limit:
                    errors.append(f"TP3 ({tp3}) should be < entry ({entry_limit})")
                # TPs should be in descending order
                if not (tp1 > tp2 > tp3):
                    errors.append(f"TPs should be descending: TP1={tp1} > TP2={tp2} > TP3={tp3}")

            if errors:
                violations.append({
                    "symbol": signal.get("symbol", "N/A"),
                    "timestamp": signal.get("timestamp", "N/A"),
                    "direction": direction,
                    "entry_limit": entry_limit,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "errors": errors,
                })

        print(f"\n{'=' * 60}")
        print(f"TAKE PROFIT DIRECTION CORRECTNESS TEST")
        print(f"{'=' * 60}")
        print(f"  Generated signals checked:   {len(load_generated_signals):,}")
        print(f"  Violations:                  {len(violations)}")
        print(f"{'=' * 60}")

        if violations:
            print(f"\nTP DIRECTION VIOLATIONS (first 20):")
            for v in violations[:20]:
                print(f"\n  {v['symbol']} @ {v['timestamp']}:")
                print(f"    Direction: {v['direction']}")
                print(f"    Entry:     {v['entry_limit']}")
                print(f"    TP1:       {v['tp1']}")
                print(f"    TP2:       {v['tp2']}")
                print(f"    TP3:       {v['tp3']}")
                for e in v['errors']:
                    print(f"    ERROR: {e}")

        assert len(violations) == 0, (
            f"CRITICAL FAIL: {len(violations)} signals have take profits in wrong direction or order.\n"
            f"LONG: TPs must be above entry in ascending order.\n"
            f"SHORT: TPs must be below entry in descending order."
        )


# =============================================================================
# TEST: COMPREHENSIVE RISK PARAMETERS SUMMARY
# =============================================================================

class TestRiskParametersSummary:
    """Summary test for all risk parameters."""

    def test_risk_parameters_summary(
        self,
        load_reference_signals: List[dict],
        load_generated_signals: List[dict],
        comparator: SignalComparator,
    ):
        """Summary of all risk parameter mismatches."""
        result = comparator.compare_batch(
            references=load_reference_signals,
            generated=load_generated_signals,
            timestamp_tolerance_sec=60,
        )

        # Count mismatches by field category
        categories = {
            "entry_zone": [],
            "stop_loss": [],
            "take_profits": [],
            "risk_reward": [],
        }

        for comp_result in result.results:
            for mismatch in comp_result.mismatches:
                field = mismatch.field
                if "entry_zone" in field:
                    categories["entry_zone"].append(mismatch)
                elif "stop_loss" in field:
                    categories["stop_loss"].append(mismatch)
                elif "take_profits" in field:
                    categories["take_profits"].append(mismatch)
                elif "risk_reward" in field:
                    categories["risk_reward"].append(mismatch)

        total_pairs = len(result.results)

        print(f"\n{'=' * 70}")
        print(f"RISK PARAMETERS SUMMARY")
        print(f"{'=' * 70}")
        print(f"  Total matched pairs: {total_pairs:,}")
        print(f"")
        print(f"  {'Category':<20} {'Mismatches':>12} {'Rate':>10}")
        print(f"  {'-' * 44}")

        all_ok = True
        for category, mismatches in categories.items():
            count = len(mismatches)
            rate = count / total_pairs if total_pairs > 0 else 0
            status = "OK" if count == 0 else "ISSUES"
            print(f"  {category:<20} {count:>12,} {rate:>9.2%} {status}")
            if count > 0:
                all_ok = False

        print(f"{'=' * 70}")

        # This is informational - individual tests will fail on specific issues
        if not all_ok:
            print("\nNOTE: Some risk parameter categories have mismatches.")
            print("Check individual test results for details.")
