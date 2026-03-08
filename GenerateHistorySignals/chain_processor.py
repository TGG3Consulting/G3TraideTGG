# -*- coding: utf-8 -*-
"""
Chain Processor - Signal chain numbering and grouping.

Logic:
- Signals are grouped by symbol + direction
- If gap between signals < dedup_days -> same chain
- If gap >= dedup_days -> new chain
"""

from typing import List, Dict
from collections import defaultdict

from strategies.base import Signal


def process_chains(
    signals: List[Signal],
    dedup_days: int = 14
) -> List[Signal]:
    """
    Process signals and assign chain ID/seq.

    Args:
        signals: List of signals
        dedup_days: Threshold days for determining new chain

    Returns:
        Same list with filled chain fields
    """
    if not signals:
        return signals

    # Group by symbol + direction
    groups: Dict[str, List[Signal]] = defaultdict(list)
    for s in signals:
        key = f"{s.symbol}_{s.direction}"
        groups[key].append(s)

    # Process each group
    for key, group_signals in groups.items():
        # Sort by date
        group_signals.sort(key=lambda x: x.date)

        chain_num = 0
        chain_seq = 0
        prev_date = None

        for signal in group_signals:
            # Determine gap
            if prev_date is None:
                gap_days = 0
                chain_num = 1
                chain_seq = 1
            else:
                gap_days = (signal.date - prev_date).days
                if gap_days >= dedup_days:
                    chain_num += 1
                    chain_seq = 1
                else:
                    chain_seq += 1

            # Fill fields
            # ВАЖНО: Включаем strategy в signal_id для корректной дедупликации
            # Разные стратегии могут генерировать сигнал на один символ в один день
            strategy_name = signal.metadata.get('strategy', 'unknown')
            signal.signal_id = f"{signal.date.strftime('%Y%m%d')}_{signal.symbol}_{signal.direction}_{strategy_name}"
            signal.chain_id = f"{signal.symbol}_{signal.direction}_C{chain_num:03d}"
            signal.chain_seq = chain_seq
            signal.chain_gap_days = gap_days
            signal.is_chain_first = (chain_seq == 1)

            prev_date = signal.date

        # Second pass - fill chain_total and is_chain_last
        chain_counts: Dict[str, int] = defaultdict(int)
        for signal in group_signals:
            chain_counts[signal.chain_id] += 1

        for signal in group_signals:
            signal.chain_total = chain_counts[signal.chain_id]

        # Determine is_chain_last
        chain_last_seq: Dict[str, int] = {}
        for signal in group_signals:
            chain_last_seq[signal.chain_id] = max(
                chain_last_seq.get(signal.chain_id, 0),
                signal.chain_seq
            )

        for signal in group_signals:
            signal.is_chain_last = (signal.chain_seq == chain_last_seq[signal.chain_id])

    return signals


def get_chain_summary(signals: List[Signal]) -> Dict[str, Dict]:
    """
    Get summary statistics for each chain.

    Args:
        signals: Processed signals with chain fields

    Returns:
        Dict mapping chain_id to summary stats
    """
    chains: Dict[str, List[Signal]] = defaultdict(list)
    for s in signals:
        if s.chain_id:
            chains[s.chain_id].append(s)

    summary = {}
    for chain_id, chain_signals in chains.items():
        chain_signals.sort(key=lambda x: x.date)
        summary[chain_id] = {
            "chain_id": chain_id,
            "symbol": chain_signals[0].symbol,
            "direction": chain_signals[0].direction,
            "count": len(chain_signals),
            "start_date": chain_signals[0].date,
            "end_date": chain_signals[-1].date,
            "duration_days": (chain_signals[-1].date - chain_signals[0].date).days,
        }

    return summary
