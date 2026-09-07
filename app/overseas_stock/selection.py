from dataclasses import dataclass
from pathlib import Path

from app.auth.settings import PROJECT_ROOT
from app.overseas_stock.market_snapshot import build_overseas_market_snapshot, OverseasMarketSnapshot, OverseasSnapshotError
from app.overseas_stock.scoring import compute_overseas_score_components
from app.gate2.adapter import condition_scores_from_v1
from app.gate2.score_v2 import weighted_gate2_score, passes_threshold
from app.gate2.schema import load_artifact, default_artifact, ScoreV2Artifact

US_ARTIFACT_PATH = PROJECT_ROOT / "app" / "gate2" / "artifacts" / "score_v2_us_w0.json"


@dataclass(frozen=True)
class OverseasScoringParams:
    rebound_from_low_pct: float = 0.01
    controlled_down_day_min: float = -3.0
    controlled_down_day_max: float = -0.3
    gap_down_open_min_pct: float = 0.3
    gap_down_open_max_pct: float = 2.5
    range_recovery_min_ratio: float = 0.5


@dataclass(frozen=True)
class OverseasCandidate:
    symbol: str
    exchange: str
    snapshot: OverseasMarketSnapshot
    final_score: float
    contributions: dict
    missing: tuple
    passed_threshold: bool
    score_components: dict
    conditions: dict


def load_us_artifact(path=US_ARTIFACT_PATH) -> ScoreV2Artifact:
    if Path(path).exists():
        return load_artifact(path)
    return default_artifact()


def score_overseas_candidate(snapshot, *, exchange, artifact, params) -> OverseasCandidate:
    components = compute_overseas_score_components(
        snapshot,
        rebound_from_low_pct=params.rebound_from_low_pct,
        controlled_down_day_min=params.controlled_down_day_min,
        controlled_down_day_max=params.controlled_down_day_max,
        gap_down_open_min_pct=params.gap_down_open_min_pct,
        gap_down_open_max_pct=params.gap_down_open_max_pct,
        range_recovery_min_ratio=params.range_recovery_min_ratio,
    )
    conditions = condition_scores_from_v1(components, artifact.normalization_caps)
    result = weighted_gate2_score(conditions, artifact.weights)
    passed = passes_threshold(result, artifact)
    return OverseasCandidate(
        snapshot.symbol,
        exchange,
        snapshot,
        result.final_score,
        result.contributions,
        result.missing,
        passed,
        components,
        conditions,
    )


def select_overseas_top(candidates, *, require_threshold=True):
    pool = [c for c in candidates if c.passed_threshold] if require_threshold else list(candidates)
    if not pool:
        return None
    return sorted(pool, key=lambda c: (-c.final_score, c.symbol))[0]


def scan_overseas_universe(symbols, *, fetch_detail, exchange="NASD", artifact=None, params=None) -> list:
    artifact = artifact or load_us_artifact()
    params = params or OverseasScoringParams()
    candidates = []
    for symbol in symbols:
        try:
            output = fetch_detail(symbol, exchange)
            snap = build_overseas_market_snapshot(output)
            candidates.append(
                score_overseas_candidate(snap, exchange=exchange, artifact=artifact, params=params)
            )
        except Exception:
            # Scanner resilience: a single symbol's fetch/parse failure must NOT abort the whole universe.
            continue
    return candidates
