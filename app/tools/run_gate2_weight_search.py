"""CLI: orchestrate the gate2 score_v2 weight search + final report (V1).

End-to-end selection pipeline over R1 per-horizon evaluation records:

1. Load one horizon's records parquet (``--records``), FILTERING by the date
   range predicate on ``ts`` (never assume the file is pre-trimmed to the
   wanted period).
2. Split ONCE, early, into a **tuning** frame (``TUNING_START..TUNING_END``) and
   a **holdout** frame (``HOLDOUT_START..HOLDOUT_END``). The holdout records
   NEVER enter Stage A — the search function only ever receives the tuning
   frame, so a record dated inside the holdout cannot influence selection.
3. **Stage A** (record-level walk-forward): candidates =
   ``generate_coordinate_candidates(default_artifact())`` +
   ``generate_random_candidates(base, n, seed)``; windows =
   ``build_daily_walk_forward_windows(tuning, train_days, test_days)``; per
   window pick the best candidate on train via
   ``candidate_objective_v2(evaluate_candidate_v2(train, cand), mode="dd_adjusted")``
   and evaluate it on test; aggregate per-candidate test-window results
   (win counts + mean test objective). This reuses the W2/W1 primitives; the
   thin aggregation loop lives here because the existing ``walk_forward_search``
   is bound to the v1 objective (plan §7 V1: thin-orchestration-loop path).
4. **Stage B** (expensive; gated by ``--stage-b/--no-stage-b``, default on):
   ``run_portfolio_replay`` (S1) per top-5 finalist over the tuning-period dates
   (parquet provider over ``--minute-parquet-root``).
5. **Holdout**: for the selected best + baseline ``default_artifact()`` — a
   Stage-A-style record evaluation AND (if Stage B enabled) a
   ``run_portfolio_replay`` over the holdout dates.
6. Outputs: ``<out-dir>/final_report.md`` + the selected artifact JSON via
   ``save_artifact`` to ``--artifact-out``. The report embeds the plan §9 risk
   text IN FULL, the baseline's same metrics side-by-side, per-window win
   counts, the selection-count distribution, and the judgment line
   "홀드아웃에서 baseline 이하이면 채택 불가" with the computed PASS/FAIL verdict.

Safety: no broker/KIS/Toss network calls. Like ``app.tools.build_gate2_records``,
this CLI **refuses to run when ``BUY_SCAN_QUOTE_KIS_ENV`` is set** — a truthy
value would make the real scanner (reached via Stage A/B ``run_scan_at``) issue a
live quote token (service.py:649 -> quote_account.py:186-199). Applying the
selected artifact to ``app/gate2/artifacts/`` is operator work.

Usage::

    python -m app.tools.run_gate2_weight_search \
        --records results/gate2_backtest/records_2023-01-02_2025-06-16_h30.parquet \
        --out-dir results/gate2_backtest \
        --artifact-out app/gate2/artifacts/score_v2_w1_candidate.json \
        --minute-parquet-root data/toss_minute_parquet_backfill
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.gate2.schema import CONDITION_SCORE_NAMES, ScoreV2Artifact
from app.research.gate2.weight_search import (
    EvaluationRecord,
    build_daily_walk_forward_windows,
    candidate_objective_v2,
    evaluate_candidate_v2,
    generate_coordinate_candidates,
    generate_random_candidates,
)

# Tuning / holdout split (plan §7 V1). Named constants so the boundary is
# explicit and auditable; overridable by CLI args for testing ONLY. The holdout
# window is NEVER part of Stage A search.
TUNING_START = date(2023, 1, 2)
TUNING_END = date(2025, 3, 31)
HOLDOUT_START = date(2025, 4, 1)
HOLDOUT_END = date(2025, 6, 16)

# Plan §9 risk/limits text, embedded VERBATIM in the final report (required by
# plan §7 V1). Source of truth: docs/gate2_minute_backtest_plan_20260702.md §9
# ("리스크·한계 (final_report에 전문 수록)") — copied here as a constant so the
# report is self-contained; keep in sync if §9 changes.
RISK_SECTION_TEXT = (
    "생존편향(현 200종목 고정 → 튜닝은 2023+ 중심) / 비조정 가격(D1 게이트로 제외) / "
    "분 내부 경로 미상\n"
    "(S1 stop_first 보수 모드) / 매도 감시 30s→1분 근사 / 체결=다음 분봉 open+고정 "
    "bps(호가·부분체결 없음) /\n"
    "volume rank = 200종목 내 프록시(전시장 아님; power/fluctuation 랭크도 동일 프록시) / "
    "운영 노이즈\n"
    "(rate-limit 백오프·부분평가·잔고 실패) 미모델 → 리플레이는 실물 대비 낙관 / "
    "과최적화 방어 =\n"
    "walk-forward + 홀드아웃 + baseline 판정. **목적은 후보 간 상대 비교** — "
    "절대 수익 예측 아님."
)


def load_records(
    records_path, start: date, end: date, *, sample_every: int = 1
) -> list[EvaluationRecord]:
    """Load one horizon's records parquet as ``EvaluationRecord``s, FILTERING by
    the ``[start, end]`` date range on ``ts`` (both bounds inclusive).

    The predicate is applied at the pyarrow level (never assume the file only
    holds the wanted period — plan §7 V1). Each row's 15 condition-score columns
    become the record's ``scores`` dict (NaN -> ``None``, mirroring the
    record-stage portfolio-condition Nones); ``forward_return_bps`` is carried
    through. ``truncated`` is not needed by the search and is dropped.

    ``sample_every=N`` keeps every Nth row of the date-filtered frame
    (file-order positions 0, N, 2N, ... — deterministic systematic sampling,
    applied AFTER the date filter so out-of-range rows never shift the stride).
    The production h30 file holds ~41M rows; materializing them all as
    ``EvaluationRecord`` objects (a 15-key dict each) needs tens of GB, and
    minute-cadence records of the same symbol are heavily autocorrelated, so a
    stride both fits memory and improves record independence for Stage A.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    lo = f"{start.isoformat()}T00:00:00"
    hi = f"{end.isoformat()}T23:59:59.999999"
    table = pq.read_table(records_path)
    ts_col = table.column("ts")
    mask = pc.and_(
        pc.greater_equal(ts_col, lo),
        pc.less_equal(ts_col, hi),
    )
    table = table.filter(mask)
    stride = max(int(sample_every), 1)
    if stride > 1:
        table = table.take(list(range(0, table.num_rows, stride)))

    symbols = table.column("symbol").to_pylist()
    timestamps = table.column("ts").to_pylist()
    returns = table.column("forward_return_bps").to_pylist()
    score_cols = {name: table.column(name).to_pylist() for name in CONDITION_SCORE_NAMES}

    records: list[EvaluationRecord] = []
    for i in range(table.num_rows):
        scores = {}
        for name in CONDITION_SCORE_NAMES:
            value = score_cols[name][i]
            scores[name] = None if value is None else float(value)
        records.append(
            EvaluationRecord(
                symbol=str(symbols[i]),
                ts=str(timestamps[i]),
                scores=scores,
                forward_return_bps=float(returns[i]),
            )
        )
    return records


