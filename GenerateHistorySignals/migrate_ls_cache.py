# -*- coding: utf-8 -*-
"""
Миграция кэша L/S Ratio: конвертация из процентов (0-100) в decimal (0-1).
Однократный запуск после фикса coinalyze_client.py.
"""

import json
import glob
import os

CACHE_DIR = "cache/binance"

def migrate():
    pattern = os.path.join(CACHE_DIR, "*", "ls_ratio.json")
    files = glob.glob(pattern)

    print(f"Найдено {len(files)} файлов ls_ratio.json")

    migrated = 0
    skipped = 0

    for filepath in files:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            skipped += 1
            continue

        # Проверяем первую запись - если longAccount > 1, нужна миграция
        first_long = float(data[0].get("longAccount", 0))
        if first_long <= 1:
            skipped += 1
            continue

        # Мигрируем
        for record in data:
            long_val = float(record.get("longAccount", 50))
            short_val = float(record.get("shortAccount", 50))
            record["longAccount"] = f"{long_val / 100:.4f}"
            record["shortAccount"] = f"{short_val / 100:.4f}"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)

        symbol = os.path.basename(os.path.dirname(filepath))
        print(f"  ✓ {symbol}: {len(data)} records")
        migrated += 1

    print(f"\nГотово: {migrated} мигрировано, {skipped} пропущено (уже в формате 0-1)")

if __name__ == "__main__":
    migrate()
