# TRADE BOT - SESSION LOG
# ========================
# Этот файл содержит историю всех действий по разработке TradeBot.
# ТОЛЬКО ДОПИСЫВАТЬ, НЕ УДАЛЯТЬ!

---

## 2024-03-06 | Сессия 1 | Начало разработки

### Действие 1: Создание базовой структуры
**Что сделано:**
- Создана директория `G:\BinanceFriend\tradebot\`
- Структура:
  ```
  tradebot/
  ├── __init__.py
  ├── requirements.txt
  ├── example_run.py
  ├── core/
  │   ├── __init__.py
  │   ├── models.py      # TradeSignal, Order, Position
  │   └── interfaces.py  # ExchangeInterface (абстракция)
  ├── adapters/
  │   └── __init__.py
  └── api/
      ├── __init__.py
      ├── schemas.py     # Pydantic модели
      └── server.py      # FastAPI сервер
  ```

**Зачем:**
- Изолированный модуль для торговли
- Exchange-agnostic ядро
- API для приёма сигналов от telegram_runner.py

**Для чего:**
- TradeBot принимает сигналы через HTTP API
- Ядро не знает про конкретные биржи
- Адаптеры (Binance и др.) реализуют ExchangeInterface

---

### Действие 2: Анализ параметров telegram_runner.py
**Дата:** 2024-03-06

**ВСЕ ПАРАМЕТРЫ telegram_runner.py:**

#### ГРУППА 1: КОНФИГУРАЦИЯ И ДАННЫЕ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --config | str | config.json | Путь к конфигу |
| --symbols | str | "" | Символы через запятую |
| --top | int | 20 | Топ N символов по объёму |

#### ГРУППА 2: СТРАТЕГИИ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --strategies | str | "all" | Список стратегий или "all" |
| --strategy | str | None | Одна стратегия (приоритет) |

#### ГРУППА 3: ФИЛЬТРЫ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --coin-regime | flag | False | Фильтр по режиму монеты |
| --vol-filter-low | flag | False | Фильтр низкой волатильности |
| --vol-filter-high | flag | False | Фильтр высокой волатильности |
| --ml | flag | False | ML фильтрация |
| --ml-model-dir | str | "models" | Директория ML моделей |

#### ГРУППА 4: ТОРГОВЫЕ ПАРАМЕТРЫ (КРИТИЧНЫ ДЛЯ ТРЕЙДИНГА!)
| Параметр | Тип | Default | Описание | ВЛИЯНИЕ НА ТОРГОВЛЮ |
|----------|-----|---------|----------|---------------------|
| --sl | float | None | Stop Loss % | Уровень SL ордера |
| --tp | float | None | Take Profit % | Уровень TP ордера |

#### ГРУППА 5: ТАЙМФРЕЙМ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --bar | str | "daily" | Таймфрейм: 1,5,15,60,240,daily |

#### ГРУППА 6: SMAEMA СПЕЦИФИЧНЫЕ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --fast-type | str | None | SMA или EMA |
| --fast-period | int | None | Период быстрой MA |
| --slow-type | str | None | SMA или EMA |
| --slow-period | int | None | Период медленной MA |
| --offset-pct | float | None | Оффсет входа % |
| --order-lifetime | int | None | Время жизни ордера (свечи) |

#### ГРУППА 7: РЕЖИМ РАБОТЫ
| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| --dry-run | flag | False | Не отправлять, только лог |

---

### Действие 3: Определение параметров влияющих на торговлю

**ПАРАМЕТРЫ, НАПРЯМУЮ ВЛИЯЮЩИЕ НА ТОРГОВЛЮ:**

1. **--sl, --tp** → Уровни SL/TP для ордеров
2. **--coin-regime** → regime_action (FULL/DYN/OFF) определяет размер позиции:
   - FULL = $100 (полный размер)
   - DYN = $1 (динамический/тестовый)
   - OFF = пропустить сигнал
3. **--strategies** → Какие стратегии активны
4. **--bar** → Таймфрейм (влияет на частоту сигналов)

**ДАННЫЕ ИЗ СИГНАЛА, НУЖНЫЕ ДЛЯ ТОРГОВЛИ:**
```python
signal_data = {
    "signal_id": str,        # Уникальный ID
    "symbol": str,           # BTCUSDT
    "direction": str,        # LONG/SHORT
    "entry": float,          # Цена входа
    "tp": float,             # Take Profit цена
    "sl": float,             # Stop Loss цена
    "tp_pct": float,         # TP в %
    "sl_pct": float,         # SL в %
    "strategy": str,         # Название стратегии
    "regime_action": str,    # FULL/DYN/OFF - РАЗМЕР ПОЗИЦИИ!
    "coin_regime": str,      # BULL/BEAR/SIDEWAYS
    "coin_volatility": float # ATR%
}
```

---

### Действие 4: Модификация telegram_runner.py
**Дата:** 2024-03-06

**Что сделано:**
Добавлены новые параметры в telegram_runner.py (БЕЗ изменения логики генерации):

1. `--tradebot-url` (str) - URL TradeBot API для отправки сигналов
2. `--continuous` (flag) - непрерывный режим работы
3. `--interval` (int, default=86400) - интервал между запусками в секундах

**Добавленные функции:**
```python
async def send_signal_to_tradebot(tradebot_url, signal_data, logger) -> bool
    # Отправляет сигнал POST-запросом в TradeBot API
    # Формирует payload из signal_data
    # Возвращает True/False

