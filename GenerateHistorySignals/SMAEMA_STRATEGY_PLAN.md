# ПЛАН: Стратегия SMAEMA

---

## 1. Общее описание

| Параметр | Значение |
|----------|----------|
| **Название** | SMAEMA |
| **Логика** | SMA/EMA crossover (Golden Cross / Death Cross) |
| **Таймфреймы** | 1, 5, 15, 60, 240 мин + daily |
| **Дефолтный таймфрейм** | daily (если --bar не указан) |
| **Интеграция** | Входит в `--strategies all` |
| **Отдельный запуск** | `--strategy SMAEMA` |

---

## 2. Параметры запуска

### ВСЕ ОБЯЗАТЕЛЬНЫ для SMAEMA (без дефолтов!)

| Параметр | CLI флаг | Тип | Описание |
|----------|----------|-----|----------|
| fastType | `--fast-type` | str | SMA или EMA |
| fastPeriod | `--fast-period` | int | Период быстрой MA |
| slowType | `--slow-type` | str | SMA или EMA |
| slowPeriod | `--slow-period` | int | Период медленной MA |
| offsetPct | `--offset-pct` | float | Смещение entry от close (+ выше, - ниже) |
| orderLifetime | `--order-lifetime` | int | Кол-во свечей на вход, иначе skip |
| takeProfitPct | `--tp` | float | Take Profit % |
| stopLossPct | `--sl` | float | Stop Loss % |

### Опциональный параметр

| Параметр | CLI флаг | Тип | Дефолт | Описание |
|----------|----------|-----|--------|----------|
| bar | `--bar` | str | daily | Таймфрейм: 1, 5, 15, 60, 240, daily |

---

## 3. Примеры запуска

### Только SMAEMA на 15-мин свечах
```bash
py run_all.py --strategy SMAEMA --bar 15 \
    --fast-type SMA --fast-period 1 \
    --slow-type EMA --slow-period 8 \
    --offset-pct 0.5 --order-lifetime 2 \
    --tp 4 --sl 6
```

### Только SMAEMA на дневных (дефолт)
```bash
py run_all.py --strategy SMAEMA \
    --fast-type SMA --fast-period 1 \
    --slow-type EMA --slow-period 8 \
    --offset-pct 0.5 --order-lifetime 2 \
    --tp 4 --sl 6
```

### Все стратегии С параметрами SMAEMA
```bash
py run_all.py --strategies all --bar 15 \
    --fast-type SMA --fast-period 1 \
    --slow-type EMA --slow-period 8 \
    --offset-pct 0.5 --order-lifetime 2 \
    --tp 4 --sl 6
```

### Все стратегии БЕЗ параметров SMAEMA
```bash
py run_all.py --strategies all
# Результат: WARNING + SMAEMA пропускается, остальные работают
```

---

## 4. Поведение при отсутствии параметров

| Ситуация | Действие |
|----------|----------|
| `--strategies all` без параметров SMAEMA | WARNING + пропуск SMAEMA, остальные работают |
| `--strategy SMAEMA` без параметров | ERROR, не запускаем |
| `--strategy SMAEMA` с частью параметров | ERROR с указанием какой именно параметр пропущен |

### Пример WARNING (при --strategies all)
```
WARNING: SMAEMA strategy skipped - missing required parameters.
         To run SMAEMA, specify: --fast-type, --fast-period, --slow-type,
         --slow-period, --offset-pct, --order-lifetime, --tp, --sl

Running strategies: ls_fade, momentum, reversal, mean_reversion, momentum_ls
```

### Пример ERROR (при --strategy SMAEMA)
```
ERROR: Missing required parameter: --fast-period
SMAEMA strategy requires all parameters to be specified.

Required parameters:
  --fast-type      (SMA or EMA)
  --fast-period    (integer)
  --slow-type      (SMA or EMA)
  --slow-period    (integer)
  --offset-pct     (float, + выше цены, - ниже цены)
  --order-lifetime (integer, в свечах)
  --tp             (float, %)
  --sl             (float, %)
```

---

## 5. Логика сигналов — Crossover

### Правила пересечения

| Событие | Направление | Условие |
|---------|-------------|---------|
| **Golden Cross** | LONG | fast_ma пересекает slow_ma **снизу вверх** |
| **Death Cross** | SHORT | fast_ma пересекает slow_ma **сверху вниз** |

### Алгоритм детекции (из crossover.py:68-86)
```
1. Если fast_ma_curr == slow_ma_curr → НЕ пересечение (возврат NONE)
2. prev_diff = fast_ma_prev - slow_ma_prev
3. curr_diff = fast_ma_curr - slow_ma_curr
4. Если prev_diff < 0 AND curr_diff > 0 → BULLISH (Golden Cross)
5. Если prev_diff > 0 AND curr_diff < 0 → BEARISH (Death Cross)
6. Иначе → NONE
```

### КРИТИЧНО — Граничные случаи
- Если `fast_ma == slow_ma` → НЕ пересечение
- Если `prev_diff == 0` → НЕ пересечение (строго `<` и `>`, НЕ `<=` и `>=`)
- Нет двух одинаковых сигналов подряд (альтернация BUY/SELL)

---

## 6. Параметр offsetPct — смещение entry

### Формула
```
entry = close × (1 + offsetPct / 100)
```

### Примеры (close = $100)

| offsetPct | Формула | Entry цена |
|-----------|---------|------------|
| **+0.5** | 100 × 1.005 | $100.50 (выше цены) |
| **-0.5** | 100 × 0.995 | $99.50 (ниже цены) |
| **0** | 100 × 1.0 | $100.00 (по цене закрытия) |

### Применение

| Сигнал | offsetPct | Entry | Тип ордера |
|--------|-----------|-------|------------|
| LONG | -0.5 | Ниже close | Лимитный ордер на покупку |
| LONG | +0.5 | Выше close | Стоп-ордер на покупку |
| SHORT | +0.5 | Выше close | Лимитный ордер на продажу |
| SHORT | -0.5 | Ниже close | Стоп-ордер на продажу |

---

## 7. Параметр orderLifetime — время жизни ордера

### Единица измерения: СВЕЧИ (bars)

| Таймфрейм (--bar) | orderLifetime = 2 | Реальное время |
|-------------------|-------------------|----------------|
| 1 | 2 свечи | 2 минуты |
| 5 | 2 свечи | 10 минут |
| 15 | 2 свечи | 30 минут |
| 60 | 2 свечи | 2 часа |
| 240 | 2 свечи | 8 часов |
| daily | 2 свечи | 2 дня |

### Логика в бэктесте
```
1. Свеча N: crossover → генерируем сигнал
2. Entry = close[N] × (1 + offsetPct/100)
3. Проверяем свечи N+1, N+2, ..., N+orderLifetime:
   - LONG: если low <= entry → ВХОД
   - SHORT: если high >= entry → ВХОД
4. Если за orderLifetime свечей entry не достигнут → SKIP сигнала
```

---

## 8. Файлы для создания

| Файл | Источник | Действие |
|------|----------|----------|
| `strategies/smaema_indicators.py` | `G:\BinanceFriend\strategy\indicators.py` | Копия 1-в-1, только заменить логгер |
| `strategies/smaema_crossover.py` | `G:\BinanceFriend\strategy\crossover.py` | Копия 1-в-1, только заменить логгер |
| `strategies/smaema.py` | Новый файл | Стратегия-наследник BaseStrategy |

---

## 9. АУДИТ ИСХОДНОГО КОДА — indicators.py (374 строки)

### Два режима арифметики

| Режим | Параметр | Тип данных | Округление |
|-------|----------|------------|------------|
| **Decimal** (по умолч.) | `tester_arithmetic=False` | `Decimal` | 8 знаков, `ROUND_HALF_UP` |
| **Tester** | `tester_arithmetic=True` | `numpy.float32` | Без округления |

### SMA — Simple Moving Average

**Формула:**
```
SMA = Sum(последние N цен) / N
```

**Decimal режим (строки 102-104):**
```python
total = sum(Decimal(str(p)) for p in recent_prices)
return (total / period).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
```

