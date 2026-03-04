# Cross-Exchange Detection Module

This module provides cross-exchange manipulation detection capabilities for BinanceFriend.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ManipulationScreener                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     ExchangeManager                           │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│   │
│  │  │ Binance │ │  Bybit  │ │   OKX   │ │ Bitget  │ │  ...    ││   │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘│   │
│  │       │           │           │           │           │      │   │
│  │       └───────────┴───────────┴───────────┴───────────┘      │   │
│  │                            │                                  │   │
│  │                     on_trade / on_orderbook                   │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  CrossExchangeStateStore                      │   │
│  │  ┌─────────────────────────────────────────────────────────┐ │   │
│  │  │ prices: Dict[exchange][symbol] -> PricePoint            │ │   │
│  │  │ orderbooks: Dict[exchange][symbol] -> UnifiedOrderBook  │ │   │
│  │  │ funding: Dict[exchange][symbol] -> FundingPoint         │ │   │
│  │  │ oi: Dict[exchange][symbol] -> OIPoint                   │ │   │
│  │  └─────────────────────────────────────────────────────────┘ │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  DetectorOrchestrator                         │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│   │
│  │  │ CX-001  │ │ CX-002  │ │ CX-003  │ │ CX-004  │ │ CX-005  ││   │
│  │  │ Price   │ │ Volume  │ │ Funding │ │   OI    │ │Liquidity││   │
│  │  │Diverge  │ │  Corr   │ │   Arb   │ │Migration│ │  Hunt   ││   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘│   │
│  │  ┌─────────┐                                                 │   │
│  │  │ CX-006  │                                                 │   │
│  │  │Spoofing │                                                 │   │
│  │  └─────────┘                                                 │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│                        AlertDispatcher                               │
│                               │                                      │
│                    ┌──────────┴──────────┐                          │
│                    ▼                     ▼                          │
│              Local Log              Telegram                        │
└─────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. ExchangeManager (`src/exchanges/manager.py`)

Manages connections to all exchanges.

```python
from src.exchanges.manager import ExchangeManager
from config.settings import settings

manager = ExchangeManager(settings.exchanges)

# Connect all enabled exchanges
await manager.connect_all()

# Register callbacks
manager.on_trade(lambda exchange, trade: print(f"{exchange}: {trade}"))
manager.on_orderbook(lambda exchange, ob: print(f"{exchange}: {ob}"))

# Subscribe to symbols
await manager.subscribe_symbols(["BTC/USDT", "ETH/USDT"])

# Get stats
print(manager.get_stats())

# Cleanup
await manager.disconnect_all()
```

### 2. StateStore (`src/cross_exchange/state_store.py`)

Thread-safe state storage for cross-exchange data.

```python
from src.cross_exchange.state_store import StateStore

store = StateStore()

# Update price from exchange
store.update_price("binance", "BTC/USDT", Decimal("50000"), datetime.now())
store.update_price("bybit", "BTC/USDT", Decimal("50010"), datetime.now())

# Get cross-exchange view
cross_price = store.get_cross_price("BTC/USDT")
print(f"Spread: {cross_price.spread_pct}%")
print(f"VWAP: {cross_price.vwap}")
print(f"Prices: {cross_price.prices}")

# Get common symbols across all exchanges
common = store.common_symbols()
```

### 3. DetectorOrchestrator (`src/cross_exchange/correlator.py`)

Runs all detectors on symbols.

```python
from src.cross_exchange.correlator import DetectorOrchestrator, DetectorOrchestratorConfig

config = DetectorOrchestratorConfig(
    enable_price_divergence=True,
    enable_volume_correlation=True,
    enable_funding_arbitrage=True,
    enable_oi_migration=True,
    enable_liquidity_hunt=True,
    enable_spoofing_cross=True,
    min_severity="WARNING",
)

orchestrator = DetectorOrchestrator(store, config)

# Analyze single symbol
detections = await orchestrator.analyze_symbol("BTC/USDT")

# Analyze all symbols
all_detections = await orchestrator.analyze_all()
```

## Detectors

| ID | Name | Description | Thresholds |
|---|---|---|---|
| CX-001 | Price Divergence | Price difference between exchanges | WARNING: 0.5%, ALERT: 1%, CRITICAL: 2% |
| CX-002 | Volume Correlation | Wash trading detection via volume patterns | WARNING: r>0.8, ALERT: r>0.9, CRITICAL: r>0.95 |
| CX-003 | Funding Arbitrage | Funding rate divergence | WARNING: 0.03%, ALERT: 0.05%, CRITICAL: 0.1% |
| CX-004 | OI Migration | Open Interest movement between exchanges | WARNING: 10%, ALERT: 20%, CRITICAL: 30% |
| CX-005 | Liquidity Hunt | Price manipulation for liquidation cascade | WARNING: 2%, ALERT: 3%, CRITICAL: 5% |
| CX-006 | Spoofing Cross | Fake orders on one exchange, execution on another | Confidence-based |

## Adding a New Exchange

### Step 1: Create Connector

