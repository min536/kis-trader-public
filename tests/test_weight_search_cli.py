"""Slice V1 — gate2 weight-search orchestration CLI tests.

``app.tools.run_gate2_weight_search`` loads per-horizon evaluation-record parquet
(R1 output), splits it ONCE into a tuning frame (2023-01-02..2025-03-31) and a
holdout frame (2025-04-01..2025-06-16), runs a walk-forward Stage A search over
the tuning frame only (W2 primitives: daily windows + dd_adjusted objective),
runs an EXPENSIVE Stage B portfolio replay (S1) over the top-5 finalists, then
evaluates the winner + baseline on the holdout, and writes a final report +
selected artifact JSON.

Safety mirrors the sibling CLIs: no network, no broker; the CLI refuses to run
when ``BUY_SCAN_QUOTE_KIS_ENV`` is set. All test outputs go to ``tmp_path`` —
never the repo ``results/`` or ``app/gate2/artifacts/``.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.gate2.schema import CONDITION_SCORE_NAMES


def _write_records_parquet(path, rows: list[dict]) -> None:
    """Write an R1-shaped per-horizon records parquet (columns: symbol, ts, the
    15 score columns, forward_return_bps, truncated) from a list of row dicts.

    Missing score keys default to 1.0 so ``weighted_gate2_score`` sees a full
    key set; callers can override individual scores per row.
    """
    import pandas as pd

    from app.research.replay.record_runner import PARQUET_COLUMNS

    materialized: list[dict] = []
    for row in rows:
        record = {name: float(row.get(name, 1.0)) for name in CONDITION_SCORE_NAMES}
        record["symbol"] = row["symbol"]
        record["ts"] = row["ts"]
        record["forward_return_bps"] = float(row.get("forward_return_bps", 0.0))
        record["truncated"] = bool(row.get("truncated", False))
        materialized.append(record)
    frame = pd.DataFrame(materialized, columns=list(PARQUET_COLUMNS))
    frame.to_parquet(path, index=False)


def test_load_records_filters_by_date_range(tmp_path):
    """V1 ②: ``load_records`` returns only records whose ``ts`` date is within
    ``[start, end]`` — the file may contain out-of-range rows (never assume it
    is pre-trimmed), and both the lower and upper bounds are inclusive."""
    from app.tools.run_gate2_weight_search import load_records

    path = tmp_path / "records_h30.parquet"
    _write_records_parquet(
        path,
        [
            {"symbol": "A", "ts": "2022-12-31T09:05:00"},  # before window
            {"symbol": "A", "ts": "2023-01-02T09:05:00"},  # lower bound (in)
            {"symbol": "A", "ts": "2024-06-03T10:00:00"},  # in
            {"symbol": "A", "ts": "2025-03-31T15:00:00"},  # upper bound (in)
            {"symbol": "A", "ts": "2025-04-01T09:05:00"},  # after window
        ],
    )

    records = load_records(path, date(2023, 1, 2), date(2025, 3, 31))
    got_dates = sorted(r.ts[:10] for r in records)
    assert got_dates == ["2023-01-02", "2024-06-03", "2025-03-31"]


def test_load_records_sample_every_takes_every_nth_after_date_filter(tmp_path):
    """V1 (④ 41M-row scale): ``sample_every=N`` deterministically keeps every
    Nth record of the DATE-FILTERED frame (file-order positions 0, N, 2N, ...) —
    the stride is applied after the date filter so out-of-range rows never shift
    it, and omitting the parameter keeps every record (stride 1)."""
    from app.tools.run_gate2_weight_search import load_records

    path = tmp_path / "records_h30.parquet"
    rows = [
        {"symbol": "A", "ts": "2022-12-31T09:05:00", "forward_return_bps": -1.0},
    ] + [
        {
            "symbol": "A",
            "ts": f"2023-01-02T09:{minute:02d}:00",
            "forward_return_bps": float(minute),
        }
        for minute in range(1, 10)
    ]
    _write_records_parquet(path, rows)

    sampled = load_records(
        path, date(2023, 1, 2), date(2023, 1, 2), sample_every=3
    )
    assert [r.forward_return_bps for r in sampled] == [1.0, 4.0, 7.0]

    full = load_records(path, date(2023, 1, 2), date(2023, 1, 2))
    assert len(full) == 9


def test_tuning_holdout_split_boundaries_are_disjoint(tmp_path):
    """V1 ②: the constants split records ONCE into disjoint tuning/holdout frames
    at the named boundaries — a record on 2025-03-31 is tuning, one on 2025-04-01
    is holdout, and the two frames never share a record."""
    from app.tools.run_gate2_weight_search import (
        HOLDOUT_END,
        HOLDOUT_START,
        TUNING_END,
        TUNING_START,
        load_records,
        split_tuning_holdout,
    )

    assert TUNING_START == date(2023, 1, 2)
    assert TUNING_END == date(2025, 3, 31)
    assert HOLDOUT_START == date(2025, 4, 1)
    assert HOLDOUT_END == date(2025, 6, 16)

    path = tmp_path / "records_h30.parquet"
    _write_records_parquet(
        path,
        [
            {"symbol": "A", "ts": "2025-03-31T15:00:00"},  # last tuning day
            {"symbol": "A", "ts": "2025-04-01T09:05:00"},  # first holdout day
            {"symbol": "A", "ts": "2025-06-16T14:00:00"},  # last holdout day
        ],
    )
    all_records = load_records(path, TUNING_START, HOLDOUT_END)
    tuning, holdout = split_tuning_holdout(
        all_records, TUNING_START, TUNING_END, HOLDOUT_START, HOLDOUT_END
    )

    assert sorted(r.ts[:10] for r in tuning) == ["2025-03-31"]
    assert sorted(r.ts[:10] for r in holdout) == ["2025-04-01", "2025-06-16"]
    tuning_ids = {id(r) for r in tuning}
    assert not any(id(r) in tuning_ids for r in holdout)


def _synthetic_tuning_records():
    """A tiny, deterministic set of tuning-period records with a clear signal:
    ``pullback_strength_score`` is high on winners and low on losers, so a
    candidate that up-weights it beats one that down-weights it. Spread across
    enough days that ``build_daily_walk_forward_windows(train_days, test_days)``
    forms at least one complete window.
    """
    from app.tools.run_gate2_weight_search import load_records  # noqa: F401

    rows = []
    # 8 trading days, 2023-01-02 .. 2023-01-11 (skip a weekend gap harmlessly —
    # the daily-window builder groups by unique date string).
    days = [
        "2023-01-02",
        "2023-01-03",
        "2023-01-04",
        "2023-01-05",
        "2023-01-06",
        "2023-01-09",
        "2023-01-10",
        "2023-01-11",
    ]
    for day in days:
        for k in range(6):
            # winners: high pullback score + positive return.
            rows.append(
                {
                    "symbol": f"W{k}",
                    "ts": f"{day}T09:{5 + k:02d}:00",
                    "pullback_strength_score": 95.0,
                    "forward_return_bps": 120.0,
                }
            )
            # losers: low pullback score + negative return.
            rows.append(
                {
                    "symbol": f"L{k}",
                    "ts": f"{day}T10:{5 + k:02d}:00",
                    "pullback_strength_score": 5.0,
                    "forward_return_bps": -80.0,
                }
            )
    return rows


def test_stage_a_is_deterministic_and_prefers_the_signal(tmp_path):
    """V1 ③: Stage A over synthetic tuning records is deterministic under the
    seed (two runs give identical finalist versions) and its top finalist beats
    the baseline default artifact on mean test objective (the synthetic signal
    rewards up-weighting ``pullback_strength_score``). The reported tuning record
    count matches the input (proof the search saw exactly the tuning frame)."""
    from app.gate2.schema import default_artifact
    from app.tools.run_gate2_weight_search import load_records, run_stage_a

    path = tmp_path / "records_h30.parquet"
    _write_records_parquet(path, _synthetic_tuning_records())
    tuning = load_records(path, date(2023, 1, 2), date(2025, 3, 31))
    assert tuning, "expected synthetic tuning records to load"

    common = dict(
        base=default_artifact(),
        train_days=4,
        test_days=2,
        n_random=8,
        seed=20260702,
        min_selected=1,
        dd_lambda=0.5,
    )
    result_a = run_stage_a(tuning, **common)
    result_b = run_stage_a(tuning, **common)

    # Deterministic under the seed.
    assert [c.version for c in result_a.finalists] == [
        c.version for c in result_b.finalists
    ]
    # Exactly-5 finalists (top-5 by aggregate) and the tuning frame was the only
    # input the search saw.
    assert len(result_a.finalists) == 5
    assert result_a.tuning_record_count == len(tuning)
    assert result_a.window_count >= 1

    # The chosen best out-performs the baseline default on mean test objective:
    # the winners carry a strong pullback signal, so a candidate that keeps or
    # raises that weight and thresholds sensibly wins vs. the mixed baseline.
    best = result_a.finalists[0]
    assert result_a.mean_test_objective[best.version] >= result_a.mean_test_objective.get(
        default_artifact().version, float("-inf")
    )


import app.market_data.live_snapshot as live_snapshot  # noqa: E402
import app.math_models.history as history_module  # noqa: E402


def _redirect_live_snapshot(monkeypatch, tmp_dir):
    """Dual-redirect precedent (tests/test_scan_driver.py): env alone is
    ineffective warm because ``SNAPSHOT_PATH`` is import-time-resolved. Redirect
    BOTH the dir env AND the import-cached ``SNAPSHOT_PATH``."""
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(tmp_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", tmp_dir / "live_snapshot.json"
    )


def _isolate_stores(monkeypatch, tmp_path):
    """Replicate the test_scan_driver store isolation (Stage A/B reach the REAL
    scanner via ``run_scan_at``, which redirects these stores per scan)."""
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()
    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)


def _write_minute_day(root, day, rows):
    """Write one ``date=YYYY-MM-DD/part.parquet`` minute partition."""
    import pandas as pd

    part_dir = root / f"date={day.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(part_dir / "part.parquet")


def _riser_minute_rows(symbol, day, minutes):
    """Pullback-then-grind-up bars (mirrors the R1/F2 riser fixture) so the real
    scanner produces a gate1/candidate buy for Stage B."""
    rows = []
    for m in minutes:
        ts = datetime(day.year, day.month, day.day, 9, m)
        if m == 0:
            o, h, low, c, v = 70000, 70050, 70000, 70000, 50_000
        elif m <= 15:
            o, h, low, c, v = 69800, 69800, 69000, 69200, 80_000
        else:
            base = 69500 + (m - 16) * 5
            o, h, low, c, v = base, base + 40, base - 10, base + 20, 70_000
        rows.append(
            {
                "symbol": symbol,
                "datetime": ts.isoformat(),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": int(v),
            }
        )
    return rows


def _prev_day_close_rows(symbol, prev_day, close):
    ts = datetime(prev_day.year, prev_day.month, prev_day.day, 9, 0)
    return [
        {
            "symbol": symbol,
            "datetime": ts.isoformat(),
            "open": float(close),
            "high": float(close),
            "low": float(close),
            "close": float(close),
            "volume": 10_000,
        }
    ]


def _build_minute_cache(root, symbol="005930"):
    """A minute cache with a prior-day close plus one tuning trading day and one
    holdout trading day (each a riser session), so Stage B replays over 1-day
    tuning and 1-day holdout date lists."""
    minutes = range(0, 21)  # 09:00 .. 09:20 (fast)
    tuning_day = date(2024, 6, 3)
    tuning_prev = date(2024, 5, 31)
    holdout_day = date(2025, 4, 1)
    holdout_prev = date(2025, 3, 31)
    _write_minute_day(root, tuning_prev, _prev_day_close_rows(symbol, tuning_prev, 70000.0))
    _write_minute_day(root, tuning_day, _riser_minute_rows(symbol, tuning_day, minutes))
    _write_minute_day(root, holdout_prev, _prev_day_close_rows(symbol, holdout_prev, 70000.0))
    _write_minute_day(root, holdout_day, _riser_minute_rows(symbol, holdout_day, minutes))


def _records_spanning_windows(*, winner_symbol="005930"):
    """Records with a strong pullback signal across tuning days (so Stage A forms
    windows and prefers up-weighting pullback) plus a few holdout-dated records
    (so holdout record-level eval is non-empty). Holdout records are BENIGN here
    — the poison test is separate."""
    rows = []
    tuning_days = [
        "2023-01-02",
        "2023-01-03",
        "2023-01-04",
        "2023-01-05",
        "2023-01-06",
        "2023-01-09",
    ]
    for day in tuning_days:
        for k in range(6):
            rows.append(
                {
                    "symbol": f"W{k}",
                    "ts": f"{day}T09:{5 + k:02d}:00",
                    "pullback_strength_score": 95.0,
                    "forward_return_bps": 120.0,
                }
            )
            rows.append(
                {
                    "symbol": f"L{k}",
                    "ts": f"{day}T10:{5 + k:02d}:00",
                    "pullback_strength_score": 5.0,
                    "forward_return_bps": -80.0,
                }
            )
    # A handful of holdout-window records (benign, moderate signal).
    for k in range(6):
        rows.append(
            {
                "symbol": f"H{k}",
                "ts": f"2025-04-01T09:{5 + k:02d}:00",
                "pullback_strength_score": 90.0,
                "forward_return_bps": 60.0,
            }
        )
    return rows


def test_cli_end_to_end_writes_report_and_valid_artifact(monkeypatch, tmp_path):
    """V1 ④: the full CLI produces a ``final_report.md`` (with a baseline row, the
    §9 risk text IN FULL, and the verdict line) and a selected artifact JSON that
    loads via ``load_artifact`` and validates. Stage B is ON over tiny 1-day
    tuning/holdout date lists."""
    from app.gate2.schema import load_artifact, validate_artifact
    from app.tools import run_gate2_weight_search
    from app.tools.run_gate2_weight_search import RISK_SECTION_TEXT

    _isolate_stores(monkeypatch, tmp_path)

    records_path = tmp_path / "records_h30.parquet"
    _write_records_parquet(records_path, _records_spanning_windows())
    minute_root = tmp_path / "minute"
    _build_minute_cache(minute_root)

    out_dir = tmp_path / "out"
    artifact_out = tmp_path / "selected.json"

    summary = run_gate2_weight_search.main(
        [
            "--records",
            str(records_path),
            "--out-dir",
            str(out_dir),
            "--artifact-out",
            str(artifact_out),
            "--minute-parquet-root",
            str(minute_root),
            "--random-candidates",
            "6",
            "--seed",
            "20260702",
            "--train-days",
            "4",
            "--test-days",
            "2",
            "--min-selected",
            "1",
            "--initial-cash",
            "1000000",
            "--stage-b",
        ]
    )

    # Machine-readable summary.
    assert summary["selected_version"]
    assert summary["baseline_version"] == "score_v2_w0"
    assert summary["verdict"] in {"PASS", "FAIL"}
    assert summary["stage_b_enabled"] is True
    assert summary["tuning_record_count"] == 6 * 6 * 2  # 6 days * 6 winners+6 losers

    # Report file exists and contains the required sections.
    report_path = out_dir / "final_report.md"
    assert report_path.exists()
    text = report_path.read_text()
    assert "## 1. Selection" in text
    assert "baseline (default_artifact)" in text
    assert "score_v2_w0" in text  # baseline side-by-side row
    assert RISK_SECTION_TEXT in text  # §9 verbatim, IN FULL
    assert "홀드아웃에서 baseline 이하이면 채택 불가" in text  # judgment line
    assert f"verdict: {summary['verdict']}" in text

    # Selected artifact loads and validates.
    assert artifact_out.exists()
    loaded = load_artifact(artifact_out)
    validate_artifact(loaded)
    assert loaded.version == summary["selected_version"]

    # Never wrote the repo results/ or app/gate2/artifacts/.
    assert str(tmp_path) in str(report_path)
    assert str(tmp_path) in str(artifact_out)


def test_no_stage_b_holdout_skips_the_expensive_holdout_replay(monkeypatch, tmp_path):
    """V1 ④ 운영 컷: ``--no-stage-b-holdout`` keeps Stage B TUNING (winner
    selection still uses tuning-replay returns — checkpoints are read/written
    same as always) but skips the Stage B HOLDOUT portfolio replay entirely.
    The verdict/report fall back to the always-computed record-level holdout
    metric (existing ``_compute_verdict``/``_render_report`` behavior when
    ``stage_b_holdout`` is empty — no new fallback logic needed here). This is
    the operator's cut when the holdout replay's calendar cost is not worth
    the wait but the tuning-informed winner selection already ran."""
    from app.tools import run_gate2_weight_search

    _isolate_stores(monkeypatch, tmp_path)
    records_path = tmp_path / "records_h30.parquet"
    _write_records_parquet(records_path, _records_spanning_windows())
    minute_root = tmp_path / "minute"
    _build_minute_cache(minute_root)

    holdout_replay_calls = {"n": 0}
    real_replay = run_gate2_weight_search._stage_b_replay

    def counting_replay(artifact, provider, dates, **kwargs):
        holdout_replay_calls["n"] += 1
        return real_replay(artifact, provider, dates, **kwargs)

    monkeypatch.setattr(
        run_gate2_weight_search, "_stage_b_replay", counting_replay
    )

    summary = run_gate2_weight_search.main(
        [
            "--records", str(records_path),
            "--out-dir", str(tmp_path / "out"),
            "--artifact-out", str(tmp_path / "selected.json"),
            "--minute-parquet-root", str(minute_root),
            "--random-candidates", "6",
            "--seed", "20260702",
            "--train-days", "4",
            "--test-days", "2",
            "--min-selected", "1",
            "--initial-cash", "1000000",
            "--stage-b",
            "--no-stage-b-holdout",
        ]
    )

    # Every replay call was a TUNING replay (one per finalist) — none for holdout.
    assert holdout_replay_calls["n"] == len(summary["finalists"])
    assert summary["stage_b_enabled"] is True

    text = (tmp_path / "out" / "final_report.md").read_text()
    assert "## 1. Selection" in text  # report still renders

    # Second run with the SAME out-dir reuses tuning checkpoints (0 new tuning
    # replays) and still performs no holdout replay.
    holdout_replay_calls["n"] = 0
    run_gate2_weight_search.main(
        [
            "--records", str(records_path),
            "--out-dir", str(tmp_path / "out"),
            "--artifact-out", str(tmp_path / "selected.json"),
            "--minute-parquet-root", str(minute_root),
            "--random-candidates", "6",
            "--seed", "20260702",
            "--train-days", "4",
            "--test-days", "2",
            "--min-selected", "1",
            "--initial-cash", "1000000",
            "--stage-b",
            "--no-stage-b-holdout",
        ]
    )
    assert holdout_replay_calls["n"] == 0


def test_max_stage_b_finalists_caps_the_expensive_replays(monkeypatch, tmp_path):
    """V1 ④ 비용 상한: ``--max-stage-b-finalists N`` restricts the expensive
    Stage B portfolio replay to the top-N Stage A finalists (the rest are still
    selected/ranked, but not replayed). Stage A selection is unchanged — only
    the replay set is capped — and holdout still replays exactly the winner +
    baseline. One finalist replay is hours over the full window; capping is how
    a scoped re-run lands in hours instead of days."""
    from app.tools import run_gate2_weight_search

    _isolate_stores(monkeypatch, tmp_path)
    records_path = tmp_path / "records_h30.parquet"
    _write_records_parquet(records_path, _records_spanning_windows())
    minute_root = tmp_path / "minute"
    _build_minute_cache(minute_root)

    replayed: list = []
    real_replay = run_gate2_weight_search._stage_b_replay

    def tracking_replay(artifact, *args, **kwargs):
        replayed.append(artifact.version)
        return real_replay(artifact, *args, **kwargs)

    monkeypatch.setattr(
        run_gate2_weight_search, "_stage_b_replay", tracking_replay
    )

    summary = run_gate2_weight_search.main(
        [
            "--records", str(records_path),
            "--out-dir", str(tmp_path / "out"),
            "--artifact-out", str(tmp_path / "selected.json"),
            "--minute-parquet-root", str(minute_root),
            "--random-candidates", "6",
            "--seed", "20260702",
            "--train-days", "4",
            "--test-days", "2",
            "--min-selected", "1",
            "--initial-cash", "1000000",
            "--stage-b",
            "--max-stage-b-finalists", "2",
        ]
    )

    # Stage A still selected the full finalist slate...
    assert len(summary["finalists"]) > 2
    # ...but Stage B tuning replayed only the top 2 of them.
    tuning_replays = [v for v in replayed if v in summary["finalists"]]
    assert len(set(tuning_replays)) == 2
    assert set(tuning_replays) == set(summary["finalists"][:2])
    tuning_checkpoints = sorted(
        (tmp_path / "out").glob("stage_b_tuning_*.json")
    )
    assert len(tuning_checkpoints) == 2


def test_stage_b_tuning_replays_resume_from_checkpoints(monkeypatch, tmp_path):
    """V1 ④ 재기동성: Stage B는 finalist별 튜닝 리플레이 결과를 out_dir의
    ``stage_b_tuning_<version>.json`` 체크포인트로 저장하고, 같은 out_dir로
    재실행하면 저장된 finalist의 리플레이를 건너뛴다(holdout 리플레이 2회만
    다시 수행). 튜닝 리플레이는 finalist당 수 시간이라 외부 kill 후 전체
    재계산은 감당 불가 — 재개가 운영 요구사항."""
    from app.tools import run_gate2_weight_search

    _isolate_stores(monkeypatch, tmp_path)

    records_path = tmp_path / "records_h30.parquet"
    _write_records_parquet(records_path, _records_spanning_windows())
    minute_root = tmp_path / "minute"
    _build_minute_cache(minute_root)

    calls = {"n": 0}
    real_replay = run_gate2_weight_search._stage_b_replay

    def counting_replay(*args, **kwargs):
        calls["n"] += 1
        return real_replay(*args, **kwargs)

    monkeypatch.setattr(
        run_gate2_weight_search, "_stage_b_replay", counting_replay
    )

    argv = [
        "--records", str(records_path),
        "--out-dir", str(tmp_path / "out"),
        "--artifact-out", str(tmp_path / "selected.json"),
        "--minute-parquet-root", str(minute_root),
        "--random-candidates", "6",
        "--seed", "20260702",
        "--train-days", "4",
        "--test-days", "2",
        "--min-selected", "1",
        "--initial-cash", "1000000",
        "--stage-b",
    ]

    first = run_gate2_weight_search.main(argv)
    finalist_count = len(first["finalists"])
    assert finalist_count > 0
    assert calls["n"] == finalist_count + 2  # tuning replays + holdout(selected, baseline)
    checkpoints = sorted((tmp_path / "out").glob("stage_b_tuning_*.json"))
    assert len(checkpoints) == finalist_count
    saved = {p.name: p.read_text() for p in checkpoints}

    calls["n"] = 0
    second = run_gate2_weight_search.main(argv)
    assert calls["n"] == 2  # holdout only — every tuning replay served from checkpoint
    assert second["selected_version"] == first["selected_version"]
    assert {p.name: p.read_text() for p in checkpoints} == saved  # untouched


def test_holdout_poison_records_never_influence_stage_a(monkeypatch, tmp_path):
    """V1 ⑤: records dated INSIDE the holdout window can NEVER reach Stage A.

    Two proofs, structural (not objective-dependent):

    1. **Count proof.** ``run_stage_a`` is invoked (via the CLI) on a file that
       ALSO contains a heavy block of poison rows dated in the holdout window
       (2025-04+), yet the reported ``tuning_record_count`` equals the exact
       tuning-only hand count — the search's input was trimmed to the tuning
       frame before it ran. Meanwhile ``holdout_record_count`` grows by exactly
       the poison mass, so the rows WERE loaded, just confined to the holdout
       side of the single early split.
    2. **Invariance proof.** The full Stage A aggregate (finalists / selection /
       win counts) is byte-identical with and without the poison — an inverted
       high-``pullback`` block that, had it leaked into a tuning window, would
       have contaminated the objective there. Its total absence from the Stage A
       result is the separation guarantee.

    Additionally, re-dating that SAME poison INTO the tuning window is shown to
    grow the search input (``tuning_record_count``) — confirming it is the split,
    not some filter in the search, that excludes the holdout rows. Stage B is OFF
    (holdout separation is a pure Stage A concern), so this stays fast.
    """
    from app.tools import run_gate2_weight_search

    _isolate_stores(monkeypatch, tmp_path)
    minute_root = tmp_path / "minute"
    _build_minute_cache(minute_root)

    clean_rows = _records_spanning_windows()
    tuning_only_count = sum(1 for r in clean_rows if r["ts"][:10] <= "2025-03-31")

    def _run(records_path, out_dir, artifact_out):
        return run_gate2_weight_search.main(
            [
                "--records", str(records_path),
                "--out-dir", str(out_dir),
                "--artifact-out", str(artifact_out),
                "--minute-parquet-root", str(minute_root),
                "--random-candidates", "8",
                "--seed", "20260702",
                "--train-days", "4",
                "--test-days", "2",
                "--min-selected", "1",
                "--no-stage-b",
            ]
        )

    clean_path = tmp_path / "records_clean.parquet"
    _write_records_parquet(clean_path, clean_rows)
    clean = _run(clean_path, tmp_path / "out_clean", tmp_path / "clean.json")

    # Poison block: an inverted high-pullback signal dated in the HOLDOUT window.
    poison_block = []
    for day in ("2025-04-01", "2025-04-02", "2025-04-03", "2025-05-01", "2025-06-16"):
        for k in range(40):
            poison_block.append(
                {
                    "symbol": f"P{k}",
                    "ts": f"{day}T09:{(k % 50):02d}:00",
                    "pullback_strength_score": 99.0,
                    "forward_return_bps": -5000.0,
                }
            )
    poison_path = tmp_path / "records_poison.parquet"
    _write_records_parquet(poison_path, clean_rows + poison_block)
    poisoned = _run(poison_path, tmp_path / "out_poison", tmp_path / "poison.json")

    # (1) Count proof: the search saw EXACTLY the tuning frame either way.
    assert clean["tuning_record_count"] == tuning_only_count
    assert poisoned["tuning_record_count"] == tuning_only_count
    # The poison rows were loaded, just routed to the holdout side of the split.
    assert (
        poisoned["holdout_record_count"]
        == clean["holdout_record_count"] + len(poison_block)
    )

    # (2) Invariance proof: Stage A aggregate is unchanged by the poison.
    assert poisoned["finalists"] == clean["finalists"]
    assert poisoned["selected_version"] == clean["selected_version"]
    assert poisoned["win_counts"] == clean["win_counts"]

    # Control: the SAME poison mass, re-dated INTO the tuning window, DOES grow
    # the search input — so it is the split (not a search-side filter) that keeps
    # the holdout out. (No selection assertion here; only the input-size lever.)
    leaked_block = [
        {**row, "ts": row["ts"].replace(row["ts"][:10], "2023-01-05")}
        for row in poison_block
    ]
    leaked_path = tmp_path / "records_leaked.parquet"
    _write_records_parquet(leaked_path, clean_rows + leaked_block)
    leaked = _run(leaked_path, tmp_path / "out_leaked", tmp_path / "leaked.json")
    assert leaked["tuning_record_count"] == tuning_only_count + len(leaked_block)


def test_cli_refuses_when_buy_scan_quote_env_set(monkeypatch, tmp_path):
    """V1 ①: like the R1 CLI, the orchestration CLI refuses to run when
    ``BUY_SCAN_QUOTE_KIS_ENV`` is set (a truthy value would make the real scanner
    issue a live quote token). The guard fires before any parquet load."""
    from app.tools import run_gate2_weight_search

    monkeypatch.setenv("BUY_SCAN_QUOTE_KIS_ENV", "live")

    with pytest.raises(RuntimeError, match="BUY_SCAN_QUOTE_KIS_ENV"):
        run_gate2_weight_search.main(
            [
                "--records",
                str(tmp_path / "records_h30.parquet"),
                "--out-dir",
                str(tmp_path / "out"),
                "--artifact-out",
                str(tmp_path / "artifact.json"),
                "--minute-parquet-root",
                str(tmp_path / "parquet"),
            ]
        )
