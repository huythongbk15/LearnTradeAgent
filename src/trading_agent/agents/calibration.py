"""Confidence calibration for agent/LLM outputs (audit Phase 5).

A confidence of 0.8 is *calibrated* when the signal is right ~80% of the
time.  This module computes reliability diagrams + Expected Calibration
Error (ECE) from (confidence, correct) observations and can rescale a raw
confidence into a calibrated one using bin-level empirical accuracy.

Usage::

    cal = ConfidenceCalibrator()
    cal.add_observation(0.8, correct=True)
    cal.add_observation(0.6, correct=False)
    ...
    calibrated = cal.calibrate(0.75)
    report = cal.report()   # {bins: [...], ece: float, n: int}
"""

from __future__ import annotations

from dataclasses import dataclass

BIN_EDGES = tuple(i / 10 for i in range(11))  # 0.0, 0.1, ..., 1.0


@dataclass
class ReliabilityBin:
    """Empirical accuracy inside a confidence bin."""

    bin_index: int
    confidence_low: float
    confidence_high: float
    count: int
    avg_confidence: float
    accuracy: float


class ConfidenceCalibrator:
    """Collect (confidence, correct) pairs and calibrate new confidences."""

    def __init__(self, bins: int = 10) -> None:
        self.n_bins = bins
        # per-bin: total confidence, correct count, observation count
        self._bin_confidence: list[float] = [0.0] * bins
        self._bin_correct: list[int] = [0] * bins
        self._bin_count: list[int] = [0] * bins

    # ── ingestion ──────────────────────────────────────────────────────────

    def add_observation(self, confidence: float, correct: bool) -> None:
        """Record one (confidence, correctness) observation."""
        confidence = max(0.0, min(1.0, float(confidence)))
        idx = min(int(confidence * self.n_bins), self.n_bins - 1)
        self._bin_confidence[idx] += confidence
        self._bin_count[idx] += 1
        if correct:
            self._bin_correct[idx] += 1

    def add_many(self, pairs: list[tuple[float, bool]]) -> None:
        for confidence, correct in pairs:
            self.add_observation(confidence, correct)

    # ── analysis ───────────────────────────────────────────────────────────

    @property
    def total_observations(self) -> int:
        return sum(self._bin_count)

    def reliability_curve(self) -> list[ReliabilityBin]:
        """Per-bin empirical accuracy vs average confidence."""
        bins: list[ReliabilityBin] = []
        for idx in range(self.n_bins):
            count = self._bin_count[idx]
            if count == 0:
                continue
            avg_conf = self._bin_confidence[idx] / count
            accuracy = self._bin_correct[idx] / count
            bins.append(
                ReliabilityBin(
                    bin_index=idx,
                    confidence_low=idx / self.n_bins,
                    confidence_high=(idx + 1) / self.n_bins,
                    count=count,
                    avg_confidence=avg_conf,
                    accuracy=accuracy,
                )
            )
        return bins

    def expected_calibration_error(self) -> float:
        """ECE = sum(|accuracy - confidence| * count) / total."""
        total = self.total_observations
        if total == 0:
            return 0.0
        error = 0.0
        for bin_ in self.reliability_curve():
            error += abs(bin_.accuracy - bin_.avg_confidence) * bin_.count
        return error / total

    # ── calibration ────────────────────────────────────────────────────────

    def calibrate(self, confidence: float) -> float:
        """Rescale raw confidence to empirical accuracy of its bin.

        Unseen bins (no observations) fall back to the raw confidence.
        """
        confidence = max(0.0, min(1.0, float(confidence)))
        idx = min(int(confidence * self.n_bins), self.n_bins - 1)
        count = self._bin_count[idx]
        if count == 0:
            return confidence
        return self._bin_correct[idx] / count

    def report(self) -> dict:
        """Compact summary for dashboards/CLI."""
        bins = self.reliability_curve()
        return {
            "n": self.total_observations,
            "ece": round(self.expected_calibration_error(), 4),
            "bins": [
                {
                    "low": round(b.confidence_low, 2),
                    "high": round(b.confidence_high, 2),
                    "count": b.count,
                    "avg_confidence": round(b.avg_confidence, 3),
                    "accuracy": round(b.accuracy, 3),
                }
                for b in bins
            ],
        }

    # ── persistence ────────────────────────────────────────────────────────

    def to_json(self) -> dict:
        return {
            "n_bins": self.n_bins,
            "bin_confidence": self._bin_confidence,
            "bin_correct": self._bin_correct,
            "bin_count": self._bin_count,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ConfidenceCalibrator":
        cal = cls(bins=int(data["n_bins"]))
        cal._bin_confidence = list(map(float, data["bin_confidence"]))
        cal._bin_correct = list(map(int, data["bin_correct"]))
        cal._bin_count = list(map(int, data["bin_count"]))
        return cal
