# BinanceFriend

## Manipulation Detection Screener

Система обнаружения манипуляций (pump-and-dump) на криптовалютных рынках Binance.

---

## Возможности

- **Universe Scanning** - сканирует все 1500+ торговых пар Binance
- **Vulnerability Filter** - выявляет пары уязвимые к манипуляциям (низкая ликвидность, тонкий стакан)
- **Real-Time Monitoring** - мониторит уязвимые пары через WebSocket в реальном времени
- **Detection Engine** - детектирует подозрительную активность:
  - Volume spikes (аномальный объём)
  - Price velocity (слишком быстрое движение цены)
  - Order book manipulation (перекос стакана)
  - Wash trading (торговля с самим собой)
  - Coordinated buying/selling (координированная покупка/продажа)
  - Active pump/dump sequences
- **Alert Dispatcher** - отправляет алерты в Binance API

---

## Установка

```bash
# Клонировать или скопировать проект
cd G:\BinanceFriend

# Создать виртуальное окружение
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Установить зависимости
pip install -r requirements.txt
```

---

## Быстрый старт

```bash
# Запустить с настройками по умолчанию
python run.py

# Или с параметрами
python run.py --max-symbols 50 --rescan-interval 120

# Справка
python run.py --help
```

---

## Параметры командной строки

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `--max-symbols` | 100 | Максимум пар для мониторинга |
| `--rescan-interval` | 300 | Интервал пересканирования (сек) |
| `--api-url` | - | URL Binance API для алертов |
| `--api-key` | - | API ключ |
| `--api-secret` | - | API секрет |
| `--log-file` | logs/alerts.jsonl | Путь к файлу логов |
| `--log-level` | INFO | Уровень логирования |

---

## Структура проекта

```
BinanceFriend/
├── src/
│   └── screener/
│       ├── __init__.py          # Экспорты
│       ├── main.py              # Entry point
│       ├── models.py            # Dataclasses и Enums
│       ├── screener.py          # Главный класс
│       ├── universe_scanner.py  # Сканер всех пар
│       ├── vulnerability_filter.py  # Фильтр уязвимых
│       ├── realtime_monitor.py  # WebSocket мониторинг
│       ├── detection_engine.py  # Движок детекции
│       └── alert_dispatcher.py  # Отправка алертов
├── config/
│   └── config.yaml              # Конфигурация
├── logs/                        # Логи и алерты
├── tests/                       # Тесты
├── requirements.txt             # Зависимости
├── run.py                       # Скрипт запуска
└── README.md                    # Этот файл
```

---

## Типы детекций

### VOLUME_SPIKE
Аномальный объём торгов.
- **WARNING**: объём > 10x от среднего
- **ALERT**: объём > 20x
- **CRITICAL**: объём > 50x

### PRICE_VELOCITY
Слишком быстрое движение цены.
- **ALERT**: > 10% за минуту или > 20% за 5 минут
- **CRITICAL**: > 50% за 5 минут

### ORDERBOOK_IMBALANCE
Сильный перекос стакана.
- **WARNING**: > 60% в одну сторону
- **ALERT**: > 80%

### WASH_TRADING
Торговля с самим собой (одинаковые размеры ордеров).
- **ALERT**: > 30% трейдов одинакового размера
- **CRITICAL**: > 50%

### COORDINATED_BUYING/SELLING
Координированная торговля.
- **WARNING**: > 85% трейдов в одну сторону
- **ALERT**: > 90%

### ACTIVE_PUMP / ACTIVE_DUMP
Комбинация факторов: volume spike + price velocity + imbalance.
- **CRITICAL**: высокая уверенность в манипуляции

---

## Формат алертов

Алерты сохраняются в `logs/alerts.jsonl` в JSON формате:

```json
{
  "symbol": "XXXUSDT",
  "timestamp": "2025-02-16T12:34:56.789",
  "severity": "CRITICAL",
  "type": "ACTIVE_PUMP",
  "score": 95,
  "details": {
    "volume_spike_ratio": 45.2,
    "price_change_5m_pct": 32.5,
    "book_imbalance": 0.85
  },
  "evidence": [
    "ACTIVE PUMP DETECTED",
    "Volume: 45.2x normal",
    "Price: +32.5% in 5min",
    "Book: 85% imbalanced"
  ]
}
```

---

## API Binance (когда дадут)

Когда Binance предоставит API endpoint, добавить в конфигурацию:

```yaml
# config/config.yaml
alerts:
  binance_api_url: "https://api.binance.com/manipulation-alerts"
  api_key: "YOUR_API_KEY"
  api_secret: "YOUR_API_SECRET"
```

Или через командную строку:
```bash
python run.py --api-url "https://..." --api-key "..." --api-secret "..."
```

---

## Требования

- Python 3.11+
- aiohttp
- websockets
- structlog
- pydantic

---

## Лицензия

Proprietary - для использования в рамках сотрудничества с Binance.