def split_tuning_holdout(
    records,
    tuning_start: date,
    tuning_end: date,
    holdout_start: date,
    holdout_end: date,
):
    """Partition ``records`` ONCE into ``(tuning, holdout)`` by the ``ts`` date.

    Done early and exactly once so the holdout can NEVER leak into Stage A: the
    caller passes only ``tuning`` into the search. A record whose date falls in
    neither window is dropped. The two returned lists are disjoint.
    """
    ts_start = tuning_start.isoformat()
    ts_end = tuning_end.isoformat()
    hd_start = holdout_start.isoformat()
    hd_end = holdout_end.isoformat()
    tuning: list = []
    holdout: list = []
    for record in records:
        day = record.ts[:10]
        if ts_start <= day <= ts_end:
            tuning.append(record)
        elif hd_start <= day <= hd_end:
            holdout.append(record)
    return tuning, holdout


@dataclass(frozen=True)
class StageAResult:
    """Aggregated Stage A walk-forward outcome.

    ``finalists`` is the top-5 candidate artifacts by aggregate (train-win count,
    then realized mean test objective, then candidate order). ``win_counts`` maps
    version -> number of windows the candidate was the train-best. Two mean-test
    maps are kept: ``mean_test_objective`` (over the windows where the candidate
    was picked; ``-inf`` if never picked) and ``mean_test_objective_all`` (over
    EVERY window, used for a fair baseline vs. winner side-by-side).
    ``tuning_record_count``/``window_count`` are echoed so callers can prove the
    search saw exactly the tuning frame.
    """

    finalists: tuple[ScoreV2Artifact, ...]
    win_counts: dict[str, int]
    mean_test_objective: dict[str, float]
    mean_test_objective_all: dict[str, float]
    tuning_record_count: int
    window_count: int
    candidate_count: int