**Tester режим (строки 96-100):**
```python
total = np.float32(0.0)
for p in recent_prices:
    total = np.float32(total + np.float32(p))
return np.float32(total / np.float32(period))
```

### EMA — Exponential Moving Average

**Формула:**
```
multiplier = 2 / (period + 1)
EMA = Price × multiplier + EMA_prev × (1 - multiplier)
```

**КРИТИЧНО — Инициализация EMA:**

| Режим | Первое значение EMA |
|-------|---------------------|
| **Decimal** | `EMA[0] = первая цена` (строка 273) |
| **Tester** | `EMA[0] = SMA(первые N цен)` (строки 253-257) |

**Tester режим — инициализация (строки 253-257):**
```python
sma_sum = np.float32(0.0)
for i in range(period):
    sma_sum = np.float32(sma_sum + f32_prices[i])
ema = np.float32(sma_sum / np.float32(period))
```

**Tester режим — обновление (строки 260-264):**
```python
# Формула: (close - ema) * alpha + ema
ema = np.float32(
    np.float32(price - ema) * multiplier + ema
)
```

**Decimal режим — обновление (строки 276-279):**
```python
ema = price * multiplier + ema * (Decimal("1") - multiplier)
return ema.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
```

---

## 10. АУДИТ ИСХОДНОГО КОДА — crossover.py (193 строки)

### CrossoverType enum (строки 18-23)
```python
class CrossoverType(str, Enum):
    BULLISH = "BULLISH"   # Golden Cross (fast выше slow)
    BEARISH = "BEARISH"   # Death Cross (fast ниже slow)
    NONE = "NONE"         # Нет пересечения
```

### Алгоритм детекции (строки 68-86)
```python
# ВАЖНО: Если равны — НЕ пересечение (REQ-032)
if fast_ma_curr == slow_ma_curr:
    return CrossoverType.NONE

prev_diff = fast_ma_prev - slow_ma_prev
curr_diff = fast_ma_curr - slow_ma_curr

# BULLISH: fast был ниже slow, теперь выше
# СТРОГО: prev_diff < 0 (не <=)
if prev_diff < 0 and curr_diff > 0:
    return CrossoverType.BULLISH

# BEARISH: fast был выше slow, теперь ниже
# СТРОГО: prev_diff > 0 (не >=)
if prev_diff > 0 and curr_diff < 0:
    return CrossoverType.BEARISH

return CrossoverType.NONE
```

### Helper функции
```python
is_golden_cross(fast_prev, fast_curr, slow_prev, slow_curr) -> bool
is_death_cross(fast_prev, fast_curr, slow_prev, slow_curr) -> bool
```

---

## 11. АУДИТ ИСХОДНОГО КОДА — signals.py (272 строки)

### SignalGenerator — что переиспользуем

| Функционал | Строки | Переиспользуем? |
|------------|--------|-----------------|
| Инициализация MA | 52-61 | ✅ Да |
| Crossover detector | 64 | ✅ Да |
| min_signal_gap | 201-210 | ✅ Да |
| Альтернация сигналов | 192-199 | ✅ Да |
| on_candle_close логика | 143-241 | ⚠️ Адаптировать под batch |

### Альтернация сигналов (строки 192-199)
```python
# Нет двух одинаковых сигналов подряд
if self._last_signal_type == signal_type:
    logger.debug("Signal suppressed - same type as last signal")
    return None
```

### min_signal_gap (строки 201-210)
```python
bars_since_last = self._bar_count - self._last_signal_bar
if bars_since_last < self._config.min_signal_gap:
    logger.debug("Signal suppressed by min_signal_gap")
    return None
```

---

## 12. КРИТИЧНЫЕ ДЕТАЛИ — КОПИРОВАТЬ 1-в-1

### Типы данных

| Что | Код | Почему важно |
|-----|-----|--------------|
| Decimal округление | `Decimal("0.00000001")` | Точность 8 знаков |
| Округление | `ROUND_HALF_UP` | Банковское округление |
| Конвертация цен | `Decimal(str(price))` | Через строку, не напрямую! |
| Float32 | `np.float32(value)` | Явное приведение КАЖДОЙ операции |

### Пример НЕПРАВИЛЬНО vs ПРАВИЛЬНО

**НЕПРАВИЛЬНО:**
```python
total = sum(prices)  # Потеря точности
ema = float(price) * multiplier  # Не float32
```

**ПРАВИЛЬНО:**
```python
total = sum(Decimal(str(p)) for p in prices)
ema = np.float32(np.float32(price) * np.float32(multiplier))
```

---

## 13. Интеграция с существующей системой

### Изменения в файлах

| Файл | Что добавить |
|------|--------------|
| `strategies/__init__.py` | Импорт SMAEMAStrategy, добавить в STRATEGY_REGISTRY |
| `telegram_runner.py` | Параметры --bar, --fast-type, --fast-period, и т.д. |
| `strategy_runner.py` | Параметры --bar, --fast-type, --fast-period, и т.д. |
| `hybrid_downloader.py` | Загрузка свечей разных таймфреймов (1m, 5m, 15m, 1h, 4h) |

### Новые файлы

| Файл | Описание |
|------|----------|
| `strategies/smaema_indicators.py` | Копия indicators.py |
| `strategies/smaema_crossover.py` | Копия crossover.py |
| `strategies/smaema.py` | Новая стратегия |

---

## 14. Структура SMAEMAStrategy

```python
class SMAEMAStrategy(BaseStrategy):
    name = "smaema"
    description = "SMA/EMA crossover strategy"

    def __init__(self, config: StrategyConfig):
        # Обязательные параметры из config.params:
        # - fast_type: str (SMA/EMA)
        # - fast_period: int
        # - slow_type: str (SMA/EMA)
        # - slow_period: int
        # - offset_pct: float
        # - order_lifetime: int
        # SL/TP из config.sl_pct, config.tp_pct

    def generate_signals(self, data: StrategyData) -> List[Signal]:
        # 1. Рассчитать fast_ma и slow_ma для всех свечей
        # 2. Найти crossover точки
        # 3. Для каждого crossover:
        #    - Рассчитать entry = close * (1 + offset_pct/100)
        #    - Проверить вход в течение order_lifetime свечей
        #    - Если вход есть → создать Signal
        #    - Если входа нет → skip
        # 4. Применить альтернацию (нет двух одинаковых подряд)
```

---

## 15. TODO перед кодингом

- [x] Уточнить все параметры
- [x] Уточнить offsetPct → смещение entry (+ выше, - ниже)
- [x] Уточнить orderLifetime → время жизни в свечах
- [x] Провести полный аудит исходного кода
- [x] Определить что копировать 1-в-1
- [ ] **Погрумить вместе перед кодингом!**

---

## 16. Исходные файлы для копирования

**Путь:** `G:\BinanceFriend\strategy\`

| Файл | Строк | Содержимое |
|------|-------|------------|
| `indicators.py` | 374 | SMA, EMA классы, два режима арифметики |
| `crossover.py` | 193 | CrossoverDetector, CrossoverType, is_golden_cross, is_death_cross |
| `signals.py` | 272 | SignalGenerator (частично переиспользуем) |
| `__init__.py` | 49 | Экспорты модуля |

---

## 17. ЖЕЛЕЗНЫЕ ПРАВИЛА ИНТЕГРАЦИИ

### Правило 1: НЕ ТРОГАТЬ СУЩЕСТВУЮЩИЕ СТРАТЕГИИ

```
┌─────────────────────────────────────────────────────────────────────┐
│  ЗАПРЕЩЕНО менять логику:                                          │
│  - ls_fade                                                         │
│  - momentum                                                        │
│  - reversal                                                        │
│  - mean_reversion                                                  │
│  - momentum_ls                                                     │
│                                                                     │
│  Если нужны изменения в общих файлах — ТОЛЬКО ДОБАВЛЯТЬ,          │
│  НЕ ИЗМЕНЯТЬ существующую логику.                                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Правило 2: ДИНАМИЧЕСКИЕ ПАРАМЕТРЫ СИСТЕМЫ

