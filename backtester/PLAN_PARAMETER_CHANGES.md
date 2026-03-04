# ПЛАН ИЗМЕНЕНИЙ ПАРАМЕТРОВ ГЕНЕРАТОРА СИГНАЛОВ

**Дата создания:** 2026-02-27
**Статус:** ГОТОВ К РЕАЛИЗАЦИИ
**База анализа:** 10 бэктест-файлов, ~134,000 сделок, 46 монет

---

## ИСТОЧНИКИ ДАННЫХ

| Файл | Описание |
|------|----------|
| `PATTERNS_TABLE.md` | Единая таблица 15 паттернов по 10 файлам |
| `AUDIT_SIGNAL_GENERATOR.md` | Полный аудит кода генератора |
| `RECOMMENDATIONS_NEW_PARAMS.md` | Рекомендации по параметрам |
| `АУДИТ_backtest_signals_part2_*.md` | Детальный аудит Part2 |
| `PLAN_OI_SPIKE_IMPLEMENTATION.md` | План OI_SPIKE (уже реализован) |

---

## ЭТАП 1: КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ (реализовать первыми)

### 1.1 OI_SPIKE BONUS [ВЫПОЛНЕНО]

| Параметр | Файл | Было | Стало |
|----------|------|------|-------|
| `oi_spike_bonus` поле | `models.py` | не было | `oi_spike_bonus: int = 0` |
| В `total` property | `models.py` | не было | добавлено в сумму |
| В `to_dict()` | `models.py` | не было | добавлено |
| `oi_spike_bonus_points` | `models.py` SignalConfig | не было | `= 10` |
| Проверка OI_SPIKE | `accumulation_detector.py` | не было | добавлена после VOLUME_SPIKE |

**Статус:** ВЫПОЛНЕНО в этой сессии

---

### 1.2 МИНИМАЛЬНЫЙ ACCUMULATION SCORE

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `min_accumulation_score` | `models.py` SignalConfig | 65 | **50** | Score 45-50 даёт 90% убытков. Score 50+ прибылен в 83% файлов |

**Файлы для изменения:**
- `GenerateHistorySignals/signals/models.py` строка ~219
- `src/signals/models.py` строка ~250

**Что менять:**
```
Найти:   min_accumulation_score: int = 65
Заменить: min_accumulation_score: int = 50
```

---

### 1.3 COORDINATED_BUYING БАЛЛЫ

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `coordinated_buying` баллы | `accumulation_detector.py` | +10 | **+3** | Убыточен в 100% файлов. 9938 сделок = -328 PnL |

**Файлы для изменения:**
- `GenerateHistorySignals/signals/accumulation_detector.py`
- `src/signals/accumulation_detector.py`

**Что менять:**
```
Найти:   score.coordinated_buying = 10
Заменить: score.coordinated_buying = 3
```

---

### 1.4 SYMBOL BLACKLIST (новый параметр)

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `symbol_blacklist` | SignalConfig | не было | `["COMPUSDT", "YFIUSDT", "KSMUSDT"]` | 1 монета = 80%+ убытков в 83% файлов |

**Файлы для изменения:**
- `GenerateHistorySignals/signals/models.py` - добавить в SignalConfig
- `src/signals/models.py` - добавить в SignalConfig
- `GenerateHistorySignals/signal_runner.py` - добавить проверку
- `src/signals/signal_generator.py` - добавить проверку

**Что добавить в SignalConfig:**
```
symbol_blacklist: list = field(default_factory=lambda: ["COMPUSDT", "YFIUSDT", "KSMUSDT"])
```

**Что добавить в signal_runner/signal_generator:**
```
if symbol in self.config.symbol_blacklist:
    return None  # Пропускаем токсичные монеты
```

---

## ЭТАП 2: ВАЖНЫЕ ИЗМЕНЕНИЯ

### 2.1 BLOCKED HOURS (новый параметр)

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `blocked_hours_utc` | SignalConfig | не было | `[10, 11, 12]` | Часы 10-12 UTC убыточны в 67% файлов |

**Что добавить в SignalConfig:**
```
blocked_hours_utc: list = field(default_factory=lambda: [10, 11, 12])
```

**Что добавить в signal_runner:**
```
signal_hour = timestamp.hour
if signal_hour in self.config.blocked_hours_utc:
    return None  # Не торгуем в убыточные часы
```

---

