"""M-C1 — regime library (offline): k-means clustering, conditional gate2
weight search per regime, and library build (plan §4).

Leaf research module. numpy/pandas/pyarrow imported inside functions; the only
``app.*`` imports are the read-only gate2 sibling contracts (schema + the W2
weight-search harness). Consumes gate2 records/artifacts read-only.
"""

from __future__ import annotations


def kmeans(z_matrix, k, *, seed: int = 20260702, iters: int = 100):
    """Lloyd's k-means over ``z_matrix`` (rows = samples).

    Initial centroids = a seed-RNG sample of ``k`` distinct rows; each iteration
    assigns every row to its nearest centroid (ties → lowest index) then recomputes
    centroids as the mean of their members (an empty cluster keeps its prior
    centroid). Iterates until labels are stable or ``iters`` is reached. Fully
    deterministic for a fixed ``seed``. Returns ``(centroids, labels)`` as numpy
    arrays (``centroids`` shape ``(k, n_features)``, ``labels`` shape ``(n,)``).
    """
    import numpy as np

    x = np.asarray(z_matrix, dtype=float)
    n = x.shape[0]
    rng = np.random.default_rng(seed)
    init_idx = rng.choice(n, size=k, replace=False)
    centroids = x[init_idx].copy()

    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        # (n, k) squared distances; argmin ties resolve to the lowest index.
        dists = ((x[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        new_labels = dists.argmin(axis=1)
        for j in range(k):
            members = x[new_labels == j]
            if len(members) > 0:
                centroids[j] = members.mean(axis=0)
        if np.array_equal(new_labels, labels):
            labels = new_labels
            break
        labels = new_labels
    return centroids, labels


def silhouette(z_matrix, labels) -> float:
    """Mean silhouette coefficient over all samples (standard definition).

    For each sample ``i``: ``a`` = mean intra-cluster distance to the other
    members of its cluster, ``b`` = the minimum over other clusters of the mean
    distance to that cluster's members; ``s = (b - a) / max(a, b)``. A sample
    whose cluster is a singleton contributes ``s = 0`` (convention). The result
    is the mean of ``s`` over all samples.
    """
    import numpy as np

    x = np.asarray(z_matrix, dtype=float)
    labs = np.asarray(labels)
    n = x.shape[0]
    unique = np.unique(labs)
    if len(unique) < 2:
        return 0.0

    # Pairwise Euclidean distances (n, n).
    diff = x[:, None, :] - x[None, :, :]
    dist = np.sqrt((diff**2).sum(axis=2))

    scores = np.zeros(n, dtype=float)
    for i in range(n):
        own = labs[i]
        own_mask = labs == own
        own_count = int(own_mask.sum())
        if own_count <= 1:
            scores[i] = 0.0
            continue
        # a: mean distance to other members of own cluster (exclude self).
        a = dist[i, own_mask].sum() / (own_count - 1)
        b = np.inf
        for other in unique:
            if other == own:
                continue
            other_mask = labs == other
            mean_other = dist[i, other_mask].mean()
            if mean_other < b:
                b = mean_other
        denom = max(a, b)
        scores[i] = 0.0 if denom == 0 else (b - a) / denom
    return float(scores.mean())


def choose_k(z_matrix, *, k_range=(3, 4, 5, 6, 7), seed) -> int:
    """Pick the ``k`` in ``k_range`` whose k-means labelling has the highest mean
    silhouette (ties → the smallest ``k``, favouring the simpler model).

    Each candidate ``k`` is clustered with the same ``seed`` so the choice is
    deterministic.
    """
    best_k = None
    best_score = float("-inf")
    for k in k_range:
        _, labels = kmeans(z_matrix, k, seed=seed)
        score = silhouette(z_matrix, labels)
        if score > best_score:
            best_score = score
            best_k = k
    return best_k


def conditional_kr_days(regime_us_days, kr_us_map):
    """KR trading days whose mapped US session is in ``regime_us_days``.

    ``kr_us_map`` is the ``(kr_day, us_day, gap_days)`` frame from
    ``calendar_map.to_frame``. Returns the matching ``kr_day`` values as a
    sorted ascending tuple.
    """
    wanted = set(regime_us_days)
    mask = kr_us_map["us_day"].isin(wanted)
    days = kr_us_map.loc[mask, "kr_day"].tolist()
    return tuple(sorted(days))


def records_from_frame(frame):
    """Convert an R1 records frame to ``EvaluationRecord``s.

    Each row's 15 condition-score columns become the record's ``scores`` dict
    (NaN -> ``None``, mirroring the record-stage portfolio-condition Nones);
    ``forward_return_bps`` is carried through. ``truncated`` is dropped — it is
    not part of ``EvaluationRecord`` and the search does not use it. This is the
    same row→record mapping as ``run_gate2_weight_search.load_records`` (plan §4
    "records_from_frame ... 본체 W2 함수 재사용").
    """
    import math

    from app.gate2.schema import CONDITION_SCORE_NAMES
    from app.research.gate2.weight_search import EvaluationRecord

    symbols = frame["symbol"].tolist()
    timestamps = frame["ts"].tolist()
    returns = frame["forward_return_bps"].tolist()
    score_cols = {name: frame[name].tolist() for name in CONDITION_SCORE_NAMES}

    records = []
    for i in range(len(frame)):
        scores = {}
        for name in CONDITION_SCORE_NAMES:
            value = score_cols[name][i]
            if value is None or (isinstance(value, float) and math.isnan(value)):
                scores[name] = None
            else:
                scores[name] = float(value)
        records.append(
            EvaluationRecord(
                symbol=str(symbols[i]),
                ts=str(timestamps[i]),
                scores=scores,
                forward_return_bps=float(returns[i]),
            )
        )
    return records


def load_records_for_days(records_path, kr_days):
    """Load records for a SCATTERED set of ``kr_days`` at the pyarrow level.

    Unlike ``run_gate2_weight_search.load_records`` (a single CONTIGUOUS
    ``[start, end]`` range), a regime's conditional KR days are non-contiguous.
    A single ``pq.read_table`` is filtered by ONE combined mask = OR over the
    per-day ranges ``[f"{d}T00:00:00", f"{d}T23:59:59.999999"]`` (ISO strings
    compare lexicographically), so the full-period frame never stays resident
    (plan §4: "kr_day 필터를 pyarrow 단계에서 적용"). The surviving rows go
    through the same ``records_from_frame`` mapping.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(records_path)
    ts_col = table.column("ts")

    mask = None
    for d in kr_days:
        lo = f"{d.isoformat()}T00:00:00"
        hi = f"{d.isoformat()}T23:59:59.999999"
        day_mask = pc.and_(
            pc.greater_equal(ts_col, lo),
            pc.less_equal(ts_col, hi),
        )
        mask = day_mask if mask is None else pc.or_(mask, day_mask)

    if mask is None:
        return []

    table = table.filter(mask)
    return records_from_frame(table.to_pandas())


def _eval_summary(evaluation) -> dict:
    """Serialisable summary of a ``CandidateEvaluationV2`` for the library."""
    return {
        "version": evaluation.version,
        "selected_count": evaluation.selected_count,
        "mean_selected_return_bps": evaluation.mean_selected_return_bps,
        "hit_rate": evaluation.hit_rate,
        "total_return_bps": evaluation.total_return_bps,
    }


def evaluate_regime(
    records,
    kr_days,
    base_artifact,
    candidates,
    *,
    min_days: int = 40,
    min_selected_train: int = 30,
) -> dict:
    """Conditional gate2 search + OOS/baseline judgement for one regime (plan §4).

    ``records`` are the ``EvaluationRecord``s already loaded for this regime's
    ``kr_days`` (see ``load_records_for_days``). Steps:

    1. ``len(kr_days) < min_days`` → ``{"enrolled": False, "reason": "min_days"}``.
    2. Time-ordered 70/30 split BY DAY: the first 70% of the sorted ``kr_days``
       are train days, the last 30% are test days. Records are bucketed by their
       ``ts`` date (``ts[:10]``).
    3. Train: pick the candidate maximising ``candidate_objective_v2(
       mode="dd_adjusted", min_selected=min_selected_train)`` over the train
       records (ties → ``candidates`` input order, matching the W1 harness).
    4. Guard interaction (review #13): ``min_selected_test = max(10,
       test_day_count)``. If the winner selects fewer than that on the test set
       → ``{"enrolled": False, "reason": "min_selected_test"}``.
    5. Test: evaluate the winner AND ``base_artifact`` on the test records with
       the same ``dd_adjusted`` objective, side by side. ``enrolled`` is True
       ONLY if the candidate test objective strictly exceeds the baseline's.

    The returned dict always carries ``candidate_test`` / ``baseline_test``
    summaries once past the day-count guard (both null before then).
    """
    from app.research.gate2.weight_search import (
        candidate_objective_v2,
        evaluate_candidate_v2,
    )

    ordered_days = sorted(set(kr_days))
    if len(ordered_days) < min_days:
        return {
            "enrolled": False,
            "reason": "min_days",
            "candidate_test": None,
            "baseline_test": None,
        }

    # 70/30 time-ordered split by day. Days are compared as ``YYYY-MM-DD``
    # strings so a ``date``-keyed kr_days set matches an ``EvaluationRecord.ts``
    # prefix regardless of whether kr_days are ``date`` or ISO strings.
    split_at = int(len(ordered_days) * 0.7)
    train_days = {_day_key(d) for d in ordered_days[:split_at]}
    test_days_list = ordered_days[split_at:]
    test_days = {_day_key(d) for d in test_days_list}
    test_day_count = len(test_days_list)

    train_records = [r for r in records if _record_day(r) in train_days]
    test_records = [r for r in records if _record_day(r) in test_days]

    # Train: argmax dd_adjusted objective; ties resolve to input order.
    best_artifact = None
    best_key = None
    for candidate in candidates:
        train_eval = evaluate_candidate_v2(train_records, candidate)
        objective = candidate_objective_v2(
            train_eval, mode="dd_adjusted", min_selected=min_selected_train
        )
        key = (objective, train_eval.selected_count)
        if best_key is None or key > best_key:
            best_key = key
            best_artifact = candidate

    # Test: winner + baseline under the same metric.
    winner_test = evaluate_candidate_v2(test_records, best_artifact)
    baseline_test = evaluate_candidate_v2(test_records, base_artifact)

    # Guard interaction (review #13): day-proportional minimum test selections.
    min_selected_test = max(10, test_day_count)
    if winner_test.selected_count < min_selected_test:
        return {
            "enrolled": False,
            "reason": "min_selected_test",
            "winner_version": best_artifact.version,
            "candidate_test": _eval_summary(winner_test),
            "baseline_test": _eval_summary(baseline_test),
        }

    winner_objective = candidate_objective_v2(
        winner_test, mode="dd_adjusted", min_selected=min_selected_test
    )
    baseline_objective = candidate_objective_v2(
        baseline_test, mode="dd_adjusted", min_selected=1
    )

    enrolled = winner_objective > baseline_objective
    return {
        "enrolled": enrolled,
        "reason": "improved" if enrolled else "baseline_not_beaten",
        "winner_version": best_artifact.version,
        "winner_artifact": best_artifact,
        "candidate_objective": winner_objective,
        "baseline_objective": baseline_objective,
        "candidate_test": _eval_summary(winner_test),
        "baseline_test": _eval_summary(baseline_test),
    }


def _record_day(record) -> str:
    """The ``YYYY-MM-DD`` date prefix of an ``EvaluationRecord.ts``."""
    return record.ts[:10]


def _day_key(day) -> str:
    """Normalise a ``date`` or ISO-date string to a ``YYYY-MM-DD`` key."""
    isoformat = getattr(day, "isoformat", None)
    return isoformat() if isoformat is not None else str(day)[:10]


def build_regime_library(
    feature_frame,
    kr_us_map,
    records_for_kr_days,
    base_artifact,
    candidates,
    *,
    k=None,
    seed: int = 20260702,
    assets=None,
    min_days: int = 40,
    min_selected_train: int = 30,
) -> dict:
    """Cluster US days into regimes, run conditional gate2 search per regime,
    and assemble the regime-library dict (plan §4 schema).

    ``feature_frame`` is indexed by ``us_day`` with ``{asset}_{feature}``
    columns. The **fixed** scaler (mean/std, sample std ddof=1) is computed once
    here from ``feature_frame`` and stored in the library — the morning job
    reuses it verbatim (no expanding recomputation). US days are z-scored with
    that fixed scaler and clustered by k-means (``k`` = given, else
    ``choose_k``). ``max_assign_distance`` is the p95 of each training row's
    Euclidean distance to its assigned centroid.

    For each regime the conditional KR days (``conditional_kr_days``) are
    resolved, their records fetched via the ``records_for_kr_days(kr_days)``
    callable, and ``evaluate_regime`` decides enrolment. Enrolled regimes carry
    the winning artifact (as a plain dict); non-enrolled ones carry ``artifact:
    null`` and the rejection ``reason``. ``fallback`` is always ``"baseline"``.
    """
    from dataclasses import asdict
    from datetime import datetime, timezone

    import numpy as np

    feature_columns = list(feature_frame.columns)
    raw = feature_frame[feature_columns].to_numpy(dtype=float)

    # Fixed scaler (build-time constants; ddof=1 sample std to match M-F1).
    mean_vec = raw.mean(axis=0)
    std_vec = raw.std(axis=0, ddof=1)
    safe_std = np.where(std_vec == 0, 1.0, std_vec)
    z = (raw - mean_vec) / safe_std

    resolved_k = k if k is not None else choose_k(z, seed=seed)
    centroids, labels = kmeans(z, resolved_k, seed=seed)

    # p95 of each training row's distance to its assigned centroid.
    assign_dists = np.sqrt(((z - centroids[labels]) ** 2).sum(axis=1))
    max_assign_distance = float(np.percentile(assign_dists, 95))

    us_days = list(feature_frame.index)
    as_of = _day_key(max(us_days)) if us_days else None

    regimes = []
    for regime_id in range(resolved_k):
        member_us_days = {
            us_days[i] for i in range(len(us_days)) if labels[i] == regime_id
        }
        kr_days = conditional_kr_days(member_us_days, kr_us_map)
        records = records_for_kr_days(kr_days)
        outcome = evaluate_regime(
            records,
            kr_days,
            base_artifact,
            candidates,
            min_days=min_days,
            min_selected_train=min_selected_train,
        )

        enrolled = bool(outcome.get("enrolled"))
        artifact = None
        if enrolled and outcome.get("winner_artifact") is not None:
            artifact = asdict(outcome["winner_artifact"])

        regimes.append(
            {
                "regime_id": regime_id,
                "n_us_days": len(member_us_days),
                "n_kr_days": len(kr_days),
                "artifact": artifact,
                "enrolled": enrolled,
                "reason": outcome.get("reason", ""),
                "candidate_test": outcome.get("candidate_test"),
                "baseline_test": outcome.get("baseline_test"),
            }
        )

    return {
        "version": "regime_lib_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "assets": list(assets) if assets is not None else [],
        "feature_columns": feature_columns,
        "scaler": {
            "mean": {c: float(mean_vec[i]) for i, c in enumerate(feature_columns)},
            "std": {c: float(std_vec[i]) for i, c in enumerate(feature_columns)},
            "as_of": as_of,
        },
        "k": resolved_k,
        "centroids": [[float(v) for v in row] for row in centroids],
        "max_assign_distance": max_assign_distance,
        "regimes": regimes,
        "fallback": "baseline",
    }
