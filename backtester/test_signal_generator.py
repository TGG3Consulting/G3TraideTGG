# -*- coding: utf-8 -*-
"""
ManipBackTester - Генератор тестовых сигналов.

Создаёт тестовые сигналы для проверки бэктестера.

Использование:
    python -m backtester.test_signal_generator
    python -m backtester.test_signal_generator --count 10
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Добавить parent в path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtester.config import SIGNALS_FILE


# Реальные символы с Binance Futures
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "SOLUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT",
    "AVAXUSDT", "LINKUSDT", "ATOMUSDT", "UNIUSDT", "XLMUSDT",
]

# Примерные цены (для генерации реалистичных уровней)
APPROX_PRICES = {
    "BTCUSDT": Decimal("45000"),
    "ETHUSDT": Decimal("2500"),
    "BNBUSDT": Decimal("300"),
    "XRPUSDT": Decimal("0.55"),
    "ADAUSDT": Decimal("0.45"),
    "DOGEUSDT": Decimal("0.08"),
    "SOLUSDT": Decimal("100"),
    "DOTUSDT": Decimal("7"),
    "MATICUSDT": Decimal("0.85"),
    "LTCUSDT": Decimal("70"),
    "AVAXUSDT": Decimal("35"),
    "LINKUSDT": Decimal("15"),
    "ATOMUSDT": Decimal("9"),
    "UNIUSDT": Decimal("6"),
    "XLMUSDT": Decimal("0.12"),
}


def generate_signal(symbol: str, timestamp: datetime, direction: str = None) -> dict:
    """
    Сгенерировать один тестовый сигнал.

    Формат идентичен signal_logger.py
    """
    if direction is None:
        direction = random.choice(["LONG", "SHORT"])

    base_price = APPROX_PRICES.get(symbol, Decimal("100"))

    # Добавить случайное отклонение ±5%
    variation = Decimal(str(random.uniform(0.95, 1.05)))
    current_price = base_price * variation

    # Округление в зависимости от цены
    if current_price >= 1000:
        precision = Decimal("0.01")
    elif current_price >= 10:
        precision = Decimal("0.001")
    elif current_price >= 1:
        precision = Decimal("0.0001")
    else:
        precision = Decimal("0.000001")

    current_price = current_price.quantize(precision)

    # Параметры сигнала
    sl_pct = Decimal(str(random.uniform(0.03, 0.07)))  # 3-7% SL
    tp1_pct = sl_pct * Decimal("1.5")  # TP1 = 1.5x риска
    tp2_pct = sl_pct * Decimal("3.0")  # TP2 = 3x риска
    tp3_pct = sl_pct * Decimal("5.0")  # TP3 = 5x риска

    if direction == "LONG":
        entry_limit = current_price * Decimal("0.995")  # Вход чуть ниже
        entry_zone_low = entry_limit * Decimal("0.99")
        entry_zone_high = current_price
        stop_loss = entry_limit * (1 - sl_pct)
        tp1 = entry_limit * (1 + tp1_pct)
        tp2 = entry_limit * (1 + tp2_pct)
        tp3 = entry_limit * (1 + tp3_pct)
    else:
        entry_limit = current_price * Decimal("1.005")  # Вход чуть выше
        entry_zone_low = current_price
        entry_zone_high = entry_limit * Decimal("1.01")
        stop_loss = entry_limit * (1 + sl_pct)
        tp1 = entry_limit * (1 - tp1_pct)
        tp2 = entry_limit * (1 - tp2_pct)
        tp3 = entry_limit * (1 - tp3_pct)

    # Округлить все цены
    for var in [entry_limit, entry_zone_low, entry_zone_high, stop_loss, tp1, tp2, tp3]:
        var = var.quantize(precision)

    signal_id = f"TEST-{random.randint(10000, 99999)}"

    signal_record = {
        "log_version": "1.0",
        "logged_at": timestamp.isoformat(),

        "signal": {
            "signal_id": signal_id,
            "symbol": symbol,
            "timestamp": timestamp.isoformat(),
            "direction": direction,
            "signal_type": "НАКОПЛЕНИЕ",
            "confidence": random.choice(["СРЕДНЯЯ", "ВЫСОКАЯ", "ОЧЕНЬ ВЫСОКАЯ"]),
            "probability": random.randint(60, 90),

            "entry_zone": {
                "low": str(entry_zone_low.quantize(precision)),
                "high": str(entry_zone_high.quantize(precision)),
                "limit": str(entry_limit.quantize(precision)),
            },
            "current_price": str(current_price),

            "stop_loss": str(stop_loss.quantize(precision)),
            "stop_loss_pct": float(sl_pct * 100),

            "take_profits": [
                {
                    "label": "TP1",
                    "price": str(tp1.quantize(precision)),
                    "percent": float(tp1_pct * 100),
                    "portion": 30
                },
                {
                    "label": "TP2",
                    "price": str(tp2.quantize(precision)),
                    "percent": float(tp2_pct * 100),
                    "portion": 40
                },
                {
                    "label": "TP3",
                    "price": str(tp3.quantize(precision)),
                    "percent": float(tp3_pct * 100),
                    "portion": 30
                }
            ],

            "risk_reward": float(tp2_pct / sl_pct),
            "valid_hours": 24,

            "evidence": [
                f"Test signal generated at {timestamp}",
                f"Direction: {direction}",
                f"R:R = {float(tp2_pct / sl_pct):.1f}"
            ],

            "details": {
                "test_signal": True,
                "generated_at": datetime.now().isoformat()
            },

            "scenarios": {},
            "trigger_detections": ["TEST_TRIGGER"]
        },

        "accumulation_score": {
            "oi_growth": random.randint(10, 20),
            "funding_cheap": random.randint(5, 15),
            "crowd_bearish": random.randint(10, 20),
            "total": random.randint(65, 85)
        }
    }

    return signal_record


def generate_test_signals(count: int = 5, days_back: int = 7) -> list:
    """
    Сгенерировать несколько тестовых сигналов.

    Args:
        count: Количество сигналов
        days_back: На сколько дней назад генерировать

    Returns:
        Список сигналов
    """
    signals = []
    now = datetime.now()

    for i in range(count):
        # Случайное время в пределах days_back дней
        random_hours = random.randint(1, days_back * 24)
        timestamp = now - timedelta(hours=random_hours)

        # Случайный символ
        symbol = random.choice(SYMBOLS)

        signal = generate_signal(symbol, timestamp)
        signals.append(signal)

    # Сортировать по времени
    signals.sort(key=lambda s: s["signal"]["timestamp"])

    return signals


def write_signals(signals: list, output_path: Path = None) -> None:
    """Записать сигналы в JSONL файл."""
    if output_path is None:
        output_path = SIGNALS_FILE

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        for signal in signals:
            line = json.dumps(signal, ensure_ascii=False)
            f.write(line + "\n")

    print(f"[OK] Generated {len(signals)} test signals")
    print(f"   Saved to: {output_path}")


def main():
    # Fix Windows console encoding
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Generate test signals for backtester")

    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of signals to generate (default: 5)"
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Generate signals for last N days (default: 7)"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (default: logs/signals.jsonl)"
    )

    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing file instead of overwriting"
    )

    args = parser.parse_args()

    print("\n[TEST] Generating test signals for ManipBackTester...")

    signals = generate_test_signals(count=args.count, days_back=args.days)

    output_path = Path(args.output) if args.output else SIGNALS_FILE

    if args.append and output_path.exists():
        # Читаем существующие
        existing = []
        with open(output_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    existing.append(json.loads(line))

        signals = existing + signals
        print(f"   Appending to {len(existing)} existing signals")

    write_signals(signals, output_path)

    print("\n[INFO] Generated signals:")
    for s in signals[-args.count:]:  # Показать только новые
        sig = s["signal"]
        print(f"   {sig['timestamp'][:16]} | {sig['symbol']:10} | {sig['direction']:5} | prob={sig['probability']}%")


if __name__ == "__main__":
    main()
