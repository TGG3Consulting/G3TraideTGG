# PRODUCTION AUDIT REPORT: BinanceFriend Trading Signal System

**Auditor**: Claude Opus 4.5
**Date**: 2026-02-20
**Codebase**: G:\BinanceFriend
**Scope**: Infrastructure/Quality (NOT trading strategy)

---

## A) EXECUTIVE SUMMARY

The BinanceFriend system is a reasonably well-architected cryptocurrency signal detection platform with evidence of prior debugging (FIX-1 through FIX-16, CALC-1 through CALC-5, LEAK-1 through LEAK-4, RACE-1 through RACE-5). The code shows signs of iterative quality improvement.

**Top 15 Issues by Priority:**

| # | Severity | Location | Summary |
|---|----------|----------|---------|
| 1 | **CRITICAL** | `futures_monitor.py:1686-1690` | Fire-and-forget `asyncio.create_task()` for callbacks without tracking |
| 2 | **CRITICAL** | `signal_generator.py:533-537` | Unbounded dict growth in `_recent_signals` cleanup |
| 3 | **HIGH** | `futures_monitor.py:919-923` | OI history 75min retention vs 2min tolerance = edge case failures |
| 4 | **HIGH** | `realtime_monitor.py:331` | WebSocket reconnection gap can miss/duplicate data |
| 5 | **HIGH** | `accumulation_detector.py:112-117` | Detection cache cleanup runs on every add, O(n) |
| 6 | **MEDIUM** | `futures_monitor.py:750` vs `realtime_monitor.py:658` | Inconsistent ATR clamp: both changed to [1,20] but comments differ |
| 7 | **MEDIUM** | `detection_engine.py:66` | `_recent_detections` dict grows unbounded between hourly cleanups |
| 8 | **MEDIUM** | `signal_generator.py:408-410` | Bare `except Exception` swallows signal generation errors |
| 9 | **MEDIUM** | `risk_calculator.py:248` | SL calculation uses `max()` of default vs volatility, may be too tight |
| 10 | **LOW** | `models.py:118` | `AccumulationScore.total` max 100 cap may hide extreme conditions |
| 11 | **LOW** | `futures_monitor.py:1007-1010` | Funding history hard-coded 24 records, should be configurable |
| 12 | **LOW** | Multiple files | Decimal→float conversions scattered, precision loss possible |
| 13 | **LOW** | `accumulation_detector.py:70-71` | Magic numbers for MIN_SPOT/FUTURES_VOLUME_USD |
| 14 | **INFO** | `realtime_monitor.py:440-441` | Callback exceptions logged at debug level, should be warning |
| 15 | **INFO** | All detectors | No metrics/counters for observability (detections/min, etc.) |

**Overall Assessment**: Production-ready with caveats. The system handles rate limits properly, has smart deduplication, and uses ATR-adaptive orderbook analysis. However, there are memory management concerns and some edge cases in time window calculations.

---

## B) SYSTEM MAP (Data Flow)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  SPOT WebSocket              │  FUTURES REST/WS           │  CrossExchange  │
│  ├─ @trade                   │  ├─ /fapi/v1/openInterest  │  └─ StateStore  │
│  ├─ @depth20@100ms           │  ├─ /fapi/v1/premiumIndex  │     (13 exch)   │
│  └─ @kline_1m                │  ├─ /futures/data/LS       │                 │
│                              │  └─ depth WS @depth20@100ms│                 │
└──────────┬───────────────────┴──────────┬──────────────────┴────────┬───────┘
           │                              │                           │
           ▼                              ▼                           │
┌──────────────────────┐      ┌───────────────────────┐               │
│  RealTimeMonitor     │      │  FuturesMonitor       │               │
│  realtime_monitor.py │      │  futures_monitor.py   │               │
│  ├─ SymbolState      │      │  ├─ FuturesState      │               │
│  ├─ ATR calculation  │      │  ├─ OI/Funding/LS     │               │
│  └─ Warmup baselines │      │  └─ OI+Price Diverg.  │               │
└──────────┬───────────┘      └──────────┬────────────┘               │
           │                              │                           │
           ▼                              ▼                           │
