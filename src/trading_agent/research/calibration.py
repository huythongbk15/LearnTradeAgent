"""Train-only probability calibration, conformal intervals, and exposure gates."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class CalibrationState(Enum):
    CALIBRATED = "calibrated"
    DEGRADED = "degraded"
    UNCALIBRATED = "uncalibrated"
    STALE = "stale"


class CalibrationMethod(Enum):
    ISOTONIC = "isotonic"
    PLATT = "platt"
    TEMPERATURE = "temperature"


@dataclass(frozen=True)
class DataWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("window end must not precede start")

    def overlaps(self, other: "DataWindow") -> bool:
        return not (self.end < other.start or other.end < self.start)


@dataclass(frozen=True)
class CalibrationArtifact:
    calibration_id: str
    model_artifact_id: str
    train_window: DataWindow
    validation_window: DataWindow
    sample_count: int
    method: CalibrationMethod
    brier: float
    ece: float
    reliability_data: tuple[dict[str, float | int], ...]
    input_hash: str
    created_at: datetime
    parameters: dict[str, Any]

    def state(
        self,
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(days=30),
        max_ece: float = 0.10,
        max_brier: float = 0.25,
        min_samples: int = 30,
    ) -> CalibrationState:
        current = now or datetime.now(UTC)
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if current - created > max_age:
            return CalibrationState.STALE
        if (
            self.sample_count < min_samples
            or self.ece > max_ece
            or self.brier > max_brier
        ):
            return CalibrationState.DEGRADED
        return CalibrationState.CALIBRATED


def calibration_state(
    artifact: CalibrationArtifact | None,
    *,
    now: datetime | None = None,
) -> CalibrationState:
    return (
        CalibrationState.UNCALIBRATED if artifact is None else artifact.state(now=now)
    )


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_binary_sample(
    predictions: np.ndarray,
    outcomes: np.ndarray,
    label: str,
) -> tuple[np.ndarray, np.ndarray]:
    probabilities = np.asarray(predictions, dtype=float)
    labels = np.asarray(outcomes, dtype=float)
    if probabilities.ndim != 1 or probabilities.shape != labels.shape:
        raise ValueError(f"{label} predictions/outcomes must be equal-length 1D arrays")
    if probabilities.size < 10:
        raise ValueError(f"{label} sample requires at least 10 observations")
    if not np.all(np.isfinite(probabilities)) or not np.all(np.isfinite(labels)):
        raise ValueError(f"{label} sample must be finite")
    if np.any((probabilities < 0.0) | (probabilities > 1.0)):
        raise ValueError(f"{label} predictions must be probabilities in [0, 1]")
    if np.any((labels != 0.0) & (labels != 1.0)):
        raise ValueError(f"{label} outcomes must be binary")
    return probabilities, labels


def _logit(probabilities: np.ndarray) -> np.ndarray:
    clipped = np.clip(probabilities, 1e-9, 1.0 - 1e-9)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-values))


def _fit_parameters(
    method: CalibrationMethod,
    probabilities: np.ndarray,
    outcomes: np.ndarray,
) -> dict[str, Any]:
    if method == CalibrationMethod.ISOTONIC:
        model = IsotonicRegression(out_of_bounds="clip", increasing=True)
        model.fit(probabilities, outcomes)
        return {
            "x_thresholds": model.X_thresholds_.tolist(),
            "y_thresholds": model.y_thresholds_.tolist(),
        }
    logits = _logit(probabilities)
    if method == CalibrationMethod.PLATT:
        model = LogisticRegression(C=1e6, solver="lbfgs", random_state=0)
        model.fit(logits.reshape(-1, 1), outcomes.astype(int))
        return {
            "coefficient": float(model.coef_[0, 0]),
            "intercept": float(model.intercept_[0]),
        }
    if method == CalibrationMethod.TEMPERATURE:

        def loss(log_temperature: float) -> float:
            temperature = math.exp(log_temperature)
            calibrated = np.clip(_sigmoid(logits / temperature), 1e-12, 1.0 - 1e-12)
            return float(
                -np.mean(
                    outcomes * np.log(calibrated)
                    + (1.0 - outcomes) * np.log(1.0 - calibrated)
                )
            )

        result = minimize_scalar(loss, bounds=(-4.0, 4.0), method="bounded")
        if not result.success:
            raise RuntimeError("temperature calibration optimization failed")
        return {"temperature": float(math.exp(result.x))}
    raise ValueError(f"unsupported calibration method: {method}")


def apply_calibrator(
    probabilities: np.ndarray,
    artifact: CalibrationArtifact,
) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    if np.any((values < 0.0) | (values > 1.0)) or not np.all(np.isfinite(values)):
        raise ValueError("probabilities must be finite and in [0, 1]")
    parameters = artifact.parameters
    if artifact.method == CalibrationMethod.ISOTONIC:
        result = np.interp(
            values,
            np.asarray(parameters["x_thresholds"], dtype=float),
            np.asarray(parameters["y_thresholds"], dtype=float),
        )
    elif artifact.method == CalibrationMethod.PLATT:
        result = _sigmoid(
            float(parameters["coefficient"]) * _logit(values)
            + float(parameters["intercept"])
        )
    elif artifact.method == CalibrationMethod.TEMPERATURE:
        result = _sigmoid(_logit(values) / float(parameters["temperature"]))
    else:
        raise ValueError(f"unsupported calibration method: {artifact.method}")
    return np.clip(result, 0.0, 1.0)


def reliability_diagram(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    *,
    bins: int = 10,
) -> tuple[tuple[dict[str, float | int], ...], float]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    records: list[dict[str, float | int]] = []
    ece = 0.0
    for index in range(bins):
        upper_inclusive = index == bins - 1
        mask = (probabilities >= edges[index]) & (
            probabilities <= edges[index + 1]
            if upper_inclusive
            else probabilities < edges[index + 1]
        )
        count = int(np.sum(mask))
        if count == 0:
            continue
        mean_prediction = float(np.mean(probabilities[mask]))
        observed_rate = float(np.mean(outcomes[mask]))
        ece += count / len(probabilities) * abs(mean_prediction - observed_rate)
        records.append(
            {
                "bin_lower": float(edges[index]),
                "bin_upper": float(edges[index + 1]),
                "count": count,
                "mean_prediction": mean_prediction,
                "observed_rate": observed_rate,
            }
        )
    return tuple(records), float(ece)


def fit_calibration_artifact(
    *,
    method: CalibrationMethod | str,
    model_artifact_id: str,
    train_predictions: np.ndarray,
    train_outcomes: np.ndarray,
    validation_predictions: np.ndarray,
    validation_outcomes: np.ndarray,
    train_window: DataWindow,
    validation_window: DataWindow,
    created_at: datetime | None = None,
) -> CalibrationArtifact:
    """Fit only on train, then score the frozen mapping on validation."""

    method = (
        method if isinstance(method, CalibrationMethod) else CalibrationMethod(method)
    )
    if train_window.overlaps(validation_window):
        raise ValueError("calibration train and validation windows must be disjoint")
    train_probability, train_label = _validate_binary_sample(
        train_predictions, train_outcomes, "train"
    )
    validation_probability, validation_label = _validate_binary_sample(
        validation_predictions, validation_outcomes, "validation"
    )
    parameters = _fit_parameters(method, train_probability, train_label)
    provisional = CalibrationArtifact(
        calibration_id="pending",
        model_artifact_id=model_artifact_id,
        train_window=train_window,
        validation_window=validation_window,
        sample_count=int(validation_probability.size),
        method=method,
        brier=0.0,
        ece=0.0,
        reliability_data=(),
        input_hash="pending",
        created_at=created_at or datetime.now(UTC),
        parameters=parameters,
    )
    calibrated = apply_calibrator(validation_probability, provisional)
    reliability, ece = reliability_diagram(calibrated, validation_label)
    brier = float(np.mean((calibrated - validation_label) ** 2))
    input_hash = _canonical_sha256(
        {
            "model_artifact_id": model_artifact_id,
            "method": method.value,
            "train_predictions": train_probability.tolist(),
            "train_outcomes": train_label.tolist(),
            "validation_predictions": validation_probability.tolist(),
            "validation_outcomes": validation_label.tolist(),
            "train_window": asdict(train_window),
            "validation_window": asdict(validation_window),
        }
    )
    calibration_id = f"cal_{_canonical_sha256({'input_hash': input_hash, 'parameters': parameters})[:32]}"
    return CalibrationArtifact(
        calibration_id=calibration_id,
        model_artifact_id=model_artifact_id,
        train_window=train_window,
        validation_window=validation_window,
        sample_count=int(validation_probability.size),
        method=method,
        brier=brier,
        ece=ece,
        reliability_data=reliability,
        input_hash=input_hash,
        created_at=provisional.created_at,
        parameters=parameters,
    )


class CalibrationArtifactStore:
    """Append-only JSON store for immutable calibration evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def put(self, artifact: CalibrationArtifact) -> Path:
        destination = self.path / f"{artifact.calibration_id}.json"
        serializable = asdict(artifact)
        serializable["method"] = artifact.method.value
        serializable["created_at"] = artifact.created_at.isoformat()
        serializable["train_window"] = {
            "start": artifact.train_window.start.isoformat(),
            "end": artifact.train_window.end.isoformat(),
        }
        serializable["validation_window"] = {
            "start": artifact.validation_window.start.isoformat(),
            "end": artifact.validation_window.end.isoformat(),
        }
        payload = json.dumps(serializable, sort_keys=True, indent=2)
        if destination.exists():
            if destination.read_text(encoding="utf-8") != payload:
                raise RuntimeError("calibration artifact id collision")
            return destination
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
        return destination

    def get(self, calibration_id: str) -> CalibrationArtifact | None:
        source = self.path / f"{calibration_id}.json"
        if not source.exists():
            return None
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["method"] = CalibrationMethod(payload["method"])
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        payload["train_window"] = DataWindow(
            datetime.fromisoformat(payload["train_window"]["start"]),
            datetime.fromisoformat(payload["train_window"]["end"]),
        )
        payload["validation_window"] = DataWindow(
            datetime.fromisoformat(payload["validation_window"]["start"]),
            datetime.fromisoformat(payload["validation_window"]["end"]),
        )
        payload["reliability_data"] = tuple(payload["reliability_data"])
        return CalibrationArtifact(**payload)