def build_candidates(base: ScoreV2Artifact, n_random: int, seed: int):
    """Stage A candidate pool: coordinate-descent candidates around ``base`` plus
    ``n_random`` seeded random candidates. ``generate_random_candidates`` freezes
    the two portfolio-stateless dims (its default ``frozen_keys``); the coordinate
    builder is used as-is (plan §7 V1)."""
    candidates = list(generate_coordinate_candidates(base=base))
    candidates += list(generate_random_candidates(base, n=n_random, seed=seed))
    return candidates


def _objective_for(records, candidate, *, min_selected, dd_lambda) -> float:
    evaluation = evaluate_candidate_v2(records, candidate)
    return candidate_objective_v2(
        evaluation,
        mode="dd_adjusted",
        min_selected=min_selected,
        dd_lambda=dd_lambda,
    )


def run_stage_a(
    tuning_records,
    *,
    base: ScoreV2Artifact,
    train_days: int,
    test_days: int,
    n_random: int,
    seed: int,
    min_selected: int,
    dd_lambda: float,
) -> StageAResult:
    """Record-level walk-forward Stage A (plan §7 V1).

    Thin orchestration loop over the W2 primitives (the existing
    ``walk_forward_search`` is bound to the v1 objective, so V1 drives the
    dd_adjusted objective here): build candidates, build daily windows over the
    TUNING frame only, and per window pick the train-best candidate by the
    dd_adjusted objective and score it on the test slice. Aggregate per-candidate
    train-win counts and realized mean test objective, then take the top-5.
    """
    candidates = build_candidates(base, n_random, seed)
    windows = build_daily_walk_forward_windows(
        tuning_records, train_days, test_days
    )

    win_counts: dict[str, int] = {}
    picked_test_objectives: dict[str, list[float]] = {}
    all_test_objectives: dict[str, list[float]] = {c.version: [] for c in candidates}

    for window in windows:
        train_days_set = set(window.train_ts)
        test_days_set = set(window.test_ts)
        train_records = [r for r in tuning_records if r.ts[:10] in train_days_set]
        test_records = [r for r in tuning_records if r.ts[:10] in test_days_set]

        # Every candidate's test objective this window (for the baseline
        # side-by-side and finalist ranking).
        test_obj_by_version: dict[str, float] = {}
        for candidate in candidates:
            test_obj = _objective_for(
                test_records,
                candidate,
                min_selected=min_selected,
                dd_lambda=dd_lambda,
            )
            test_obj_by_version[candidate.version] = test_obj
            all_test_objectives[candidate.version].append(test_obj)

        # Train-best candidate (strict max; ties broken by candidate order).
        best_version = None
        best_key = None
        for candidate in candidates:
            train_obj = _objective_for(
                train_records,
                candidate,
                min_selected=min_selected,
                dd_lambda=dd_lambda,
            )
            key = train_obj
            if best_key is None or key > best_key:
                best_key = key
                best_version = candidate.version
        if best_version is not None:
            win_counts[best_version] = win_counts.get(best_version, 0) + 1
            picked_test_objectives.setdefault(best_version, []).append(
                test_obj_by_version[best_version]
            )

    mean_test_objective = {
        version: (sum(vals) / len(vals) if vals else float("-inf"))
        for version, vals in picked_test_objectives.items()
    }
    mean_test_objective_all = {
        version: (sum(vals) / len(vals) if vals else float("-inf"))
        for version, vals in all_test_objectives.items()
    }

    finalists = _rank_finalists(candidates, win_counts, mean_test_objective)
    return StageAResult(
        finalists=tuple(finalists[:5]),
        win_counts=win_counts,
        mean_test_objective=mean_test_objective,
        mean_test_objective_all=mean_test_objective_all,
        tuning_record_count=len(tuning_records),
        window_count=len(windows),
        candidate_count=len(candidates),
    )


