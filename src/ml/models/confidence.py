# -*- coding: utf-8 -*-
"""
Confidence Calibrator for ML System.

Calibrates model probability outputs to be well-calibrated.

Methods:
- Isotonic Regression: Non-parametric, works well with many samples
- Platt Scaling: Sigmoid calibration, works with fewer samples

Usage:
    calibrator = ConfidenceCalibrator()
    calibrator.fit(probabilities, actual_outcomes)
    calibrated = calibrator.calibrate(new_probabilities)
"""

import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class ConfidenceCalibrator:
    """
    Calibrates probability predictions for reliability.

    Well-calibrated probabilities: if model says 70% confidence,
    it should be correct ~70% of the time.
    """

    def __init__(self, method: Optional[str] = None):
        """
        Initialize calibrator.

        Args:
            method: Calibration method ('isotonic' or 'platt')
        """
        self._config = settings.ml.models
        self._method = method or self._config.calibration_method
        self._calibrator = None
        self._is_fitted = False
        self._fit_timestamp: Optional[datetime] = None

        logger.info("confidence_calibrator_init", method=self._method)

    @property
    def is_fitted(self) -> bool:
        """Whether calibrator has been fitted."""
        return self._is_fitted

    def fit(
        self,
        probabilities: np.ndarray,
        outcomes: np.ndarray,
    ) -> "ConfidenceCalibrator":
        """
        Fit calibrator to probability-outcome pairs.

        Args:
            probabilities: Uncalibrated probabilities (0 to 1)
            outcomes: Actual binary outcomes (0 or 1)

        Returns:
            Self
        """
        probabilities = np.asarray(probabilities).flatten()
        outcomes = np.asarray(outcomes).flatten()

        # Clip probabilities to avoid edge cases
        probabilities = np.clip(probabilities, 1e-7, 1 - 1e-7)

        logger.info(
            "fitting_confidence_calibrator",
            method=self._method,
            n_samples=len(probabilities),
            mean_prob=float(np.mean(probabilities)),
            positive_rate=float(np.mean(outcomes)),
        )

        if self._method == "isotonic":
            self._fit_isotonic(probabilities, outcomes)
        elif self._method == "platt":
            self._fit_platt(probabilities, outcomes)
        else:
            raise ValueError(f"Unknown calibration method: {self._method}")

        self._is_fitted = True
        self._fit_timestamp = datetime.now(timezone.utc)

        return self

    def _fit_isotonic(self, probabilities: np.ndarray, outcomes: np.ndarray) -> None:
        """Fit isotonic regression calibrator."""
        from sklearn.isotonic import IsotonicRegression

        self._calibrator = IsotonicRegression(out_of_bounds="clip")
        self._calibrator.fit(probabilities, outcomes)

    def _fit_platt(self, probabilities: np.ndarray, outcomes: np.ndarray) -> None:
        """Fit Platt scaling (sigmoid) calibrator."""
        from sklearn.linear_model import LogisticRegression

        # Transform probabilities to log-odds
        log_odds = np.log(probabilities / (1 - probabilities)).reshape(-1, 1)

        self._calibrator = LogisticRegression(C=1e10, solver="lbfgs")
        self._calibrator.fit(log_odds, outcomes)

    def calibrate(self, probabilities: np.ndarray) -> np.ndarray:
        """
        Calibrate probability predictions.

        Args:
            probabilities: Uncalibrated probabilities

        Returns:
            Calibrated probabilities
        """
        if not self._is_fitted:
            raise ValueError("Calibrator not fitted. Call fit() first.")

        probabilities = np.asarray(probabilities).flatten()
        probabilities = np.clip(probabilities, 1e-7, 1 - 1e-7)

        if self._method == "isotonic":
            return self._calibrator.predict(probabilities)
        elif self._method == "platt":
            log_odds = np.log(probabilities / (1 - probabilities)).reshape(-1, 1)
            return self._calibrator.predict_proba(log_odds)[:, 1]

        return probabilities

    def calibrate_with_bounds(
        self,
        probabilities: np.ndarray,
        n_bootstrap: int = 100,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Calibrate with confidence intervals using bootstrap.

        Args:
            probabilities: Uncalibrated probabilities
            n_bootstrap: Number of bootstrap samples

        Returns:
            Tuple of (calibrated, lower_bound, upper_bound)
        """
        calibrated = self.calibrate(probabilities)

        # Bootstrap for uncertainty
        # Note: This is a simplified version - full bootstrap would
        # require storing training data
        std = np.sqrt(calibrated * (1 - calibrated) / 100)  # Approximate

        lower = np.clip(calibrated - 1.96 * std, 0, 1)
        upper = np.clip(calibrated + 1.96 * std, 0, 1)

        return calibrated, lower, upper

    def reliability_diagram_data(
        self,
        probabilities: np.ndarray,
        outcomes: np.ndarray,
        n_bins: int = 10,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Get data for reliability diagram.

        Args:
            probabilities: Predicted probabilities
            outcomes: Actual outcomes
            n_bins: Number of bins

        Returns:
            Tuple of (bin_centers, true_fractions, sample_counts)
        """
        probabilities = np.asarray(probabilities)
        outcomes = np.asarray(outcomes)

        bin_edges = np.linspace(0, 1, n_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        true_fractions = []
        sample_counts = []

        for i in range(n_bins):
            mask = (probabilities >= bin_edges[i]) & (probabilities < bin_edges[i + 1])
            if mask.sum() > 0:
                true_fractions.append(outcomes[mask].mean())
                sample_counts.append(mask.sum())
            else:
                true_fractions.append(np.nan)
                sample_counts.append(0)

        return bin_centers, np.array(true_fractions), np.array(sample_counts)

    def expected_calibration_error(
        self,
        probabilities: np.ndarray,
        outcomes: np.ndarray,
        n_bins: int = 10,
    ) -> float:
        """
        Calculate Expected Calibration Error (ECE).

        Lower is better. 0 = perfect calibration.

        Args:
            probabilities: Predicted probabilities
            outcomes: Actual outcomes
            n_bins: Number of bins

        Returns:
            ECE value
        """
        bin_centers, true_fractions, counts = self.reliability_diagram_data(
            probabilities, outcomes, n_bins
        )

        total_samples = counts.sum()
        if total_samples == 0:
            return 1.0

        ece = 0.0
        for i in range(len(bin_centers)):
            if counts[i] > 0 and not np.isnan(true_fractions[i]):
                ece += counts[i] * abs(true_fractions[i] - bin_centers[i])

        return ece / total_samples

    def save(self, path: Optional[str] = None) -> str:
        """Save calibrator to file."""
        if path is None:
            save_dir = Path(settings.ml.models.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
            path = str(save_dir / "confidence_calibrator.pkl")

        data = {
            "method": self._method,
            "calibrator": self._calibrator,
            "is_fitted": self._is_fitted,
            "fit_timestamp": self._fit_timestamp,
        }

        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info("calibrator_saved", path=path)
        return path

    def load(self, path: str) -> "ConfidenceCalibrator":
        """Load calibrator from file."""
        with open(path, "rb") as f:
            data = pickle.load(f)

        self._method = data["method"]
        self._calibrator = data["calibrator"]
        self._is_fitted = data["is_fitted"]
        self._fit_timestamp = data.get("fit_timestamp")

        logger.info("calibrator_loaded", path=path)
        return self


class DirectionalConfidenceCalibrator:
    """
    Separate calibrators for long/short predictions.

    Allows different calibration for each direction.
    """

    def __init__(self, method: Optional[str] = None):
        """Initialize directional calibrator."""
        self._long_calibrator = ConfidenceCalibrator(method)
        self._short_calibrator = ConfidenceCalibrator(method)
        self._is_fitted = False

    @property
    def is_fitted(self) -> bool:
        """Whether calibrators have been fitted."""
        return self._is_fitted

    def fit(
        self,
        long_probs: np.ndarray,
        long_outcomes: np.ndarray,
        short_probs: np.ndarray,
        short_outcomes: np.ndarray,
    ) -> "DirectionalConfidenceCalibrator":
        """
        Fit calibrators for both directions.

        Args:
            long_probs: Long prediction probabilities
            long_outcomes: Long actual outcomes
            short_probs: Short prediction probabilities
            short_outcomes: Short actual outcomes

        Returns:
            Self
        """
        self._long_calibrator.fit(long_probs, long_outcomes)
        self._short_calibrator.fit(short_probs, short_outcomes)
        self._is_fitted = True

        logger.info("directional_calibrators_fitted")
        return self

    def calibrate_long(self, probabilities: np.ndarray) -> np.ndarray:
        """Calibrate long probabilities."""
        return self._long_calibrator.calibrate(probabilities)

    def calibrate_short(self, probabilities: np.ndarray) -> np.ndarray:
        """Calibrate short probabilities."""
        return self._short_calibrator.calibrate(probabilities)

    def calibrate(
        self,
        probabilities: np.ndarray,
        directions: np.ndarray,
    ) -> np.ndarray:
        """
        Calibrate probabilities based on direction.

        Args:
            probabilities: Uncalibrated probabilities
            directions: Direction for each sample (1=long, -1=short)

        Returns:
            Calibrated probabilities
        """
        if not self._is_fitted:
            raise ValueError("Calibrators not fitted.")

        calibrated = np.zeros_like(probabilities)

        long_mask = directions == 1
        short_mask = directions == -1

        if long_mask.any():
            calibrated[long_mask] = self._long_calibrator.calibrate(
                probabilities[long_mask]
            )

        if short_mask.any():
            calibrated[short_mask] = self._short_calibrator.calibrate(
                probabilities[short_mask]
            )

        return calibrated

    def save(self, path: str) -> str:
        """Save both calibrators."""
        data = {
            "long_calibrator": self._long_calibrator,
            "short_calibrator": self._short_calibrator,
            "is_fitted": self._is_fitted,
        }

        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info("directional_calibrators_saved", path=path)
        return path

    def load(self, path: str) -> "DirectionalConfidenceCalibrator":
        """Load both calibrators."""
        with open(path, "rb") as f:
            data = pickle.load(f)

        self._long_calibrator = data["long_calibrator"]
        self._short_calibrator = data["short_calibrator"]
        self._is_fitted = data["is_fitted"]

        logger.info("directional_calibrators_loaded", path=path)
        return self