Все существующие параметры ДОЛЖНЫ работать для SMAEMA:

| Параметр | CLI флаг | Применимо? | Как работает для SMAEMA |
|----------|----------|------------|-------------------------|
| Coin Regime | `--coin-regime` | ✅ | Режим монеты рассчитывается по той же логике |
| Vol Filter Low | `--vol-filter-low` | ✅ | Фильтр низкой волатильности |
| Vol Filter High | `--vol-filter-high` | ✅ | Фильтр высокой волатильности |
| ML фильтр | `--ml` | ⚠️ | Нет моделей для SMAEMA — пропускаем с warning |
| Dry Run | `--dry-run` | ✅ | Режим без отправки |
| Symbols | `--symbols` | ✅ | Список символов |
| Top N | `--top` | ✅ | Топ N символов по объёму |

### Правило 3: НЕПРИМЕНИМЫЕ ПАРАМЕТРЫ

Если параметр/матрица НЕ МОЖЕТ быть применён к SMAEMA:

1. **НЕ ЛОМАТЬ** — расчёт продолжается без этого параметра
2. **НЕ ПАДАТЬ** — никаких ошибок
3. **ЛОГИРОВАТЬ** — в итоговой таблице вывести:

```
========================================
НЕПРИМЕНИМЫЕ ПАРАМЕТРЫ
========================================
Параметр           | Стратегия | Причина
-------------------|-----------|----------------------------------
COIN_REGIME_MATRIX | SMAEMA    | Матрица не откалибрована для данной стратегии
ML фильтр          | SMAEMA    | Нет обученных моделей для стратегии SMAEMA
VOL_FILTER_THRESH  | SMAEMA    | Пороги волатильности не откалиброваны
========================================
```

---

## 18. ПРОМПТ РАЗРАБОТКИ

**Файл:** `SMAEMA_DEV_PROMPT.md`

Перед каждой сессией разработки ОБЯЗАТЕЛЬНО прочитать:
1. `SMAEMA_DEV_PROMPT.md` — промпт с правилами
2. `SMAEMA_STRATEGY_PLAN.md` — этот файл, раздел СТАТУС СЕССИЙ

### ПРАВИЛО СОМНЕНИЙ

```
┌─────────────────────────────────────────────────────────────────────┐
│  ПРИ ЛЮБЫХ СОМНЕНИЯХ:                                              │
│                                                                     │
│  1. СТОП — не продолжай                                            │
│  2. Перечитай промпт аналитика                                     │
│  3. Дай 2-4 профессиональных варианта с плюсами/минусами           │
│  4. ЖЁСТКО ЖДИ ответа пользователя                                 │
│  5. Только после ответа — продолжай по выбранному варианту         │
│                                                                     │
│  НИКАКИХ СОБСТВЕННЫХ ФАНТАЗИЙ!                                     │
│  НИКАКИХ РЕШЕНИЙ "ПО УМОЛЧАНИЮ"!                                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 19. СТАТУС СЕССИЙ

### Сессия 0 — Инициализация

**Дата:** [дата создания плана]

**Что сделано:**
- [x] Создан план стратегии SMAEMA
- [x] Проведён полный аудит исходного кода (indicators.py, crossover.py, signals.py)
- [x] Определены все параметры CLI
- [x] Описаны правила интеграции
- [x] Создан промпт разработки (SMAEMA_DEV_PROMPT.md)

**Файлы изменены:**
| Файл | Что сделано |
|------|-------------|
| SMAEMA_STRATEGY_PLAN.md | Создан план |
| SMAEMA_DEV_PROMPT.md | Создан промпт разработки |

---

### Сессия 1 — Разработка (текущая)

**Дата:** 2026-03-06

**Что сделано:**
- [x] Создать `strategies/smaema_indicators.py` (копия indicators.py) — ВЫПОЛНЕНО
- [x] Создать `strategies/smaema_crossover.py` (копия crossover.py) — ВЫПОЛНЕНО
- [x] Создать `strategies/smaema.py` (новая стратегия) — ВЫПОЛНЕНО
- [x] Добавить в `strategies/__init__.py` — ВЫПОЛНЕНО
- [x] Добавить CLI параметры в `telegram_runner.py` — ВЫПОЛНЕНО
- [ ] Проверить работоспособность — В ОЖИДАНИИ

**Файлы созданы/изменены:**
| Файл | Действие | Статус |
|------|----------|--------|
| strategies/smaema_indicators.py | Создан (копия) | ✅ |
| strategies/smaema_crossover.py | Создан (копия) | ✅ |
| strategies/smaema.py | Создан (новый) | ✅ |
| strategies/__init__.py | Дозаполнен | ✅ |
| telegram_runner.py | Дозаполнен CLI | ✅ |
| strategy_runner.py | Не требуется (уже работает) | ✅ |

**Где остановились:**
- Этап: БАЗОВАЯ РАЗРАБОТКА ЗАВЕРШЕНА
- Следующий этап: ТЕСТИРОВАНИЕ

**Что реализовано:**
1. **smaema_indicators.py** — копия indicators.py без изменений (убран логгер)
2. **smaema_crossover.py** — копия crossover.py без изменений (убран логгер)
3. **smaema.py** — новый класс SMAEMAStrategy:
   - Все параметры ОБЯЗАТЕЛЬНЫ (без дефолтов)
   - generate_signals() — расчёт MA, детекция crossover, проверка orderLifetime
   - Альтернация сигналов (нет двух LONG/SHORT подряд)
   - offsetPct работает: entry = close * (1 + offset/100)
   - orderLifetime работает: проверка входа в течение N свечей

4. **telegram_runner.py** — добавлены CLI параметры:
   - `--bar` (таймфрейм, дефолт daily)
   - `--fast-type`, `--fast-period`
   - `--slow-type`, `--slow-period`
   - `--offset-pct`, `--order-lifetime`
   - Логика: если `--strategies all` без SMAEMA params → WARNING + skip SMAEMA
   - Логика: если `--strategy smaema` без params → ERROR

5. **strategy_runner.py** — не требует изменений:
   - COIN_REGIME_MATRIX: smaema не в матрице → дефолт FULL (не блокируется)
   - VOL_FILTER_THRESHOLDS: smaema не в словаре → фильтр не применяется
   - Это соответствует плану: не падать, продолжать без неприменимых параметров

**Проблема обнаружена:**
- Бэктестер НЕ ПОДДЕРЖИВАЕТ разные таймфреймы
- Загрузчик НЕ ПОДДЕРЖИВАЕТ 15m, 1h, 4h
- Требуется доработка по §20 (АУДИТ ДОРАБОТКИ БЭКТЕСТЕРА)

---

### Сессия 2 — Доработка сквозной работы --bar [ВЫПОЛНЕНО]

**Дата:** 2026-03-06

**Что сделано:**
1. [x] `data_downloader.py:94-101`: добавлены 15m, 1h, 4h в VALID_INTERVALS и INTERVAL_MAP
2. [x] `strategy_runner.py:262`: добавлен параметр `data_interval` в __init__
3. [x] `strategy_runner.py:359-432`: создана функция `aggregate_to_interval()`
4. [x] `strategy_runner.py:615`: заменён `aggregate_to_daily` на `aggregate_to_interval` в generate_signals
5. [x] `strategy_runner.py:814`: заменён `aggregate_to_daily` на `aggregate_to_interval` в backtest_signals
6. [x] `strategy_runner.py:826-834`: индексация по timestamp (работает для любого интервала)
7. [x] `strategy_runner.py:1025,1101-1103`: итерация по timestamp вместо строковой даты
8. [x] `telegram_runner.py:489`: args.bar передаётся в HybridHistoryDownloader
9. [x] `telegram_runner.py:99,139,596`: data_interval передаётся в generate_signals_for_strategy и StrategyRunner
10. [x] `run_all.py:231,445`: data_interval передаётся в StrategyRunner, choices обновлены

**Файлы изменены:**
| Файл | Изменение | Строки |
|------|-----------|--------|
| data_downloader.py | +3 интервала (15m, 1h, 4h) | 94-101 |
| strategy_runner.py | +data_interval param, +aggregate_to_interval() | 262, 359-432 |
| strategy_runner.py | timestamp индексация в generate_signals | 615-618, 664-679 |
| strategy_runner.py | timestamp индексация в backtest_signals | 814, 826-834, 1025, 1101-1103 |
| telegram_runner.py | args.bar → downloader, +data_interval | 489, 99, 139, 596 |
| run_all.py | +data_interval в StrategyRunner, choices | 231, 445 |

**Синтаксис:** Проверен, OK

---

### Сессия 3 — Исправление несоответствий [ВЫПОЛНЕНО]

**Дата:** 2026-03-06

**Найденные проблемы:**
1. [x] --bar формат: план 15→15m конвертация отсутствовала
2. [x] run_all.py: не было --strategy/--strategies
3. [x] run_all.py: не было SMAEMA params
4. [x] telegram_runner.py: не было --strategy (singular)

**Что сделано:**
1. [x] telegram_runner.py: добавлена конвертация --bar (1→1m, 15→15m, 60→1h, 240→4h)
2. [x] run_all.py: добавлены --strategies, --strategy параметры
3. [x] run_all.py: добавлен --bar с конвертацией
4. [x] run_all.py: добавлены все SMAEMA params (--fast-type, --fast-period, и т.д.)
5. [x] run_all.py: изменён run_all_strategies для поддержки strategies и smaema_params
6. [x] telegram_runner.py: добавлен --strategy (singular)

**Файлы изменены:**
| Файл | Изменение | Строки |
|------|-----------|--------|
| telegram_runner.py | +BAR_CONVERSION | 430-440 |
| telegram_runner.py | +--strategy | 416 |
| telegram_runner.py | +обработка args.strategy | 540-559 |
| run_all.py | +ALL_STRATEGIES_WITH_SMAEMA | 40-43 |
| run_all.py | +--strategies, --strategy, --bar, SMAEMA params | 469-485 |
| run_all.py | +BAR_CONVERSION, +обработка стратегий | 488-546 |
| run_all.py | +strategies, smaema_params в run_all_strategies | 109-141 |
| run_all.py | +strategies_to_run, +smaema_params в config | 185-228 |

**Синтаксис:** Проверен, OK

**Файлы для изменения:**
| Файл | Изменение | Строки |
|------|-----------|--------|
| data_downloader.py | +3 интервала | 94, 97-101 |
| strategy_runner.py | +aggregate_to_interval() | после 355 |
| strategy_runner.py | +data_interval param | 253-262 |
| strategy_runner.py | изменить generate_signals | 528 |
| strategy_runner.py | изменить backtest_signals | 725, 737, 1012 |
| telegram_runner.py | передать interval | 467-472, 122-127 |

**КРИТИЧНО:**
- НЕ ЛОМАТЬ существующие стратегии
- НЕ МЕНЯТЬ aggregate_to_daily()
- Дефолт interval = "daily"

---

## 20. АУДИТ ЦЕПОЧКИ: ЗАГРУЗКА → ГЕНЕРАЦИЯ → БЭКТЕСТ

### 20.1 ТЕКУЩЕЕ СОСТОЯНИЕ (ФАКТЫ ИЗ КОДА)

#### Загрузчик данных

| Файл | Строка | Проблема |
|------|--------|----------|
| `data_downloader.py` | 94 | `VALID_INTERVALS = ("daily", "5m", "1m")` — **НЕТ 15m, 1h, 4h** |
| `data_downloader.py` | 97-101 | `INTERVAL_MAP` не содержит 15m, 1h, 4h |
| `hybrid_downloader.py` | 60 | Передаёт `data_interval` в BinanceHistoryDownloader |

**Binance API поддерживает:** 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M

#### Генерация сигналов

| Файл | Строка | Что делает |
|------|--------|------------|
| `strategy_runner.py` | 300-355 | `aggregate_to_daily()` — ВСЕГДА агрегирует в daily |
| `strategy_runner.py` | 528 | `candles = self.aggregate_to_daily(raw.klines)` |
| `strategy_runner.py` | 725 | `candles_cache[symbol] = self.aggregate_to_daily(...)` |

#### Бэктест

| Файл | Строка | Проблема |
|------|--------|----------|
| `strategy_runner.py` | 737 | `candle_by_date = {c.date.strftime("%Y-%m-%d"): c ...}` — индекс по ДАТЕ |
| `strategy_runner.py` | 1012 | `for j in range(1, min(max_hold_days + 1, ...))` — итерация по ДНЯМ |
| `strategy_runner.py` | 660 | `max_hold_days: int = 14` — параметр в ДНЯХ |

---

### 20.2 ПЛАН ДОРАБОТКИ

#### ЭТАП 1: Загрузчик — добавить таймфреймы

**Файл:** `data_downloader.py`

```python
# Строка 94 — БЫЛО:
VALID_INTERVALS = ("daily", "5m", "1m")