def _rank_finalists(candidates, win_counts, mean_test_objective):
    """Rank candidates by (train-win count desc, realized mean test objective
    desc), with the ``candidates`` input order as the final stable tie-break."""
    order_index = {c.version: i for i, c in enumerate(candidates)}

    def sort_key(candidate):
        version = candidate.version
        wins = win_counts.get(version, 0)
        mean_obj = mean_test_objective.get(version, float("-inf"))
        return (-wins, -mean_obj, order_index[version])

    return sorted(candidates, key=sort_key)


def _iter_partition_dates_in(parquet_root: Path, start: date, end: date):
    """Ascending trading ``date``s from ``date=YYYY-MM-DD`` partitions within
    ``[start, end]`` (the only days with minute data — no market calendar)."""
    days: list[date] = []
    for date_dir in sorted(p for p in parquet_root.glob("date=*") if p.is_dir()):
        part = date_dir / "part.parquet"
        if not part.exists():
            continue
        try:
            day = date.fromisoformat(date_dir.name[len("date=") :])
        except ValueError:
            continue
        if start <= day <= end:
            days.append(day)
    return days


def _safe_version(version: str) -> str:
    """Filesystem-safe candidate version for checkpoint filenames."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(version))


def _stage_b_replay(artifact, provider, dates, *, settings, costs, initial_cash):
    """One finalist's Stage B portfolio replay (S1). Returns the report."""
    from app.research.replay.sim_runner import run_portfolio_replay

    return run_portfolio_replay(
        artifact=artifact,
        provider=provider,
        dates=dates,
        settings=settings,
        costs=costs,
        initial_cash=initial_cash,
    )


