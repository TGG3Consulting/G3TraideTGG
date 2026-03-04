# ADDENDUM TO VERIFICATION REPORT

**Verifier**: Claude Opus 4.5 (Principal Engineer Auditor)
**Date**: 2026-02-20
**Source**: AUDIT_VERIFICATION_2026-02-20.md

---

## 1. NEW-ISSUE-006: Invalid Format Specifiers — NOT CONFIRMED

### Поиск

Искал паттерн `:,0f` (без точки — невалидный Python).

### Результат

**НЕ НАЙДЕНО**. Все format specifiers в `signal_formatter.py` используют корректный синтаксис `:,.0f` (запятая как thousands separator + точка + 0 decimals + float).

### Доказательство (signal_formatter.py:75-92)

```python
# Lines 75-77 — SPOT orderbook
lines.append(f"🔵 SPOT (ATR ±{spot_atr:.1f}%):")
lines.append(f"   Bid: ${spot_bid:,.0f} | Ask: ${spot_ask:,.0f}")
lines.append(f"   Imbalance: {imb_pct:.0f}% → {imb_side}")

# Lines 90-92 — FUTURES orderbook
lines.append(f"🟠 FUTURES (ATR ±{fut_atr:.1f}%):")
lines.append(f"   Bid: ${fut_bid:,.0f} | Ask: ${fut_ask:,.0f}")
lines.append(f"   Imbalance: {imb_pct:.0f}% → {imb_side}")
```

**Verdict**: `:,.0f` — ВАЛИДНЫЙ Python format specifier. Issue НЕ существует.

---

## 2. NEW-ISSUE-004: futures_book_imbalance_atr Ambiguity — CONFIRMED

### Полная цитата (futures_monitor.py:192-211)

```python
@property
def futures_book_imbalance_atr(self) -> Decimal:
    """
    Дисбаланс стакана в ATR-зоне.
    Положительный = bid pressure (покупатели сильнее)
    Отрицательный = ask pressure (продавцы сильнее)
    """
    bid = self.futures_bid_volume_atr
    ask = self.futures_ask_volume_atr
    total = bid + ask

    if total == 0:
        return Decimal("0")  # ← Причина 1: нет данных

    if bid < 100 or ask < 100:
        return Decimal("0")  # ← Причина 2: недостаточный объём

    raw_imbalance = (bid - ask) / total
    return Decimal(str(round(float(raw_imbalance), 4)))
    # ↑ Причина 3: реально сбалансированный стакан тоже даёт ~0
```

### Проблема

Возвращается `Decimal("0")` в трёх разных случаях:
1. `total == 0` — нет данных вообще
2. `bid < 100 or ask < 100` — недостаточный объём ($100 threshold)
3. Реально сбалансированный стакан (bid ≈ ask)

Downstream код не может отличить "нет данных" от "сбалансировано".

### Verdict: CONFIRMED (LOW severity)

---

## 3. NEW-ISSUE-001: Spread vs ATR — CONFIRMED

### SPOT ATR Volume (realtime_monitor.py:590-613)

```python
def _calculate_atr_volumes(self, state: "SymbolState"):
    """Рассчитать объёмы в ATR-зоне от mid price."""
    mid = state.mid_price
    if mid == 0:
        return

    atr_pct = state.atr_1h_pct / 100
    lower_bound = mid * (1 - atr_pct)
    upper_bound = mid * (1 + atr_pct)

    # ⚠️ SPREAD НЕ ПРОВЕРЯЕТСЯ!
    # Если spread_pct > atr_pct, то lower_bound может быть выше best_bid
    # Результат: bid_volume_atr = 0 при существующем стакане

    state.bid_volume_atr = sum(
        p * q for p, q in state.raw_bids
        if lower_bound <= p <= mid
    )
    state.ask_volume_atr = sum(
        p * q for p, q in state.raw_asks
        if mid <= p <= upper_bound
    )
```

### FUTURES ATR Volume (futures_monitor.py:585-609)

```python
def _calculate_futures_atr_volumes(self, state: FuturesState):
    """Рассчитать объёмы в ATR-зоне для futures."""
    mid = state.futures_mid_price
    if mid == 0:
        return

    atr_pct = state.futures_atr_1h_pct / 100
    lower_bound = mid * (1 - atr_pct)
    upper_bound = mid * (1 + atr_pct)

    # ⚠️ SPREAD НЕ ПРОВЕРЯЕТСЯ!
    # Идентичная проблема как в SPOT

    bid_volume = Decimal("0")
    for price, qty in state.futures_raw_bids:
        if lower_bound <= price <= mid:
            bid_volume += price * qty

    ask_volume = Decimal("0")
    for price, qty in state.futures_raw_asks:
        if mid <= price <= upper_bound:
            ask_volume += price * qty

    state.futures_bid_volume_atr = bid_volume
    state.futures_ask_volume_atr = ask_volume
```

