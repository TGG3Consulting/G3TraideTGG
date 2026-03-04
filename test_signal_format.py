# -*- coding: utf-8 -*-
"""
Показать точный формат торгового сигнала.
"""

import sys
sys.path.insert(0, '.')

from datetime import datetime
from decimal import Decimal

from src.signals.models import (
    TradeSignal,
    SignalDirection,
    SignalType,
    SignalConfidence,
    TakeProfit,
)
from src.signals.signal_formatter import SignalFormatter


def create_example_signal() -> TradeSignal:
    """Создать пример сигнала с реалистичными данными."""
    return TradeSignal(
        signal_id="a1b2c3d4",
        symbol="TRUMPUSDT",
        timestamp=datetime.now(),
        direction=SignalDirection.LONG,
        signal_type=SignalType.ACCUMULATION,
        confidence=SignalConfidence.HIGH,
        probability=78,
        entry_zone_low=Decimal("12.50"),
        entry_zone_high=Decimal("12.80"),
        entry_limit=Decimal("12.65"),
        current_price=Decimal("12.72"),
        stop_loss=Decimal("11.75"),
        stop_loss_pct=-7.1,
        take_profits=[
            TakeProfit(
                price=Decimal("14.00"),
                percent=10.7,
                portion=30,
                label="TP1"
            ),
            TakeProfit(
                price=Decimal("15.35"),
                percent=21.3,
                portion=40,
                label="TP2"
            ),
            TakeProfit(
                price=Decimal("17.15"),
                percent=35.6,
                portion=30,
                label="TP3"
            ),
        ],
        risk_reward_ratio=3.2,
        valid_hours=24,
        evidence=[
            "OI vyros +18.5% za chas (FuturesMonitor)",
            "Funding: -0.025% (longi deshevye)",
            "Tolpa: 62.3% v shortah (contrarian signal)",
            "COORDINATED_BUYING detekciya (gruppa pokupaet)",
            "Volume spike bez dvizheniya ceny (tihoe nakoplenie)",
        ],
        details={
            "oi_change_1h": "+18.5%",
            "oi_change_5m": "+2.1%",
            "funding": "-0.0250%",
            "long_pct": "37.7%",
            "short_pct": "62.3%",
            "volume_ratio": "8.5x",
            "book_imbalance": "+0.35",
        },
        scenarios={
            "pump_started": "Pamp nachalsya (cena > $12.80): -> Peredvinut SL na $12.80",
            "sideways": "Bokovik > 6 chasov: -> Derzhat esli OI rastyot, inache vyjti",
            "invalidation": "Otmena setapa: -> Zakryt esli OI upal -5% ili Funding > +0.05%",
        },
        trigger_detections=["WHALE_ACCUMULATION_CRITICAL", "OI_SPIKE_HIGH"],
        links={
            "binance_futures": "https://www.binance.com/ru/futures/TRUMPUSDT",
            "tradingview": "https://www.tradingview.com/chart/?symbol=BINANCE:TRUMPUSDT.P",
            "coinglass": "https://www.coinglass.com/tv/TRUMP_USDT",
        },
    )


def main():
    # Создать пример сигнала
    signal = create_example_signal()

    # Форматировать
    formatter = SignalFormatter()

    full_text = formatter.format_signal(signal)
    compact_text = formatter.format_signal_compact(signal)
    update_text = formatter.format_signal_update(signal, "tp_hit", "TP1 ($14.00)")

    # Записать в файл (UTF-8)
    with open("signal_example.txt", "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("ПОЛНЫЙ ФОРМАТ СИГНАЛА (HTML для Telegram):\n")
        f.write("=" * 70 + "\n\n")
        f.write(full_text)
        f.write("\n\n")
        f.write("=" * 70 + "\n")
        f.write("КОМПАКТНЫЙ ФОРМАТ (для групп):\n")
        f.write("=" * 70 + "\n\n")
        f.write(compact_text)
        f.write("\n\n")
        f.write("=" * 70 + "\n")
        f.write("ПРИМЕР UPDATE (TP HIT):\n")
        f.write("=" * 70 + "\n\n")
        f.write(update_text)
        f.write("\n")

    print("Saved to signal_example.txt")


if __name__ == "__main__":
    main()