def run(
    *,
    records_path,
    out_dir,
    artifact_out,
    minute_parquet_root,
    tuning_start: date,
    tuning_end: date,
    holdout_start: date,
    holdout_end: date,
    n_random: int,
    seed: int,
    train_days: int,
    test_days: int,
    min_selected: int,
    dd_lambda: float,
    initial_cash: float,
    stage_b_enabled: bool,
    sample_every: int = 1,
    max_stage_b_finalists: int | None = None,
    stage_b_holdout_enabled: bool = True,
    log=print,
) -> dict:
    """Orchestrate Stage A -> Stage B -> holdout and write the final report +
    selected artifact. Returns a machine-readable summary dict (plan §7 V1)."""
    from app.gate2.schema import default_artifact, save_artifact
    from app.research.replay.broker_sim import SimCostParams
    from app.research.replay.parquet_provider import ParquetMinuteProvider
    from app.research.replay.scan_driver import replay_settings

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    minute_parquet_root = Path(minute_parquet_root)

    baseline = default_artifact()

    # (1)+(2) Load and split ONCE — the holdout never reaches Stage A.
    all_records = load_records(
        records_path, tuning_start, holdout_end, sample_every=sample_every
    )
    tuning_records, holdout_records = split_tuning_holdout(
        all_records, tuning_start, tuning_end, holdout_start, holdout_end
    )
    log(
        f"[weight_search] loaded records={len(all_records)} "
        f"tuning={len(tuning_records)} holdout={len(holdout_records)} "
        f"sample_every={max(int(sample_every), 1)}"
    )

    # (3) Stage A — search over the tuning frame ONLY.
    stage_a = run_stage_a(
        tuning_records,
        base=baseline,
        train_days=train_days,
        test_days=test_days,
        n_random=n_random,
        seed=seed,
        min_selected=min_selected,
        dd_lambda=dd_lambda,
    )
    log(
        f"[weight_search] stage A: {stage_a.candidate_count} candidates, "
        f"{stage_a.window_count} windows, finalists="
        f"{[c.version for c in stage_a.finalists]}"
    )

    settings = replay_settings()
    costs = SimCostParams(
        buy_fee_bps=0.0,
        sell_fee_bps=0.0,
        sell_tax_bps=0.0,
        buy_slippage_bps=0.0,
        sell_slippage_bps=0.0,
    )

    # (4) Stage B — expensive portfolio replay over the finalists (tuning dates).
    # ``max_stage_b_finalists`` caps how many top finalists get the (hours-long)
    # replay; Stage A selection/ranking above is untouched, so the cap only
    # bounds compute, not which candidates were considered. The winner is then
    # chosen among the REPLAYED finalists (a capped-out finalist has no Stage B
    # return to compare, so it cannot be picked by Stage B return).
    stage_b_tuning: dict[str, dict] = {}
    if stage_b_enabled and stage_a.finalists:
        tuning_dates = _iter_partition_dates_in(
            minute_parquet_root, tuning_start, tuning_end
        )
        provider = ParquetMinuteProvider(minute_parquet_root)
        replay_finalists = stage_a.finalists
        if max_stage_b_finalists is not None and max_stage_b_finalists >= 0:
            replay_finalists = replay_finalists[:max_stage_b_finalists]
        for finalist in replay_finalists:
            # Finalist-level checkpoint: one tuning replay is hours of work, so
            # a rerun (crash, external kill) must resume, not recompute. The
            # cached dict is exactly ``report.to_dict()`` — same shape the
            # winner selection reads.
            checkpoint = (
                out_dir / f"stage_b_tuning_{_safe_version(finalist.version)}.json"
            )
            if checkpoint.exists():
                stage_b_tuning[finalist.version] = json.loads(
                    checkpoint.read_text()
                )
                log(
                    f"[weight_search] stage B tuning {finalist.version}: "
                    f"cached ({checkpoint.name})"
                )
                continue
            log(
                f"[weight_search] stage B tuning {finalist.version}: "
                f"replaying {len(tuning_dates)} days..."
            )
            report = _stage_b_replay(
                finalist,
                provider,
                tuning_dates,
                settings=settings,
                costs=costs,
                initial_cash=initial_cash,
            )
            stage_b_tuning[finalist.version] = report.to_dict()
            tmp = checkpoint.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(stage_b_tuning[finalist.version]))
            tmp.replace(checkpoint)  # atomic — a kill mid-write never corrupts
            log(
                f"[weight_search] stage B tuning {finalist.version}: "
                f"return={report.total_return_pct:.2f}% trades={report.trade_count}"
            )

    # Select the winner: by Stage B tuning return when enabled, else Stage A top.
    selected = _select_winner(stage_a.finalists, stage_b_tuning, stage_b_enabled)

    # (5) Holdout — record-level for winner + baseline, and Stage B if enabled.
    holdout_record_metrics = {
        selected.version: _holdout_record_metric(
            holdout_records, selected, min_selected=min_selected
        ),
        baseline.version: _holdout_record_metric(
            holdout_records, baseline, min_selected=min_selected
        ),
    }
    stage_b_holdout: dict[str, dict] = {}
    if stage_b_enabled and stage_b_holdout_enabled:
        holdout_dates = _iter_partition_dates_in(
            minute_parquet_root, holdout_start, holdout_end
        )
        provider = ParquetMinuteProvider(minute_parquet_root)
        for artifact in (selected, baseline):
            report = _stage_b_replay(
                artifact,
                provider,
                holdout_dates,
                settings=settings,
                costs=costs,
                initial_cash=initial_cash,
            )
            stage_b_holdout[artifact.version] = report.to_dict()
            log(
                f"[weight_search] stage B holdout {artifact.version}: "
                f"return={report.total_return_pct:.2f}%"
            )

    verdict = _compute_verdict(
        selected,
        baseline,
        stage_b_holdout=stage_b_holdout,
        holdout_record_metrics=holdout_record_metrics,
        stage_b_enabled=stage_b_enabled,
    )

    # (6) Outputs — report markdown + selected artifact JSON.
    report_path = out_dir / "final_report.md"
    report_text = _render_report(
        selected=selected,
        baseline=baseline,
        stage_a=stage_a,
        stage_b_tuning=stage_b_tuning,
        stage_b_holdout=stage_b_holdout,
        holdout_record_metrics=holdout_record_metrics,
        verdict=verdict,
        stage_b_enabled=stage_b_enabled,
    )
    report_path.write_text(report_text)
    save_artifact(selected, artifact_out)

    return {
        "selected_version": selected.version,
        "baseline_version": baseline.version,
        "verdict": verdict["verdict"],
        "verdict_passed": verdict["passed"],
        "finalists": [c.version for c in stage_a.finalists],
        "win_counts": stage_a.win_counts,
        "tuning_record_count": stage_a.tuning_record_count,
        "holdout_record_count": len(holdout_records),
        "window_count": stage_a.window_count,
        "stage_b_enabled": stage_b_enabled,
        "report_path": str(report_path),
        "artifact_path": str(artifact_out),
    }


