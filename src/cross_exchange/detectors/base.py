# -*- coding: utf-8 -*-
"""
Base Cross-Exchange Detector.

Provides common interface and utilities for all cross-exchange detectors.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.cross_exchange.state_store import StateStore

logger = structlog.get_logger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class Severity(Enum):
    """Detection severity levels."""
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"
    CRITICAL = "critical"

    def __lt__(self, other: "Severity") -> bool:
        order = [Severity.INFO, Severity.WARNING, Severity.ALERT, Severity.CRITICAL]
        return order.index(self) < order.index(other)


class DetectionType(Enum):
    """Types of cross-exchange detections."""
    PRICE_DIVERGENCE = "CX-001_PRICE_DIVERGENCE"
    VOLUME_CORRELATION = "CX-002_VOLUME_CORRELATION"
    FUNDING_ARBITRAGE = "CX-003_FUNDING_ARBITRAGE"
    OI_MIGRATION = "CX-004_OI_MIGRATION"
    LIQUIDITY_HUNT = "CX-005_LIQUIDITY_HUNT"
    SPOOFING_CROSS = "CX-006_SPOOFING_CROSS"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Detection:
    """
    A detected cross-exchange manipulation pattern.

    Attributes:
        detection_type: Type of detection (CX-XXX code)
        severity: How serious this detection is
        symbol: Affected trading pair
        timestamp: When detected
        exchanges: List of involved exchanges
        description: Human-readable description
        details: Additional data for analysis
        confidence: Detection confidence (0.0 - 1.0)
        recommended_action: What to do about it
    """
    detection_type: DetectionType
    severity: Severity
    symbol: str
    timestamp: datetime
    exchanges: List[str]
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    recommended_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "type": self.detection_type.value,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "exchanges": self.exchanges,
            "description": self.description,
            "details": self._serialize_details(),
            "confidence": self.confidence,
            "recommended_action": self.recommended_action,
        }

    def _serialize_details(self) -> Dict[str, Any]:
        """Serialize details, converting Decimal to string."""
        result = {}
        for key, value in self.details.items():
            if isinstance(value, Decimal):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = {
                    k: str(v) if isinstance(v, Decimal) else v
                    for k, v in value.items()
                }
            else:
                result[key] = value
        return result

    def __str__(self) -> str:
        return (
            f"[{self.severity.value.upper()}] {self.detection_type.value} "
            f"on {self.symbol}: {self.description}"
        )


@dataclass
class DetectorConfig:
    """
    Base configuration for detectors.

    Subclass this for specific detector configurations.
    """
    enabled: bool = True
    dedup_seconds: int = 30  # Minimum time between same alerts

    # Thresholds (override in subclasses)
    warning_threshold: float = 0.0
    alert_threshold: float = 0.0
    critical_threshold: float = 0.0


# =============================================================================
# BASE DETECTOR
# =============================================================================

class BaseCrossDetector(ABC):
    """
    Abstract base class for cross-exchange detectors.

    All detectors must implement the `analyze` method.
    Provides common utilities for deduplication, logging, etc.
    """

    # Override in subclasses
    DETECTION_TYPE: DetectionType = None
    NAME: str = "base_detector"

    # Smart deduplication constants
    DEDUP_EXACT_MATCH_SEC = 300   # 5 минут для полного дубля
    DEDUP_SAME_TYPE_SEC = 3       # 3 секунды для того же типа с другими параметрами

    def __init__(self, config: DetectorConfig):
        """
        Initialize detector.

        Args:
            config: Detector-specific configuration
        """
        self.config = config
        # {symbol: (timestamp, fingerprint)}
        self._last_alerts: Dict[str, Tuple[datetime, str]] = {}
        self._lock = asyncio.Lock()
        self.logger = logger.bind(detector=self.NAME)

    @abstractmethod
    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze symbol for this manipulation pattern.

        Args:
            symbol: Trading pair to analyze (e.g., "BTC/USDT")
            state: Cross-exchange state store with current data

        Returns:
            Detection if pattern found, None otherwise
        """
        pass

    def _compute_fingerprint(self, detection: Detection) -> str:
        """Compute unique fingerprint from detection details."""
        data = {
            "symbol": detection.symbol,
            "type": detection.detection_type.value,
            "confidence": round(detection.confidence, 2),
            "details": detection.details,
        }
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(serialized.encode()).hexdigest()[:16]

    def _should_alert(self, detection: Detection) -> bool:
        """
        Smart deduplication check.

        - Полный дубль (все параметры 1 в 1) → 5 минут
        - Тот же тип, но другие параметры → 3 секунды

        Args:
            detection: Detection to check

        Returns:
            True if alert should be sent
        """
        now = datetime.now(timezone.utc)
        last_record = self._last_alerts.get(detection.symbol)

        if last_record is None:
            return True

        last_time, last_fingerprint = last_record
        elapsed = (now - last_time).total_seconds()
        current_fingerprint = self._compute_fingerprint(detection)

        # Полный дубль → 5 минут
        if current_fingerprint == last_fingerprint:
            if elapsed < self.DEDUP_EXACT_MATCH_SEC:
                self.logger.debug(
                    "cx_dedup_exact_match",
                    symbol=detection.symbol,
                    elapsed=f"{elapsed:.1f}s",
                )
                return False
        # Тот же тип, другие параметры → 3 секунды
        else:
            if elapsed < self.DEDUP_SAME_TYPE_SEC:
                self.logger.debug(
                    "cx_dedup_same_type",
                    symbol=detection.symbol,
                    elapsed=f"{elapsed:.1f}s",
                )
                return False

        return True

    def _record_alert(self, detection: Detection) -> None:
        """Record detection for deduplication."""
        fingerprint = self._compute_fingerprint(detection)
        self._last_alerts[detection.symbol] = (datetime.now(timezone.utc), fingerprint)

    def _get_severity(self, value: float) -> Severity:
        """
        Determine severity based on value and thresholds.

        Args:
            value: Metric value to evaluate

        Returns:
            Appropriate severity level
        """
        if value >= self.config.critical_threshold:
            return Severity.CRITICAL
        elif value >= self.config.alert_threshold:
            return Severity.ALERT
        elif value >= self.config.warning_threshold:
            return Severity.WARNING
        else:
            return Severity.INFO

    def _calculate_confidence(
        self,
        value: float,
        threshold: float,
        max_confidence_at: float = None
    ) -> float:
        """
        Calculate confidence score based on how much value exceeds threshold.

        Args:
            value: Observed value
            threshold: Minimum threshold
            max_confidence_at: Value at which confidence reaches 1.0

        Returns:
            Confidence score 0.0 to 1.0
        """
        if value < threshold:
            return 0.0

        if max_confidence_at is None:
            max_confidence_at = threshold * 3

        # Linear interpolation
        confidence = (value - threshold) / (max_confidence_at - threshold)
        return min(1.0, max(0.0, confidence))

    def _create_detection(
        self,
        symbol: str,
        severity: Severity,
        exchanges: List[str],
        description: str,
        details: Dict[str, Any],
        confidence: float = 0.5,
        recommended_action: Optional[str] = None
    ) -> Detection:
        """Create a Detection object with standard fields."""
        return Detection(
            detection_type=self.DETECTION_TYPE,
            severity=severity,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            exchanges=exchanges,
            description=description,
            details=details,
            confidence=confidence,
            recommended_action=recommended_action,
        )

    async def analyze_safe(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze with error handling.

        Wraps analyze() to catch and log exceptions.
        """
        if not self.config.enabled:
            return None

        try:
            async with self._lock:
                return await self.analyze(symbol, state)
        except Exception as e:
            self.logger.error(
                "detector_error",
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return None