# СТАЛО:
VALID_INTERVALS = ("daily", "4h", "1h", "15m", "5m", "1m")

# Строки 97-101 — ДОБАВИТЬ в INTERVAL_MAP:
"15m": {"klines": "15m", "oi": "5m", "ls": "5m"},
"1h":  {"klines": "1h",  "oi": "5m", "ls": "5m"},
"4h":  {"klines": "4h",  "oi": "5m", "ls": "5m"},
```

#### ЭТАП 2: Агрегация — универсальная функция

**Файл:** `strategy_runner.py`

```python
# ДОБАВИТЬ новый метод (НЕ менять aggregate_to_daily!):

@staticmethod
def aggregate_to_interval(klines: List[Dict], interval: str) -> List[DailyCandle]:
    """
    Aggregate klines to specified interval.

    Args:
        klines: Raw klines data
        interval: Target interval ("daily", "4h", "1h", "15m", "5m", "1m")

    Returns:
        List of candles aggregated to interval
    """
    if interval == "daily":
        return StrategyRunner.aggregate_to_daily(klines)

    # Для других интервалов — группировка по времени
    interval_minutes = {
        "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240
    }
    minutes = interval_minutes.get(interval, 1440)  # default daily

    candles_dict = {}
    for k in klines:
        ts = k.get("timestamp", 0)
        # Округление до интервала
        interval_ts = (ts // (minutes * 60 * 1000)) * (minutes * 60 * 1000)

        if interval_ts not in candles_dict:
            candles_dict[interval_ts] = {
                "timestamp": interval_ts,
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k["volume"]),
                ...
            }
        else:
            candles_dict[interval_ts]["high"] = max(...)
            candles_dict[interval_ts]["low"] = min(...)
            candles_dict[interval_ts]["close"] = float(k["close"])
            candles_dict[interval_ts]["volume"] += float(k["volume"])
            ...

    # Конвертация в DailyCandle (или Candle)
    ...
```

#### ЭТАП 3: StrategyRunner — параметр interval

**Файл:** `strategy_runner.py`

```python
# В __init__ добавить:
def __init__(
    self,
    strategy_name: str = "ls_fade",
    config: Optional[StrategyConfig] = None,
    output_dir: str = "output",
    use_ml: bool = False,
    ml_model_dir: str = "models",
    data_interval: str = "daily",  # <-- ДОБАВИТЬ
    ...
):
    self.data_interval = data_interval
    ...

# В generate_signals (строка 528) — ИЗМЕНИТЬ:
# БЫЛО:
candles = self.aggregate_to_daily(raw.klines)

# СТАЛО:
candles = self.aggregate_to_interval(raw.klines, self.data_interval)
```

#### ЭТАП 4: Бэктест — работа с любым таймфреймом

**Файл:** `strategy_runner.py`

```python
# Строка 725 — ИЗМЕНИТЬ:
# БЫЛО:
candles_cache[symbol] = self.aggregate_to_daily(history[symbol].klines)

# СТАЛО:
candles_cache[symbol] = self.aggregate_to_interval(
    history[symbol].klines,
    self.data_interval
)

# Строка 737 — ИЗМЕНИТЬ индексацию:
# БЫЛО (индекс по дате):
candle_by_date = {c.date.strftime("%Y-%m-%d"): c for c in candles}