### 2.2 VOLUME_ACCUMULATION БАЛЛЫ

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `volume_accumulation` при spike+stable | `accumulation_detector.py` | +10 | **+5** | VOLUME_SPIKE_HIGH убыточен |
| `volume_accumulation` при spike only | `accumulation_detector.py` | +5 | **+3** | Снижаем вес |

**Что менять:**
```
Найти:   score.volume_accumulation = 10
Заменить: score.volume_accumulation = 5

Найти:   score.volume_accumulation = 5
Заменить: score.volume_accumulation = 3
```

---

### 2.3 TAKE PROFIT МНОЖИТЕЛИ

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| TP1 множитель | `risk_calculator.py` | 1.2 | **1.0** | TP3 rate < 8%, TIMEOUT прибыльнее TP3 |
| TP2 множитель | `risk_calculator.py` | 2.5 | **2.0** | Быстрее фиксируем |
| TP3 множитель | `risk_calculator.py` | 4.0 | **3.0** | Только 7.3% достигают TP3 |

**Файлы для изменения:**
- `GenerateHistorySignals/signals/risk_calculator.py`
- `src/signals/risk_calculator.py`

**Что менять (hardcoded значения):**
```
Найти:   tp1_mult = 1.2
Заменить: tp1_mult = 1.0

Найти:   tp2_mult = 2.5
Заменить: tp2_mult = 2.0

Найти:   tp3_mult = 4.0
Заменить: tp3_mult = 3.0
```

---

### 2.4 MIN PROBABILITY

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `min_probability` | SignalConfig | 60 | **50** | Prob 55 лучше Prob 50 в 80% файлов |

**Что менять:**
```
Найти:   min_probability: int = 60
Заменить: min_probability: int = 50
```

---

## ЭТАП 3: ЖЕЛАТЕЛЬНЫЕ ИЗМЕНЕНИЯ

### 3.1 STOP LOSS ПАРАМЕТРЫ

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| ATR множитель | `risk_calculator.py` | 1.5 | **1.8** | SL rate 61% — слишком тесно |
| SL min | `risk_calculator.py` | 2% | **2.5%** | Волатильные монеты |
| SL max | `risk_calculator.py` | 8% | **6%** | Слишком много риска |

**Что менять:**
```
Найти:   sl_pct = atr_pct * 1.5
Заменить: sl_pct = atr_pct * 1.8

Найти:   sl_pct = max(2.0, min(8.0, sl_pct))
Заменить: sl_pct = max(2.5, min(6.0, sl_pct))
```

---

### 3.2 MAX VOLUME SPIKE (новый параметр)

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `max_volume_spike` | SignalConfig | не было | `2.0` | Spike > 2.0 убыточен в 67% файлов |

**Что добавить в SignalConfig:**
```
max_volume_spike: float = 2.0
```

**Что добавить в accumulation_detector (или signal_runner):**
```
if spot_state and spot_state.volume_spike_ratio > self.config.max_volume_spike:
    # Слишком высокий spike = FOMO, пропускаем
    return None
```

---

### 3.3 OI_SPIKE THRESHOLD

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `oi_spike_threshold` | `signal_runner.py` | 3.0% | **2.5%** | OI_SPIKE прибылен, но редкий. Снижаем порог |

**Что менять:**
```
Найти:   self._oi_threshold = 3.0
Заменить: self._oi_threshold = 2.5
```

---

### 3.4 BUY_RATIO THRESHOLD

| Параметр | Файл | Было | Стало | Обоснование |
|----------|------|------|-------|-------------|
| `buy_ratio_threshold` | `signal_runner.py` | 0.60 | **0.70** | COORDINATED_BUYING убыточен — ужесточаем |

**Что менять:**
```
Найти:   self._buy_threshold = 0.60
Заменить: self._buy_threshold = 0.70
```

---

## ЭТАП 4: ИСПРАВЛЕНИЯ БАГОВ [ПРИЧИНА НАЙДЕНА]

### 4.1 CROWD_BEARISH = 20 ВЕЗДЕ

| Проблема | Описание |
|----------|----------|
| Симптом | 100% сигналов имеют `acc_crowd_bearish = 20`, `long_account_pct = 5000` |
| **ПРИЧИНА НАЙДЕНА** | coinalyze_client.py использует `h.get("c", 1.0)` — поля "c" НЕТ в L/S API! |
| Действие | См. ЭТАП 4 в конце плана — три изменения в coinalyze_client.py |

