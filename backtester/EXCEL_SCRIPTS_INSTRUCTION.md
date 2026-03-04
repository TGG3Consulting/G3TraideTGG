# Инструкция по Excel скриптам для бэктестера

## Расположение скриптов

```
G:\BinanceFriend\backtester\
├── add_analysis_sheet.py   # Добавляет лист Analysis с таблицами
├── add_comments.py         # Добавляет русские комментарии к колонкам
└── EXCEL_SCRIPTS_INSTRUCTION.md  # Эта инструкция
```

---

## Скрипт 1: add_analysis_sheet.py

### Назначение
Добавляет новый лист **"Analysis"** в Excel файл бэктеста с тремя таблицами:

1. **PnL BY SYMBOL** - сводка по монетам (от убыточных к прибыльным)
   - Symbol, Total Net PnL, Trades Count, Avg PnL per Trade, Win Rate %

2. **PnL BY PERIOD** - сводка по периодам YYYY-MM (от убыточных к прибыльным)
   - Period, Total Net PnL, Trades Count, Best Symbol, Best PnL, Worst Symbol, Worst PnL

3. **PERIOD-SYMBOL MATRIX** - полная матрица период × монета
   - Зелёные ячейки = прибыль, красные = убыток
   - Итоги по строкам и колонкам

### Как использовать

1. Открыть файл `add_analysis_sheet.py`
2. Изменить путь в переменной `INPUT_FILE`:
   ```python
   INPUT_FILE = r"G:\BinanceFriend\backtester\output\ТВОЙ_ФАЙЛ.xlsx"
   ```
3. Закрыть Excel файл (если открыт)
4. Запустить:
   ```bash
   cd G:\BinanceFriend\backtester
   python add_analysis_sheet.py
   ```

### Важно
- Основной лист "Backtest Results" НЕ изменяется
- Если лист "Analysis" уже существует - он будет перезаписан
- Данные берутся из колонок: C (Symbol), D (Timestamp), AF (Net PnL)

---

## Скрипт 2: add_comments.py

### Назначение
Добавляет русские комментарии ко всем 155 колонкам в заголовках.
При наведении мыши на заголовок появляется описание колонки.

### Как использовать

1. Открыть файл `add_comments.py`
2. Изменить пути:
   ```python
   INPUT_FILE = r"G:\BinanceFriend\backtester\output\ТВОЙ_ФАЙЛ.xlsx"
   OUTPUT_FILE = r"G:\BinanceFriend\backtester\output\ТВОЙ_ФАЙЛ_WITH_COMMENTS.xlsx"
   ```
3. Закрыть Excel файл (если открыт)
4. Запустить:
   ```bash
   cd G:\BinanceFriend\backtester
   python add_comments.py
   ```

### Важно
- Создаётся НОВЫЙ файл (OUTPUT_FILE), оригинал не изменяется
- Комментарии добавляются к листу "Backtest Results" и "Analysis"

---

## Быстрый запуск обоих скриптов

```bash
cd G:\BinanceFriend\backtester

# 1. Сначала добавить таблицы
python add_analysis_sheet.py

# 2. Потом добавить комментарии
python add_comments.py
```

---

## Структура колонок (для справки)

| Колонка | Название | Описание |
|---------|----------|----------|
| A (1) | № | Порядковый номер |
| B (2) | Signal ID | Уникальный ID сигнала |
| C (3) | Symbol | Торговая пара |
| D (4) | Timestamp | Время сигнала |
| E (5) | Direction | LONG/SHORT |
| AF (32) | Net PnL | Чистая прибыль/убыток |
| AH (34) | Net % | Чистый PnL в процентах |
| AJ (36) | TP1 Hit | Достигнут ли TP1 |
| AK (37) | TP2 Hit | Достигнут ли TP2 |
| AL (38) | TP3 Hit | Достигнут ли TP3 |
| AM (39) | SL Hit | Сработал ли Stop Loss |

Полный список комментариев см. в словаре `COLUMN_COMMENTS` в файле `add_comments.py`.

---

## Команда для Claude в следующих сессиях

Если пользователь говорит: **"давай по скриптам таблицу"** или **"добавь анализ в Excel"**:

1. НЕ писать новый код
2. Спросить путь к файлу
3. Отредактировать `INPUT_FILE` в скриптах
4. Запустить скрипты по инструкции выше
