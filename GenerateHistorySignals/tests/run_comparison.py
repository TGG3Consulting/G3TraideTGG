#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Signal Comparison Runner - Standalone script for comparing signals.

Usage:
    python run_comparison.py --reference path/to/signals.jsonl --generated path/to/output/
    python run_comparison.py --reference path/to/signals.jsonl --generated path/to/output/ --report report.html

Exit codes:
    0 = All CRITICAL tests passed (direction, confidence, sl_direction OK)
    1 = CRITICAL mismatches found
"""

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from comparator import (
    SignalComparator,
    BatchComparisonResult,
    ComparisonResult,
    FieldMismatch,
    load_jsonl,
    load_all_jsonl_from_dir,
)


# =============================================================================
# FIELD CATEGORIES
# =============================================================================

CRITICAL_FIELDS = {
    "signal.direction",
    "signal.confidence",
}

WARNING_FIELDS = {
    "signal.probability",
    "signal.signal_type",
    "signal.valid_hours",
    "accumulation_score.total",
}

PRICE_FIELDS = {
    "signal.entry_zone.limit",
    "signal.stop_loss",
    "signal.take_profits[0].price",
    "signal.take_profits[1].price",
    "signal.take_profits[2].price",
}

SCORE_FIELDS = {
    "accumulation_score.oi_growth",
    "accumulation_score.oi_stability",
    "accumulation_score.funding_cheap",
    "accumulation_score.funding_gradient",
    "accumulation_score.crowd_bearish",
    "accumulation_score.crowd_bullish",
    "accumulation_score.coordinated_buying",
    "accumulation_score.volume_accumulation",
    "accumulation_score.wash_trading_penalty",
    "accumulation_score.extreme_funding_penalty",
    "accumulation_score.orderbook_against_penalty",
}


# =============================================================================
# HELPER: GET NESTED VALUE
# =============================================================================

def get_nested(obj: dict, path: str) -> Any:
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
# CHECK SL/TP DIRECTION
# =============================================================================

def check_sl_direction(signals: List[dict]) -> List[dict]:
    """Check if stop loss is on correct side of entry."""
    violations = []

    for sig in signals:
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
                "symbol": signal.get("symbol"),
                "timestamp": signal.get("timestamp"),
                "direction": direction,
                "entry": entry_limit,
                "sl": stop_loss,
            })

    return violations


# =============================================================================
# COMPARISON REPORT
# =============================================================================

@dataclass
class ComparisonReport:
    """Full comparison report."""
    reference_count: int
    generated_count: int
    matched: int
    unmatched_reference: int
    unmatched_generated: int

    # Mismatch counts by field
    field_mismatches: Dict[str, int]

    # Critical issues
    direction_mismatches: int
    confidence_mismatches: int
    sl_direction_violations: int

    # All results
    results: List[ComparisonResult]
    unmatched_reference_ids: List[str]
    unmatched_generated_ids: List[str]

    @property
    def match_rate(self) -> float:
        if self.reference_count == 0:
            return 0.0
        return self.matched / self.reference_count * 100

    @property
    def critical_issues(self) -> int:
        return self.direction_mismatches + self.confidence_mismatches + self.sl_direction_violations

    @property
    def warnings(self) -> int:
        return sum(
            self.field_mismatches.get(f, 0)
            for f in WARNING_FIELDS
        )


def run_comparison(
    reference_signals: List[dict],
    generated_signals: List[dict],
) -> ComparisonReport:
    """Run full comparison and return report."""
    comparator = SignalComparator()

    # Run batch comparison
    result = comparator.compare_batch(
        references=reference_signals,
        generated=generated_signals,
        timestamp_tolerance_sec=60,
    )

    # Count mismatches by field
    field_mismatches: Dict[str, int] = {}
    for comp_result in result.results:
        for mismatch in comp_result.mismatches:
            field = mismatch.field
            field_mismatches[field] = field_mismatches.get(field, 0) + 1

    # Count critical mismatches
    direction_mismatches = field_mismatches.get("signal.direction", 0)
    confidence_mismatches = field_mismatches.get("signal.confidence", 0)

    # Check SL direction in generated signals
    sl_violations = check_sl_direction(generated_signals)

    return ComparisonReport(
        reference_count=result.total_reference,
        generated_count=result.total_generated,
        matched=result.matched,
        unmatched_reference=result.unmatched_reference,
        unmatched_generated=result.unmatched_generated,
        field_mismatches=field_mismatches,
        direction_mismatches=direction_mismatches,
        confidence_mismatches=confidence_mismatches,
        sl_direction_violations=len(sl_violations),
        results=result.results,
        unmatched_reference_ids=result.unmatched_reference_ids,
        unmatched_generated_ids=result.unmatched_generated_ids,
    )


# =============================================================================
# CONSOLE OUTPUT
# =============================================================================

def print_report(report: ComparisonReport) -> None:
    """Print comparison report to console."""
    print()
    print("═" * 60)
    print("SIGNAL PARITY REPORT")
    print("═" * 60)
    print()

    # Overview
    print(f"Reference signals:   {report.reference_count:,}")
    print(f"Generated signals:   {report.generated_count:,}")
    print(f"Matched pairs:       {report.matched:,} ({report.match_rate:.1f}%)")
    print(f"Unmatched reference: {report.unmatched_reference:,} ({report.unmatched_reference / report.reference_count * 100:.1f}%)" if report.reference_count else "")
    print(f"Unmatched generated: {report.unmatched_generated:,} ({report.unmatched_generated / report.generated_count * 100:.1f}%)" if report.generated_count else "")
    print()

    # Field mismatches
    all_fields = sorted(set(
        list(CRITICAL_FIELDS) +
        list(WARNING_FIELDS) +
        list(PRICE_FIELDS) +
        list(SCORE_FIELDS)
    ))

    for field in all_fields:
        count = report.field_mismatches.get(field, 0)
        pct = count / report.matched * 100 if report.matched else 0

        if count == 0:
            status = "✓"
        elif field in CRITICAL_FIELDS:
            status = "✗ CRITICAL"
        else:
            status = "⚠"

        # Shorten field name for display
        display_name = field.replace("signal.", "").replace("accumulation_score.", "score.")
        print(f"{display_name:<30} {count:>6} mismatches ({pct:>5.1f}%) {status}")

    # SL direction check
    if report.sl_direction_violations > 0:
        print(f"\nSL_DIRECTION_CORRECT:          {report.sl_direction_violations:>6} violations  ✗ CRITICAL")
    else:
        print(f"\nSL_DIRECTION_CORRECT:               0 violations  ✓")

    print()
    print("─" * 60)
    print(f"CRITICAL ISSUES: {report.critical_issues}")
    print(f"WARNINGS: {report.warnings}")
    print("═" * 60)
    print()


# =============================================================================
# HTML REPORT
# =============================================================================

def generate_html_report(
    report: ComparisonReport,
    output_path: str,
) -> None:
    """Generate HTML report with all mismatches."""
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signal Parity Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #555;
            margin-top: 30px;
        }}
        .summary {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        .stat {{
            padding: 15px;
            background: #f8f9fa;
            border-radius: 4px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }}
        .critical {{ color: #dc3545; }}
        .warning {{ color: #ffc107; }}
        .ok {{ color: #28a745; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            margin-top: 10px;
        }}
        th, td {{
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background: #333;
            color: white;
            position: sticky;
            top: 0;
        }}
        tr:hover {{
            background: #f5f5f5;
        }}
        .mismatch-row {{
            background: #fff3cd;
        }}
        .field-critical {{
            background: #f8d7da;
        }}
        .timestamp {{
            font-family: monospace;
            font-size: 12px;
        }}
        .filter-bar {{
            margin: 20px 0;
            padding: 15px;
            background: white;
            border-radius: 8px;
        }}
        .filter-bar input, .filter-bar select {{
            padding: 8px;
            margin-right: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Signal Parity Report</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="summary">
            <div class="summary-grid">
                <div class="stat">
                    <div class="stat-value">{report.reference_count:,}</div>
                    <div class="stat-label">Reference Signals</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{report.generated_count:,}</div>
                    <div class="stat-label">Generated Signals</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{report.matched:,}</div>
                    <div class="stat-label">Matched Pairs</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{report.match_rate:.1f}%</div>
                    <div class="stat-label">Match Rate</div>
                </div>
                <div class="stat">
                    <div class="stat-value {'critical' if report.critical_issues > 0 else 'ok'}">{report.critical_issues}</div>
                    <div class="stat-label">Critical Issues</div>
                </div>
                <div class="stat">
                    <div class="stat-value {'warning' if report.warnings > 0 else 'ok'}">{report.warnings}</div>
                    <div class="stat-label">Warnings</div>
                </div>
            </div>
        </div>

        <h2>Field Mismatches Summary</h2>
        <table>
            <tr>
                <th>Field</th>
                <th>Mismatches</th>
                <th>Percentage</th>
                <th>Status</th>
            </tr>
"""

    # Add field rows
    all_fields = sorted(report.field_mismatches.keys())
    for field in all_fields:
        count = report.field_mismatches[field]
        pct = count / report.matched * 100 if report.matched else 0

        if field in CRITICAL_FIELDS:
            status_class = "critical"
            status_text = "CRITICAL"
        elif count > 0:
            status_class = "warning"
            status_text = "WARNING"
        else:
            status_class = "ok"
            status_text = "OK"

        html_content += f"""
            <tr class="{'field-critical' if field in CRITICAL_FIELDS and count > 0 else ''}">
                <td>{html.escape(field)}</td>
                <td>{count:,}</td>
                <td>{pct:.2f}%</td>
                <td class="{status_class}">{status_text}</td>
            </tr>
"""

    html_content += """
        </table>

        <h2>Mismatch Details</h2>
        <div class="filter-bar">
            <input type="text" id="symbolFilter" placeholder="Filter by symbol..." onkeyup="filterTable()">
            <select id="fieldFilter" onchange="filterTable()">
                <option value="">All fields</option>
"""

    # Add field filter options
    for field in sorted(report.field_mismatches.keys()):
        html_content += f'                <option value="{html.escape(field)}">{html.escape(field)}</option>\n'

    html_content += """
            </select>
        </div>

        <table id="mismatchTable">
            <tr>
                <th>Symbol</th>
                <th>Timestamp</th>
                <th>Field</th>
                <th>Expected</th>
                <th>Actual</th>
                <th>Delta</th>
            </tr>
"""

    # Add mismatch rows (limit to first 1000 for performance)
    row_count = 0
    for comp_result in report.results:
        for mismatch in comp_result.mismatches:
            if row_count >= 1000:
                break

            row_class = "field-critical" if mismatch.field in CRITICAL_FIELDS else "mismatch-row"
            delta_str = f"{mismatch.delta:.6f}" if mismatch.tolerance > 0 else "-"

            html_content += f"""
            <tr class="{row_class}">
                <td>{html.escape(comp_result.symbol)}</td>
                <td class="timestamp">{html.escape(comp_result.timestamp)}</td>
                <td>{html.escape(mismatch.field)}</td>
                <td>{html.escape(str(mismatch.expected))}</td>
                <td>{html.escape(str(mismatch.actual))}</td>
                <td>{delta_str}</td>
            </tr>
"""
            row_count += 1

        if row_count >= 1000:
            break

    if row_count >= 1000:
        total_mismatches = sum(len(r.mismatches) for r in report.results)
        html_content += f"""
            <tr>
                <td colspan="6" style="text-align: center; font-style: italic;">
                    Showing first 1000 mismatches of {total_mismatches:,} total
                </td>
            </tr>
"""

    html_content += """
        </table>

        <script>
            function filterTable() {
                const symbolFilter = document.getElementById('symbolFilter').value.toUpperCase();
                const fieldFilter = document.getElementById('fieldFilter').value;
                const table = document.getElementById('mismatchTable');
                const rows = table.getElementsByTagName('tr');

                for (let i = 1; i < rows.length; i++) {
                    const cells = rows[i].getElementsByTagName('td');
                    if (cells.length === 0) continue;

                    const symbol = cells[0].textContent.toUpperCase();
                    const field = cells[2].textContent;

                    const symbolMatch = !symbolFilter || symbol.includes(symbolFilter);
                    const fieldMatch = !fieldFilter || field === fieldFilter;

                    rows[i].style.display = (symbolMatch && fieldMatch) ? '' : 'none';
                }
            }
        </script>
    </div>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"HTML report saved to: {output_path}")


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Compare signals from BinanceFriend (reference) and GenerateHistorySignals (generated)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run_comparison.py --reference logs/signals.jsonl --generated output/
    python run_comparison.py --reference logs/ --generated output/ --report report.html

Exit codes:
    0 = All CRITICAL tests passed
    1 = CRITICAL mismatches found (direction, confidence, or SL direction)
        """,
    )

    parser.add_argument(
        "--reference",
        type=str,
        required=True,
        help="Path to reference signals (file or directory with .jsonl files)",
    )
    parser.add_argument(
        "--generated",
        type=str,
        required=True,
        help="Path to generated signals (file or directory with .jsonl files)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to save HTML report (optional)",
    )

    args = parser.parse_args()

    # Load reference signals
    ref_path = Path(args.reference)
    if ref_path.is_file():
        print(f"Loading reference signals from: {ref_path}")
        reference_signals = load_jsonl(str(ref_path))
    elif ref_path.is_dir():
        print(f"Loading reference signals from directory: {ref_path}")
        reference_signals = load_all_jsonl_from_dir(str(ref_path), "*.jsonl")
    else:
        print(f"ERROR: Reference path does not exist: {ref_path}")
        return 1

    if not reference_signals:
        print("ERROR: No reference signals loaded")
        return 1

    print(f"  Loaded {len(reference_signals):,} reference signals")

    # Load generated signals
    gen_path = Path(args.generated)
    if gen_path.is_file():
        print(f"Loading generated signals from: {gen_path}")
        generated_signals = load_jsonl(str(gen_path))
    elif gen_path.is_dir():
        print(f"Loading generated signals from directory: {gen_path}")
        generated_signals = load_all_jsonl_from_dir(str(gen_path), "*.jsonl")
    else:
        print(f"ERROR: Generated path does not exist: {gen_path}")
        return 1

    if not generated_signals:
        print("ERROR: No generated signals loaded")
        return 1

    print(f"  Loaded {len(generated_signals):,} generated signals")

    # Run comparison
    print("\nRunning comparison...")
    report = run_comparison(reference_signals, generated_signals)

    # Print console report
    print_report(report)

    # Generate HTML report if requested
    if args.report:
        generate_html_report(report, args.report)

    # Return exit code based on critical issues
    if report.critical_issues > 0:
        print("RESULT: FAIL - Critical issues found")
        return 1
    else:
        print("RESULT: PASS - No critical issues")
        return 0


if __name__ == "__main__":
    sys.exit(main())
