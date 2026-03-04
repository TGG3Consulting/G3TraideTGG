# -*- coding: utf-8 -*-
"""
Data Validation Utilities for ML System.

Validates data quality before training and inference:
- Missing values
- Outliers
- Duplicates
- Data consistency

Usage:
    checker = DataQualityChecker()
    report = checker.validate(df)
    if not report.is_valid:
        df = checker.clean(df)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class DataQualityReport:
    """Report on data quality."""

    total_rows: int
    total_columns: int

    # Missing data
    missing_by_column: Dict[str, int]
    missing_total: int
    missing_pct: float

    # Duplicates
    duplicate_rows: int
    duplicate_timestamps: int

    # Outliers
    outliers_by_column: Dict[str, int]
    outliers_total: int

    # Timestamp issues
    timestamp_gaps: int
    timestamp_not_monotonic: bool
    timestamp_range_days: float

    # Value range issues
    invalid_prices: int  # <= 0 or inf
    invalid_volumes: int  # < 0
    invalid_funding: int  # outside [-3%, +3%]

    # Issues list
    issues: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether data passes quality checks."""
        return len(self.issues) == 0


class DataQualityChecker:
    """
    Validates data quality for ML training.

    Implements comprehensive checks per the prompt requirements.
    """

    def __init__(
        self,
        max_missing_pct: float = 5.0,
        max_outlier_pct: float = 1.0,
        outlier_std_threshold: float = 5.0,
    ):
        """
        Initialize quality checker.

        Args:
            max_missing_pct: Maximum allowed missing percentage
            max_outlier_pct: Maximum allowed outlier percentage
            outlier_std_threshold: Std deviations for outlier detection
        """
        self._max_missing_pct = max_missing_pct
        self._max_outlier_pct = max_outlier_pct
        self._outlier_std = outlier_std_threshold

        logger.info(
            "data_quality_checker_init",
            max_missing_pct=max_missing_pct,
            outlier_std_threshold=outlier_std_threshold,
        )

    def validate(self, df: pd.DataFrame) -> DataQualityReport:
        """
        Validate DataFrame quality.

        Args:
            df: DataFrame to validate

        Returns:
            DataQualityReport
        """
        issues = []

        # Basic info
        total_rows = len(df)
        total_columns = len(df.columns)

        # Missing data
        missing_by_col = df.isnull().sum().to_dict()
        missing_total = df.isnull().sum().sum()
        missing_pct = (missing_total / (total_rows * total_columns) * 100) if total_rows > 0 else 0

        if missing_pct > self._max_missing_pct:
            issues.append(f"Missing data {missing_pct:.1f}% > {self._max_missing_pct}%")

        # Critical columns check
        critical_cols = ["price", "close", "timestamp"]
        for col in critical_cols:
            if col in df.columns and df[col].isnull().any():
                issues.append(f"Critical column '{col}' has missing values")

        # Duplicates
        duplicate_rows = df.duplicated().sum()
        duplicate_ts = 0
        if "timestamp" in df.columns:
            duplicate_ts = df.duplicated(subset=["timestamp"]).sum()
            if duplicate_ts > 0:
                issues.append(f"{duplicate_ts} duplicate timestamps")

        # Outliers
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        outliers_by_col = {}

        for col in numeric_cols:
            if col in ["price", "open_interest", "volume"]:
                continue  # Don't flag these as outliers

            mean = df[col].mean()
            std = df[col].std()
            if std > 0:
                outliers = ((df[col] - mean).abs() > self._outlier_std * std).sum()
                if outliers > 0:
                    outliers_by_col[col] = int(outliers)

        outliers_total = sum(outliers_by_col.values())
        outlier_pct = outliers_total / (total_rows * len(numeric_cols)) * 100 if total_rows > 0 else 0

        if outlier_pct > self._max_outlier_pct:
            issues.append(f"Outliers {outlier_pct:.1f}% > {self._max_outlier_pct}%")

        # Timestamp checks
        timestamp_gaps = 0
        timestamp_not_monotonic = False
        timestamp_range_days = 0

        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"])

            # Check monotonic
            if not ts.is_monotonic_increasing:
                timestamp_not_monotonic = True
                issues.append("Timestamps not monotonically increasing")

            # Check gaps
            if len(ts) > 1:
                diffs = ts.diff().dropna()
                median_diff = diffs.median()
                large_gaps = (diffs > median_diff * 3).sum()
                timestamp_gaps = int(large_gaps)
                if large_gaps > len(ts) * 0.01:  # > 1% gaps
                    issues.append(f"{large_gaps} large timestamp gaps detected")

            # Range
            timestamp_range_days = (ts.max() - ts.min()).days

        # Value range checks
        invalid_prices = 0
        if "price" in df.columns:
            invalid_prices = ((df["price"] <= 0) | ~np.isfinite(df["price"])).sum()
            if invalid_prices > 0:
                issues.append(f"{invalid_prices} invalid price values")

        if "close" in df.columns:
            invalid = ((df["close"] <= 0) | ~np.isfinite(df["close"])).sum()
            invalid_prices += invalid
            if invalid > 0:
                issues.append(f"{invalid} invalid close values")

        invalid_volumes = 0
        for vol_col in ["volume", "volume_1h", "volume_1m"]:
            if vol_col in df.columns:
                invalid = (df[vol_col] < 0).sum()
                invalid_volumes += invalid
                if invalid > 0:
                    issues.append(f"{invalid} negative {vol_col} values")

        invalid_funding = 0
        if "funding_rate" in df.columns:
            invalid_funding = (
                (df["funding_rate"].abs() > 0.03) &  # > 3%
                df["funding_rate"].notna()
            ).sum()
            # This is a warning, not error
            if invalid_funding > 0:
                logger.warning(f"extreme_funding_rates", count=invalid_funding)

        report = DataQualityReport(
            total_rows=total_rows,
            total_columns=total_columns,
            missing_by_column=missing_by_col,
            missing_total=int(missing_total),
            missing_pct=float(missing_pct),
            duplicate_rows=int(duplicate_rows),
            duplicate_timestamps=int(duplicate_ts),
            outliers_by_column=outliers_by_col,
            outliers_total=outliers_total,
            timestamp_gaps=timestamp_gaps,
            timestamp_not_monotonic=timestamp_not_monotonic,
            timestamp_range_days=float(timestamp_range_days),
            invalid_prices=int(invalid_prices),
            invalid_volumes=int(invalid_volumes),
            invalid_funding=int(invalid_funding),
            issues=issues,
        )

        logger.info(
            "data_quality_validated",
            is_valid=report.is_valid,
            issues_count=len(issues),
        )

        return report

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean data by fixing common issues.

        Args:
            df: DataFrame to clean

        Returns:
            Cleaned DataFrame
        """
        df = df.copy()
        original_len = len(df)

        # Remove duplicates
        if "timestamp" in df.columns:
            df = df.drop_duplicates(subset=["timestamp"], keep="last")

        # Sort by timestamp
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)

        # Replace inf with NaN
        df = df.replace([np.inf, -np.inf], np.nan)

        # Forward fill then backward fill for numeric columns
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        df[numeric_cols] = df[numeric_cols].fillna(method="ffill").fillna(method="bfill")

        # Fill remaining NaN with 0
        df = df.fillna(0)

        # Remove outliers (clip to 5 std)
        for col in numeric_cols:
            if col in ["price", "close", "open_interest", "volume"]:
                continue  # Don't clip these

            mean = df[col].mean()
            std = df[col].std()
            if std > 0:
                lower = mean - self._outlier_std * std
                upper = mean + self._outlier_std * std
                df[col] = df[col].clip(lower, upper)

        logger.info(
            "data_cleaned",
            original_rows=original_len,
            cleaned_rows=len(df),
            removed=original_len - len(df),
        )

        return df

    def validate_training_data(
        self,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        min_train_samples: int = 500,
        min_test_samples: int = 100,
        min_days: int = 90,
    ) -> List[str]:
        """
        Validate training/test data split.

        Args:
            train_df: Training data
            test_df: Test data
            min_train_samples: Minimum train samples
            min_test_samples: Minimum test samples
            min_days: Minimum data period

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if len(train_df) < min_train_samples:
            errors.append(
                f"Insufficient train samples: {len(train_df)} < {min_train_samples}"
            )

        if len(test_df) < min_test_samples:
            errors.append(
                f"Insufficient test samples: {len(test_df)} < {min_test_samples}"
            )

        # Check temporal ordering
        if "timestamp" in train_df.columns and "timestamp" in test_df.columns:
            train_max = pd.to_datetime(train_df["timestamp"].max())
            test_min = pd.to_datetime(test_df["timestamp"].min())

            if train_max >= test_min:
                errors.append(
                    "Data leakage: train data overlaps with test data"
                )

            # Check period
            train_min = pd.to_datetime(train_df["timestamp"].min())
            period = (train_max - train_min).days

            if period < min_days:
                errors.append(
                    f"Insufficient data period: {period} < {min_days} days"
                )

        return errors
