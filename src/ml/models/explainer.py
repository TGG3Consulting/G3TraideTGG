# -*- coding: utf-8 -*-
"""
Model Explainer for ML System.

Provides explainability for model predictions using SHAP values.
Helps understand why the model made specific decisions.

Usage:
    explainer = ModelExplainer(model)
    explanation = explainer.explain_prediction(features)
    print(explanation.human_readable)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import structlog

from config.settings import settings

if TYPE_CHECKING:
    from src.ml.models.ensemble import ModelEnsemble


logger = structlog.get_logger(__name__)


# Try to import SHAP, but make it optional
try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False
    logger.warning("shap_not_installed", message="Install shap for explainability: pip install shap")


@dataclass
class FeatureContribution:
    """Contribution of a single feature to the prediction."""

    name: str
    value: float  # Actual feature value
    contribution: float  # SHAP value / contribution to prediction
    direction: str  # "positive" or "negative"

    @property
    def abs_contribution(self) -> float:
        """Absolute contribution magnitude."""
        return abs(self.contribution)


@dataclass
class Explanation:
    """
    Explanation of a model prediction.

    Contains SHAP values and human-readable interpretation.
    """

    # Raw SHAP values
    shap_values: Optional[np.ndarray] = None

    # Top contributing features
    top_features: List[FeatureContribution] = field(default_factory=list)

    # Human-readable explanation
    human_readable: str = ""

    # Base prediction (expected value)
    base_value: float = 0.0

    # Final prediction
    prediction: float = 0.0

    # Confidence in explanation (0-1)
    explanation_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "top_features": [
                {
                    "name": f.name,
                    "value": f.value,
                    "contribution": f.contribution,
                    "direction": f.direction,
                }
                for f in self.top_features
            ],
            "human_readable": self.human_readable,
            "base_value": self.base_value,
            "prediction": self.prediction,
            "explanation_confidence": self.explanation_confidence,
        }


class ModelExplainer:
    """
    Explains model predictions using SHAP.

    Provides:
    - SHAP values for each feature
    - Top contributing features
    - Human-readable explanations
    """

    def __init__(
        self,
        model: Any = None,
        feature_names: Optional[List[str]] = None,
        background_data: Optional[np.ndarray] = None,
    ):
        """
        Initialize model explainer.

        Args:
            model: Trained model (LightGBM, XGBoost, etc.)
            feature_names: Names of features
            background_data: Background data for SHAP (sample of training data)
        """
        self._model = model
        self._feature_names = feature_names or []
        self._background_data = background_data
        self._explainer = None

        if not SHAP_AVAILABLE:
            logger.warning("shap_not_available_explainer_limited")

        logger.info(
            "model_explainer_init",
            has_model=model is not None,
            feature_count=len(self._feature_names),
            shap_available=SHAP_AVAILABLE,
        )

    def set_model(
        self,
        model: Any,
        feature_names: Optional[List[str]] = None,
        background_data: Optional[np.ndarray] = None,
    ) -> None:
        """
        Set or update the model.

        Args:
            model: Trained model
            feature_names: Feature names
            background_data: Background data for SHAP
        """
        self._model = model
        if feature_names:
            self._feature_names = feature_names
        if background_data is not None:
            self._background_data = background_data
        self._explainer = None  # Reset explainer

        logger.debug("model_set_for_explainer")

    def _init_explainer(self) -> bool:
        """Initialize SHAP explainer if not already done."""
        if self._explainer is not None:
            return True

        if not SHAP_AVAILABLE:
            return False

        if self._model is None:
            logger.warning("no_model_for_explainer")
            return False

        try:
            # Try TreeExplainer first (faster for tree models)
            self._explainer = shap.TreeExplainer(self._model)
            logger.debug("initialized_tree_explainer")
            return True
        except Exception:
            pass

        try:
            # Fall back to KernelExplainer
            if self._background_data is not None:
                self._explainer = shap.KernelExplainer(
                    self._model.predict,
                    self._background_data[:100]  # Limit background size
                )
                logger.debug("initialized_kernel_explainer")
                return True
        except Exception as e:
            logger.warning("failed_to_init_explainer", error=str(e))

        return False

    def explain_prediction(
        self,
        features: np.ndarray,
        top_n: int = 5,
    ) -> Explanation:
        """
        Explain a single prediction.

        Args:
            features: Feature vector (1D array)
            top_n: Number of top features to return

        Returns:
            Explanation with SHAP values and interpretation
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)

        # Try SHAP explanation
        if SHAP_AVAILABLE and self._init_explainer():
            return self._explain_with_shap(features, top_n)

        # Fallback to feature importance explanation
        return self._explain_with_importance(features, top_n)

    def _explain_with_shap(
        self,
        features: np.ndarray,
        top_n: int,
    ) -> Explanation:
        """Explain using SHAP values."""
        try:
            shap_values = self._explainer.shap_values(features)

            # Handle multi-class output
            if isinstance(shap_values, list):
                # Use class 1 (positive class) for binary classification
                shap_values = shap_values[1] if len(shap_values) > 1 else shap_values[0]

            shap_vals = shap_values[0] if shap_values.ndim > 1 else shap_values
            feature_vals = features[0]

            # Get base value
            base_value = (
                self._explainer.expected_value[1]
                if isinstance(self._explainer.expected_value, (list, np.ndarray))
                else self._explainer.expected_value
            )

            # Get prediction
            prediction = base_value + np.sum(shap_vals)

            # Get top contributing features
            top_features = self._get_top_features(
                shap_vals, feature_vals, top_n
            )

            # Generate human-readable explanation
            human_readable = self._generate_explanation(top_features, prediction)

            return Explanation(
                shap_values=shap_vals,
                top_features=top_features,
                human_readable=human_readable,
                base_value=float(base_value),
                prediction=float(prediction),
                explanation_confidence=0.9,  # High confidence with SHAP
            )

        except Exception as e:
            logger.warning("shap_explanation_failed", error=str(e))
            return self._explain_with_importance(features, top_n)

    def _explain_with_importance(
        self,
        features: np.ndarray,
        top_n: int,
    ) -> Explanation:
        """Fallback explanation using feature importance."""
        feature_vals = features[0] if features.ndim > 1 else features

        # Try to get feature importance from model
        importance = None
        if hasattr(self._model, "feature_importances_"):
            importance = self._model.feature_importances_
        elif hasattr(self._model, "feature_importance"):
            importance = self._model.feature_importance()

        if importance is None:
            return Explanation(
                human_readable="Unable to explain prediction (no SHAP or feature importance)",
                explanation_confidence=0.0,
            )

        # Create pseudo-contributions based on importance * normalized value
        contributions = []
        for i, (imp, val) in enumerate(zip(importance, feature_vals)):
            name = self._feature_names[i] if i < len(self._feature_names) else f"feature_{i}"

            # Normalize value to [-1, 1] range for contribution direction
            norm_val = np.tanh(val) if abs(val) > 1 else val
            contribution = imp * norm_val

            contributions.append(
                FeatureContribution(
                    name=name,
                    value=float(val),
                    contribution=float(contribution),
                    direction="positive" if contribution > 0 else "negative",
                )
            )

        # Sort by absolute contribution
        contributions.sort(key=lambda x: x.abs_contribution, reverse=True)
        top_features = contributions[:top_n]

        human_readable = self._generate_explanation(top_features, None)

        return Explanation(
            top_features=top_features,
            human_readable=human_readable,
            explanation_confidence=0.5,  # Lower confidence without SHAP
        )

    def _get_top_features(
        self,
        shap_values: np.ndarray,
        feature_values: np.ndarray,
        top_n: int,
    ) -> List[FeatureContribution]:
        """Get top contributing features from SHAP values."""
        contributions = []

        for i, (shap_val, feat_val) in enumerate(zip(shap_values, feature_values)):
            name = self._feature_names[i] if i < len(self._feature_names) else f"feature_{i}"

            contributions.append(
                FeatureContribution(
                    name=name,
                    value=float(feat_val),
                    contribution=float(shap_val),
                    direction="positive" if shap_val > 0 else "negative",
                )
            )

        # Sort by absolute contribution
        contributions.sort(key=lambda x: x.abs_contribution, reverse=True)
        return contributions[:top_n]

    def _generate_explanation(
        self,
        top_features: List[FeatureContribution],
        prediction: Optional[float],
    ) -> str:
        """Generate human-readable explanation."""
        if not top_features:
            return "No significant features identified."

        lines = []

        if prediction is not None:
            direction = "bullish" if prediction > 0.5 else "bearish"
            confidence = abs(prediction - 0.5) * 2 * 100
            lines.append(f"Prediction: {direction} ({confidence:.1f}% confidence)")
            lines.append("")

        lines.append("Key factors:")

        for i, feat in enumerate(top_features, 1):
            impact = "increases" if feat.direction == "positive" else "decreases"
            magnitude = "strongly" if feat.abs_contribution > 0.1 else "slightly"

            # Format feature name nicely
            name = feat.name.replace("_", " ").title()

            lines.append(
                f"  {i}. {name} ({feat.value:.4f}) {magnitude} {impact} prediction"
            )

        return "\n".join(lines)

    def explain_batch(
        self,
        features: np.ndarray,
        top_n: int = 5,
    ) -> List[Explanation]:
        """
        Explain multiple predictions.

        Args:
            features: Feature matrix (n_samples, n_features)
            top_n: Number of top features per explanation

        Returns:
            List of Explanation objects
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)

        explanations = []
        for i in range(len(features)):
            exp = self.explain_prediction(features[i:i+1], top_n)
            explanations.append(exp)

        return explanations

    def get_feature_importance_ranking(self) -> List[Tuple[str, float]]:
        """
        Get global feature importance ranking.

        Returns:
            List of (feature_name, importance) tuples sorted by importance
        """
        if self._model is None:
            return []

        importance = None
        if hasattr(self._model, "feature_importances_"):
            importance = self._model.feature_importances_
        elif hasattr(self._model, "feature_importance"):
            importance = self._model.feature_importance()

        if importance is None:
            return []

        ranking = []
        for i, imp in enumerate(importance):
            name = self._feature_names[i] if i < len(self._feature_names) else f"feature_{i}"
            ranking.append((name, float(imp)))

        ranking.sort(key=lambda x: x[1], reverse=True)
        return ranking

    def plot_explanation(
        self,
        explanation: Explanation,
        save_path: Optional[str] = None,
    ) -> None:
        """
        Plot SHAP explanation (if matplotlib available).

        Args:
            explanation: Explanation object
            save_path: Optional path to save plot
        """
        if not SHAP_AVAILABLE or explanation.shap_values is None:
            logger.warning("cannot_plot_no_shap_values")
            return

        try:
            import matplotlib.pyplot as plt

            # Create waterfall plot data
            fig, ax = plt.subplots(figsize=(10, 6))

            features = explanation.top_features
            names = [f.name for f in features]
            values = [f.contribution for f in features]
            colors = ["green" if v > 0 else "red" for v in values]

            ax.barh(names, values, color=colors)
            ax.set_xlabel("SHAP Value (Impact on Prediction)")
            ax.set_title("Feature Contributions to Prediction")
            ax.axvline(x=0, color="black", linewidth=0.5)

            plt.tight_layout()

            if save_path:
                plt.savefig(save_path)
                logger.info("saved_explanation_plot", path=save_path)
            else:
                plt.show()

            plt.close()

        except ImportError:
            logger.warning("matplotlib_not_available_for_plotting")
        except Exception as e:
            logger.warning("failed_to_plot_explanation", error=str(e))
