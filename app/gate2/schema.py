"""Gate2 score_v2 schema and weights artifact (G1).

Runtime-eligible leaf (promoted from app/research/gate2): imports stdlib only.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Union

CONDITION_SCORE_NAMES: tuple[str, ...] = (
    "pullback_strength_score",
    "rebound_strength_score",
    "controlled_down_quality_score",
    "gap_down_quality_score",
    "range_recovery_score",
    "trend_alignment_score",
    "macd_momentum_score",
    "volume_rank_score",
    "volume_power_score",
    "cost_quality_score",
    "mean_reversion_score",
    "price_velocity_score",
    "liquidity_score",
    "volatility_risk_score",
    "portfolio_diversification_score",
)


@dataclass(frozen=True)
class ScoreV2Artifact:
    version: str
    weights: dict[str, float]
    buy_threshold: float
    normalization_caps: dict[str, float]


def validate_artifact(artifact: ScoreV2Artifact) -> None:
    """Raise ValueError if the artifact violates the score_v2 contract."""
    weight_keys = set(artifact.weights)
    expected_keys = set(CONDITION_SCORE_NAMES)
    if weight_keys != expected_keys:
        missing = expected_keys - weight_keys
        extra = weight_keys - expected_keys
        raise ValueError(
            f"weights key set mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )
    for name, weight in artifact.weights.items():
        if not (0.0 <= weight <= 1.0):
            raise ValueError(f"weight out of range [0.0, 1.0]: {name}={weight}")
    if not (0.0 <= artifact.buy_threshold <= 100.0):
        raise ValueError(
            f"buy_threshold out of range [0, 100]: {artifact.buy_threshold}"
        )
    for name, cap in artifact.normalization_caps.items():
        if not (cap > 0):
            raise ValueError(f"normalization cap must be positive: {name}={cap}")


def save_artifact(artifact: ScoreV2Artifact, path: Union[str, Path]) -> None:
    """Serialize the artifact to JSON at ``path``."""
    Path(path).write_text(json.dumps(asdict(artifact), indent=2, sort_keys=True))


def load_artifact(path: Union[str, Path]) -> ScoreV2Artifact:
    """Load a ScoreV2Artifact from JSON at ``path``."""
    data = json.loads(Path(path).read_text())
    return ScoreV2Artifact(
        version=data["version"],
        weights=dict(data["weights"]),
        buy_threshold=data["buy_threshold"],
        normalization_caps=dict(data["normalization_caps"]),
    )


def default_artifact() -> ScoreV2Artifact:
    """Return the default score_v2 weights artifact (version score_v2_w0)."""
    weights = {
        "pullback_strength_score": 1.0,
        "rebound_strength_score": 1.0,
        "controlled_down_quality_score": 1.0,
        "gap_down_quality_score": 1.0,
        "range_recovery_score": 1.0,
        "volume_rank_score": 1.0,
        "volume_power_score": 1.0,
        "trend_alignment_score": 0.5,
        "macd_momentum_score": 0.5,
        "cost_quality_score": 0.5,
        "mean_reversion_score": 0.5,
        "price_velocity_score": 0.5,
        "volatility_risk_score": 0.5,
        "portfolio_diversification_score": 0.5,
        "liquidity_score": 0.25,
    }
    normalization_caps = {
        "trend_alignment": 0.35,
        "macd_momentum": 0.20,
        "cost_quality": 0.35,
        "mean_reversion": 0.30,
        "velocity_bonus": 0.20,
        "velocity_penalty": 0.20,
        "diversification_bonus": 0.15,
        "portfolio_correlation_penalty": 0.37,
        "variance_increase_penalty": 0.50,
    }
    return ScoreV2Artifact(
        version="score_v2_w0",
        weights=weights,
        buy_threshold=60.0,
        normalization_caps=normalization_caps,
    )