@dataclass(frozen=True)
class PredictionInterval:
    lower: float
    upper: float
    coverage: float

    def __post_init__(self) -> None:
        if self.upper < self.lower:
            raise ValueError("prediction interval upper must be >= lower")
        if not 0.0 < self.coverage < 1.0:
            raise ValueError("coverage must be in (0, 1)")

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def crosses_zero(self) -> bool:
        return self.lower <= 0.0 <= self.upper


@dataclass(frozen=True)
class ConformalArtifact:
    conformal_id: str
    model_artifact_id: str
    calibration_window: DataWindow
    sample_count: int
    alpha: float
    residual_quantile: float
    input_hash: str
    created_at: datetime


def fit_split_conformal(
    *,
    model_artifact_id: str,
    calibration_predictions: np.ndarray,
    calibration_outcomes: np.ndarray,
    calibration_window: DataWindow,
    alpha: float = 0.10,
) -> ConformalArtifact:
    predictions = np.asarray(calibration_predictions, dtype=float)
    outcomes = np.asarray(calibration_outcomes, dtype=float)
    if (
        predictions.ndim != 1
        or predictions.shape != outcomes.shape
        or predictions.size < 20
    ):
        raise ValueError(
            "conformal calibration requires equal 1D samples of size >= 20"
        )
    if not np.all(np.isfinite(predictions)) or not np.all(np.isfinite(outcomes)):
        raise ValueError("conformal calibration sample must be finite")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    residuals = np.abs(outcomes - predictions)
    quantile_level = min(
        1.0, math.ceil((len(residuals) + 1) * (1.0 - alpha)) / len(residuals)
    )
    quantile = float(np.quantile(residuals, quantile_level, method="higher"))
    input_hash = _canonical_sha256(
        {
            "model_artifact_id": model_artifact_id,
            "predictions": predictions.tolist(),
            "outcomes": outcomes.tolist(),
            "window": asdict(calibration_window),
            "alpha": alpha,
        }
    )
    return ConformalArtifact(
        conformal_id=f"conf_{input_hash[:32]}",
        model_artifact_id=model_artifact_id,
        calibration_window=calibration_window,
        sample_count=int(predictions.size),
        alpha=float(alpha),
        residual_quantile=quantile,
        input_hash=input_hash,
        created_at=datetime.now(UTC),
    )