┌──────────────────────┐      ┌───────────────────────┐               │
│  DetectionEngine     │      │  FuturesDetection     │               │
│  detection_engine.py │      │  (inside futures_mon) │               │
│  ├─ Volume spikes    │      │  ├─ WHALE_ACCUMULATION│               │
│  ├─ Price velocity   │      │  ├─ OI_SPIKE/DROP     │               │
│  ├─ Orderbook imbal. │      │  ├─ FUNDING_EXTREME   │               │
│  ├─ Wash trading     │      │  └─ WEAK_PUMP/DUMP    │               │
│  └─ Pump sequence    │      └──────────┬────────────┘               │
└──────────┬───────────┘                  │                           │
           │                              │                           │
           └──────────────┬───────────────┘                           │
                          ▼                                           │
              ┌───────────────────────┐                               │
              │  SignalGenerator      │◄──────────────────────────────┘
              │  signal_generator.py  │
              │  ├─ on_detection()    │
              │  ├─ 1h cooldown       │
              │  └─ AccumulationDetect│
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  RiskCalculator       │
              │  risk_calculator.py   │
              │  ├─ Entry zone        │
              │  ├─ Stop Loss (ATR)   │
              │  └─ Take Profits      │
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  TradeSignal          │
              │  (models.py)          │
              └───────────┬───────────┘
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ AlertDispatch │ │ TelegramNotif │ │ SignalLogger  │
│ (HTTP API)    │ │ (Telegram Bot)│ │ (JSONL files) │
└───────────────┘ └───────────────┘ └───────────────┘
```

**Key Data Transformations:**
1. `Trade` → `SymbolState` (aggregates trades into 1m/5m/1h windows)
2. `SymbolState` → `Detection` (pattern matching)
3. `FuturesState` → `FuturesDetection` (OI/funding analysis)
4. `Detection` → `AccumulationScore` → `TradeSignal` (signal generation)
5. `TradeSignal` → `RiskLevels` (entry/SL/TP calculation)

---

## C) ISSUE REGISTER

### ISSUE-001: Fire-and-Forget Async Callbacks
| Field | Value |
|-------|-------|
| **ID** | ISSUE-001 |
| **Severity** | CRITICAL |
| **Location** | `futures_monitor.py:1686-1690` |
| **Also affects** | `realtime_monitor.py:437-451`, `signal_generator.py` |
| **Symptom** | Orphaned tasks, silent failures, memory leak from task references |
| **Root Cause** | `asyncio.create_task(result)` without storing reference or error handling |
| **Reproduction** | 1. Callback raises exception 2. Exception lost, task orphaned 3. Over time, GC issues |
| **Fix** | Track tasks in a set, add done callback for error logging |
| **Code** | See below |
| **Risk if Unfixed** | Silent data loss, memory growth, debugging nightmares |

```python
# CURRENT (futures_monitor.py:1686-1690):
if asyncio.iscoroutine(result):
    asyncio.create_task(result)  # FIRE AND FORGET

# FIX:
class FuturesMonitor:
    def __init__(self):
        ...
        self._pending_callbacks: set[asyncio.Task] = set()

    async def _emit_detection(self, detection):
        ...
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            self._pending_callbacks.add(task)
            task.add_done_callback(self._callback_done)

    def _callback_done(self, task: asyncio.Task):
        self._pending_callbacks.discard(task)
        if task.exception():
            logger.error("callback_failed", error=str(task.exception()))
```

---

### ISSUE-002: Unbounded Dict Growth in Signal Generator
| Field | Value |
|-------|-------|
| **ID** | ISSUE-002 |
| **Severity** | CRITICAL |
| **Location** | `signal_generator.py:528-537` |
| **Symptom** | Memory growth over days/weeks of operation |
| **Root Cause** | Cleanup only happens when `_record_signal()` is called; if no signals for days, old entries persist |
| **Reproduction** | 1. Run system for 48h with no signals 2. `_recent_signals` retains all entries |
| **Fix** | Periodic cleanup task or cleanup on every read |
| **Code** | See below |
| **Risk if Unfixed** | OOM after extended operation |

```python
# CURRENT (signal_generator.py:528-537):
def _record_signal(self, symbol: str) -> None:
    self._recent_signals[symbol] = datetime.now()
    # Cleanup only happens HERE
    cutoff = datetime.now() - timedelta(hours=24)
    self._recent_signals = {s: t for s, t in self._recent_signals.items() if t > cutoff}

