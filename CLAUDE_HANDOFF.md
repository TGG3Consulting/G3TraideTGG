# CLAUDE SESSION HANDOFF - BinanceFriend

## ДАТА: 2026-02-20

## ТЕКУЩАЯ ЗАДАЧА: ATR-based Orderbook + FUTURES depth - ЗАВЕРШЕНО

### ЧТО БЫЛО СДЕЛАНО (СЕССИЯ 1):

#### 1. models.py (screener) - ГОТОВО
Добавлены новые поля в `SymbolState`:
- `bid_volume_atr`, `ask_volume_atr` - объёмы в пределах ±ATR%
- `raw_bids`, `raw_asks` - полный стакан для расчёта
- `klines_1h` - список (high, low, close) для ATR
- `atr_1h_pct` - ATR как процент от цены
- `book_imbalance_atr` - property для imbalance на ATR

#### 2. realtime_monitor.py - ГОТОВО
- `_process_kline()` - теперь сохраняет HLC и пересчитывает ATR
- `_process_depth()` - сохраняет raw orderbook и вызывает ATR расчёт
- `_calculate_atr_volumes()` - НОВЫЙ метод, фильтрует стакан по ±ATR%
- `_calculate_atr_pct()` - НОВЫЙ метод, считает ATR из klines

### ЧТО СДЕЛАНО (СЕССИЯ 2):

#### 3. futures_monitor.py - ГОТОВО ✅
Добавлено:
- WebSocket подключение к `wss://fstream.binance.com/stream`
- Подписка на `{symbol}@depth@100ms` для каждого символа
- Обработка depth данных (`_process_depth_message`)
- Расчёт ATR-based volumes (`_calculate_futures_atr_volumes`)
- Загрузка klines для ATR (`_klines_monitor_loop`, `_fetch_klines`)
- **ДЕТЕКЦИЯ `FUTURES_ORDERBOOK_IMBALANCE`** - новая детекция
- Новые поля в FuturesState:
  - `futures_best_bid`, `futures_best_ask`
  - `futures_bid_volume_atr`, `futures_ask_volume_atr`
  - `futures_raw_bids`, `futures_raw_asks`
  - `futures_klines_1h`, `futures_atr_1h_pct`
  - Properties: `futures_mid_price`, `futures_spread_pct`, `futures_book_imbalance_atr`

#### 4. detection_engine.py - ГОТОВО ✅
- `_detect_orderbook_manipulation` теперь использует `book_imbalance_atr`
- `_detect_pump_sequence` использует `book_imbalance_atr`
- `_enrich_detection` добавляет ATR-based данные в details

#### 5. signal_formatter.py - ГОТОВО ✅
- Добавлена секция "📊 СТАКАН:" в формат сигнала
- Показывает SPOT orderbook: bid/ask volume, imbalance, ATR%
- Показывает FUTURES orderbook: bid/ask volume, imbalance, ATR%

### ЧТО СДЕЛАНО (СЕССИЯ 3 - ПОЛНАЯ ИНТЕГРАЦИЯ ORDERBOOK):

#### 6. models.py (signals/AccumulationScore) - ГОТОВО ✅
Добавлены orderbook факторы:
- `spot_bid_pressure` (0-10) - сильный bid wall SPOT
- `spot_ask_weakness` (0-5) - слабые asks SPOT
- `spot_imbalance_score` (0-5) - imbalance SPOT
- `futures_bid_pressure` (0-10) - сильный bid wall FUTURES
- `futures_ask_weakness` (0-5) - слабые asks FUTURES
- `futures_imbalance_score` (0-5) - imbalance FUTURES
- `orderbook_divergence` (0-5) - согласованность SPOT/FUTURES
- `orderbook_against_penalty` (-10 to 0) - штраф если стакан против
- Property `orderbook_total` - сумма orderbook факторов

#### 7. accumulation_detector.py - ГОТОВО ✅
- Добавлен `realtime_monitor` в конструктор
- Новый метод `_calculate_orderbook_score()` - расчёт всех orderbook факторов
- Обновлён `_determine_direction()` - учитывает orderbook imbalance
- Обновлён `_calculate_probability()` - бонусы за orderbook
- Обновлён `_collect_evidence()` - evidence для orderbook

#### 8. signal_generator.py - ГОТОВО ✅
- Передаёт `realtime_monitor` в `AccumulationDetector`
- Собирает ATR данные для SPOT и FUTURES в `_collect_details()`
- Добавлен триггер `FUTURES_ORDERBOOK_IMBALANCE`
- `_direction_from_detection()` обрабатывает все ORDERBOOK_IMBALANCE

#### 9. risk_calculator.py - ГОТОВО ✅
- `_estimate_volatility()` - приоритетно использует ATR данные
- `_calculate_entry_zone()` - корректирует entry по orderbook
- `_calculate_stop_loss()` - адаптирует SL по orderbook:
  - Сильный wall в нашу сторону = SL ближе (wall защищает)
  - Wall против нас = SL шире
  - SL не меньше ATR (защита от выбивания)

#### 10. futures_monitor.py (детекция) - ГОТОВО ✅
- Новый метод `_check_futures_orderbook_detections()`
- Генерирует `FUTURES_ORDERBOOK_IMBALANCE` детекции
- Пороги: ALERT > 50%, WARNING > 30%

