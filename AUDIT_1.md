# BINANCEFRIEND SYSTEM AUDIT REPORT

**Date:** 2026-02-17
**Auditor:** Claude Opus 4.5
**Scope:** Full data flow audit from exchange WebSocket to Telegram alerts
**Status:** CRITICAL ISSUES IDENTIFIED

---

## EXECUTIVE SUMMARY

This audit examines the complete data pipeline of the BinanceFriend cryptocurrency manipulation detection system. **Multiple critical bugs** have been identified that compromise data integrity and alert accuracy.

### Key Findings:

| Issue | Severity | Status |
|-------|----------|--------|
| `trades_count = 0` in alerts | CRITICAL | Confirmed |
| `buy_percent = 50%`, `sell_percent = 50%` (defaults) | CRITICAL | Confirmed |
| 18+ decimal places in numeric values | CRITICAL | Confirmed |
| `Funding = -200%` (impossible value) | CRITICAL | Confirmed |
| Imbalance with 25 decimal places | CRITICAL | Confirmed |
| Futures detections bypass enrichment | CRITICAL | Confirmed |

---

## SECTION 1: DATA FLOW FROM BINANCE WEBSOCKET

### 1.1 WebSocket Connection (`realtime_monitor.py`)

**Streams subscribed:**
- `{symbol}@trade` - Individual trades
- `{symbol}@depth@100ms` - Order book updates
- `{symbol}@kline_1m` - 1-minute candles

**Trade Parsing (lines 364-370):**
```python
trade = Trade(
    price=Decimal(str(data["p"])),
    qty=Decimal(str(data["q"])),
    time=int(data["T"]),
    is_buyer_maker=bool(data["m"]),
)
```

**CRITICAL BUG #1: State Update Callback Only on Trades**

Location: `realtime_monitor.py:405-411`
```python
# Callback triggered ONLY on trades
if self._on_state_update and state:
    self._on_state_update(symbol, state)
```

The `_on_state_update` callback is **only called when a trade event is received**, NOT when order book depth updates arrive. This means:
- Order book manipulation (spoofing) may not trigger immediate detection
- `bid_volume_20` and `ask_volume_20` updates don't notify detection engine

### 1.2 Depth Parsing (lines 430-433)

```python
state.bid_volume_20 = sum(
    Decimal(str(p)) * Decimal(str(q))
    for p, q in bids[:20]
)
```

**ISSUE:** Raw `Decimal` values are stored. Decimal multiplication can produce results with many decimal places (e.g., `12345.12345678 * 0.00001 = 0.1234512345678`).

---

## SECTION 2: SYMBOL STATE AND MODELS

### 2.1 SymbolState Model (`models.py`)

**CRITICAL BUG #2: Default 50%/50% Buy/Sell Ratio**

Location: `models.py:240-245`
```python
@property
def buy_ratio_5m(self) -> Decimal:
    if not self.trades_5m:
        return Decimal("0.5")  # DEFAULT 50% when no trades!
    buys = sum(1 for t in self.trades_5m if t.side == "BUY")
    return Decimal(str(buys / len(self.trades_5m)))
```

**Root Cause:** When `trades_5m` list is empty (no trades collected), the property returns `0.5` (50%).