def conformal_interval(
    prediction: float, artifact: ConformalArtifact
) -> PredictionInterval:
    return PredictionInterval(
        lower=float(prediction) - artifact.residual_quantile,
        upper=float(prediction) + artifact.residual_quantile,
        coverage=1.0 - artifact.alpha,
    )


@dataclass(frozen=True)
class ExposureUncertainty:
    calibration_state: CalibrationState
    ece: float
    ood_score: float
    interval: PredictionInterval
    regime_entropy: float


class MonotonicExposurePolicy:
    """Each uncertainty component is a non-increasing exposure multiplier."""

    _CALIBRATION_MULTIPLIER = {
        CalibrationState.CALIBRATED: 1.0,
        CalibrationState.DEGRADED: 0.50,
        CalibrationState.UNCALIBRATED: 0.25,
        CalibrationState.STALE: 0.10,
    }

    def __init__(self, *, interval_width_scale: float = 0.01) -> None:
        if interval_width_scale <= 0.0:
            raise ValueError("interval_width_scale must be positive")
        self.interval_width_scale = float(interval_width_scale)

    def allowed_directional_exposure(
        self,
        requested_exposure: float,
        uncertainty: ExposureUncertainty,
    ) -> float:
        requested = max(0.0, float(requested_exposure))
        if uncertainty.interval.crosses_zero:
            return 0.0
        calibration = self._CALIBRATION_MULTIPLIER[uncertainty.calibration_state]
        calibration_quality = float(np.clip(1.0 - uncertainty.ece, 0.0, 1.0))
        ood = float(np.clip(1.0 - uncertainty.ood_score, 0.0, 1.0))
        interval = 1.0 / (1.0 + uncertainty.interval.width / self.interval_width_scale)
        regime = float(np.clip(1.0 - uncertainty.regime_entropy, 0.0, 1.0))
        return requested * calibration * calibration_quality * ood * interval * regime