### Проблема

Если `spread_pct > atr_pct`:
- `lower_bound` окажется ВЫШЕ `best_bid`
- Все bids будут ниже `lower_bound`, ни один не попадёт в фильтр
- `bid_volume_atr = 0` при реально существующем стакане
- `book_imbalance_atr = 0` (из-за $100 threshold в property)

### Verdict: CONFIRMED (MEDIUM severity)

---

## 4. ПОЛНЫЙ СПИСОК МЕСТ ДЛЯ ИСПРАВЛЕНИЯ

### CRITICAL (исправить первыми)

| # | Файл | Строки | Issue | Описание |
|---|------|--------|-------|----------|
| 1 | `futures_monitor.py` | 1686-1690 | ISSUE-001 | Fire-and-forget asyncio.create_task без сохранения reference |
| 2 | `realtime_monitor.py` | 440-441 | ISSUE-001 | Fire-and-forget asyncio.create_task без сохранения reference |

### HIGH

| # | Файл | Строки | Issue | Описание |
|---|------|--------|-------|----------|
| 3 | `realtime_monitor.py` | 320-370 | ISSUE-004 | WebSocket reconnection без backfill пропущенных данных |
| 4 | `signal_generator.py` | 408-410 | ISSUE-008 | Exception swallowed — только `str(e)` без traceback |

### MEDIUM

| # | Файл | Строки | Issue | Описание |
|---|------|--------|-------|----------|
| 5 | `futures_monitor.py` | 585-609 | NEW-001 | ATR volume: spread не проверяется |
| 6 | `realtime_monitor.py` | 590-613 | NEW-001 | ATR volume: spread не проверяется |
| 7 | `accumulation_detector.py` | 112-117 | ISSUE-005 | O(n) cleanup на каждый вызов add_detection() |
| 8 | `futures_monitor.py` | 1690 | ISSUE-014 | Callback error на debug level |
| 9 | `realtime_monitor.py` | 443 | ISSUE-014 | Callback error на debug level |

### LOW

| # | Файл | Строки | Issue | Описание |
|---|------|--------|-------|----------|
| 10 | `futures_monitor.py` | 192-211 | NEW-004 | Imbalance возвращает 0 без различия причины |
| 11 | `models.py` (screener) | 211-231 | NEW-004 | SPOT imbalance — аналогичная проблема |
| 12 | `signal_generator.py` | 158-164 | NEW-003 | Cooldown проверяется ПОСЛЕ add_detection |
| 13 | `signal_generator.py` | 453+ | ISSUE-012 | Decimal→float для display (низкий риск) |
| 14 | `risk_calculator.py` | 296 | ISSUE-012 | sl_pct/100 precision (float уже) |
| 15 | `futures_monitor.py` | 1007-1010 | ISSUE-011 | Hard-coded funding_history limit 24 |
| 16 | `accumulation_detector.py` | 70-71 | ISSUE-013 | Hard-coded MIN_VOLUME constants |
| 17 | `models.py` (signals) | 118 | ISSUE-010 | AccumulationScore cap 100 (UI concern) |

### INFRASTRUCTURE (не баги, а улучшения)

| # | Файл | Issue | Описание |
|---|------|-------|----------|
| 18 | Весь проект | ISSUE-015 | Нет prometheus/observability метрик |

---

## 5. ISSUES ДЛЯ УДАЛЕНИЯ ИЗ AUDIT_REPORT

| Issue | Причина удаления |
|-------|------------------|
| **ISSUE-006** | NOT VERIFIED — ATR clamp идентичен в обоих файлах |
| **ISSUE-009** | STRATEGY — торговый параметр 1.2x, не баг инфраструктуры |

---

## 6. ISSUES ДЛЯ ПОНИЖЕНИЯ SEVERITY

| Issue | Было | Стало | Причина |
|-------|------|-------|---------|
| ISSUE-002 | CRITICAL | LOW | Dict ограничен кол-вом символов, cleanup есть |
| ISSUE-003 | HIGH | MEDIUM | 15min buffer достаточен для большинства случаев |
| ISSUE-007 | MEDIUM | LOW | Cleanup работает, размер ограничен |

---

*End of Addendum*
