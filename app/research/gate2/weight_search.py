"""Walk-forward weight/threshold search harness for score_v2 (W1).

Leaf research module: imports stdlib + gate2 sibling modules only (no other app.* imports).
"""

import random
from dataclasses import dataclass
from typing import Optional

from app.gate2.schema import (
    CONDITION_SCORE_NAMES,
    ScoreV2Artifact,
    validate_artifact,
)
from app.gate2.score_v2 import weighted_gate2_score


@dataclass(frozen=True)
class EvaluationRecord:
    symbol: str
    ts: str
    scores: dict
    forward_return_bps: float


@dataclass(frozen=True)
class WalkForwardWindow:
    train_ts: tuple[str, ...]
    test_ts: tuple[str, ...]


def build_walk_forward_windows(
    records,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
) -> list[WalkForwardWindow]:
    """Roll fixed-length train/test windows over the sorted unique timestamps."""
    if train_size < 1:
        raise ValueError(f"train_size must be >= 1: {train_size}")
    if test_size < 1:
        raise ValueError(f"test_size must be >= 1: {test_size}")
    if step is None:
        step = test_size
    if step < 1:
        raise ValueError(f"step must be >= 1: {step}")

    unique_ts = sorted({record.ts for record in records})
    n = len(unique_ts)
    windows: list[WalkForwardWindow] = []
    i = 0
    while i < n:
        train = unique_ts[i : i + train_size]
        test = unique_ts[i + train_size : i + train_size + test_size]
        if len(train) == train_size and len(test) == test_size:
            windows.append(
                WalkForwardWindow(train_ts=tuple(train), test_ts=tuple(test))
            )
        i += step
    return windows


@dataclass(frozen=True)
class CandidateEvaluation:
    version: str
    selected_count: int
    mean_selected_return_bps: float
    hit_rate: float
    total_return_bps: float


def evaluate_candidate(records, artifact: ScoreV2Artifact) -> CandidateEvaluation:
    """Evaluate one candidate artifact over the supplied records."""
    selected_returns: list[float] = []
    for record in records:
        result = weighted_gate2_score(record.scores, artifact.weights)
        if result.final_score >= artifact.buy_threshold:
            selected_returns.append(record.forward_return_bps)

    selected_count = len(selected_returns)
    total_return_bps = sum(selected_returns)
    if selected_count == 0:
        mean_selected_return_bps = 0.0
        hit_rate = 0.0
    else:
        mean_selected_return_bps = total_return_bps / selected_count
        wins = sum(1 for r in selected_returns if r > 0)
        hit_rate = wins / selected_count

    return CandidateEvaluation(
        version=artifact.version,
        selected_count=selected_count,
        mean_selected_return_bps=mean_selected_return_bps,
        hit_rate=hit_rate,
        total_return_bps=total_return_bps,
    )


def candidate_objective(
    evaluation: CandidateEvaluation, min_selected: int = 1
) -> float:
    """Objective: mean selected return, or -inf when too few selections."""
    if evaluation.selected_count < min_selected:
        return float("-inf")
    return evaluation.mean_selected_return_bps


def generate_coordinate_candidates(
    base: ScoreV2Artifact,
    scales=(0.5, 1.5),
    thresholds=(55.0, 60.0, 65.0),
) -> list[ScoreV2Artifact]:
    """Generate coordinate-descent candidate artifacts around ``base``."""
    caps = dict(base.normalization_caps)
    candidates: list[ScoreV2Artifact] = []

    for threshold in thresholds:
        candidates.append(
            ScoreV2Artifact(
                version=f"base@thr{threshold:g}",
                weights=dict(base.weights),
                buy_threshold=threshold,
                normalization_caps=dict(caps),
            )
        )

    for name in CONDITION_SCORE_NAMES:
        for scale in scales:
            if scale == 1.0:
                continue
            for threshold in thresholds:
                weights = dict(base.weights)
                weights[name] = min(1.0, max(0.0, base.weights[name] * scale))
                candidates.append(
                    ScoreV2Artifact(
                        version=f"{name}x{scale:g}@thr{threshold:g}",
                        weights=weights,
                        buy_threshold=threshold,
                        normalization_caps=dict(caps),
                    )
                )

    for candidate in candidates:
        validate_artifact(candidate)
    return candidates


