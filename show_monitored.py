# -*- coding: utf-8 -*-
"""
Показать какие монеты мониторятся.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from src.screener.universe_scanner import UniverseScanner
from src.screener.vulnerability_filter import VulnerabilityFilter
from config.settings import settings


async def show_monitored():
    print("=" * 70)
    print("MONITORED SYMBOLS")
    print("=" * 70)

    # 1. Показать исключённые монеты
    excluded = settings.filter.excluded_base_assets
    print(f"\n[EXCLUDED] {len(excluded)} top coins:")
    print(", ".join(excluded[:15]) + "...")

    # 2. Сканировать все пары Binance
    scanner = UniverseScanner()
    print(f"\n[SCANNING] Binance spot pairs...")

    all_symbols = await scanner.scan()
    print(f"   Found {len(all_symbols)} total pairs")

    # 3. Фильтровать уязвимые
    vuln_filter = VulnerabilityFilter()
    print(f"\n[FILTERING] Looking for vulnerable pairs...")
    print(f"   Criteria: volume < ${settings.filter.max_volume_usd:,.0f}")
    print(f"             depth < ${settings.filter.max_depth_usd:,.0f}")
    print(f"             spread > {settings.filter.min_spread_pct}%")

    vulnerable = await vuln_filter.filter(all_symbols)

    # 4. Лимит
    max_symbols = settings.screener.max_monitored_symbols
    monitored = vulnerable[:max_symbols]

    print(f"\n[RESULT] {len(vulnerable)} vulnerable pairs found")
    print(f"         Monitoring top {len(monitored)} (limit: {max_symbols})")

    # 5. Показать список
    print("\n" + "-" * 70)
    print("MONITORED PAIRS:")
    print("-" * 70)

    for i, sym in enumerate(monitored, 1):
        symbol = sym.symbol if hasattr(sym, 'symbol') else str(sym)
        vuln = ""
        if hasattr(sym, 'vulnerability_level'):
            vuln = f" [vuln: {sym.vulnerability_level.name}]"
        print(f"  {i:3}. {symbol}{vuln}")

    print("\n" + "=" * 70)

    await scanner.close()
    await vuln_filter.close()


if __name__ == "__main__":
    asyncio.run(show_monitored())