---

## СВОДНАЯ ТАБЛИЦА ВСЕХ ИЗМЕНЕНИЙ

| # | Параметр | Файл | Было | Стало | Этап | Статус |
|---|----------|------|------|-------|------|--------|
| 1 | `oi_spike_bonus` | models.py | 0 | +10 | 1 | ВЫПОЛНЕНО |
| 2 | `min_accumulation_score` | models.py | 65 | 50 | 1 | ВЫПОЛНЕНО |
| 3 | `coordinated_buying` баллы | accumulation_detector.py | +10 | +3 | 1 | ВЫПОЛНЕНО |
| 4 | `symbol_blacklist` | models.py + signal_runner.py | - | список | 1 | ВЫПОЛНЕНО |
| 5 | `blocked_hours_utc` | models.py + signal_runner.py | - | [10,11,12] | 2 | ВЫПОЛНЕНО |
| 6 | `volume_accumulation` (stable) | accumulation_detector.py | +10 | +5 | 2 | ВЫПОЛНЕНО |
| 7 | `volume_accumulation` (only) | accumulation_detector.py | +5 | +3 | 2 | ВЫПОЛНЕНО |
| 8 | TP1 множитель | risk_calculator.py | 1.2 | 1.0 | 2 | ВЫПОЛНЕНО |
| 9 | TP2 множитель | risk_calculator.py | 2.5 | 1.75 | 2 | ВЫПОЛНЕНО |
| 10 | TP3 множитель | risk_calculator.py | 4.0 | 2.5 | 2 | ВЫПОЛНЕНО |
| 11 | `min_probability` | models.py | 60 | 50 | 2 | ВЫПОЛНЕНО |
| 12 | ATR множитель SL | risk_calculator.py | 1.5 | 1.8 | 3 | ВЫПОЛНЕНО |
| 13 | SL min | risk_calculator.py | 2% | 3% | 3 | ВЫПОЛНЕНО |
| 14 | SL max | risk_calculator.py | 8% | 6% | 3 | ВЫПОЛНЕНО |
| 15 | `max_volume_spike` | models.py + signal_runner/generator | - | 2.0 | 3 | ВЫПОЛНЕНО |
| 16 | `oi_spike_threshold` | config.py | 3.0 | 2.5 | 3 | ВЫПОЛНЕНО |
| 17 | `buy_ratio_threshold` | config.py | 0.60 | 0.70 | 3 | ВЫПОЛНЕНО |
| 18 | **L/S Ratio парсинг** | coinalyze_client.py | `h.get("c")` | `h.get("r/l/s")` | 4 | ОЖИДАЕТ |

---

## ОЖИДАЕМЫЙ РЕЗУЛЬТАТ

| Метрика | Было | После Этапа 1 | После Этапа 2 | После Этапа 3 |
|---------|------|---------------|---------------|---------------|
| Сигналов | 100% | -20% | -35% | -40% |
| Win Rate | 36% | 38% | 40% | 41% |
| SL Rate | 61% | 58% | 54% | 50% |
| TP3 Rate | 7% | 8% | 10% | 12% |
| Avg PnL | -0.034 | -0.01 | +0.005 | +0.015 |

---

## ПОРЯДОК РЕАЛИЗАЦИИ

### Сессия 1: ВЫПОЛНЕНО
- [x] OI_SPIKE BONUS добавлен

### Сессия 2 (текущая): ЭТАП 1 ВЫПОЛНЕН
- [x] `min_accumulation_score` = 50
- [x] `coordinated_buying` = +3
- [x] `symbol_blacklist` добавлен

### Сессия 3: ЭТАП 2 — ВЫПОЛНЕНО
- [x] `blocked_hours_utc` добавить [10, 11, 12]
- [x] `volume_accumulation` уменьшить (10→5, 5→3)
- [x] TP множители изменить (1.0 / 1.75 / 2.5)
- [x] `min_probability` = 50

### Сессия 4: ЭТАП 3 — ВЫПОЛНЕНО
- [x] SL параметры (ATR mult 1.8, min 3%, max 6%)
- [x] `max_volume_spike` = 2.0 добавлен
- [x] `oi_spike_threshold` = 2.5
- [x] `buy_ratio_threshold` = 0.70

### Сессия 5: ЭТАП 4 — КРИТИЧЕСКИЙ БАГ L/S RATIO
- [ ] Исправить парсинг Coinalyze L/S API (3 изменения в coinalyze_client.py)
- [ ] Пересоздать L/S кэш с правильным парсингом
- [ ] Верифицировать данные в кэше