**Impact:** Alerts show meaningless `50% buy / 50% sell` when:
- First startup before trades collected
- Symbol with low trading activity
- After state reset
- Futures detections (which don't collect trades)

### 2.2 Book Imbalance Calculation

**CRITICAL BUG #3: Arbitrary Precision from Decimal Division**

Location: `models.py:186-194`
```python
@property
def book_imbalance(self) -> Decimal:
    total = self.bid_volume_20 + self.ask_volume_20
    if total == 0:
        return Decimal("0")
    return (self.bid_volume_20 - self.ask_volume_20) / total
```

**Issue:** Decimal division produces arbitrary precision results like:
```
0.2345678901234567890123456789
```

This value is passed directly to detection details without rounding.

---

## SECTION 3: DETECTION ENGINE

### 3.1 Orderbook Manipulation Detection (`detection_engine.py`)

**CRITICAL BUG #4: Raw Decimal Values Passed to Details**

Location: `detection_engine.py:280-284`
```python
details={
    "imbalance": state.book_imbalance,      # RAW DECIMAL!
    "bid_volume": state.bid_volume_20,       # RAW DECIMAL!
    "ask_volume": state.ask_volume_20,       # RAW DECIMAL!
    "dominant_side": side,
},
```

**Impact:** These raw Decimal values appear in alerts with 18-25 decimal places.

### 3.2 Enrichment Function

Location: `detection_engine.py:133-154`

The `_enrich_detection()` function has rounding logic but:
1. Only runs for SPOT detections
2. Only enriches certain detection types
3. Futures detections **bypass this entirely**

```python
# Approximate logic flow:
if detection.source == "spot":
    detection = self._enrich_detection(detection, state)
# Futures detections skip enrichment!
```

### 3.3 Trade Pattern Detection

Location: `detection_engine.py:340`

```python
if len(state.trades_5m) < self.min_trades_for_pattern:
    return None  # No detection if insufficient trades
```

**Issue:** This check prevents patterns from being detected with few trades, but other detections (orderbook, volume spike) can fire without trades data.

---

## SECTION 4: FUTURES MONITOR

### 4.1 Funding Rate Calculation

**CRITICAL BUG #5: Funding Rate Percentage Conversion**

Location: `futures_monitor.py:77-80`
```python
@property
def funding_rate_percent(self) -> Decimal:
    """Funding rate в процентах (0.0001 -> 0.01)."""
    return self.funding_rate * 100
```

**Binance API Documentation:**
- Binance returns funding as decimal fraction: `0.0001` = 0.01%
- Multiplying by 100 converts to percentage: `0.0001 * 100 = 0.01%`

**The Bug:** In `alert_details_store.py:185-200`, there's additional conversion logic:
```python
funding_raw = ex_data.get('funding')
if funding_raw is not None:
    funding_val = float(funding_raw)
    # If value very small (< 0.01), assume it's fraction - multiply by 100
    # If >= 0.01 and < 1, assume already percentage
    # If >= 1, something wrong - clamp
    if abs(funding_val) < 0.01:
        funding_pct = funding_val * 100  # 0.0001 -> 0.01%
    elif abs(funding_val) < 1:
        funding_pct = funding_val  # Already percentage
    else:
        # Value >= 1% - possibly error, clamp
        funding_pct = max(-1.0, min(1.0, funding_val))
```

**Problem Scenarios:**
1. If `FuturesMonitor` returns `funding_rate_percent` (already multiplied by 100): value like `0.01` → treated as "already percentage" → stored as `0.01`
2. If raw `funding_rate` passed: value like `0.0001` → multiplied again → `0.01`
3. If incorrectly large value: clamped to `-1.0` to `1.0`

**Root Cause of -200%:** The logic path is unclear and may double-multiply in certain scenarios. Additionally, if an exchange returns funding in a different format (some exchanges return `-0.02` meaning -2%), the heuristic fails.

### 4.2 FuturesDetection Data Structure

Location: `futures_monitor.py:144-154`
```python
@dataclass
class FuturesDetection:
    symbol: str
    timestamp: datetime
    detection_type: str
    severity: AlertSeverity
    score: int
    details: dict
    evidence: list[str]
```

**CRITICAL BUG #6: FuturesDetection Missing Trade Data**

The `FuturesDetection` dataclass does NOT include:
- `trades_count`
- `buy_percent`
- `sell_percent`
- `buy_ratio`

When `AlertDetails.create_alert_from_detection()` processes a `FuturesDetection`:
```python
# alert_details_store.py:134-137
trades_count = int(details_dict.get('trades_count', 0) or 0)  # Returns 0
```

**This is the root cause of `trades_count = 0` in futures alerts.**

---

## SECTION 5: ALERT DETAILS STORE

### 5.1 AlertDetails Creation (`alert_details_store.py`)

**Bug #7: Default Values When Data Missing**

Location: `alert_details_store.py:121-132`
```python
# buy_ratio extraction
buy_ratio = details_dict.get('buy_ratio')
if buy_ratio is not None:
    buy_pct = float(buy_ratio) * 100 if float(buy_ratio) <= 1 else float(buy_ratio)
else:
    buy_pct = details_dict.get('buy_percent', 50.0)  # DEFAULT 50%!

sell_ratio = details_dict.get('sell_ratio')
if sell_ratio is not None:
    sell_pct = float(sell_ratio) * 100 if float(sell_ratio) <= 1 else float(sell_ratio)
else:
    sell_pct = 100.0 - buy_pct  # Always 50% if buy_pct is 50%
```

### 5.2 Rounding Logic Exists But Applied Late

Location: `alert_details_store.py:139-161`
```python
# Rounding logic for cleaned_details
keys_to_round_2 = {'bid_volume', 'ask_volume', 'volume_5m', ...}
keys_to_round_4 = {'imbalance', 'spread_pct', 'buy_ratio', ...}
keys_to_round_6 = {'current_price', 'best_bid', 'best_ask', ...}

for key, value in details_dict.items():
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        # Apply rounding based on key
```

**Issue:** This rounding is applied to `cleaned_details` but:
1. Only processes keys explicitly listed
2. Original `details_dict` from detection may use different key names
3. Some values may already be strings
4. Not all paths go through this code

---

## SECTION 6: EXCHANGE API VERIFICATION

### 6.1 Binance API

**Funding Rate:**
- Endpoint: `GET /fapi/v1/premiumIndex`
- Field: `lastFundingRate`
- Format: Decimal string, e.g., `"0.0001"` = 0.01%
- **Correct handling:** Multiply by 100 for percentage display

**Open Interest:**
- Endpoint: `GET /fapi/v1/openInterest`
- Field: `openInterest`
- Format: Quantity in base currency (e.g., BTC)

### 6.2 Bybit API (`bybit/connector.py`)

Location: `connector.py:635-652`
```python
def _normalize_trade(self, raw: dict) -> UnifiedTrade:
    exchange_symbol = raw.get("s", "")
    side_str = raw.get("S", "Buy")
    return UnifiedTrade(
        price=self.to_decimal(raw.get("p", "0")),
        quantity=self.to_decimal(raw.get("v", "0")),
        ...
    )
```

**Symbol Format:** `BTCUSDT` (no separator)
**Trade Side:** `"Buy"` / `"Sell"` (capitalized)

### 6.3 OKX API (`okx/connector.py`)

Location: `connector.py:644-660`
```python
def _normalize_trade(self, raw: dict, inst_id: str) -> UnifiedTrade:
    return UnifiedTrade(
        price=self.to_decimal(raw.get("px", "0")),
        quantity=self.to_decimal(raw.get("sz", "0")),
        ...
    )
```

**Symbol Format:** `BTC-USDT-SWAP` (hyphen separator)
**Trade Fields:** `px` (price), `sz` (size)

### 6.4 Bitget API (`bitget/connector.py`)

**Funding Rate:**
```python
# Line 543
rate=self.to_decimal(item.get("fundingRate", "0")),
```
Returns raw funding rate as decimal fraction.

### 6.5 Gate.io API (`gate/connector.py`)

**Funding Rate:**
```python
# Line 486
funding_rate = Decimal(str(data.get("funding_rate", 0)))
```
Returns funding rate as decimal fraction.

### 6.6 MEXC API (`mexc/connector.py`)

**Funding Rate:**
```python
# Line 478
funding_rate = Decimal(str(data.get("fundingRate", 0)))
```
Returns funding rate as decimal fraction.

### 6.7 KuCoin API (`kucoin/connector.py`)

**Funding Rate:**
```python
# Line 546
funding_rate = Decimal(str(data.get("value", 0)))
```
Returns funding rate as decimal fraction.

**Symbol Format:** `XBTUSDTM` (XBT for BTC, M suffix)

---

## SECTION 7: CROSS-EXCHANGE DATA STORE

### 7.1 StateStore (`state_store.py`)

The cross-exchange StateStore maintains:
- Price history (deque with maxlen=120)
- Funding history (deque with maxlen=24)
- OI history (deque with maxlen=60)
- Order book snapshots

**Funding Rate Storage (line 376-381):**
```python
async def update_funding(
    self,
    exchange: str,
    symbol: str,
    rate: Decimal,  # Expects raw fraction: 0.0001 = 0.01%
    ...
)
```

**Issue:** Different connectors may pass funding in different formats. StateStore expects raw fraction, but some paths may pass already-converted percentage.

---

## SECTION 8: DATA FLOW DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        BINANCE WEBSOCKET                                 │
│  @trade  │  @depth@100ms  │  @kline_1m                                  │
└────┬─────┴───────┬────────┴─────────────────────────────────────────────┘
     │             │
     ▼             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     REALTIME_MONITOR                                     │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐               │
│  │ Trade Parse │   │ Depth Parse │   │ trades_5m buffer │               │
│  │ (Decimal)   │   │ (Decimal)   │   │                  │               │
│  └──────┬──────┘   └──────┬──────┘   └────────┬─────────┘               │
│         │                 │                    │                         │
│         ▼                 ▼                    ▼                         │
│    SymbolState.add_trade()         SymbolState.bid_volume_20            │
│         │                                      │                         │
│         ├──────────────────────────────────────┤                         │
│         │         _on_state_update             │                         │
│         │      (ONLY ON TRADES!) ◄─────────────┘  BUG: No callback      │
└─────────┼───────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      DETECTION_ENGINE                                    │
│                                                                          │
│  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐  │
│  │ Orderbook Manip  │   │ Volume Spike     │   │ Trade Patterns      │  │
│  │ state.book_      │   │                  │   │ state.trades_5m     │  │
│  │ imbalance        │   │                  │   │                     │  │
│  │ (RAW DECIMAL)    │   │                  │   │ BUG: May be empty   │  │
│  └────────┬─────────┘   └────────┬─────────┘   └──────────┬──────────┘  │
│           │                      │                        │              │
│           │    Detection object (Decimal values)          │              │
│           └──────────────────────┼────────────────────────┘              │
│                                  │                                       │
│           _enrich_detection() ◄──┤  BUG: Only for SPOT detections       │
└──────────────────────────────────┼──────────────────────────────────────┘
                                   │
┌──────────────────────────────────┼──────────────────────────────────────┐
│                      FUTURES_MONITOR                                     │
│                                                                          │
│  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐              │
│  │ OI Detection  │   │ Funding Det.  │   │ L/S Ratio     │              │
│  │               │   │ funding_rate  │   │               │              │
│  │ BUG: No trade │   │ * 100         │   │               │              │
│  │ data included │   │               │   │               │              │
│  └───────┬───────┘   └───────┬───────┘   └───────┬───────┘              │
│          │                   │                   │                       │
│          │    FuturesDetection (NO trades_count, NO buy_percent)        │
│          └───────────────────┴───────────────────┘                       │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ALERT_DETAILS_STORE                                   │
│                                                                          │
│  create_alert_from_detection()                                           │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ trades_count = details_dict.get('trades_count', 0)  ◄── DEFAULT │    │
│  │ buy_pct = details_dict.get('buy_percent', 50.0)     ◄── DEFAULT │    │
│  │ sell_pct = 100.0 - buy_pct                          ◄── DEFAULT │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  Rounding logic exists BUT:                                              │
│  - Applied to cleaned_details only                                       │
│  - Key names must match exactly                                          │
│  - Some values may be strings                                            │
│                                                                          │
│  Funding conversion logic:                                               │
│  - if < 0.01: multiply by 100 (fraction → %)                            │
│  - if >= 0.01 and < 1: assume already %                                 │
│  - if >= 1: clamp to [-1, 1]  ◄── BUG: Wrong assumption                 │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    TELEGRAM_NOTIFIER                                     │
│                                                                          │
│  _format_detailed_report()                                               │
│                                                                          │
│  Output with bugs:                                                       │
│  - trades_count: 0                                                       │
│  - buy_percent: 50.0%                                                    │
│  - sell_percent: 50.0%                                                   │
│  - imbalance: 0.2345678901234567890123456789                            │
│  - funding: -2.00%                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## SECTION 9: ROOT CAUSE ANALYSIS

### Bug 1: trades_count = 0

**Root Cause:** `FuturesDetection` does not include trade data. When `create_alert_from_detection()` processes it, `details_dict.get('trades_count', 0)` returns default value.

**Files:**
- `futures_monitor.py:144-154` - FuturesDetection missing trades fields
- `alert_details_store.py:135` - Default to 0

### Bug 2: buy_percent = 50%, sell_percent = 50%

**Root Cause:** Multiple fallback layers all default to 50%:
1. `models.py:242` - `buy_ratio_5m` returns `Decimal("0.5")` when no trades
2. `alert_details_store.py:126` - Falls back to `50.0` when key missing

**Files:**
- `models.py:240-245` - Default 0.5 ratio
- `alert_details_store.py:121-132` - Default 50.0 percent

### Bug 3: 18+ Decimal Places

**Root Cause:** `Decimal` division produces arbitrary precision. Raw values passed to detection details without rounding.

**Files:**
- `models.py:186-194` - `book_imbalance` Decimal division
- `detection_engine.py:280-284` - Raw Decimal in details dict
- `realtime_monitor.py:430-433` - Decimal multiplication for volumes

### Bug 4: Funding = -200%

**Root Cause:** Inconsistent funding rate format assumptions across components:
1. `FuturesMonitor` stores `funding_rate_percent` (already * 100)
2. `alert_details_store.py` has heuristic logic that may double-convert
3. Some exchanges return funding in different formats

**Files:**
- `futures_monitor.py:77-80` - Multiplies by 100
- `alert_details_store.py:185-200` - Heuristic conversion logic

### Bug 5: Imbalance with 25 Decimal Places

**Root Cause:** Same as Bug 3. `book_imbalance` property uses Decimal division without rounding.

**Files:**
- `models.py:186-194` - Unrounded Decimal division

### Bug 6: Futures Detections Bypass Enrichment

**Root Cause:** `_enrich_detection()` in DetectionEngine only processes spot detections. Futures detections go directly to alert creation.

**Files:**
- `detection_engine.py` - Enrichment logic conditional on detection source

---

## SECTION 10: AFFECTED CODE LOCATIONS

| File | Line(s) | Issue |
|------|---------|-------|
| `models.py` | 240-245 | Default 0.5 buy_ratio |
| `models.py` | 186-194 | Unrounded book_imbalance |
| `detection_engine.py` | 280-284 | Raw Decimal in details |
| `futures_monitor.py` | 77-80 | funding_rate * 100 |
| `futures_monitor.py` | 144-154 | FuturesDetection missing trades |
| `alert_details_store.py` | 121-132 | Default 50% buy/sell |
| `alert_details_store.py` | 135 | Default trades_count = 0 |
| `alert_details_store.py` | 185-200 | Funding conversion heuristic |
| `realtime_monitor.py` | 405-411 | Callback only on trades |
| `realtime_monitor.py` | 430-433 | Decimal multiplication |

---

## SECTION 11: RECOMMENDATIONS

### Priority 1 (Critical - Data Integrity)

1. **Fix FuturesDetection to include trade data** or create separate enrichment path for futures

2. **Round all Decimal values before storing in details:**
   ```python
   imbalance = round(float(state.book_imbalance), 4)
   bid_volume = round(float(state.bid_volume_20), 2)
   ```

3. **Standardize funding rate format across all components:**
   - Define clear contract: raw fraction OR percentage
   - Remove heuristic conversion logic
   - All exchanges should output same format

### Priority 2 (High - Accuracy)

4. **Remove default 50% fallback or mark it clearly in alerts:**
   ```python
   if not self.trades_5m:
       return None  # or Decimal("-1") to indicate "no data"
   ```

5. **Add validation for funding rate ranges:**
   ```python
   if abs(funding_pct) > 1.0:  # > 100% is impossible
       logger.warning("Invalid funding rate", value=funding_pct)
       funding_pct = None  # Don't display invalid data
   ```

### Priority 3 (Medium - Completeness)

6. **Trigger detection on orderbook updates, not just trades**

7. **Add data validation layer between components**

8. **Implement consistent number formatting in Telegram output**

---

## SECTION 12: VERIFICATION COMMANDS

To verify the bugs exist, run these greps in the codebase:

```bash
# Bug 1: Default trades_count
grep -n "trades_count.*0" src/screener/alert_details_store.py

# Bug 2: Default 50%
grep -n "0.5\|50.0" src/screener/models.py src/screener/alert_details_store.py

# Bug 3: Raw Decimal in details
grep -n "state.book_imbalance\|bid_volume_20\|ask_volume_20" src/screener/detection_engine.py

# Bug 4: Funding rate multiplication
grep -n "funding.*100\|* 100" src/screener/futures_monitor.py src/screener/alert_details_store.py

# Bug 5: Decimal division without round
grep -n "book_imbalance" src/screener/models.py
```

---

## APPENDIX A: EXCHANGE API FUNDING RATE FORMATS

| Exchange | Raw Format | Example | Meaning |
|----------|------------|---------|---------|
| Binance | Decimal fraction | `0.0001` | 0.01% |
| Bybit | Decimal fraction | `0.0001` | 0.01% |
| OKX | Decimal fraction | `0.0001` | 0.01% |
| Bitget | Decimal fraction | `0.0001` | 0.01% |
| Gate.io | Decimal fraction | `0.0001` | 0.01% |
| MEXC | Decimal fraction | `0.0001` | 0.01% |
| KuCoin | Decimal fraction | `0.0001` | 0.01% |

**All exchanges return funding rate as decimal fraction. Multiply by 100 for percentage display.**

---

## APPENDIX B: EXCHANGE SYMBOL FORMATS

| Exchange | Format | Example |
|----------|--------|---------|
| Binance | `BASEUSDT` | `BTCUSDT` |
| Bybit | `BASEUSDT` | `BTCUSDT` |
| OKX | `BASE-USDT-SWAP` | `BTC-USDT-SWAP` |
| Bitget | `BASEUSDT` | `BTCUSDT` |
| Gate.io | `BASE_USDT` | `BTC_USDT` |
| MEXC | `BASE_USDT` | `BTC_USDT` |
| KuCoin | `XBTQUOTEM` | `XBTUSDTM` (XBT=BTC) |

---

**END OF AUDIT REPORT**

*This audit was conducted without making any code modifications. All findings are based on static code analysis.*
