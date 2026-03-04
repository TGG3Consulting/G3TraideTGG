# -*- coding: utf-8 -*-
"""
Model Serialization Utilities for ML System.

Handles saving and loading of ML models, scalers, and metadata.

Usage:
    serializer = ModelSerializer("models/ml")
    serializer.save_model(model, "direction_classifier")
    loaded_model = serializer.load_model("direction_classifier")
"""

import json
import pickle
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


# Try to import joblib (more efficient for numpy arrays)
try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False


@dataclass
class ModelMetadata:
    """Metadata for a saved model."""

    model_name: str
    model_type: str
    version: str
    created_at: str
    feature_names: List[str]
    training_samples: int
    training_period_start: str
    training_period_end: str
    metrics: Dict[str, float]
    config: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict) -> "ModelMetadata":
        """Create from dictionary."""
        return cls(**data)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


class ModelSerializer:
    """
    Serializes and deserializes ML models.

    Handles:
    - Model saving/loading (pickle/joblib)
    - Metadata management
    - Version control
    - Scaler persistence
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
    ):
        """
        Initialize serializer.

        Args:
            base_dir: Base directory for model storage
        """
        self._base_dir = Path(base_dir or settings.ml.models.save_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

        self._use_joblib = JOBLIB_AVAILABLE

        logger.info(
            "model_serializer_init",
            base_dir=str(self._base_dir),
            use_joblib=self._use_joblib,
        )

    def save_model(
        self,
        model: Any,
        name: str,
        metadata: Optional[ModelMetadata] = None,
        version: Optional[str] = None,
    ) -> Path:
        """
        Save a model to disk.

        Args:
            model: Model object to save
            name: Model name (e.g., "direction_classifier")
            metadata: Optional metadata
            version: Optional version string

        Returns:
            Path to saved model
        """
        version = version or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        model_dir = self._base_dir / name / version
        model_dir.mkdir(parents=True, exist_ok=True)

        # Save model
        model_path = model_dir / "model.pkl"
        self._save_object(model, model_path)

        # Save metadata
        if metadata:
            metadata_path = model_dir / "metadata.json"
            with open(metadata_path, "w") as f:
                json.dump(metadata.to_dict(), f, indent=2)

        # Update latest symlink/pointer
        latest_path = self._base_dir / name / "latest.txt"
        latest_path.write_text(version)

        logger.info(
            "model_saved",
            name=name,
            version=version,
            path=str(model_path),
        )

        return model_path

    def load_model(
        self,
        name: str,
        version: Optional[str] = None,
    ) -> Optional[Any]:
        """
        Load a model from disk.

        Args:
            name: Model name
            version: Optional version (default: latest)

        Returns:
            Loaded model or None if not found
        """
        # Get version
        if version is None:
            version = self._get_latest_version(name)
            if version is None:
                logger.warning("no_model_found", name=name)
                return None

        model_path = self._base_dir / name / version / "model.pkl"

        if not model_path.exists():
            logger.warning("model_file_not_found", path=str(model_path))
            return None

        model = self._load_object(model_path)

        logger.info(
            "model_loaded",
            name=name,
            version=version,
        )

        return model

    def load_metadata(
        self,
        name: str,
        version: Optional[str] = None,
    ) -> Optional[ModelMetadata]:
        """
        Load model metadata.

        Args:
            name: Model name
            version: Optional version (default: latest)

        Returns:
            ModelMetadata or None
        """
        if version is None:
            version = self._get_latest_version(name)
            if version is None:
                return None

        metadata_path = self._base_dir / name / version / "metadata.json"

        if not metadata_path.exists():
            return None

        try:
            with open(metadata_path, "r") as f:
                data = json.load(f)
            return ModelMetadata.from_dict(data)
        except Exception as e:
            logger.warning("failed_to_load_metadata", error=str(e))
            return None

    def save_scaler(
        self,
        scaler: Any,
        name: str = "scaler",
    ) -> Path:
        """
        Save a scaler/preprocessor.

        Args:
            scaler: Scaler object
            name: Scaler name

        Returns:
            Path to saved scaler
        """
        scaler_path = self._base_dir / f"{name}.pkl"
        self._save_object(scaler, scaler_path)

        logger.info("scaler_saved", name=name, path=str(scaler_path))
        return scaler_path

    def load_scaler(
        self,
        name: str = "scaler",
    ) -> Optional[Any]:
        """
        Load a scaler/preprocessor.

        Args:
            name: Scaler name

        Returns:
            Loaded scaler or None
        """
        scaler_path = self._base_dir / f"{name}.pkl"

        if not scaler_path.exists():
            logger.warning("scaler_not_found", name=name)
            return None

        scaler = self._load_object(scaler_path)
        logger.info("scaler_loaded", name=name)
        return scaler

    def save_feature_names(
        self,
        feature_names: List[str],
        filename: str = "feature_names.json",
    ) -> Path:
        """
        Save feature names list.

        Args:
            feature_names: List of feature names
            filename: Output filename

        Returns:
            Path to saved file
        """
        path = self._base_dir / filename
        with open(path, "w") as f:
            json.dump(feature_names, f, indent=2)

        logger.info("feature_names_saved", count=len(feature_names))
        return path

    def load_feature_names(
        self,
        filename: str = "feature_names.json",
    ) -> List[str]:
        """
        Load feature names list.

        Args:
            filename: Input filename

        Returns:
            List of feature names
        """
        path = self._base_dir / filename

        if not path.exists():
            logger.warning("feature_names_not_found")
            return []

        with open(path, "r") as f:
            names = json.load(f)

        logger.info("feature_names_loaded", count=len(names))
        return names

    def save_config(
        self,
        config: Dict[str, Any],
        filename: str = "config.json",
    ) -> Path:
        """
        Save configuration used for training.

        Args:
            config: Configuration dictionary
            filename: Output filename

        Returns:
            Path to saved file
        """
        path = self._base_dir / filename
        with open(path, "w") as f:
            json.dump(config, f, indent=2, default=str)

        logger.info("config_saved")
        return path

    def load_config(
        self,
        filename: str = "config.json",
    ) -> Dict[str, Any]:
        """
        Load training configuration.

        Args:
            filename: Input filename

        Returns:
            Configuration dictionary
        """
        path = self._base_dir / filename

        if not path.exists():
            logger.warning("config_not_found")
            return {}

        with open(path, "r") as f:
            config = json.load(f)

        return config

    def list_models(self) -> List[str]:
        """
        List all saved models.

        Returns:
            List of model names
        """
        models = []
        for item in self._base_dir.iterdir():
            if item.is_dir() and (item / "latest.txt").exists():
                models.append(item.name)
        return sorted(models)

    def list_versions(self, name: str) -> List[str]:
        """
        List all versions of a model.

        Args:
            name: Model name

        Returns:
            List of version strings
        """
        model_dir = self._base_dir / name
        if not model_dir.exists():
            return []

        versions = []
        for item in model_dir.iterdir():
            if item.is_dir() and (item / "model.pkl").exists():
                versions.append(item.name)

        return sorted(versions, reverse=True)

    def delete_model(
        self,
        name: str,
        version: Optional[str] = None,
    ) -> bool:
        """
        Delete a model or version.

        Args:
            name: Model name
            version: Optional version (deletes all if None)

        Returns:
            True if deleted successfully
        """
        import shutil

        if version:
            path = self._base_dir / name / version
        else:
            path = self._base_dir / name

        if not path.exists():
            return False

        try:
            shutil.rmtree(path)
            logger.info("model_deleted", name=name, version=version)
            return True
        except Exception as e:
            logger.error("failed_to_delete_model", error=str(e))
            return False

    def _save_object(self, obj: Any, path: Path) -> None:
        """Save object using joblib or pickle."""
        if self._use_joblib:
            joblib.dump(obj, path)
        else:
            with open(path, "wb") as f:
                pickle.dump(obj, f)

    def _load_object(self, path: Path) -> Any:
        """Load object using joblib or pickle."""
        if self._use_joblib:
            return joblib.load(path)
        else:
            with open(path, "rb") as f:
                return pickle.load(f)

    def _get_latest_version(self, name: str) -> Optional[str]:
        """Get latest version of a model."""
        latest_path = self._base_dir / name / "latest.txt"

        if latest_path.exists():
            return latest_path.read_text().strip()

        # Fallback: get most recent version directory
        versions = self.list_versions(name)
        return versions[0] if versions else None


def save_ensemble(
    ensemble: "Any",
    path: str,
    metadata: Optional[Dict] = None,
) -> None:
    """
    Convenience function to save a model ensemble.

    Args:
        ensemble: ModelEnsemble instance
        path: Save directory
        metadata: Optional metadata dict
    """
    serializer = ModelSerializer(path)

    # Save each model in ensemble
    if hasattr(ensemble, "_direction_model") and ensemble._direction_model:
        serializer.save_model(ensemble._direction_model, "direction")

    if hasattr(ensemble, "_sl_model") and ensemble._sl_model:
        serializer.save_model(ensemble._sl_model, "sl")

    if hasattr(ensemble, "_tp_model") and ensemble._tp_model:
        serializer.save_model(ensemble._tp_model, "tp")

    if hasattr(ensemble, "_lifetime_model") and ensemble._lifetime_model:
        serializer.save_model(ensemble._lifetime_model, "lifetime")

    if hasattr(ensemble, "_confidence_calibrator") and ensemble._confidence_calibrator:
        serializer.save_model(ensemble._confidence_calibrator, "calibrator")

    # Save feature names
    if hasattr(ensemble, "_feature_names"):
        serializer.save_feature_names(ensemble._feature_names)

    # Save metadata
    if metadata:
        serializer.save_config(metadata, "ensemble_metadata.json")

    logger.info("ensemble_saved", path=path)


def load_ensemble(
    path: str,
    ensemble_class: Type,
) -> "Any":
    """
    Convenience function to load a model ensemble.

    Args:
        path: Load directory
        ensemble_class: Class to instantiate

    Returns:
        Loaded ensemble instance
    """
    serializer = ModelSerializer(path)
    ensemble = ensemble_class()

    # Load each model
    ensemble._direction_model = serializer.load_model("direction")
    ensemble._sl_model = serializer.load_model("sl")
    ensemble._tp_model = serializer.load_model("tp")
    ensemble._lifetime_model = serializer.load_model("lifetime")
    ensemble._confidence_calibrator = serializer.load_model("calibrator")

    # Load feature names
    ensemble._feature_names = serializer.load_feature_names()

    # Mark as loaded
    ensemble._is_loaded = True

    logger.info("ensemble_loaded", path=path)
    return ensemble