async def main_continuous()
    # Wrapper для непрерывного режима
    # Парсит --continuous и --interval
    # Запускает main() в цикле с заданным интервалом
```

**Изменения в main():**
- После `signal_data["ml"] = {...}` добавлен вызов `send_signal_to_tradebot()`
- Добавлен `return total_sent` для внешнего использования

**Зачем:**
- Интеграция с TradeBot без изменения логики генерации
- Непрерывный режим для 24/7 работы

**ЛОГИКА ГЕНЕРАЦИИ НЕ ТРОНУТА!**

---

### Действие 5: Создание TradeAppTG
**Дата:** 2024-03-06

**Что сделано:**
Создан `tradebot/trade_app_tg.py` - главный лаунчер системы.

**Формат запуска:**
```bash
py -3.12 trade_app_tg.py "telegram_runner(--symbols BTCUSDT --coin-regime)"
py -3.12 trade_app_tg.py "telegram_runner(--top 20 --continuous --interval 3600)"
```

**Что делает:**
1. Парсит аргументы telegram_runner из командной строки
2. Извлекает торговые параметры (sl, tp, coin-regime, strategies, bar)
3. Запускает TradeBot API сервер в фоне
4. Запускает telegram_runner.py как subprocess
5. Автоматически добавляет `--tradebot-url` к аргументам
6. Стримит вывод telegram_runner в лог

**Функции:**
```python
parse_telegram_runner_args(cmd_line) -> List[str]
    # Парсит "telegram_runner(--arg val)" → ["--arg", "val"]

extract_trading_config(args) -> Dict[str, Any]
    # Извлекает sl_pct, tp_pct, coin_regime_enabled, strategies, bar

run_tradebot_server(host, port, logger)
    # Запускает FastAPI сервер

run_telegram_runner(args, tradebot_url, logger) -> Popen
    # Запускает telegram_runner.py как subprocess
```

**Зачем:**
- Единая точка входа для всей системы
- TradeBot понимает параметры от telegram_runner.py
- Готовность к торговле сразу после генерации сигнала

---

### Действие 6: Обновление зависимостей
**Дата:** 2024-03-06

**Добавлено в telegram_runner.py:**
```python
import aiohttp  # HTTP client для TradeBot
```

**Нужно обновить requirements:**
- aiohttp>=3.8.0

---

### ТЕКУЩАЯ СТРУКТУРА:
```
tradebot/
├── __init__.py
├── requirements.txt
├── example_run.py
├── trade_app_tg.py      # ГЛАВНЫЙ ЛАУНЧЕР
├── SESSION_LOG.md       # ЭТОТ ФАЙЛ
├── core/
│   ├── __init__.py
│   ├── models.py        # TradeSignal, Order, Position
│   └── interfaces.py    # ExchangeInterface
├── adapters/
│   └── __init__.py
└── api/
    ├── __init__.py
    ├── schemas.py
    └── server.py
```

---

### СЛЕДУЮЩИЕ ШАГИ:
1. ✅ Создать TradeAppTG - главный лаунчер
2. ✅ Добавить непрерывный режим работы telegram_runner.py
3. ✅ Интегрировать отправку сигналов в TradeBot API
4. ⏳ TradeBot должен понимать regime_action для sizing
5. ⏳ Binance Adapter - реальная торговля
6. ⏳ Тестирование на testnet

---