---

## ЭТАП 4: КРИТИЧЕСКИЙ БАГ — COINALYZE L/S RATIO API

### ПРОБЛЕМА
100% сигналов имеют `acc_crowd_bearish = 20` и `long_account_pct = 5000`.
Причина: парсинг L/S Ratio API использует НЕПРАВИЛЬНЫЕ поля.

### АУДИТ ВЫПОЛНЕН (2026-02-28)

#### Реальный ответ Coinalyze API `/long-short-ratio-history`:
```json
[{"symbol":"BTCUSDT_PERP.A","history":[
  {"t":1709251200,"r":1.1529,"l":53.55,"s":46.45},
  {"t":1709337600,"r":1.0708,"l":51.71,"s":48.29}
]}]
```

#### Поля API:
| Поле | Тип | Описание |
|------|-----|----------|
| `t` | int | Unix timestamp |
| `r` | float | Long/Short ratio (например 1.1529) |
| `l` | float | Long % (например 53.55 = 53.55%) |
| `s` | float | Short % (например 46.45 = 46.45%) |

#### ОШИБКИ В КОДЕ (coinalyze_client.py строки 309-315):

| # | Текущий код | Проблема | Правильный код |
|---|-------------|----------|----------------|
| 1 | `ratio = h.get("c", 1.0)` | Поля "c" НЕТ в L/S API! Всегда возвращает 1.0 | `ratio = h.get("r", 1.0)` |
| 2 | `long_pct = ratio / (1 + ratio)` | Вычисляет из ratio, но API даёт готовый `l` | `long_pct = h.get("l", 50) / 100` |
| 3 | `short_pct = 1 - long_pct` | Вычисляет, но API даёт готовый `s` | `short_pct = h.get("s", 50) / 100` |

**Почему OI работает, а L/S нет:**
- OI API использует OHLC формат: `{t, o, h, l, c}` — поле "c" существует
- L/S API использует другой формат: `{t, r, l, s}` — поля "c" НЕТ

### ФАЙЛЫ ДЛЯ ИЗМЕНЕНИЯ

**Файл:** `GenerateHistorySignals/coinalyze_client.py`

**Строка 309 (примерно):**
```python
# БЫЛО:
ratio = h.get("c", 1.0)

# СТАЛО:
ratio = h.get("r", 1.0)
```

**Строки 314-315 (примерно):**
```python
# БЫЛО:
long_pct = ratio / (1 + ratio)
short_pct = 1 - long_pct

# СТАЛО:
long_pct = h.get("l", 50) / 100  # API возвращает проценты (53.55), делим на 100
short_pct = h.get("s", 50) / 100  # API возвращает проценты (46.45), делим на 100
```

### ПОСЛЕ ИСПРАВЛЕНИЯ

1. Удалить старый L/S кэш: `cache/coinalyze/*_ls_*.json`
2. Запустить генерацию заново для скачивания правильных данных
3. Верифицировать: `long_account_pct` должен быть ~50-60, не 5000

---

## КОМАНДА ДЛЯ СЛЕДУЮЩЕЙ СЕССИИ

```
КРИТИЧЕСКИ ВАЖНО: Ты профессиональный программист и трейдер.
ЗАПРЕЩЕНО: костылить, фантазировать, хардкодить, делать догадки.
РАЗРЕШЕНО: строго следовать плану, проверять каждое изменение.

Открой файл G:\BinanceFriend\backtester\PLAN_PARAMETER_CHANGES.md
Реализуй ЭТАП 4 — КРИТИЧЕСКИЙ БАГ COINALYZE L/S RATIO:

1. Открой файл GenerateHistorySignals/coinalyze_client.py
2. Найди метод парсинга L/S Ratio (около строки 309)
3. Внеси ТОЧНО ТРИ изменения из плана:
   - h.get("c", 1.0) → h.get("r", 1.0)
   - long_pct = ratio / (1 + ratio) → h.get("l", 50) / 100
   - short_pct = 1 - long_pct → h.get("s", 50) / 100
4. После изменений покажи diff для верификации
5. Удали старый L/S кэш
6. Запусти тест на одной монете для проверки

НЕ ДЕЛАЙ НИЧЕГО СВЕРХ ПЛАНА!
```

---

**Конец плана**