```python
# src/exchanges/newexchange/connector.py

from src.exchanges.base import BaseExchange, ExchangeConfig, ExchangeCapability

class NewExchangeConnector(BaseExchange):
    EXCHANGE_NAME = "newexchange"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.SPOT_TRADING,
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
    }

    async def connect(self):
        # Implement WebSocket connection
        pass

    async def disconnect(self):
        # Implement graceful disconnect
        pass

    async def subscribe_trades(self, symbols, callback=None):
        # Implement trade subscription
        pass

    async def subscribe_orderbook(self, symbols, callback=None, depth=20):
        # Implement orderbook subscription
        pass

    def normalize_symbol(self, raw_symbol):
        # Convert exchange format to unified format
        # e.g., "BTCUSDT" -> "BTC/USDT"
        pass

    def normalize_trade(self, raw):
        # Convert raw trade data to UnifiedTrade
        pass

    def normalize_orderbook(self, raw):
        # Convert raw orderbook to UnifiedOrderBook
        pass
```

### Step 2: Register in ExchangeManager

```python
# src/exchanges/manager.py

# Add import
from src.exchanges.newexchange.connector import NewExchangeConnector

# Add to connector_classes dict in _init_connectors()
connector_classes = {
    ...
    "newexchange": NewExchangeConnector,
}
```

### Step 3: Add Configuration

```yaml
# config/config.yaml

exchanges:
  newexchange:
    enabled: true
    type: CEX
    ws_url: "wss://api.newexchange.com/ws"
    rest_url: "https://api.newexchange.com"
    rate_limit:
      requests_per_second: 10
      requests_per_minute: 300
```

## Adding a New Detector

### Step 1: Create Detector

```python
# src/cross_exchange/detectors/new_detector.py

from src.cross_exchange.detectors.base import (
    BaseCrossDetector,
    Detection,
    DetectionType,
    DetectorConfig,
    Severity,
)

@dataclass
class NewDetectorConfig(DetectorConfig):
    enabled: bool = True
    dedup_seconds: int = 60
    warning_threshold: float = 0.5
    alert_threshold: float = 0.7
    critical_threshold: float = 0.9

class NewDetector(BaseCrossDetector):
    DETECTION_TYPE = DetectionType.NEW_PATTERN  # Add to enum
    NAME = "new_detector"

    def __init__(self, config: NewDetectorConfig = None):
        super().__init__(config or NewDetectorConfig())
        self.config: NewDetectorConfig = self.config

    async def analyze(self, symbol: str, state: "StateStore") -> Optional[Detection]:
        # Get data from state store
        # Analyze pattern
        # Return Detection if pattern found
        pass
```

### Step 2: Register in Orchestrator

```python
# src/cross_exchange/correlator.py

# Add import
from src.cross_exchange.detectors.new_detector import NewDetector

# Add to _init_detectors()
if self.config.enable_new_detector:
    self._detectors.append(NewDetector())
```

### Step 3: Update Configuration

```yaml
# config/config.yaml

cross_exchange:
  new_detector:
    enabled: true
    warning_threshold: 0.5
    alert_threshold: 0.7
    critical_threshold: 0.9
```

## Configuration

All configuration is in `config/config.yaml`:

```yaml
cross_exchange:
  # Price Divergence (CX-001)
  price_divergence:
    threshold_low: 0.1
    threshold_medium: 0.3
    threshold_high: 0.5
    threshold_critical: 1.0

  # Volume Correlation (CX-002)
  volume_correlation:
    suspicious_threshold: 0.95
    min_data_points: 60

  # Funding Arbitrage (CX-003)
  funding_arbitrage:
    threshold_low: 0.01
    threshold_medium: 0.03
    threshold_high: 0.05
    threshold_critical: 0.1

  # OI Divergence (CX-004)
  oi_divergence:
    threshold_low: 5
    threshold_medium: 10
    threshold_high: 20
    threshold_critical: 30

  # Liquidity Hunt (CX-005)
  liquidity_hunt:
    enabled: true
    price_drop_threshold: 0.02
    recovery_window_sec: 300

  # Spoofing Cross (CX-006)
  spoofing_cross:
    enabled: true
    imbalance_threshold: 0.80
    wall_lifetime_sec: 30

  # Orchestrator
  orchestrator:
    enable_price_divergence: true
    enable_volume_correlation: true
    enable_funding_arbitrage: true
    enable_oi_migration: true
    enable_liquidity_hunt: true
    enable_spoofing_cross: true
    parallel_analysis: true
    max_concurrent_symbols: 50
    min_severity: "WARNING"

  # General
  general:
    min_exchanges: 2
    max_data_age_sec: 60
    check_interval_sec: 5
```

## Testing

Run tests:

```bash
# All cross-exchange tests
pytest tests/test_cross_exchange_integration.py -v

# Specific test
pytest tests/test_cross_exchange_integration.py::TestStateStoreIntegration -v

# With coverage
pytest tests/test_cross_exchange_integration.py --cov=src/cross_exchange
```

## Performance Considerations

1. **Memory**: StateStore uses `deque(maxlen=N)` to limit memory usage
2. **Concurrency**: Uses `asyncio.Lock` for thread-safe updates
3. **Rate Limiting**: Each exchange connector has built-in rate limiting
4. **Deduplication**: Detectors have configurable dedup intervals to avoid alert spam

## Troubleshooting

### Exchange Connection Failed

Check:
1. Exchange is enabled in `config/config.yaml`
2. WebSocket/REST URLs are correct
3. Rate limits aren't exceeded
4. Network connectivity

### No Detections Generated

Check:
1. At least 2 exchanges are connected
2. Symbols are subscribed
3. Detector is enabled in orchestrator config
4. min_severity isn't filtering all detections

### High Memory Usage

Reduce:
- `history_size` in detector configs
- `maxlen` in StateStore deques
- Number of subscribed symbols