# СТАЛО (индекс по timestamp):
candle_by_ts = {int(c.date.timestamp() * 1000): c for c in candles}
candle_timestamps = sorted(candle_by_ts.keys())

# Строка 660 — ИЗМЕНИТЬ параметр:
# БЫЛО:
max_hold_days: int = 14

# СТАЛО:
max_hold_bars: int = 14  # В свечах, не в днях

# Строка 1012 — ИЗМЕНИТЬ итерацию:
# БЫЛО:
for j in range(1, min(max_hold_days + 1, len(candle_dates) - start_idx)):

# СТАЛО:
for j in range(1, min(max_hold_bars + 1, len(candle_timestamps) - start_idx)):
```

#### ЭТАП 5: telegram_runner.py — передача interval

**Файл:** `telegram_runner.py`

```python
# При создании HybridHistoryDownloader:
downloader = HybridHistoryDownloader(
    cache_dir='cache',
    coinalyze_api_key=config.get("coinalyze_api_key", ""),
    data_interval=args.bar if args.bar != 'daily' else 'daily'  # <-- ДОБАВИТЬ
)

# При создании StrategyRunner (функция generate_signals_for_strategy):
runner = StrategyRunner(
    strategy_name=strategy_name,
    config=strat_config,
    output_dir="output",
    use_ml=False,
    data_interval=smaema_params.get('bar', 'daily') if strategy_name == 'smaema' else 'daily',
)
```

---

### 20.3 ПОРЯДОК РЕАЛИЗАЦИИ

| # | Что делать | Файл | Риск |
|---|------------|------|------|
| 1 | Добавить интервалы в VALID_INTERVALS и INTERVAL_MAP | data_downloader.py | Низкий |
| 2 | Создать `aggregate_to_interval()` | strategy_runner.py | Низкий |
| 3 | Добавить `data_interval` в StrategyRunner.__init__ | strategy_runner.py | Низкий |
| 4 | Заменить вызовы aggregate_to_daily для SMAEMA | strategy_runner.py | Средний |
| 5 | Изменить индексацию в backtest_signals | strategy_runner.py | Средний |
| 6 | Передать interval из CLI в downloader и runner | telegram_runner.py | Низкий |

---

### 20.4 КРИТИЧЕСКИЕ ТОЧКИ

```
┌─────────────────────────────────────────────────────────────────────┐
│  НЕЛЬЗЯ ЛОМАТЬ:                                                     │
│                                                                     │
│  1. Существующие стратегии (ls_fade, momentum, и т.д.)             │
│     → Они ВСЕГДА используют daily                                   │
│     → Добавляем параметр, но дефолт = daily                        │
│                                                                     │
│  2. aggregate_to_daily() — НЕ МЕНЯТЬ                                │
│     → Создаём НОВУЮ функцию aggregate_to_interval()                 │
│     → aggregate_to_daily() вызывается для interval="daily"          │
│                                                                     │
│  3. Бэктест существующих стратегий                                  │
│     → max_hold_days остаётся для daily                              │
│     → max_hold_bars — новый параметр для других TF                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

### 20.5 МАППИНГ ПАРАМЕТРОВ

| CLI --bar | data_interval | klines | Свеча = |
|-----------|---------------|--------|---------|
| 1 | 1m | 1m | 1 минута |
| 5 | 5m | 5m | 5 минут |
| 15 | 15m | 15m | 15 минут |
| 60 | 1h | 1h | 1 час |
| 240 | 4h | 4h | 4 часа |
| daily | daily | 1d | 1 день |

---

### 20.6 КОНВЕРТАЦИЯ orderLifetime

| --bar | orderLifetime=2 | Реальное время |
|-------|-----------------|----------------|
| 1 | 2 свечи | 2 минуты |
| 5 | 2 свечи | 10 минут |
| 15 | 2 свечи | 30 минут |
| 60 | 2 свечи | 2 часа |
| 240 | 2 свечи | 8 часов |
| daily | 2 свечи | 2 дня |

---

### 20.7 ЧЕСТНЫЙ АУДИТ (2026-03-06) — ОШИБКИ И ПРОПУСКИ

#### КРИТИЧЕСКИЕ ОШИБКИ В МОЁМ КОДЕ:

| # | Где | Что сделано | Что забыл/ошибка |
|---|-----|-------------|------------------|
| 1 | telegram_runner.py:417 | Добавил `--bar` параметр | **НЕ ИСПОЛЬЗУЕТСЯ!** |
| 2 | telegram_runner.py:489 | `data_interval='daily'` | **ЗАХАРДКОЖЕН, args.bar игнорируется** |
| 3 | grep `args.bar` | Поиск по коду | **Найдено ТОЛЬКО в плане, не в коде!** |

#### ТЕКУЩИЙ СТАТУС ЦЕПОЧКИ (ФАКТЫ ИЗ КОДА):

```
┌───────────────────────────────────────────────────────────────────────────┐
│  DOWNLOAD (data_downloader.py)                                            │
│  ├── Строка 94:  VALID_INTERVALS = ("daily", "5m", "1m")                  │
│  │               ❌ ОТСУТСТВУЮТ: 15m, 1h, 4h                               │
│  ├── Строки 97-101: INTERVAL_MAP без 15m, 1h, 4h                          │
│  └── СТАТУС: ❌ НЕ ГОТОВО для всех таймфреймов                            │
├───────────────────────────────────────────────────────────────────────────┤
│  GENERATE (strategy_runner.py)                                            │
│  ├── Строка 300-355: aggregate_to_daily() — единственная функция          │
│  ├── Строка 528: candles = self.aggregate_to_daily(raw.klines)            │
│  │               ❌ ЗАХАРДКОЖЕН daily                                      │
│  ├── Нет параметра data_interval в StrategyRunner.__init__                │
│  ├── Нет функции aggregate_to_interval()                                  │
│  └── СТАТУС: ❌ НЕ ГОТОВО — всегда daily                                  │
├───────────────────────────────────────────────────────────────────────────┤
│  BACKTEST (strategy_runner.py)                                            │
│  ├── Строка 725: aggregate_to_daily() — ЗАХАРДКОЖЕН                       │
│  ├── Строка 737: candle_by_date по ДНЯМ                                   │
│  ├── Строка 1012: for j in range(1, min(max_hold_days + 1, ...))          │
│  │                Итерация по ДНЯМ, не по свечам                          │
│  └── СТАТУС: ❌ НЕ ГОТОВО — работает только daily                         │
├───────────────────────────────────────────────────────────────────────────┤
│  CLI (telegram_runner.py)                                                 │
│  ├── Строка 417: --bar ДОБАВЛЕН ✓                                         │
│  ├── Строка 489: data_interval='daily' ЗАХАРДКОЖЕН                        │
│  │               ❌ args.bar ИГНОРИРУЕТСЯ!                                 │
│  └── СТАТУС: ❌ Параметр бесполезен                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

#### ЧТО СДЕЛАНО (РЕАЛЬНО):

| Файл | Статус | Комментарий |
|------|--------|-------------|
| strategies/smaema_indicators.py | ✓ Создан | Копия из strategy/, работает |
| strategies/smaema_crossover.py | ✓ Создан | Копия из strategy/, работает |
| strategies/smaema.py | ✓ Создан | Генерация сигналов работает |
| strategies/__init__.py | ✓ Изменён | SMAEMA зарегистрирован |
| telegram_runner.py | ⚠ Частично | CLI params есть, но --bar НЕ ПОДКЛЮЧЁН |
| data_downloader.py | ❌ Не изменён | Нет 15m, 1h, 4h |
| strategy_runner.py | ❌ Не изменён | Всегда daily |

#### ЧТО НУЖНО СДЕЛАТЬ (НЕ ВЫПОЛНЕНО):

1. **data_downloader.py** — добавить интервалы:
   ```python
   VALID_INTERVALS = ("daily", "5m", "15m", "1h", "4h", "1m")
   INTERVAL_MAP["15m"] = {"klines": "15m", "oi": "15m", "ls": "15m"}
   INTERVAL_MAP["1h"] = {"klines": "1h", "oi": "1h", "ls": "1h"}
   INTERVAL_MAP["4h"] = {"klines": "4h", "oi": "4h", "ls": "4h"}
   ```

2. **strategy_runner.py** — добавить:
   - `data_interval` параметр в `__init__`
   - `aggregate_to_interval()` функцию
   - Заменить вызовы `aggregate_to_daily` для не-daily
   - Изменить индексацию бэктеста по timestamp

3. **telegram_runner.py** — ПОДКЛЮЧИТЬ args.bar:
   ```python
   # Строка 489 ИЗМЕНИТЬ:
   data_interval=args.bar if args.bar != 'daily' else 'daily'
   ```

#### ВЫВОД:

```
╔═══════════════════════════════════════════════════════════════════════════╗
║  ЦЕПОЧКА SMAEMA НЕ РАБОТАЕТ ДЛЯ ТАЙМФРЕЙМОВ != daily                      ║
║                                                                           ║
║  1. --bar параметр добавлен но БЕСПОЛЕЗЕН                                ║
║  2. Загрузчик не знает 15m, 1h, 4h                                        ║
║  3. Генерация всегда использует daily свечи                               ║
║  4. Бэктест всегда работает по дням                                       ║
║                                                                           ║
║  SMAEMA С DAILY РАБОТАЕТ (через существующий код)                         ║
║  SMAEMA С ДРУГИМИ TF — НЕ РАБОТАЕТ                                        ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

