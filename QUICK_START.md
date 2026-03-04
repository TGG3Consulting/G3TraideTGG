# Быстрый старт: Установка и запуск BinanceFriend

## Шаг 1: Установка Python

1. Скачай Python 3.12 с официального сайта:
   https://www.python.org/downloads/

2. При установке **ОБЯЗАТЕЛЬНО** поставь галочку:
   - [x] Add Python to PATH

3. Нажми "Install Now"

4. Проверь установку — открой командную строку (Win+R, введи `cmd`) и набери:
   ```
   python --version
   ```
   Должно показать: `Python 3.12.x`

---

## Шаг 2: Скачивание проекта

### Вариант А: Через Git (рекомендуется)

1. Установи Git: https://git-scm.com/downloads

2. Открой командную строку и выполни:
   ```
   cd C:\
   git clone https://github.com/TGG3Consulting/G3TraideTGG.git
   cd G3TraideTGG
   ```

### Вариант Б: Скачать ZIP

1. Зайди на https://github.com/TGG3Consulting/G3TraideTGG
2. Нажми зелёную кнопку "Code" → "Download ZIP"
3. Распакуй в `C:\G3TraideTGG`

---

## Шаг 3: Установка зависимостей

1. Открой командную строку в папке проекта:
   ```
   cd C:\G3TraideTGG
   ```

2. Создай виртуальное окружение:
   ```
   python -m venv venv
   ```

3. Активируй его:
   ```
   venv\Scripts\activate
   ```

   После этого в начале строки появится `(venv)`

4. Установи зависимости:
   ```
   pip install -r requirements.txt
   pip install -r GenerateHistorySignals\requirements.txt
   ```

---

## Шаг 4: Настройка Telegram (опционально)

Если хочешь получать сигналы в Telegram:

1. Открой файл `config\telegram.json`

2. Заполни данные:
   ```json
   {
     "bot_token": "ВАШ_ТОКЕН_ОТ_BOTFATHER",
     "chat_id": "ВАШ_CHAT_ID"
   }
   ```

Как получить:
- bot_token: напиши @BotFather в Telegram, создай бота командой /newbot
- chat_id: напиши своему боту, затем зайди на https://api.telegram.org/bot<TOKEN>/getUpdates

---

## Шаг 5: Скачивание исторических данных

Перед первым запуском нужно скачать данные с Binance:

```
cd GenerateHistorySignals
python data_downloader.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2024-01-01 --end 2026-03-01
```

Или для всех 50 монет из списка:
```
python data_downloader.py --start 2024-01-01 --end 2026-03-01
```

Данные сохранятся в папку `cache/binance/`

---

## Шаг 6: Запуск генератора сигналов (бэктест)

### Базовый запуск (без фильтров, для сбора данных):
```
cd GenerateHistorySignals
python run_all.py --start 2024-01-01 --end 2026-01-31
```

### С фильтром по режиму монеты:
```
python run_all.py --start 2024-01-01 --end 2026-01-31 --coin-regime
```

### С фильтром по волатильности:
```
python run_all.py --start 2024-01-01 --end 2026-01-31 --vol-filter
```

### Все фильтры вместе:
```
python run_all.py --start 2024-01-01 --end 2026-01-31 --coin-regime --vol-filter
```

### Результаты
После выполнения появятся файлы в папке `GenerateHistorySignals/output/`:
- `backtest_momentum_YYYYMMDD_HHMMSS.xlsx`
- `backtest_mean_reversion_YYYYMMDD_HHMMSS.xlsx`
- `backtest_ls_fade_YYYYMMDD_HHMMSS.xlsx`
- и другие...

---

## Шаг 7: Запуск реального мониторинга

Для мониторинга рынка в реальном времени:

```
cd src\screener
python main.py
```

Сигналы будут отправляться в Telegram (если настроен).

---

## Полезные команды

### Посмотреть список монет:
```
python GenerateHistorySignals\get_symbols.py
```

### Анализ текущего режима рынка:
```
python GenerateHistorySignals\analyze_current_regime.py
```

### Запуск тестов:
```
pytest GenerateHistorySignals\tests\
```

---

## Структура проекта

```
G3TraideTGG/
├── GenerateHistorySignals/    # Генератор сигналов и бэктестер
│   ├── strategies/            # Торговые стратегии
│   ├── cache/                 # Кэш данных Binance
│   ├── output/                # Результаты (xlsx)
│   ├── models/                # ML модели
│   └── run_all.py             # Главный скрипт запуска
├── src/
│   ├── screener/              # Реалтайм мониторинг
│   ├── exchanges/             # Коннекторы к биржам
│   └── ml/                    # ML компоненты
├── config/                    # Конфигурация
└── backtester/                # Старый бэктестер
```

---

## Стратегии

| Стратегия | Описание |
|-----------|----------|
| momentum | Торговля по тренду |
| momentum_ls | Momentum + Long/Short ratio |
| mean_reversion | Возврат к среднему |
| ls_fade | Против толпы (Long/Short fade) |
| reversal | Разворотные паттерны |

---

## Параметры run_all.py

| Параметр | Описание |
|----------|----------|
| `--start` | Дата начала (YYYY-MM-DD) |
| `--end` | Дата конца (YYYY-MM-DD) |
| `--coin-regime` | Включить фильтр по режиму монеты |
| `--vol-filter` | Включить фильтр по волатильности |
| `--vol-filter-low` | Мин. волатильность (default: 3.0%) |
| `--vol-filter-high` | Макс. волатильность (default: 15.0%) |
| `--multi-trade` | Разрешить несколько позиций |

---

## Решение проблем

### "python не найден"
- Переустанови Python с галочкой "Add to PATH"
- Или используй полный путь: `C:\Python312\python.exe`

### "pip не найден"
```
python -m pip install --upgrade pip
```

### "ModuleNotFoundError"
```
pip install -r requirements.txt
```

### Ошибка доступа к Binance API
- Проверь интернет-соединение
- Используй VPN если Binance заблокирован

---

## Контакты

Репозиторий: https://github.com/TGG3Consulting/G3TraideTGG