# FIX: Also cleanup on _is_recent_signal check:
def _is_recent_signal(self, symbol: str) -> bool:
    # Cleanup first (amortized)
    if len(self._recent_signals) > 1000:  # Threshold
        cutoff = datetime.now() - timedelta(hours=24)
        self._recent_signals = {s: t for s, t in self._recent_signals.items() if t > cutoff}

    last_signal = self._recent_signals.get(symbol)
    if not last_signal:
        return False
    return (datetime.now() - last_signal) < timedelta(hours=1)
```

---

### ISSUE-003: OI History Retention Window Mismatch
| Field | Value |
|-------|-------|
| **ID** | ISSUE-003 |
| **Severity** | HIGH |
| **Location** | `futures_monitor.py:917-923` |
| **Symptom** | `oi_change_1h_pct` may be 0 or incorrect at edge cases |
| **Root Cause** | OI history trimmed to 75min, but `_find_oi_at_time` uses 2min tolerance. If OI update is delayed, 60min-ago record may be trimmed |
| **Reproduction** | 1. OI updates every 60s (default) 2. At minute 61, looking for data at minute 0 3. If API was slow at minute 0, that record is at minute 2 4. 75min - 2min tolerance = record at minute 73 max, but need minute 60 |
| **Fix** | Increase retention to 80min or increase tolerance |
| **Code** | `cutoff = datetime.now() - timedelta(hours=1, minutes=20)` |
| **Risk if Unfixed** | False 0% OI change readings, missed whale accumulation signals |

---

### ISSUE-004: WebSocket Reconnection Data Gap
| Field | Value |
|-------|-------|
| **ID** | ISSUE-004 |
| **Severity** | HIGH |
| **Location** | `realtime_monitor.py:320-370` |
| **Symptom** | Data loss or duplication during reconnection |
| **Root Cause** | No sequence tracking; reconnection doesn't know what was missed |
| **Reproduction** | 1. WebSocket disconnects 2. Reconnect takes 2-5 seconds 3. Trades during gap are lost 4. Volume calculations are wrong for that minute |
| **Fix** | Use REST API to backfill missing data on reconnect OR track lastUpdateId for depth |
| **Risk if Unfixed** | Incorrect volume spike detection, missed manipulation events |

---

### ISSUE-005: O(n) Detection Cache Cleanup
| Field | Value |
|-------|-------|
| **ID** | ISSUE-005 |
| **Severity** | HIGH |
| **Location** | `accumulation_detector.py:112-117` |
| **Symptom** | CPU spikes when many symbols and many detections |
| **Root Cause** | List comprehension filters all entries on every `add_detection()` call |
| **Reproduction** | 1. 50 symbols, each getting 10 detections/min 2. 500 add_detection calls/min 3. Each filters through potentially 500+ entries |
| **Fix** | Use deque with maxlen, or timestamp-indexed dict with periodic cleanup |
| **Code** | See below |
| **Risk if Unfixed** | Latency in detection processing during high-activity periods |

```python
# CURRENT:
self._recent_detections[symbol] = [
    d for d in self._recent_detections[symbol]
    if d["timestamp"] > cutoff
]

