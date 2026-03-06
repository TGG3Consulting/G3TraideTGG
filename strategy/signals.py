"""Signal generation for trading strategy.

Generates BUY/SELL signals based on MA crossovers.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Union

from src.core.config import BotConfig
from src.core.logging import get_logger
from src.core.types import Candle, MAType, Signal, SignalType
from src.exchange.kline_buffer import KlineBuffer
from src.strategy.crossover import CrossoverDetector, CrossoverType
from src.strategy.indicators import EMA, SMA, MAValue

logger = get_logger(__name__)


class SignalGenerator:
    """Generates trading signals from candlestick data.

    Uses MA crossover strategy:
    - BUY signal on golden cross (fast MA > slow MA)
    - SELL signal on death cross (fast MA < slow MA)

    Features:
    - Configurable fast/slow MA types (SMA/EMA)
    - Configurable periods
    - min_signal_gap support (REQ-020)
    - Signal price = candle close price (REQ-032)
    - tester_arithmetic mode for C++ tester compatibility
    """

    def __init__(
        self,
        bot_config: BotConfig,
        session_manager: "SessionManager | None" = None,
        tester_arithmetic: bool = False,
    ) -> None:
        """Initialize signal generator.

        Args:
            bot_config: Bot configuration with MA settings.
            session_manager: Optional session manager for signal IDs.
            tester_arithmetic: If True, use float arithmetic without rounding.
        """
        self._config = bot_config
        self._session_manager = session_manager
        self._tester_arithmetic = tester_arithmetic

        # Initialize MA calculators with arithmetic mode
        if bot_config.fast_type == MAType.SMA:
            self._fast_ma = SMA(bot_config.fast_period, tester_arithmetic=tester_arithmetic)
        else:
            self._fast_ma = EMA(bot_config.fast_period, tester_arithmetic=tester_arithmetic)

        if bot_config.slow_type == MAType.SMA:
            self._slow_ma = SMA(bot_config.slow_period, tester_arithmetic=tester_arithmetic)
        else:
            self._slow_ma = EMA(bot_config.slow_period, tester_arithmetic=tester_arithmetic)

        # Initialize crossover detector
        self._crossover = CrossoverDetector()

        # Track signal timing for min_signal_gap
        self._last_signal_bar: int = -999999
        self._bar_count: int = 0

        # Current MA values (can be Decimal or float depending on mode)
        self._current_fast_ma: MAValue = None
        self._current_slow_ma: MAValue = None

        # Signal counter (if no session manager)
        self._signal_counter: int = 0

        # Track last signal type for alternation enforcement
        self._last_signal_type: SignalType | None = None

    @property
    def bot_id(self) -> str:
        """Get bot ID."""
        return self._config.bot_id

    @property
    def symbol(self) -> str:
        """Get trading symbol."""
        return self._config.symbol

    @property
    def fast_ma_value(self) -> Decimal | None:
        """Get current fast MA value."""
        return self._current_fast_ma

    @property
    def slow_ma_value(self) -> Decimal | None:
        """Get current slow MA value."""
        return self._current_slow_ma

    @property
    def is_ready(self) -> bool:
        """Check if generator has enough data to produce signals."""
        return self._fast_ma.is_ready and self._slow_ma.is_ready

    def get_ma_values(self) -> tuple[Decimal | None, Decimal | None]:
        """Get current MA values.

        Returns:
            Tuple of (fast_ma, slow_ma).
        """
        return (self._current_fast_ma, self._current_slow_ma)

    def initialize_from_buffer(self, buffer: KlineBuffer) -> None:
        """Initialize MA calculators from historical candle buffer.

        Args:
            buffer: Kline buffer with historical candles.
        """
        closes = buffer.get_closes()

        # Feed prices to MA calculators
        for close in closes:
            self._fast_ma.update(close)
            self._slow_ma.update(close)
            self._bar_count += 1

        self._current_fast_ma = self._fast_ma.value
        self._current_slow_ma = self._slow_ma.value

        # Initialize crossover detector with current values
        if self._current_fast_ma is not None and self._current_slow_ma is not None:
            self._crossover.set_previous(self._current_fast_ma, self._current_slow_ma)

        logger.info(
            "Signal generator initialized from buffer",
            bot_id=self.bot_id,
            candles=len(closes),
            fast_ma=float(self._current_fast_ma) if self._current_fast_ma else None,
            slow_ma=float(self._current_slow_ma) if self._current_slow_ma else None,
            is_ready=self.is_ready,
        )

    def on_candle_close(self, candle: Candle) -> Signal | None:
        """Process a closed candle and generate signal if crossover.

        Args:
            candle: Closed candle.

        Returns:
            Signal if crossover detected, None otherwise.

        Note:
            Signal price = candle close price (REQ-032).
            Only generates signal for closed candles.
        """
        if not candle.is_closed:
            return None

        # Validate symbol matches
        if candle.symbol != self.symbol:
            logger.warning(
                "Candle symbol mismatch",
                expected=self.symbol,
                got=candle.symbol,
            )
            return None

        # Update MA calculators
        self._current_fast_ma = self._fast_ma.update(candle.close)
        self._current_slow_ma = self._slow_ma.update(candle.close)

        # Increment bar count
        self._bar_count += 1

        # Check if MAs are ready
        if self._current_fast_ma is None or self._current_slow_ma is None:
            return None

        # Detect crossover
        crossover = self._crossover.update(
            self._current_fast_ma, self._current_slow_ma
        )

        if crossover == CrossoverType.NONE:
            return None

        # Determine signal type from crossover
        signal_type = (
            SignalType.BUY if crossover == CrossoverType.BULLISH else SignalType.SELL
        )

        # Check alternation - reject consecutive same signals (PROD-AUDIT FIX-001)
        if self._last_signal_type == signal_type:
            logger.debug(
                "Signal suppressed - same type as last signal",
                bot_id=self.bot_id,
                signal_type=signal_type.value,
            )
            return None

        # Check min_signal_gap (REQ-020)
        bars_since_last = self._bar_count - self._last_signal_bar
        if bars_since_last < self._config.min_signal_gap:
            logger.debug(
                "Signal suppressed by min_signal_gap",
                bot_id=self.bot_id,
                bars_since_last=bars_since_last,
                min_gap=self._config.min_signal_gap,
            )
            return None

        # Get signal ID
        signal_id = self._get_next_signal_id()

        # Update last signal bar and type
        self._last_signal_bar = self._bar_count
        self._last_signal_type = signal_type

        signal = Signal(
            signal_id=signal_id,
            bot_id=self.bot_id,
            symbol=self.symbol,
            signal_type=signal_type,
            price=candle.close,  # Signal price = close price (REQ-032)
            candle_time=candle.close_time,
            fast_ma=self._current_fast_ma,
            slow_ma=self._current_slow_ma,
            created_at=datetime.now(UTC),
        )

        logger.info(
            "Signal generated",
            bot_id=self.bot_id,
            signal_id=signal_id,
            type=signal_type.value,
            price=float(candle.close),
            fast_ma=float(self._current_fast_ma),
            slow_ma=float(self._current_slow_ma),
        )

        return signal

    def _get_next_signal_id(self) -> int:
        """Get next signal ID.

        Uses session manager if available, otherwise internal counter.

        Returns:
            Next signal ID.
        """
        if self._session_manager:
            return self._session_manager.get_next_signal_id(self.bot_id)

        self._signal_counter += 1
        return self._signal_counter

    def reset(self) -> None:
        """Reset generator state."""
        self._fast_ma.reset()
        self._slow_ma.reset()
        self._crossover.reset()
        self._last_signal_bar = -999999
        self._bar_count = 0
        self._current_fast_ma = None
        self._current_slow_ma = None
        self._signal_counter = 0
        self._last_signal_type = None


# Import here to avoid circular import
from src.core.session import SessionManager  # noqa: E402