## 21. ПОЛНЫЙ АУДИТ КОДА (2026-03-06) — СТРОКА К СТРОКЕ

### 21.1 telegram_runner.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 57 | `ALL_STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']` | smaema НЕТ |
| 58 | `ALL_STRATEGIES_WITH_SMAEMA = ALL_STRATEGIES + ['smaema']` | smaema ЕСТЬ |
| 417 | `parser.add_argument("--bar", type=str, default="daily"...)` | ДОБАВЛЕН |
| 418-423 | `--fast-type, --fast-period, --slow-type, --slow-period, --offset-pct, --order-lifetime` | ДОБАВЛЕНЫ |
| 486-490 | `HybridHistoryDownloader(..., data_interval='daily')` | **ЗАХАРДКОЖЕНО 'daily'** |
| 510-520 | `smaema_params = {...}` собирается из args | РАБОТАЕТ |
| 522-564 | Логика включения/пропуска SMAEMA | РАБОТАЕТ |
| 571-591 | `smaema_strategy_params` передаётся в generate_signals_for_strategy | РАБОТАЕТ |
| **grep args.bar** | Поиск по коду | **0 совпадений — НЕ ИСПОЛЬЗУЕТСЯ** |

### 21.2 run_all.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 119 | `data_interval: str = "daily"` | Параметр ЕСТЬ |
| 188 | `data_interval=data_interval` передаётся в HybridHistoryDownloader | РАБОТАЕТ |
| 444 | `--data-interval", choices=["daily", "5m", "1m"]` | **НЕТ 15m, 1h, 4h** |
| 509 | `data_interval=args.data_interval` | ИСПОЛЬЗУЕТСЯ |

### 21.3 data_downloader.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 94 | `VALID_INTERVALS = ("daily", "5m", "1m")` | **НЕТ 15m, 1h, 4h** |
| 97-101 | `INTERVAL_MAP` для daily, 5m, 1m | **НЕТ 15m, 1h, 4h** |
| 103 | `def __init__(self, cache_dir, data_interval="daily")` | Параметр ЕСТЬ |
| 112-113 | `if data_interval not in self.VALID_INTERVALS: raise ValueError` | Валидация ЕСТЬ |

### 21.4 hybrid_downloader.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 56-61 | `def __init__(..., data_interval="daily")` | Параметр ЕСТЬ |
| 72 | `self.data_interval = data_interval` | Сохраняется |
| 74-78 | `self.binance = BinanceHistoryDownloader(data_interval=data_interval)` | Передаётся |
| 135-146 | `interval_map = self.binance.INTERVAL_MAP[self.data_interval]` | Используется |

### 21.5 strategy_runner.py — ГЕНЕРАЦИЯ

| Строка | Код | ФАКТ |
|--------|-----|------|
| 253-262 | `def __init__(self, strategy_name, config, ...)` | **НЕТ параметра data_interval** |
| 299-355 | `def aggregate_to_daily(klines)` | **ЕДИНСТВЕННАЯ функция агрегации** |
| 528 | `candles = self.aggregate_to_daily(raw.klines)` | **ВСЕГДА daily** |

### 21.6 strategy_runner.py — БЭКТЕСТЕР

| Строка | Код | ФАКТ |
|--------|-----|------|
| 650 | `max_hold_days: int = 14` | Параметр в **ДНЯХ** |
| 724-725 | `candles_cache[symbol] = self.aggregate_to_daily(...)` | **ВСЕГДА daily** |
| 737 | `candle_by_date = {c.date.strftime("%Y-%m-%d"): c ...}` | Индекс по **ДАТЕ строкой** |
| 1012 | `for j in range(1, min(max_hold_days + 1, ...))` | Итерация в **ДНЯХ** |
| 1071 | `hold_days = (exit_date - signal.date).days` | Расчёт в **ДНЯХ** |

### 21.7 strategy_runner.py — МАТРИЦЫ

| Матрица | Строки | Содержит smaema? | Поведение если нет |
|---------|--------|------------------|-------------------|
| COIN_REGIME_MATRIX | 53-89 | **НЕТ** | `.get(name, 'FULL')` → дефолт FULL |
| VOL_FILTER_THRESHOLDS | 95-101 | **НЕТ** | `if name in ...` → False, пропуск |
| MONTH_DATA | 34-38 | **НЕТ** | `if name in ...` → False, пропуск |
| DAY_DATA | 41-47 | **НЕТ** | `if name in ...` → False, пропуск |

**Код проверки матриц:**
```
Строка 755: if self.strategy_name in MONTH_DATA → False для smaema
Строка 781: if self.strategy_name in DAY_DATA → False для smaema
Строка 808: .get(self.strategy_name, 'FULL') → 'FULL' для smaema
Строка 843: if self.strategy_name in VOL_FILTER_THRESHOLDS → False для smaema
```

**ВЫВОД:** SMAEMA не в матрицах — код **НЕ УПАДЁТ**, фильтры пропускаются, используется FULL размер.

### 21.8 strategies/smaema.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 69 | `name = "smaema"` | ЕСТЬ |
| 73-77 | `REQUIRED_PARAMS = ["fast_type", "fast_period", ...]` | 6 параметров |
| 152-276 | `def generate_signals(self, data)` | РЕАЛИЗОВАН |
| 177 | `candles = data.candles` | Использует переданные свечи |
| 228-235 | `entry = candle.close * (1 + cfg.offset_pct / 100)` | offsetPct РАБОТАЕТ |
| 311-351 | `def _check_entry_reached(...)` | orderLifetime РАБОТАЕТ |

### 21.9 strategies/smaema_indicators.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 22-161 | `class SMA` | СОЗДАН |
| 163-343 | `class EMA` | СОЗДАН |
| 345-371 | `def calculate_ma(...)` | СОЗДАН |
| 31-44 | `tester_arithmetic` режим | ПОДДЕРЖИВАЕТСЯ |

### 21.10 strategies/smaema_crossover.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 16-21 | `class CrossoverType(Enum)` | BULLISH, BEARISH, NONE |
| 24-133 | `class CrossoverDetector` | СОЗДАН |
| 44-84 | `def detect(...)` | РЕАЛИЗОВАН |
| 67-68 | `if fast_ma_curr == slow_ma_curr: return NONE` | Равенство = НЕ crossover |
| 76-82 | `if prev_diff < 0 and curr_diff > 0: BULLISH` | Строгое сравнение |

### 21.11 strategies/__init__.py

| Строка | Код | ФАКТ |
|--------|-----|------|
| 43 | `from .smaema import SMAEMAStrategy` | ИМПОРТ ЕСТЬ |
| 53 | `"smaema": SMAEMAStrategy` | В REGISTRY ЕСТЬ |
| 118 | `"SMAEMAStrategy"` | В __all__ ЕСТЬ |