@dataclass(frozen=True)
class WindowResult:
    window_index: int
    best_version: str
    train_eval: CandidateEvaluation
    test_eval: CandidateEvaluation


@dataclass(frozen=True)
class WalkForwardReport:
    window_results: tuple[WindowResult, ...]
    version_win_counts: dict[str, int]
    overall_best_version: str
    mean_test_return_bps: float


def walk_forward_search(
    records,
    candidates,
    train_size,
    test_size,
    step=None,
    min_selected=1,
):
    """Run a walk-forward weight/threshold search and aggregate the report."""
    if not candidates:
        raise ValueError("candidates must be non-empty")
    windows = build_walk_forward_windows(records, train_size, test_size, step)
    if not windows:
        raise ValueError("no complete walk-forward windows for the given records")

    window_results = [
        _evaluate_window(records, candidates, window_index, window, min_selected)
        for window_index, window in enumerate(windows)
    ]

    version_win_counts = _count_version_wins(window_results, candidates)
    overall_best_version = _pick_overall_best(version_win_counts, candidates)
    mean_test_return_bps = sum(
        wr.test_eval.mean_selected_return_bps for wr in window_results
    ) / len(window_results)

    return WalkForwardReport(
        window_results=tuple(window_results),
        version_win_counts=version_win_counts,
        overall_best_version=overall_best_version,
        mean_test_return_bps=mean_test_return_bps,
    )


def _pick_overall_best(version_win_counts, candidates):
    """Pick the most-winning version, tie-broken by ``candidates`` order."""
    overall_best_version = None
    best_count = -1
    for candidate in candidates:
        version = candidate.version
        count = version_win_counts.get(version)
        if count is not None and count > best_count:
            best_count = count
            overall_best_version = version
    return overall_best_version


def build_search_console_lines(report: WalkForwardReport) -> list[str]:
    """Render the walk-forward report as deterministic console lines."""
    lines: list[str] = []
    for r in report.window_results:
        lines.append(
            f"window={r.window_index} best={r.best_version} "
            f"train_sel={r.train_eval.selected_count} "
            f"train_mean={r.train_eval.mean_selected_return_bps:.2f} "
            f"test_sel={r.test_eval.selected_count} "
            f"test_mean={r.test_eval.mean_selected_return_bps:.2f} "
            f"test_hit={r.test_eval.hit_rate:.2f}"
        )
    lines.append(
        f"overall_best={report.overall_best_version} "
        f"wins={report.version_win_counts[report.overall_best_version]}/"
        f"{len(report.window_results)} "
        f"mean_test_return_bps={report.mean_test_return_bps:.2f}"
    )
    return lines


def _count_version_wins(window_results, candidates):
    """Count winning versions, ordered by ``candidates`` input order."""
    raw_counts: dict[str, int] = {}
    for wr in window_results:
        raw_counts[wr.best_version] = raw_counts.get(wr.best_version, 0) + 1
    ordered: dict[str, int] = {}
    for candidate in candidates:
        version = candidate.version
        if version in raw_counts and version not in ordered:
            ordered[version] = raw_counts[version]
    return ordered


