# АУДИТ БЭКТЕСТЕРА

**Дата:** 2026-02-19
**Статус:** НАЙДЕНЫ ДВА РАЗНЫХ БЭКТЕСТЕРА

---

## 1. СТРУКТУРА ФАЙЛОВ

### Бэктестер 1: `backtester/` — РАБОЧИЙ ✅

```
backtester/
├── __init__.py       (279 строк)  — экспорты
├── config.py         (2113 строк) — конфигурация
├── data_loader.py    (280 строк)  — загрузка klines с Binance API
├── log_parser.py     (320 строк)  — парсинг signals.jsonl
├── main.py           (188 строк)  — entry point
├── models.py         (268 строк)  — dataclasses
├── position_simulator.py (356 строк) — СИМУЛЯЦИЯ ПОЗИЦИИ
├── report_generator.py   (500 строк) — генерация отчётов
├── test_signal_generator.py (220 строк) — генератор тестовых сигналов
├── cache/            — кэш klines
└── output/           — результаты
```

### Бэктестер 2: `src/ml/integration/backtester.py` — ФЕЙКОВЫЙ ❌

```
src/ml/integration/
└── backtester.py (619 строк) — ML бэктестер с RANDOM!
```

---

## 2. КРИТИЧЕСКИЕ РАЗЛИЧИЯ

| Аспект | `backtester/` | `src/ml/integration/backtester.py` |
|--------|---------------|-------------------------------------|
| Источник цен | Binance API (реальные klines) | **random.random()** |
| Симуляция | Свеча за свечой | Фейковая |
| Entry zone | Проверяет вход в зону | Игнорирует |
| SL/TP | Проверяет по high/low свечи | random outcome |
| Комиссии | Maker/Taker реально | Упрощённо |
| Funding | Реальный расчёт | Нет |
| Результат | РЕАЛЬНЫЙ | МУСОР |

---

## 3. АНАЛИЗ РАБОЧЕГО БЭКТЕСТЕРА (`backtester/`)

### position_simulator.py — КЛЮЧЕВОЙ ФАЙЛ

**Классы:**
- `class PositionSimulator` (строки 26-356) — симулятор позиции

**Методы:**

| Метод | Строки | Что делает | Статус |
|-------|--------|------------|--------|
| `simulate()` | 49-82 | Основной метод симуляции | ✅ РАБОТАЕТ |
| `_find_entry()` | 84-120 | Найти вход в entry zone | ✅ РАБОТАЕТ |
| `_simulate_position()` | 122-319 | Симулировать позицию | ✅ РАБОТАЕТ |
| `_calc_pnl()` | 321-332 | Расчёт PnL | ✅ РАБОТАЕТ |
| `_check_stop_loss()` | 334-341 | Проверка SL | ✅ РАБОТАЕТ |
| `_check_take_profit()` | 343-355 | Проверка TP | ✅ РАБОТАЕТ |

**Логика симуляции:**
```python
# Строка 98-118: Вход в позицию
for kline in klines:
    if signal.direction == Direction.LONG:
        if kline.low <= signal.entry_limit:  # Цена дошла до entry zone
            return kline, signal.entry_limit

# Строка 186-207: Проверка SL (приоритет над TP!)
sl_triggered = self._check_stop_loss(signal, kline)
if sl_triggered:
    exit_reason = ExitReason.STOP_LOSS
    exit_price = signal.stop_loss

# Строка 209-277: Проверка TP1 → TP2 → TP3
if self._check_take_profit(signal, kline, signal.tp1.price):
    tp1_hit = True
```

### data_loader.py — ЗАГРУЗКА ДАННЫХ

**Методы:**

| Метод | Строки | Что делает | Статус |
|-------|--------|------------|--------|
| `load_klines()` | 56-88 | Загрузить klines (кэш или API) | ✅ РАБОТАЕТ |
| `_fetch_from_binance()` | 145-216 | Скачать с Binance Futures | ✅ РАБОТАЕТ |
| `_load_from_cache()` | 230-250 | Загрузить из кэша | ✅ РАБОТАЕТ |

**Источник данных:**
```python
# Строка 171-175
resp = session.get(
    f"{self.base_url}/fapi/v1/klines",  # Binance Futures API
    params=params,
    timeout=30
)
```

---

## 4. АНАЛИЗ ФЕЙКОВОГО БЭКТЕСТЕРА (`src/ml/integration/backtester.py`)

### КРИТИЧЕСКИЙ БАГ: Random вместо реальных цен

**Файл:** `src/ml/integration/backtester.py`
**Строки:** 465-508