# FIX: Use deque with maxlen
from collections import deque
self._recent_detections: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
```

---

### ISSUE-006: Inconsistent ATR Clamp Values
| Field | Value |
|-------|-------|
| **ID** | ISSUE-006 |
| **Severity** | MEDIUM |
| **Location** | `futures_monitor.py:750`, `realtime_monitor.py:658` |
| **Symptom** | Different ATR minimums could cause SPOT/FUTURES divergence analysis to mismatch |
| **Root Cause** | Both have FIX-3/FIX-4 comments changing min from 3% to 1%, but applied at different times |
| **Reproduction** | Compare ATR values for same symbol in SPOT vs FUTURES state |
| **Fix** | Extract to shared constant in config |
| **Risk if Unfixed** | Orderbook imbalance comparisons may be inconsistent |

---

### ISSUE-007: Detection Dict Memory Between Hourly Cleanups
| Field | Value |
|-------|-------|
| **ID** | ISSUE-007 |
| **Severity** | MEDIUM |
| **Location** | `detection_engine.py:652-656`, `futures_monitor.py:1660-1665` |
| **Symptom** | Memory growth during active trading hours |
| **Root Cause** | Cleanup only removes entries >1h old; during high activity, dict grows until cleanup |
| **Reproduction** | 50 symbols x 10 detection types x active market = 500 entries/hour |
| **Fix** | Add maxsize check before insertion |
| **Risk if Unfixed** | Gradual memory increase during volatile markets |

---

### ISSUE-008: Swallowed Exception in Signal Generation
| Field | Value |
|-------|-------|
| **ID** | ISSUE-008 |
| **Severity** | MEDIUM |
| **Location** | `signal_generator.py:408-410` |
| **Symptom** | Silent signal generation failures |
| **Root Cause** | `except Exception as e: logger.error(...)` then returns None without re-raising or detailed logging |
| **Reproduction** | 1. `risk_calculator.calculate()` raises 2. Signal silently fails 3. User sees no signal for valid setup |
| **Fix** | Log full traceback, consider re-raising or returning error indicator |
| **Risk if Unfixed** | Missed trading opportunities, silent failures hard to debug |

---

### ISSUE-009: Stop Loss May Be Too Tight
| Field | Value |
|-------|-------|
| **ID** | ISSUE-009 |
| **Severity** | MEDIUM |
| **Location** | `risk_calculator.py:248` |
| **Symptom** | SL triggers prematurely on volatile assets |
| **Root Cause** | `sl_pct = max(self.config.default_sl_pct, volatility_pct * 1.2)` - 1.2x multiplier may be insufficient |
| **Reproduction** | Asset with 10% ATR gets 12% SL, but 1.5-2x ATR is standard practice |
| **Fix** | Consider `sl_pct = max(self.config.default_sl_pct, volatility_pct * 1.5)` |
| **Risk if Unfixed** | Increased stop-outs, lower win rate |

---

### ISSUE-010: AccumulationScore Total Capped at 100
| Field | Value |
|-------|-------|
| **ID** | ISSUE-010 |
| **Severity** | LOW |
| **Location** | `models.py:118` |
| **Symptom** | Extreme setups (score 130+) indistinguishable from 100 |
| **Root Cause** | `return max(0, min(100, positive + negative))` |
| **Reproduction** | All positive factors fire = 140 points, but returns 100 |
| **Fix** | Store raw score separately, use 100 cap only for display |
| **Risk if Unfixed** | Loss of signal strength differentiation |

---

### ISSUE-011: Hard-coded Funding History Limit
| Field | Value |
|-------|-------|
| **ID** | ISSUE-011 |
| **Severity** | LOW |
| **Location** | `futures_monitor.py:1007-1010` |
| **Symptom** | Cannot analyze funding trends beyond 8 days |
| **Root Cause** | `if len(state.funding_history) > 24: state.funding_history = state.funding_history[-24:]` |
| **Fix** | Make configurable in settings.yaml |
| **Risk if Unfixed** | Limited historical analysis capability |

---

### ISSUE-012: Decimal Precision Loss
| Field | Value |
|-------|-------|
| **ID** | ISSUE-012 |
| **Severity** | LOW |
| **Location** | Multiple files (detection_engine.py, signal_generator.py, etc.) |
| **Symptom** | Subtle rounding errors in financial calculations |
| **Root Cause** | `float(state.some_decimal)` conversions without explicit rounding |
| **Fix** | Use `Decimal.quantize()` for financial values, float only for display |
| **Risk if Unfixed** | Accumulated rounding errors, especially in TP/SL calculations |

---

### ISSUE-013: Magic Numbers for Minimum Volumes
| Field | Value |
|-------|-------|
| **ID** | ISSUE-013 |
| **Severity** | LOW |
| **Location** | `accumulation_detector.py:70-71` |
| **Symptom** | Cannot tune liquidity thresholds without code changes |
| **Root Cause** | `MIN_SPOT_VOLUME_USD = 1000` hard-coded |
| **Fix** | Move to SignalConfig or settings.yaml |
| **Risk if Unfixed** | Inflexible for different market conditions |

---

### ISSUE-014: Debug-Level Exception Logging
| Field | Value |
|-------|-------|
| **ID** | ISSUE-014 |
| **Severity** | INFO |
| **Location** | `realtime_monitor.py:443`, `futures_monitor.py:493` |
| **Symptom** | Production issues invisible without debug logging enabled |
| **Root Cause** | `logger.debug("trade_callback_error", error=str(e))` |
| **Fix** | Change to `logger.warning()` for callback failures |
| **Risk if Unfixed** | Harder to diagnose production issues |

---

### ISSUE-015: No Observability Metrics
| Field | Value |
|-------|-------|
| **ID** | ISSUE-015 |
| **Severity** | INFO |
| **Location** | All detector classes |
| **Symptom** | Cannot monitor system health without log parsing |
| **Root Cause** | No counters/gauges for detections/min, signals/hour, etc. |
| **Fix** | Add prometheus_client or similar metrics |
| **Risk if Unfixed** | Operational blindness, slow incident response |

---

## D) TECH DEBT REGISTER

| ID | Category | Location | Description | Effort |
|----|----------|----------|-------------|--------|
| TD-001 | Code Duplication | `futures_monitor.py:706-752`, `realtime_monitor.py:615-661` | ATR calculation duplicated | 2h |
| TD-002 | Configuration | Multiple files | Thresholds scattered between config.yaml and class constants | 4h |
| TD-003 | Type Safety | All files | Optional fields returned as None vs 0 inconsistently | 3h |
| TD-004 | Testing | N/A | No unit tests for detectors (mentioned in summary) | 16h |
| TD-005 | Documentation | models.py | Docstrings in Russian, should be English for international team | 2h |
| TD-006 | Error Handling | signal_generator.py:271-410 | Large try/except block, should be split | 2h |
| TD-007 | Async Patterns | All monitors | Mixed callback/async patterns, should standardize | 8h |
| TD-008 | Decimal Usage | risk_calculator.py | Mix of Decimal and float in calculations | 4h |

---

## E) WORK PLAN (Roadmap)

### Phase 1: Quick Wins (1-2 days)
1. **ISSUE-014**: Change debug->warning for callback errors (15min)
2. **ISSUE-006**: Extract ATR clamp to shared constant (30min)
3. **ISSUE-011**: Make funding_history limit configurable (30min)
4. **ISSUE-013**: Move MIN_VOLUME constants to config (30min)
5. **ISSUE-002**: Add cleanup check in `_is_recent_signal()` (30min)

### Phase 2: Medium Fixes (3-5 days)
1. **ISSUE-001**: Implement task tracking for async callbacks (2h)
2. **ISSUE-003**: Increase OI history retention window (1h)
3. **ISSUE-005**: Replace list with deque for detection cache (2h)
4. **ISSUE-007**: Add maxsize checks to dedup dicts (2h)
5. **ISSUE-008**: Improve exception logging in signal generation (1h)
6. **TD-001**: Extract shared ATR calculator (2h)

### Phase 3: Larger Fixes (1-2 weeks)
1. **ISSUE-004**: Implement WebSocket reconnection backfill (8h)
2. **TD-004**: Write unit tests for DetectionEngine (8h)
3. **TD-007**: Standardize async patterns (8h)
4. **ISSUE-015**: Add Prometheus metrics (4h)
5. **TD-008**: Standardize Decimal usage in risk calculations (4h)

### Phase 4: Future Enhancements
1. Circuit breaker for cascading API failures
2. Rate limiter metrics and alerting
3. A/B testing infrastructure for threshold tuning
4. Backtest integration with signal_logger JSONL files

---

## F) MINIMUM TEST SET

### Unit Tests Required

```python
# tests/test_detection_engine.py
class TestDetectionEngine:
    def test_volume_spike_detection_with_baseline(self):
        """Given avg_volume_1h=1000, volume_5m=5000, expect VOLUME_SPIKE_HIGH"""

    def test_volume_spike_no_baseline_no_detection(self):
        """Given avg_volume_1h=0, expect no volume detection (divide by zero safe)"""

    def test_wash_trading_detection(self):
        """Given 80% trades same qty, expect WASH_TRADING_LIKELY"""

    def test_orderbook_imbalance_atr_based(self):
        """Given ATR=5%, bid within 5% > ask within 5%, expect ORDERBOOK_IMBALANCE"""

    def test_deduplication_exact_match_5min(self):
        """Same detection within 5min should be filtered"""

    def test_deduplication_same_type_3sec(self):
        """Different params same type within 3sec should be filtered"""

    def test_buy_ratio_none_handling(self):
        """When trades_5m is empty, buy_ratio should be None not 0.5"""