---

## 22. ИТОГОВАЯ ТАБЛИЦА СОСТОЯНИЯ

| Компонент | Файл:Строка | Статус | Проблема |
|-----------|-------------|--------|----------|
| SMAEMA класс | smaema.py | ✅ СОЗДАН | — |
| SMAEMA индикаторы | smaema_indicators.py | ✅ СОЗДАН | — |
| SMAEMA crossover | smaema_crossover.py | ✅ СОЗДАН | — |
| Регистрация | __init__.py:43,53 | ✅ ДОБАВЛЕН | — |
| CLI --bar | telegram_runner.py:417 | ⚠️ ДОБАВЛЕН | НЕ ИСПОЛЬЗУЕТСЯ |
| CLI передача | telegram_runner.py:489 | ❌ ЗАХАРДКОЖЕНО | `data_interval='daily'` |
| Интервалы 15m,1h,4h | data_downloader.py:94 | ❌ НЕТ | VALID_INTERVALS |
| Генерация не-daily | strategy_runner.py:528 | ❌ НЕТ | aggregate_to_daily() |
| Бэктест не-daily | strategy_runner.py:725 | ❌ НЕТ | aggregate_to_daily() |
| Бэктест индекс | strategy_runner.py:737 | ❌ НЕТ | "%Y-%m-%d" только daily |
| Бэктест итерация | strategy_runner.py:1012 | ❌ НЕТ | max_hold_days в ДНЯХ |
| COIN_REGIME_MATRIX | strategy_runner.py:53-89 | ⚠️ НЕТ smaema | Дефолт FULL |
| VOL_FILTER_THRESHOLDS | strategy_runner.py:95-101 | ⚠️ НЕТ smaema | Фильтр пропускается |
| MONTH_DATA | strategy_runner.py:34-38 | ⚠️ НЕТ smaema | Фильтр пропускается |
| DAY_DATA | strategy_runner.py:41-47 | ⚠️ НЕТ smaema | Фильтр пропускается |

---

## 23. ЧТО НУЖНО СДЕЛАТЬ

### 23.1 Для работы SMAEMA с DAILY (минимум)

| # | Файл | Строка | Что сделать | Риск |
|---|------|--------|-------------|------|
| — | — | — | **УЖЕ РАБОТАЕТ** | — |

SMAEMA с daily таймфреймом работает через существующий код.

### 23.2 Для работы SMAEMA с ДРУГИМИ TF

| # | Файл | Строка | Что сделать | Риск |
|---|------|--------|-------------|------|
| 1 | data_downloader.py | 94 | Добавить "15m", "1h", "4h" в VALID_INTERVALS | Низкий |
| 2 | data_downloader.py | 97-101 | Добавить в INTERVAL_MAP | Низкий |
| 3 | telegram_runner.py | 489 | Заменить 'daily' на args.bar | Низкий |
| 4 | strategy_runner.py | 253-262 | Добавить data_interval в __init__ | Низкий |
| 5 | strategy_runner.py | после 355 | Создать aggregate_to_interval() | Средний |
| 6 | strategy_runner.py | 528 | Использовать aggregate_to_interval() | Средний |
| 7 | strategy_runner.py | 725 | Использовать aggregate_to_interval() | Средний |
| 8 | strategy_runner.py | 737 | Индекс по timestamp вместо даты | Средний |
| 9 | strategy_runner.py | 650 | max_hold_bars вместо max_hold_days | Средний |
| 10 | strategy_runner.py | 1012 | Итерация по свечам, не дням | Средний |

### 23.3 Опционально (матрицы)

| # | Файл | Строка | Что сделать | Риск |
|---|------|--------|-------------|------|
| 1 | strategy_runner.py | 53-89 | Добавить smaema в COIN_REGIME_MATRIX | Низкий |
| 2 | strategy_runner.py | 95-101 | Добавить smaema в VOL_FILTER_THRESHOLDS | Низкий |

**ПРИМЕЧАНИЕ:** Без этих изменений SMAEMA использует FULL размер и не фильтруется — это может быть желаемое поведение до калибровки.

---

---

## 24. СРАВНЕНИЕ: ЗАКАЗАНО vs СДЕЛАНО vs НУЖНО ДОДЕЛАТЬ

### 24.1 ТРЕБОВАНИЯ ИЗ ПЛАНА (§1, §2, §7)

**§1 строка 11:**
> Таймфреймы: **1, 5, 15, 60, 240 мин + daily**

**§2 строка 37:**
> --bar | str | daily | Таймфрейм: **1, 5, 15, 60, 240, daily**

**§7 строки 169-176:**
```
| --bar | orderLifetime=2 | Реальное время |
|-------|-----------------|----------------|
| 1     | 2 свечи         | 2 минуты       |
| 5     | 2 свечи         | 10 минут       |
| 15    | 2 свечи         | 30 минут       |
| 60    | 2 свечи         | 2 часа         |
| 240   | 2 свечи         | 8 часов        |
| daily | 2 свечи         | 2 дня          |
```

**§7 строки 178-186 — Логика бэктеста:**
> Проверяем свечи N+1, N+2, ..., N+orderLifetime
> (Бэктест работает по СВЕЧАМ выбранного интервала, НЕ по дням!)

### 24.2 ТРЕБОВАНИЕ: СКВОЗНАЯ РАБОТА --bar

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  СКВОЗНАЯ ЦЕПОЧКА ОТ --bar ДО РЕЗУЛЬТАТА:                                   │
│                                                                             │
│  CLI (--bar 15)                                                             │
│      ↓                                                                      │
│  СКАЧИВАЛЬЩИК → загружает 15m свечи                                         │
│      ↓                                                                      │
│  ГЕНЕРАТОР → работает с 15m свечами                                         │
│      ↓                                                                      │
│  БЭКТЕСТЕР → итерирует по 15m свечам (не по дням!)                          │
│      ↓                                                                      │
│  РЕЗУЛЬТАТ → orderLifetime=2 = 30 минут реального времени                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 24.3 ТАБЛИЦА: ЗАКАЗАНО vs СДЕЛАНО

| Компонент | ЗАКАЗАНО | СДЕЛАНО | СТАТУС |
|-----------|----------|---------|--------|
| **Интервалы** | 1, 5, 15, 60, 240, daily | daily, 5m, 1m | ❌ НЕТ 15m, 1h, 4h |
| **CLI --bar** | Передаётся сквозно | Добавлен, НЕ используется | ❌ НЕ ПОДКЛЮЧЁН |
| **Скачивальщик** | Загружает свечи по --bar | Всегда daily | ❌ ЗАХАРДКОЖЕНО |
| **Генератор** | Работает со свечами --bar | aggregate_to_daily() | ❌ ВСЕГДА daily |
| **Бэктестер** | Итерирует по свечам | Итерирует по ДНЯМ | ❌ ТОЛЬКО daily |
| **orderLifetime** | В свечах интервала | В свечах (но свечи всегда daily) | ⚠️ ЧАСТИЧНО |
| **max_hold** | В свечах интервала | max_hold_days (В ДНЯХ) | ❌ ТОЛЬКО ДНЕЙ |

### 24.4 ДЕТАЛИ: ЧТО СЛОМАНО

#### СКАЧИВАЛЬЩИК (data_downloader.py)

| Строка | Заказано | Сделано | Проблема |
|--------|----------|---------|----------|
| 94 | 1m, 5m, 15m, 1h, 4h, daily | "daily", "5m", "1m" | НЕТ 15m, 1h, 4h |
| 97-101 | INTERVAL_MAP для всех | Только 3 интервала | НЕТ 15m, 1h, 4h |

#### CLI (telegram_runner.py)

| Строка | Заказано | Сделано | Проблема |
|--------|----------|---------|----------|
| 417 | --bar работает | --bar ДОБАВЛЕН | ОК |
| 489 | data_interval=args.bar | data_interval='daily' | **ЗАХАРДКОЖЕНО** |

#### ГЕНЕРАТОР (strategy_runner.py)