### АРХИТЕКТУРА ИНТЕГРАЦИИ ORDERBOOK:

```
realtime_monitor.py          futures_monitor.py
      │                             │
      ▼                             ▼
   SPOT                          FUTURES
   depth                          depth
      │                             │
      ▼                             ▼
 bid_volume_atr              futures_bid_volume_atr
 ask_volume_atr              futures_ask_volume_atr
 book_imbalance_atr          futures_book_imbalance_atr
      │                             │
      └─────────┬───────────────────┘
                ▼
    ┌───────────────────────┐
    │  AccumulationDetector │
    │ _calculate_orderbook_ │
    │        score()        │
    └───────────┬───────────┘
                ▼
    ┌───────────────────────┐
    │   AccumulationScore   │
    │ spot_bid_pressure     │
    │ futures_bid_pressure  │
    │ orderbook_divergence  │
    └───────────┬───────────┘
                ▼
    ┌───────────────────────┐
    │    RiskCalculator     │
    │ - Entry zone adjust   │
    │ - SL/TP optimization  │
    │ - ATR-based sizing    │
    └───────────────────────┘
```

### КАК ORDERBOOK ВЛИЯЕТ НА СИГНАЛЫ:

| Компонент | Влияние |
|-----------|---------|
| AccumulationScore | +25 баллов макс за orderbook |
| Probability | +5-13% бонус |
| Direction | Учитывает сильный imbalance |
| Entry | Агрессивнее при сильном wall |
| Stop Loss | Уже если wall защищает |

### ТЕСТИРОВАНИЕ:

```bash
python run.py
```

Проверить в логах:
- `futures_atr_calculated` - ATR рассчитывается
- `depth_ws_connected` - WebSocket подключен
- `accumulation_score_calculated` с `orderbook_total > 0`
- Сигналы в Telegram показывают секцию "СТАКАН"

### ВАЖНЫЕ ДЕТАЛИ:

#### Логика ATR-based depth:
```
mid_price = (best_bid + best_ask) / 2
atr_pct = рассчитан из 60 минутных свечей (default 5%)
lower_bound = mid_price * (1 - atr_pct)
upper_bound = mid_price * (1 + atr_pct)

bid_volume_atr = sum(p*q for bids where price >= lower_bound)
ask_volume_atr = sum(p*q for asks where price <= upper_bound)
```

#### Пороги orderbook scoring:
```python
IMBALANCE_STRONG = 0.4      # 40% перекос
IMBALANCE_MODERATE = 0.2    # 20% перекос
VOLUME_RATIO_STRONG = 2.0   # bid/ask > 2x
VOLUME_RATIO_MODERATE = 1.5 # bid/ask > 1.5x
```

### ИЗМЕНЁННЫЕ ФАЙЛЫ:

1. `src/screener/models.py` - ATR поля в SymbolState ✅
2. `src/screener/realtime_monitor.py` - ATR расчёт SPOT ✅
3. `src/screener/futures_monitor.py` - WebSocket + ATR + детекция ✅
4. `src/screener/detection_engine.py` - ATR-based imbalance ✅
5. `src/signals/models.py` - orderbook в AccumulationScore ✅
6. `src/signals/accumulation_detector.py` - orderbook scoring ✅
7. `src/signals/signal_generator.py` - передача realtime_monitor ✅
8. `src/signals/signal_formatter.py` - отображение стаканов ✅
9. `src/signals/risk_calculator.py` - ATR + orderbook для SL/TP ✅

### СЕССИЯ 4 - КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ (2026-02-20):

#### Исправленные баги:

1. **Depth Stream → Snapshot**
   - `realtime_monitor.py:313`: `@depth@100ms` → `@depth20@100ms`
   - `futures_monitor.py:449`: `@depth@100ms` → `@depth20@100ms`
   - Причина: diff stream отправлял только изменения, не полный стакан

2. **FUTURES перезапись пустыми данными**
   - `futures_monitor.py:540-550`: добавлены `if bids:` / `if asks:`
   - Причина: пустой asks перезаписывал стакан → Ask = $0

3. **ATR минимум 1% → 3%**
   - `realtime_monitor.py:641`, `futures_monitor.py:720`
   - Причина: 1% слишком узко для волатильных крипто

4. **Imbalance 100% при отсутствии данных**
   - `futures_monitor.py:192-210`, `models.py:212-230`
   - Добавлена проверка: минимум $100 на каждой стороне
   - Если меньше - возвращает 0 (нет данных)

5. **Direction из одной детекции**
   - `signal_generator.py:288-301`
   - Теперь direction из `accumulation.direction` (учитывает все факторы)

6. **Минимальные пороги объёма**
   - `accumulation_detector.py:63-70`
   - SPOT: $1000 мин, FUTURES: $5000 мин
   - Стаканы меньше - игнорируются

7. **Evidence противоречит direction**
   - `accumulation_detector.py:652-672`
   - Теперь показывает "покупатели/продавцы доминируют" в зависимости от знака

### ПРЕДУПРЕЖДЕНИЯ:
- НЕ включать ML пока не исправлен путь к моделям
- НЕ менять config/ml_config.yaml без явного запроса
- ML модели лежат в models/optimal/ но конфиг ищет в models/ml/