def _select_winner(finalists, stage_b_tuning, stage_b_enabled):
    """Pick the winning artifact: the Stage B tuning-return leader when Stage B
    ran (ties by finalist order), else the Stage A top finalist."""
    if not finalists:
        raise ValueError("no Stage A finalists to select from")
    if stage_b_enabled and stage_b_tuning:
        best = None
        best_return = None
        for finalist in finalists:  # finalist order = Stage A rank (stable tie-break)
            report = stage_b_tuning.get(finalist.version)
            if report is None:
                continue
            ret = report["total_return_pct"]
            if best_return is None or ret > best_return:
                best_return = ret
                best = finalist
        if best is not None:
            return best
    return finalists[0]


def _holdout_record_metric(holdout_records, artifact, *, min_selected) -> dict:
    """Record-level holdout evaluation for one artifact (Stage-A-style): the
    selected-return mean/total plus the selected count. This is the portfolio-
    free holdout read; the Stage B holdout replay is the portfolio-aware one."""
    evaluation = evaluate_candidate_v2(holdout_records, artifact)
    return {
        "selected_count": evaluation.selected_count,
        "mean_selected_return_bps": evaluation.mean_selected_return_bps,
        "total_return_bps": evaluation.total_return_bps,
        "hit_rate": evaluation.hit_rate,
    }


def _compute_verdict(
    selected,
    baseline,
    *,
    stage_b_holdout,
    holdout_record_metrics,
    stage_b_enabled,
) -> dict:
    """Compute the adoption verdict from the holdout comparison.

    Rule (plan §7 V1): "홀드아웃에서 baseline 이하이면 채택 불가" — the selected
    candidate must be STRICTLY ABOVE the baseline on the holdout to PASS. The
    comparison metric is the Stage B holdout ``total_return_pct`` when Stage B
    ran, otherwise the record-level holdout mean selected return.
    """
    if selected.version == baseline.version:
        # The search re-selected the baseline: nothing new to adopt.
        return {
            "passed": False,
            "verdict": "FAIL",
            "basis": "selected == baseline",
            "selected_metric": None,
            "baseline_metric": None,
        }
    if stage_b_enabled and stage_b_holdout:
        selected_metric = stage_b_holdout[selected.version]["total_return_pct"]
        baseline_metric = stage_b_holdout[baseline.version]["total_return_pct"]
        basis = "stage_b_holdout_total_return_pct"
    else:
        selected_metric = holdout_record_metrics[selected.version][
            "mean_selected_return_bps"
        ]
        baseline_metric = holdout_record_metrics[baseline.version][
            "mean_selected_return_bps"
        ]
        basis = "holdout_record_mean_selected_return_bps"
    passed = selected_metric > baseline_metric
    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "basis": basis,
        "selected_metric": selected_metric,
        "baseline_metric": baseline_metric,
    }


class _SectionCounter:
    """Running ``## N. <title>`` heading counter so section numbers stay
    contiguous whether or not the optional Stage B section is emitted."""

    def __init__(self) -> None:
        self._n = 0

    def head(self, title: str) -> str:
        self._n += 1
        return f"## {self._n}. {title}"


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if value == float("-inf"):
            return "-inf"
        return f"{value:.4f}"
    return str(value)