def _select_best_candidate(train_records, candidates, min_selected):
    """Return the (eval, artifact) with the strictly-greatest objective key."""
    best_eval = None
    best_artifact = None
    best_key = None
    for candidate in candidates:
        train_eval = evaluate_candidate(train_records, candidate)
        key = (
            candidate_objective(train_eval, min_selected),
            train_eval.selected_count,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_eval = train_eval
            best_artifact = candidate
    return best_eval, best_artifact


def _evaluate_window(records, candidates, window_index, window, min_selected):
    """Pick the best train candidate for one window and score it on test."""
    train_records = [r for r in records if r.ts in set(window.train_ts)]
    test_records = [r for r in records if r.ts in set(window.test_ts)]
    best_eval, best_artifact = _select_best_candidate(
        train_records, candidates, min_selected
    )
    test_eval = evaluate_candidate(test_records, best_artifact)
    return WindowResult(
        window_index=window_index,
        best_version=best_eval.version,
        train_eval=best_eval,
        test_eval=test_eval,
    )


# ---------------------------------------------------------------------------
# W2 extensions (additive — existing signatures above are unchanged).
#
# Daily walk-forward windows, an extended objective (mean/total/dd_adjusted),
# and a seeded random-candidate generator. See
# docs/gate2_minute_backtest_plan_20260702.md §7 W2.
# ---------------------------------------------------------------------------


def build_daily_walk_forward_windows(
    records,
    train_days: int,
    test_days: int,
    step_days: Optional[int] = None,
) -> list[WalkForwardWindow]:
    """Roll fixed-length train/test windows over the DATE part of ``record.ts``.

    Mirrors ``build_walk_forward_windows`` but groups timestamps by their
    ``YYYY-MM-DD`` date prefix (the ts-unit builder stays as-is). ``step_days``
    defaults to ``test_days`` (same step convention as the ts-unit builder).
    Train/test date sets never overlap by construction (disjoint slices of the
    sorted unique-date list).
    """
    if train_days < 1:
        raise ValueError(f"train_days must be >= 1: {train_days}")
    if test_days < 1:
        raise ValueError(f"test_days must be >= 1: {test_days}")
    if step_days is None:
        step_days = test_days
    if step_days < 1:
        raise ValueError(f"step_days must be >= 1: {step_days}")

    unique_days = sorted({record.ts[:10] for record in records})
    n = len(unique_days)
    windows: list[WalkForwardWindow] = []
    i = 0
    while i < n:
        train = unique_days[i : i + train_days]
        test = unique_days[i + train_days : i + train_days + test_days]
        if len(train) == train_days and len(test) == test_days:
            windows.append(
                WalkForwardWindow(train_ts=tuple(train), test_ts=tuple(test))
            )
        i += step_days
    return windows


def generate_random_candidates(
    base: ScoreV2Artifact,
    n: int,
    seed: int,
    *,
    weight_low: float = 0.0,
    weight_high: float = 1.5,
    thresholds=(55, 60, 65),
    frozen_keys=(
        "portfolio_diversification_score",
        "volatility_risk_score",
    ),
) -> list[ScoreV2Artifact]:
    """Generate ``n`` seed-reproducible random candidate artifacts.

    Each candidate draws a fresh weight per condition in ``[weight_low,
    weight_high]`` (clamped into the schema range ``[0, 1]`` — the same clamp
    the coordinate builder uses, so ``weight_high>1`` biases toward the 1.0
    cap) EXCEPT for ``frozen_keys``, which keep ``base``'s values. Those two
    dims are signal-less on portfolio-stateless records (2차 리뷰 F-3), so
    Stage A only searches the remaining 13. ``normalization_caps`` are carried
    through from ``base`` unchanged; the ``buy_threshold`` is drawn from
    ``thresholds``. Determinism comes from a single ``random.Random(seed)``.
    """
    rng = random.Random(seed)
    frozen = set(frozen_keys)
    candidates: list[ScoreV2Artifact] = []
    for i in range(n):
        weights: dict[str, float] = {}
        for name in CONDITION_SCORE_NAMES:
            if name in frozen:
                weights[name] = base.weights[name]
                continue
            drawn = rng.uniform(weight_low, weight_high)
            weights[name] = min(1.0, max(0.0, drawn))
        threshold = rng.choice(thresholds)
        candidate = ScoreV2Artifact(
            version=f"w1-rand-{seed}-{i}",
            weights=weights,
            buy_threshold=threshold,
            normalization_caps=dict(base.normalization_caps),
        )
        validate_artifact(candidate)
        candidates.append(candidate)
    return candidates


@dataclass(frozen=True)
class CandidateEvaluationV2:
    """Superset of ``CandidateEvaluation`` that also carries the ordered
    selected-return sequence needed for drawdown-aware objectives.

    ``evaluate_candidate``'s signature/return type are intentionally left
    unchanged; this is an additive sibling used only by the W2 objective.
    """

    version: str
    selected_count: int
    mean_selected_return_bps: float
    hit_rate: float
    total_return_bps: float
    selected_returns: tuple[float, ...]


def evaluate_candidate_v2(
    records, artifact: ScoreV2Artifact
) -> CandidateEvaluationV2:
    """Like ``evaluate_candidate`` but also preserves the ordered sequence of
    selected forward returns (record order), so a drawdown proxy can be
    computed by ``candidate_objective_v2`` without changing ``evaluate_candidate``.
    """
    selected_returns: list[float] = []
    for record in records:
        result = weighted_gate2_score(record.scores, artifact.weights)
        if result.final_score >= artifact.buy_threshold:
            selected_returns.append(record.forward_return_bps)

    selected_count = len(selected_returns)
    total_return_bps = sum(selected_returns)
    if selected_count == 0:
        mean_selected_return_bps = 0.0
        hit_rate = 0.0
    else:
        mean_selected_return_bps = total_return_bps / selected_count
        wins = sum(1 for r in selected_returns if r > 0)
        hit_rate = wins / selected_count

    return CandidateEvaluationV2(
        version=artifact.version,
        selected_count=selected_count,
        mean_selected_return_bps=mean_selected_return_bps,
        hit_rate=hit_rate,
        total_return_bps=total_return_bps,
        selected_returns=tuple(selected_returns),
    )


def _max_drawdown_of_cumsum(returns) -> float:
    """Max drawdown of the cumulative sum of ``returns`` (a drawdown proxy).

    Returns 0.0 for an empty or monotonically non-decreasing cumulative curve.
    Drawdown at each step = running peak of the cumulative sum minus the
    current cumulative sum; the proxy is the maximum such gap.
    """
    cumulative = 0.0
    peak = float("-inf")
    max_dd = 0.0
    for r in returns:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd


def candidate_objective_v2(
    evaluation,
    *,
    mode: str,
    min_selected: int = 30,
    dd_lambda: float = 0.5,
) -> float:
    """Extended objective over an evaluation carrying selected returns.

    ``mode``:
      * ``"mean"``  — mean selected return (same semantics as
        ``candidate_objective`` / ``mean_selected_return_bps``).
      * ``"total"`` — sum of selected returns.
      * ``"dd_adjusted"`` — ``total - dd_lambda * max_drawdown`` where
        ``max_drawdown`` is the max drawdown of the cumulative sum of the
        selected return sequence (see ``_max_drawdown_of_cumsum``).

    Below ``min_selected`` selections → ``-inf``. ``dd_adjusted`` reads the
    ordered ``selected_returns`` sequence, so ``evaluation`` must be a
    ``CandidateEvaluationV2`` (from ``evaluate_candidate_v2``).
    """
    if evaluation.selected_count < min_selected:
        return float("-inf")
    if mode == "mean":
        return evaluation.mean_selected_return_bps
    if mode == "total":
        return evaluation.total_return_bps
    if mode == "dd_adjusted":
        max_dd = _max_drawdown_of_cumsum(evaluation.selected_returns)
        return evaluation.total_return_bps - dd_lambda * max_dd
    raise ValueError(f"unknown mode: {mode!r}")