```python
def _simulate_exit(
    self,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    is_long: bool,
    ml_win_prob: float,
    price_data: Optional[pd.DataFrame],  # НЕ ИСПОЛЬЗУЕТСЯ!
    symbol: str,
) -> Tuple[float, str, float]:
    """
    Simulate trade exit.
    """
    # Use ML win probability to determine outcome
    # This is a simplified simulation  <-- ПРИЗНАНИЕ!
    win_prob = ml_win_prob if ml_win_prob > 0 else 0.55

    import random
    outcome = random.random()  # <-- ВОТ ОН RANDOM!

    if outcome < win_prob * 0.3:
        return tp3, "TP3", random.uniform(24, 72)
    elif outcome < win_prob * 0.6:
        return tp2, "TP2", random.uniform(12, 36)
    elif outcome < win_prob:
        return tp1, "TP1", random.uniform(4, 18)
    elif outcome < win_prob + (1 - win_prob) * 0.7:
        return sl, "STOP_LOSS", random.uniform(1, 12)
    else:
        # Timeout - Random exit
        if is_long:
            exit_p = entry * (1 - random.uniform(0, abs(entry - sl) / entry))
        else:
            exit_p = entry * (1 + random.uniform(0, abs(entry - sl) / entry))
        return exit_p, "TIMEOUT", self._config.max_hold_hours
```

**Проблема:**
- Параметр `price_data` передаётся но **НЕ ИСПОЛЬЗУЕТСЯ**
- Результат определяется **random.random()**
- Все метрики (Sharpe, Win Rate, etc.) — **МУСОР**

---

## 5. ПОТОК ДАННЫХ

### Как ДОЛЖЕН работать (и работает в `backtester/`):

```
signals.jsonl
     ↓
LogParser → ParsedSignal[]
     ↓
BinanceDataLoader → Dict[symbol, Kline[]]
     ↓
PositionSimulator.simulate(signal, klines)
     ↓
  _find_entry() → проверить entry zone по kline.low/high
     ↓
  _simulate_position() → пройти свечи после входа
     ↓
    Каждая свеча:
      1. Проверить SL (kline.low <= sl_price)
      2. Проверить TP1, TP2, TP3
      3. Проверить timeout
     ↓
BacktestResult (реальный PnL)
```

### Как работает СЛОМАННЫЙ (`src/ml/integration/backtester.py`):

```
signals.jsonl
     ↓
_load_signals() → Dict[]
     ↓
_simulate_trade()
     ↓
_simulate_exit() → random.random() → ФЕЙКОВЫЙ результат
     ↓
BacktestResult (МУСОР)
```

---

## 6. ИСПОЛЬЗОВАНИЕ RANDOM

### В `backtester/` — ТОЛЬКО в тестовом генераторе (ОК):

| Файл | Строка | Использование | Проблема? |
|------|--------|---------------|-----------|
| test_signal_generator.py | 60 | `random.choice(["LONG", "SHORT"])` | НЕТ (тестовые данные) |
| test_signal_generator.py | 65 | `random.uniform(0.95, 1.05)` | НЕТ (тестовые данные) |
| ... | ... | ... | НЕТ |

### В `src/ml/integration/backtester.py` — КРИТИЧНО:

| Строка | Использование | Проблема? |
|--------|---------------|-----------|
| 486 | `import random` | **ДА — в продакшн коде** |
| 487 | `outcome = random.random()` | **ДА — определяет результат** |
| 489-508 | `random.uniform()` | **ДА — hold time и exit price** |

---

## 7. ЧТО НУЖНО ИСПРАВИТЬ

| # | Файл | Проблема | Решение | Приоритет |
|---|------|----------|---------|-----------|
| 1 | `src/ml/integration/backtester.py` | Random вместо реальных цен | Использовать `backtester/` как основу | КРИТИЧНО |
| 2 | `src/ml/integration/backtester.py` | price_data не используется | Удалить или использовать | КРИТИЧНО |
| 3 | ML система | Не интегрирована с рабочим бэктестером | Интегрировать | ВЫСОКИЙ |

---

## 8. РЕКОМЕНДАЦИЯ

### ВАРИАНТ А (быстрый):
Удалить `src/ml/integration/backtester.py` и использовать `backtester/` напрямую.

### ВАРИАНТ Б (правильный):
Переписать `src/ml/integration/backtester.py` чтобы использовал:
- `backtester.data_loader.BinanceDataLoader` для klines
- `backtester.position_simulator.PositionSimulator` для симуляции

---

## 9. КАК ЗАПУСТИТЬ РАБОЧИЙ БЭКТЕСТЕР

```bash
# Из корня проекта:
python -m backtester.main

# С параметрами:
python -m backtester.main --signals logs/signals.jsonl --interval 1m

# Очистить кэш:
python -m backtester.main --clear-cache
```

---

## 10. ВЫВОД

**РАБОЧИЙ бэктестер УЖЕ ЕСТЬ!** Это `backtester/`.

**ML бэктестер (`src/ml/integration/backtester.py`) — БЕСПОЛЕЗЕН** из-за random.

Для обучения ML нужно:
1. Использовать `backtester/` для получения реальных результатов сигналов
2. Эти результаты использовать как labels для обучения