# tests/test_futures_monitor.py
class TestFuturesMonitor:
    def test_oi_change_calculation_1h(self):
        """Given OI history 60min ago, calculate correct oi_change_1h_pct"""

    def test_oi_change_missing_history(self):
        """Given no 60min-old OI, oi_change_1h_pct should be 0"""

    def test_funding_gradient_calculation(self):
        """Given 3 funding records, calculate correct gradient"""

    def test_whale_accumulation_detection(self):
        """Given OI+15%, price stable, funding neutral, expect WHALE_ACCUMULATION_STEALTH"""

    def test_oi_price_divergence_weak_pump(self):
        """Given price+5%, OI-3%, expect WEAK_PUMP_DIVERGENCE"""

# tests/test_signal_generator.py
class TestSignalGenerator:
    def test_cooldown_prevents_spam(self):
        """Second signal for same symbol within 1h should be blocked"""

    def test_accumulation_score_calculation(self):
        """Given all positive factors, score should be capped at 100"""

    def test_direction_from_orderbook(self):
        """Strong bid imbalance should result in LONG direction"""

# tests/test_risk_calculator.py
class TestRiskCalculator:
    def test_sl_respects_atr(self):
        """SL should be at least 0.8x ATR"""

    def test_tp_ratios(self):
        """TP1/2/3 should be at configured ratios from risk"""

    def test_price_rounding_btc(self):
        """BTC price >10k should round to $1"""

    def test_price_rounding_altcoin(self):
        """Altcoin <$1 should round to 6 decimals"""