def _render_report(
    *,
    selected,
    baseline,
    stage_a: StageAResult,
    stage_b_tuning,
    stage_b_holdout,
    holdout_record_metrics,
    verdict,
    stage_b_enabled,
) -> str:
    """Render the final report markdown (plan §7 V1 required sections).

    Sections: selection summary, baseline side-by-side, per-window win counts,
    selection-count distribution, Stage B tuning/holdout tables, holdout
    record-level metrics, the §9 risk text IN FULL, and the judgment line with
    the PASS/FAIL verdict.
    """
    lines: list[str] = []
    section = _SectionCounter()
    lines.append("# Gate2 score_v2 weight search — final report (V1)")
    lines.append("")
    lines.append(section.head("Selection"))
    lines.append(f"- selected candidate: `{selected.version}`")
    lines.append(f"- baseline (default_artifact): `{baseline.version}`")
    lines.append(f"- selected buy_threshold: {selected.buy_threshold:g}")
    lines.append(f"- stage B enabled: {stage_b_enabled}")
    lines.append(f"- tuning records: {stage_a.tuning_record_count}")
    lines.append(f"- walk-forward windows: {stage_a.window_count}")
    lines.append("")

    lines.append(section.head("Baseline side-by-side (holdout)"))
    lines.append("")
    lines.append("| metric | selected | baseline |")
    lines.append("| --- | --- | --- |")
    sel_rec = holdout_record_metrics.get(selected.version, {})
    base_rec = holdout_record_metrics.get(baseline.version, {})
    lines.append(
        f"| holdout record mean bps | {_fmt(sel_rec.get('mean_selected_return_bps'))} "
        f"| {_fmt(base_rec.get('mean_selected_return_bps'))} |"
    )
    lines.append(
        f"| holdout record total bps | {_fmt(sel_rec.get('total_return_bps'))} "
        f"| {_fmt(base_rec.get('total_return_bps'))} |"
    )
    lines.append(
        f"| holdout record selected count | {_fmt(sel_rec.get('selected_count'))} "
        f"| {_fmt(base_rec.get('selected_count'))} |"
    )
    if stage_b_enabled and stage_b_holdout:
        sel_b = stage_b_holdout.get(selected.version, {})
        base_b = stage_b_holdout.get(baseline.version, {})
        lines.append(
            f"| holdout stage-B return % | {_fmt(sel_b.get('total_return_pct'))} "
            f"| {_fmt(base_b.get('total_return_pct'))} |"
        )
        lines.append(
            f"| holdout stage-B mdd % | {_fmt(sel_b.get('mdd_pct'))} "
            f"| {_fmt(base_b.get('mdd_pct'))} |"
        )
        lines.append(
            f"| holdout stage-B trades | {_fmt(sel_b.get('trade_count'))} "
            f"| {_fmt(base_b.get('trade_count'))} |"
        )
    lines.append("")

    lines.append(section.head("Per-window win counts"))
    lines.append("")
    lines.append("| candidate | train-window wins | mean test objective (picked) |")
    lines.append("| --- | --- | --- |")
    for finalist in stage_a.finalists:
        lines.append(
            f"| `{finalist.version}` | {stage_a.win_counts.get(finalist.version, 0)} "
            f"| {_fmt(stage_a.mean_test_objective.get(finalist.version))} |"
        )
    lines.append("")

    lines.append(section.head("Selection-count distribution (train-best per window)"))
    lines.append("")
    lines.append("| candidate | windows won |")
    lines.append("| --- | --- |")
    for version, count in sorted(
        stage_a.win_counts.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        lines.append(f"| `{version}` | {count} |")
    lines.append("")

    if stage_b_enabled and stage_b_tuning:
        lines.append(section.head("Stage B tuning-period replay (finalists)"))
        lines.append("")
        lines.append("| candidate | return % | mdd % | trades | win rate |")
        lines.append("| --- | --- | --- | --- | --- |")
        for finalist in stage_a.finalists:
            rep = stage_b_tuning.get(finalist.version, {})
            lines.append(
                f"| `{finalist.version}` | {_fmt(rep.get('total_return_pct'))} "
                f"| {_fmt(rep.get('mdd_pct'))} | {_fmt(rep.get('trade_count'))} "
                f"| {_fmt(rep.get('win_rate'))} |"
            )
        lines.append("")

    lines.append(section.head("Risk / limits (plan §9, verbatim)"))
    lines.append("")
    lines.append(RISK_SECTION_TEXT)
    lines.append("")

    lines.append(section.head("Verdict"))
    lines.append("")
    lines.append("홀드아웃에서 baseline 이하이면 채택 불가")
    lines.append("")
    lines.append(f"- comparison basis: {verdict['basis']}")
    lines.append(f"- selected metric: {_fmt(verdict['selected_metric'])}")
    lines.append(f"- baseline metric: {_fmt(verdict['baseline_metric'])}")
    lines.append(f"- **verdict: {verdict['verdict']}**")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description=(
            "Orchestrate the gate2 score_v2 walk-forward weight search over R1 "
            "evaluation records and write the final report (V1)."
        )
    )
    parser.add_argument("--records", required=True, help="per-horizon records parquet")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--artifact-out", required=True)
    parser.add_argument("--minute-parquet-root", required=True)
    parser.add_argument("--tuning-start", default=TUNING_START.isoformat())
    parser.add_argument("--tuning-end", default=TUNING_END.isoformat())
    parser.add_argument("--holdout-start", default=HOLDOUT_START.isoformat())
    parser.add_argument("--holdout-end", default=HOLDOUT_END.isoformat())
    parser.add_argument("--random-candidates", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--min-selected", type=int, default=30)
    parser.add_argument("--dd-lambda", type=float, default=0.5)
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    parser.add_argument(
        "--sample-every",
        type=int,
        default=1,
        help=(
            "keep every Nth date-filtered record (deterministic stride; the "
            "production 41M-row file needs this to fit memory)"
        ),
    )
    parser.add_argument(
        "--max-stage-b-finalists",
        type=int,
        default=None,
        help=(
            "cap the number of top Stage A finalists that get the hours-long "
            "Stage B portfolio replay (default: all). Stage A selection is "
            "unchanged — this only bounds Stage B compute for a scoped re-run"
        ),
    )
    stage_b = parser.add_mutually_exclusive_group()
    stage_b.add_argument("--stage-b", dest="stage_b", action="store_true")
    stage_b.add_argument("--no-stage-b", dest="stage_b", action="store_false")
    parser.set_defaults(stage_b=True)
    stage_b_holdout = parser.add_mutually_exclusive_group()
    stage_b_holdout.add_argument(
        "--stage-b-holdout", dest="stage_b_holdout", action="store_true"
    )
    stage_b_holdout.add_argument(
        "--no-stage-b-holdout", dest="stage_b_holdout", action="store_false",
        help=(
            "skip the Stage B HOLDOUT portfolio replay (Stage B tuning still "
            "runs/checkpoints normally; verdict falls back to the always-"
            "computed record-level holdout metric)"
        ),
    )
    parser.set_defaults(stage_b_holdout=True)
    args = parser.parse_args(argv)

    # Environment guard: the search reaches the real scanner (Stage A/B run_scan_at)
    # which must never issue a live quote token.
    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError(
            "gate2 weight search requires BUY_SCAN_QUOTE_KIS_ENV unset"
        )

    report = run(
        records_path=args.records,
        out_dir=args.out_dir,
        artifact_out=args.artifact_out,
        minute_parquet_root=args.minute_parquet_root,
        tuning_start=date.fromisoformat(args.tuning_start),
        tuning_end=date.fromisoformat(args.tuning_end),
        holdout_start=date.fromisoformat(args.holdout_start),
        holdout_end=date.fromisoformat(args.holdout_end),
        n_random=args.random_candidates,
        seed=args.seed,
        train_days=args.train_days,
        test_days=args.test_days,
        min_selected=args.min_selected,
        dd_lambda=args.dd_lambda,
        initial_cash=args.initial_cash,
        stage_b_enabled=args.stage_b,
        sample_every=args.sample_every,
        max_stage_b_finalists=args.max_stage_b_finalists,
        stage_b_holdout_enabled=args.stage_b_holdout,
    )
    print(
        f"gate2 weight search -> {report['report_path']} "
        f"selected={report['selected_version']} verdict={report['verdict']}"
    )
    return report


if __name__ == "__main__":  # pragma: no cover
    main()