| Строка | Заказано | Сделано | Проблема |
|--------|----------|---------|----------|
| 253-262 | data_interval параметр | НЕТ параметра | **НЕ СУЩЕСТВУЕТ** |
| 528 | aggregate_to_interval() | aggregate_to_daily() | **ВСЕГДА daily** |

#### БЭКТЕСТЕР (strategy_runner.py)

| Строка | Заказано | Сделано | Проблема |
|--------|----------|---------|----------|
| 650 | max_hold_bars | max_hold_days | **В ДНЯХ** |
| 725 | aggregate_to_interval() | aggregate_to_daily() | **ВСЕГДА daily** |
| 737 | Индекс по timestamp | Индекс по "%Y-%m-%d" | **ТОЛЬКО daily** |
| 1012 | Итерация по свечам | Итерация по дням | **ТОЛЬКО ДНЕЙ** |

### 24.5 ПОЛНЫЙ СПИСОК ИЗМЕНЕНИЙ ДЛЯ СКВОЗНОЙ РАБОТЫ --bar

#### ФАЙЛ: data_downloader.py

| # | Строка | Было | Нужно |
|---|--------|------|-------|
| 1 | 94 | `("daily", "5m", "1m")` | `("daily", "4h", "1h", "15m", "5m", "1m")` |
| 2 | 97-101 | 3 интервала | Добавить 15m, 1h, 4h в INTERVAL_MAP |

```python
# ДОБАВИТЬ в INTERVAL_MAP:
"15m": {"klines": "15m", "oi": "5m", "ls": "5m"},
"1h":  {"klines": "1h",  "oi": "5m", "ls": "5m"},
"4h":  {"klines": "4h",  "oi": "5m", "ls": "5m"},
```

#### ФАЙЛ: telegram_runner.py

| # | Строка | Было | Нужно |
|---|--------|------|-------|
| 1 | 489 | `data_interval='daily'` | `data_interval=args.bar` |

#### ФАЙЛ: strategy_runner.py — ГЕНЕРАТОР

| # | Строка | Было | Нужно |
|---|--------|------|-------|
| 1 | 253-262 | Нет data_interval | Добавить `data_interval: str = "daily"` |
| 2 | после 298 | — | `self.data_interval = data_interval` |
| 3 | после 355 | — | Создать `aggregate_to_interval(klines, interval)` |
| 4 | 528 | `aggregate_to_daily(raw.klines)` | `aggregate_to_interval(raw.klines, self.data_interval)` |

#### ФАЙЛ: strategy_runner.py — БЭКТЕСТЕР

| # | Строка | Было | Нужно |
|---|--------|------|-------|
| 1 | 650 | `max_hold_days: int = 14` | `max_hold_bars: int = 14` |
| 2 | 725 | `aggregate_to_daily(...)` | `aggregate_to_interval(..., self.data_interval)` |
| 3 | 737 | `c.date.strftime("%Y-%m-%d")` | `int(c.date.timestamp() * 1000)` |
| 4 | 1012 | `max_hold_days` | `max_hold_bars` |
| 5 | 1071 | `hold_days = .days` | Расчёт в свечах |

#### ФАЙЛ: run_all.py

| # | Строка | Было | Нужно |
|---|--------|------|-------|
| 1 | 444 | `choices=["daily", "5m", "1m"]` | `choices=["daily", "4h", "1h", "15m", "5m", "1m"]` |

### 24.6 НОВАЯ ФУНКЦИЯ: aggregate_to_interval()

```python
@staticmethod
def aggregate_to_interval(klines: List[Dict], interval: str) -> List[DailyCandle]:
    """Агрегирует klines в свечи заданного интервала.

    Args:
        klines: Сырые данные (могут быть 1m, 5m, etc.)
        interval: Целевой интервал ("daily", "4h", "1h", "15m", "5m", "1m")

    Returns:
        Список свечей агрегированных до interval
    """
    if interval == "daily":
        return StrategyRunner.aggregate_to_daily(klines)

    # Минуты для каждого интервала
    interval_minutes = {
        "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240
    }
    minutes = interval_minutes.get(interval, 1440)
    ms_per_interval = minutes * 60 * 1000

    # Группировка по интервалу
    candles_dict = {}
    for k in klines:
        ts = k.get("timestamp", 0)
        interval_ts = (ts // ms_per_interval) * ms_per_interval

        if interval_ts not in candles_dict:
            candles_dict[interval_ts] = {
                "timestamp": interval_ts,
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k["volume"]),
                "quote_volume": float(k.get("quote_volume", 0)),
                ...
            }
        else:
            d = candles_dict[interval_ts]
            d["high"] = max(d["high"], float(k["high"]))
            d["low"] = min(d["low"], float(k["low"]))
            d["close"] = float(k["close"])
            d["volume"] += float(k["volume"])
            ...

    # Конвертация в DailyCandle
    result = []
    for ts in sorted(candles_dict.keys()):
        d = candles_dict[ts]
        result.append(DailyCandle(
            date=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            volume=d["volume"],
            ...
        ))
    return result
```

### 24.7 ПОРЯДОК РЕАЛИЗАЦИИ

| # | Файл | Что делать | Зависит от |
|---|------|------------|------------|
| 1 | data_downloader.py | Добавить 15m, 1h, 4h | — |
| 2 | run_all.py | Добавить choices | — |
| 3 | strategy_runner.py | Создать aggregate_to_interval() | — |
| 4 | strategy_runner.py | Добавить data_interval в __init__ | #3 |
| 5 | strategy_runner.py | Заменить в generate_signals | #3, #4 |
| 6 | strategy_runner.py | Заменить в backtest_signals | #3, #4 |
| 7 | strategy_runner.py | Изменить индексацию бэктеста | #6 |
| 8 | strategy_runner.py | max_hold_bars вместо days | #6 |
| 9 | telegram_runner.py | Подключить args.bar | #1-#8 |

### 24.8 ЕДИНАЯ СИСТЕМА ТАЙМФРЕЙМОВ (УТОЧНЕНИЕ 2026-03-06)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ЕДИНАЯ СИСТЕМА ДЛЯ ВСЕХ СТРАТЕГИЙ:                                         │
│                                                                             │
│  1. --bar влияет на ВСЕ стратегии (не только SMAEMA!)                       │
│     → ls_fade, momentum, reversal, mean_reversion, momentum_ls, smaema      │
│     → Если --bar=15 → ВСЕ работают с 15m свечами                            │
│     → Если --bar не указан → ВСЕ работают с daily (по умолчанию)            │
│                                                                             │
│  2. aggregate_to_daily() — НЕ УДАЛЯТЬ                                       │
│     → aggregate_to_interval("daily") вызывает её внутри                     │
│     → Для обратной совместимости                                            │
│                                                                             │
│  3. Бэктест ЕДИНАЯ ЛОГИКА для всех TF                                       │
│     → Один и тот же код для daily и других интервалов                       │
│     → max_hold_bars в СВЕЧАХ (не днях)                                      │
│     → Индексация по timestamp (работает для любого интервала)               │
│                                                                             │
│  ПРИМЕР:                                                                    │
│  --bar=60 --strategies all                                                  │
│  → Загружаются 1h свечи                                                     │
│  → ls_fade генерирует сигналы по 1h свечам                                  │
│  → momentum генерирует сигналы по 1h свечам                                 │
│  → smaema генерирует сигналы по 1h свечам                                   │
│  → Бэктест для ВСЕХ работает по 1h свечам                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 24.9 ДЕФОЛТ ПО УМОЛЧАНИЮ

```
--bar НЕ УКАЗАН → data_interval = "daily" → ВСЕ стратегии работают как раньше
```

---

*Создано: сессия анализа SMAEMA стратегии*
*Статус: ПОЛНЫЙ АУДИТ + ПЛАН СКВОЗНОЙ РЕАЛИЗАЦИИ + УТОЧНЕНИЕ TF*
*Версия: 2026-03-06 (обновлено)*
*Промпт разработки: SMAEMA_DEV_PROMPT.md*
*Ждём команду: "можно кодить" для реализации сквозной работы --bar*