# tests/test_realtime_monitor.py
class TestRealTimeMonitor:
    def test_atr_calculation(self):
        """Given 60 klines, ATR should be calculated correctly"""

    def test_atr_clamp_minimum(self):
        """ATR should not go below 1%"""

    def test_volume_baseline_warmup(self):
        """After warmup, avg_volume_1h should be set"""
```

### Integration Tests Required

```python
# tests/integration/test_detection_flow.py
async def test_full_detection_to_signal_flow():
    """
    1. Inject mock WebSocket trade data
    2. Verify DetectionEngine produces detection
    3. Verify SignalGenerator produces signal
    4. Verify RiskCalculator produces valid levels
    """

async def test_deduplication_across_components():
    """
    1. Generate detection
    2. Verify FuturesMonitor dedup blocks duplicate
    3. Verify DetectionEngine dedup blocks duplicate
    4. Verify only one signal generated
    """

async def test_rate_limit_recovery():
    """
    1. Mock 429 response
    2. Verify backoff applied
    3. Verify system recovers after backoff
    """
```

### Manual Test Scenarios

| Scenario | Steps | Expected |
|----------|-------|----------|
| Warmup works | 1. Start with 10 symbols 2. Check logs for "baseline_warmup_complete" | avg_volume_1h > 0 for all |
| WebSocket reconnect | 1. Disable network 2. Re-enable after 10s | "websocket_connected" log, data resumes |
| Rate limit handling | 1. Trigger 429 2. Check backoff | Exponential delay, no crash |
| Signal dedup | 1. Generate same condition twice in 30min | Only one signal |

---

## Appendix: Files Reviewed

1. `src/signals/models.py` (317 lines) - Data models
2. `src/signals/risk_calculator.py` (405 lines) - Risk calculations
3. `src/signals/signal_generator.py` (538 lines) - Signal generation
4. `src/signals/accumulation_detector.py` (722 lines) - Whale detection
5. `src/screener/models.py` (357 lines) - Core state models
6. `src/screener/realtime_monitor.py` (797 lines) - SPOT WebSocket
7. `src/screener/futures_monitor.py` (1880 lines) - Futures monitoring
8. `src/screener/detection_engine.py` (667 lines) - Pattern detection

**Total Lines Reviewed**: ~5,683 lines

---

*End of Audit Report*
