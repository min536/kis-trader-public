"""M-C1 tests — regime library (offline): k-means, silhouette, choose_k,
conditional kr-day mapping, regime evaluation, and library build (plan §4).

Synthetic fixtures only (no data/ reads, no real records parquet).
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta as _timedelta

import numpy as np

import pandas as pd
import pytest

from app.gate2.schema import CONDITION_SCORE_NAMES, ScoreV2Artifact
from app.research.gate2.weight_search import EvaluationRecord
from app.research.us_regime.regimes import (
    build_regime_library,
    choose_k,
    conditional_kr_days,
    evaluate_regime,
    kmeans,
    load_records_for_days,
    records_from_frame,
    silhouette,
)


def _artifact(version, weights=None, buy_threshold=60.0):
    """Full-key-set ScoreV2Artifact; unspecified weights default to 0.5."""
    full = {name: 0.5 for name in CONDITION_SCORE_NAMES}
    if weights:
        full.update(weights)
    return ScoreV2Artifact(
        version=version,
        weights=full,
        buy_threshold=buy_threshold,
        normalization_caps={"trend_alignment": 0.35},
    )


def _rec(day_index, symbol, forward_return_bps, score=100.0):
    """One EvaluationRecord on synthetic day ``2022-... day_index``.

    All 15 scores share ``score`` so ``weighted_gate2_score`` is a simple linear
    function of the weights — a high uniform score makes selection depend on the
    threshold, letting tests steer selected_count deterministically.
    """
    d = date(2022, 1, 1) + _timedelta(days=day_index)
    return EvaluationRecord(
        symbol=symbol,
        ts=f"{d.isoformat()}T09:{(day_index % 50) + 5:02d}:00",
        scores={name: score for name in CONDITION_SCORE_NAMES},
        forward_return_bps=forward_return_bps,
    )


def _write_records_parquet(path, rows):
    """Write an R1-shaped per-horizon records parquet (columns: symbol, ts, the
    15 score columns, forward_return_bps, truncated) from a list of row dicts.

    Missing score keys default to 1.0; a score key set to ``None`` is written as
    a null (NaN in the parquet float column) to exercise the NaN->None path.
    """
    from app.research.replay.record_runner import PARQUET_COLUMNS

    materialized = []
    for row in rows:
        record = {}
        for name in CONDITION_SCORE_NAMES:
            value = row.get(name, 1.0)
            record[name] = None if value is None else float(value)
        record["symbol"] = row["symbol"]
        record["ts"] = row["ts"]
        record["forward_return_bps"] = float(row.get("forward_return_bps", 0.0))
        record["truncated"] = bool(row.get("truncated", False))
        materialized.append(record)
    frame = pd.DataFrame(materialized, columns=list(PARQUET_COLUMNS))
    frame.to_parquet(path, index=False)


def test_kmeans_seed_reproducibility():
    # Two well-separated blobs; a fixed seed must give identical centroids/labels
    # across runs (deterministic Lloyd + seed-RNG initial centroids).
    rng = np.random.default_rng(0)
    blob_a = rng.normal(loc=0.0, scale=0.1, size=(20, 3))
    blob_b = rng.normal(loc=10.0, scale=0.1, size=(20, 3))
    z = np.vstack([blob_a, blob_b])

    centroids1, labels1 = kmeans(z, 2, seed=20260702, iters=100)
    centroids2, labels2 = kmeans(z, 2, seed=20260702, iters=100)

    assert np.array_equal(labels1, labels2)
    assert np.allclose(centroids1, centroids2)
    # sanity: the two blobs land in different clusters
    assert len(set(labels1[:20])) == 1
    assert len(set(labels1[20:])) == 1
    assert labels1[0] != labels1[20]


def _three_blobs(seed=0, per=25, scale=0.05):
    """Three tight, well-separated blobs in 2-D (silhouette should peak at k=3)."""
    rng = np.random.default_rng(seed)
    centers = np.array([[0.0, 0.0], [10.0, 0.0], [5.0, 10.0]])
    parts = [rng.normal(loc=c, scale=scale, size=(per, 2)) for c in centers]
    return np.vstack(parts)


def test_choose_k_picks_three_for_three_clusters():
    z = _three_blobs()
    k = choose_k(z, k_range=(2, 3, 4, 5, 6), seed=20260702)
    assert k == 3


def test_silhouette_higher_for_correct_k():
    z = _three_blobs()
    _, labels3 = kmeans(z, 3, seed=20260702)
    _, labels2 = kmeans(z, 2, seed=20260702)
    s3 = silhouette(z, labels3)
    s2 = silhouette(z, labels2)
    # Well-separated 3 blobs: the 3-cluster silhouette must dominate the 2-cluster
    # one, and a good clustering scores near the +1 ceiling.
    assert s3 > s2
    assert s3 > 0.8


def test_conditional_kr_days_filters_by_regime_us_days():
    # kr_us map: three KR days pointing at two distinct US session days.
    kr_us = pd.DataFrame(
        {
            "kr_day": [date(2022, 1, 3), date(2022, 1, 4), date(2022, 1, 5)],
            "us_day": [date(2022, 1, 1), date(2022, 1, 2), date(2022, 1, 1)],
            "gap_days": [2, 2, 4],
        }
    )
    # Regime owns only us_day 2022-01-01 -> its conditional KR days are the two
    # KR days that map onto it (2022-01-03 and 2022-01-05), sorted ascending.
    out = conditional_kr_days({date(2022, 1, 1)}, kr_us)
    assert out == (date(2022, 1, 3), date(2022, 1, 5))

    # A us_day not in the regime set is excluded.
    out2 = conditional_kr_days({date(2022, 1, 2)}, kr_us)
    assert out2 == (date(2022, 1, 4),)

    # Empty regime -> empty result.
    assert conditional_kr_days(set(), kr_us) == ()


def test_load_records_for_days_filters_scattered_day_set(tmp_path):
    # Records span four calendar days; the loader must return ONLY rows on the
    # requested (scattered, non-contiguous) day set, with NaN scores -> None.
    rows = [
        {"symbol": "A", "ts": "2022-01-03T09:05:00", "forward_return_bps": 1.0},
        {"symbol": "A", "ts": "2022-01-04T09:05:00", "forward_return_bps": 2.0},
        {"symbol": "A", "ts": "2022-01-05T09:05:00", "forward_return_bps": 3.0},
        {"symbol": "A", "ts": "2022-01-06T09:05:00", "forward_return_bps": 4.0},
        # a NaN score to exercise NaN -> None
        {
            "symbol": "B",
            "ts": "2022-01-03T09:06:00",
            "forward_return_bps": 5.0,
            "liquidity_score": None,
        },
    ]
    path = tmp_path / "records_h30.parquet"
    _write_records_parquet(path, rows)

    # Scattered set: 01-03 and 01-05 only (skip 01-04 and 01-06).
    wanted = [date(2022, 1, 3), date(2022, 1, 5)]
    records = load_records_for_days(path, wanted)

    got_days = sorted({r.ts[:10] for r in records})
    assert got_days == ["2022-01-03", "2022-01-05"]
    # both 01-03 rows (A + B) and the single 01-05 row -> 3 records
    assert len(records) == 3
    # NaN score column becomes None in the scores dict.
    b_rec = next(r for r in records if r.symbol == "B")
    assert b_rec.scores["liquidity_score"] is None
    assert set(b_rec.scores) == set(CONDITION_SCORE_NAMES)


def test_records_from_frame_maps_columns_and_nan():
    frame = pd.DataFrame(
        [
            {name: 1.0 for name in CONDITION_SCORE_NAMES}
            | {
                "symbol": "A",
                "ts": "2022-01-03T09:05:00",
                "forward_return_bps": 7.0,
                "truncated": False,
            }
        ]
    )
    frame.loc[0, "liquidity_score"] = float("nan")
    records = records_from_frame(frame)
    assert len(records) == 1
    rec = records[0]
    assert rec.symbol == "A"
    assert rec.forward_return_bps == 7.0
    assert rec.scores["liquidity_score"] is None
    assert rec.scores["pullback_strength_score"] == 1.0
    # truncated is dropped (not part of EvaluationRecord).
    assert not hasattr(rec, "truncated")


def test_evaluate_regime_min_days_guard():
    # Only 39 kr_days (< min_days=40) -> rejected with reason "min_days",
    # regardless of records.
    kr_days = [date(2022, 1, 1) + _timedelta(days=i) for i in range(39)]
    base = _artifact("base")
    candidates = [_artifact("cand", buy_threshold=55.0)]
    result = evaluate_regime(
        [], kr_days, base, candidates, min_days=40, min_selected_train=30
    )
    assert result["enrolled"] is False
    assert result["reason"] == "min_days"


def test_evaluate_regime_min_selected_test_guard_review_13():
    # Review #13 interaction case: 40 kr_days total -> split_at=int(40*0.7)=28
    # train days, 12 test days. min_selected_test = max(10, 12) = 12. The winner
    # selects only 11 on test -> rejected with reason "min_selected_test".
    kr_days = [date(2022, 1, 1) + _timedelta(days=i) for i in range(40)]
    base = _artifact("base")
    # A single always-selecting candidate (score 100 * weights -> well above thr).
    candidates = [_artifact("cand", buy_threshold=55.0)]

    records = []
    # >= 30 train records across the 28 train days (days 0..27) so the train
    # objective is finite; each selects.
    for i in range(30):
        records.append(_rec(i % 28, f"T{i}", forward_return_bps=10.0))
    # Exactly 11 test records across the 12 test days (days 28..39); all select,
    # so selected_count on test = 11 < min_selected_test (12).
    for i in range(11):
        records.append(_rec(28 + i, f"S{i}", forward_return_bps=10.0))

    result = evaluate_regime(
        records, kr_days, base, candidates, min_days=40, min_selected_train=30
    )
    assert result["enrolled"] is False
    assert result["reason"] == "min_selected_test"
    assert result["candidate_test"]["selected_count"] == 11


def _regime_records(n_train, n_test, *, train_start=0, test_start=35, ret=10.0):
    """Build train + test records: one per successive day so every record lands
    on a distinct day (train days < test days by construction)."""
    records = []
    for i in range(n_train):
        records.append(_rec(train_start + (i % 35), f"T{i}", forward_return_bps=ret))
    for i in range(n_test):
        records.append(_rec(test_start + i, f"S{i}", forward_return_bps=ret))
    return records


def test_evaluate_regime_baseline_not_beaten_not_enrolled():
    # 50 kr_days -> split_at=35, 15 test days, min_selected_test=max(10,15)=15.
    # Candidate and baseline share the SAME low threshold, so they select the
    # exact same test records with identical returns -> equal objective ->
    # candidate does NOT strictly beat baseline -> enrolled False.
    kr_days = [date(2022, 1, 1) + _timedelta(days=i) for i in range(50)]
    base = _artifact("base", buy_threshold=55.0)
    candidates = [_artifact("cand", buy_threshold=55.0)]
    records = _regime_records(35, 15)

    result = evaluate_regime(
        records, kr_days, base, candidates, min_days=40, min_selected_train=30
    )
    assert result["enrolled"] is False
    assert result["reason"] == "baseline_not_beaten"
    # side-by-side summaries present
    assert result["candidate_test"]["selected_count"] == 15
    assert result["baseline_test"]["selected_count"] == 15


def test_evaluate_regime_candidate_beats_baseline_enrolled():
    # Same 50-day layout. Baseline threshold is set ABOVE the max attainable
    # score (750) so baseline selects nothing (objective 0); the candidate
    # selects all 15 positive-return test records (objective > 0) -> enrolled.
    kr_days = [date(2022, 1, 1) + _timedelta(days=i) for i in range(50)]
    base = _artifact("base", buy_threshold=760.0)
    candidates = [_artifact("cand", buy_threshold=55.0)]
    records = _regime_records(35, 15, ret=20.0)

    result = evaluate_regime(
        records, kr_days, base, candidates, min_days=40, min_selected_train=30
    )
    assert result["enrolled"] is True
    assert result["reason"] == "improved"
    assert result["winner_version"] == "cand"
    assert result["candidate_test"]["selected_count"] == 15
    assert result["baseline_test"]["selected_count"] == 0
    assert result["candidate_objective"] > result["baseline_objective"]


def _library_inputs():
    """Two well-separated regimes over synthetic US days + a kr_us map + a
    records provider that returns positive-return records for one regime."""
    # 40 US days split into two blobs in a 2-feature space.
    us_days = [date(2021, 6, 1) + _timedelta(days=i) for i in range(40)]
    rng = np.random.default_rng(1)
    blob_a = rng.normal(loc=0.0, scale=0.05, size=(20, 2))
    blob_b = rng.normal(loc=8.0, scale=0.05, size=(20, 2))
    feats = np.vstack([blob_a, blob_b])
    feature_frame = pd.DataFrame(
        feats, index=pd.Index(us_days, name="us_day"), columns=["SPX_gap_pct", "SPX_day_ret"]
    )

    # Each US day maps to three DISTINCT KR days (collision-free: stride 3 per
    # US day) so every regime clears min_days=40 after de-duplication.
    kr_base = date(2022, 1, 1)
    kr_rows = []
    for j, us in enumerate(us_days):
        for offset in range(3):
            kr_rows.append(
                {
                    "kr_day": kr_base + _timedelta(days=3 * j + offset),
                    "us_day": us,
                    "gap_days": 1,
                }
            )
    kr_us_map = pd.DataFrame(kr_rows, columns=["kr_day", "us_day", "gap_days"])

    def records_for_kr_days(kr_days):
        # One positive-return record per kr_day (all scores 100 -> candidate with
        # low threshold selects, high-threshold baseline does not).
        recs = []
        for i, k in enumerate(sorted(kr_days)):
            recs.append(
                EvaluationRecord(
                    symbol=f"R{i}",
                    ts=f"{k.isoformat()}T09:{(i % 50) + 5:02d}:00",
                    scores={name: 100.0 for name in CONDITION_SCORE_NAMES},
                    forward_return_bps=20.0,
                )
            )
        return recs

    base = _artifact("base", buy_threshold=760.0)  # never selects
    candidates = [_artifact("cand", buy_threshold=55.0)]  # always selects
    return feature_frame, kr_us_map, records_for_kr_days, base, candidates


def test_build_regime_library_schema_roundtrip(tmp_path):
    import json

    feature_frame, kr_us_map, records_for_kr_days, base, candidates = _library_inputs()

    library = build_regime_library(
        feature_frame,
        kr_us_map,
        records_for_kr_days,
        base,
        candidates,
        k=2,
        seed=20260702,
        assets=["SPX"],
        min_days=40,
        min_selected_train=30,
    )

    # Top-level schema keys (plan §4).
    assert library["version"] == "regime_lib_v1"
    assert "created_utc" in library and library["created_utc"]
    assert library["assets"] == ["SPX"]
    assert library["feature_columns"] == ["SPX_gap_pct", "SPX_day_ret"]
    assert set(library["scaler"]) == {"mean", "std", "as_of"}
    assert set(library["scaler"]["mean"]) == {"SPX_gap_pct", "SPX_day_ret"}
    assert library["k"] == 2
    assert len(library["centroids"]) == 2
    assert len(library["centroids"][0]) == 2
    assert isinstance(library["max_assign_distance"], float)
    assert library["fallback"] == "baseline"

    # Each regime carries the required keys; the enrolled ones have an artifact.
    assert len(library["regimes"]) == 2
    for regime in library["regimes"]:
        assert set(regime) >= {
            "regime_id",
            "n_us_days",
            "n_kr_days",
            "artifact",
            "enrolled",
            "reason",
            "candidate_test",
            "baseline_test",
        }
        if regime["enrolled"]:
            assert regime["artifact"] is not None
            assert set(regime["artifact"]) == {
                "version",
                "weights",
                "buy_threshold",
                "normalization_caps",
            }
        else:
            assert regime["artifact"] is None
    # At least one regime should enrol (candidate strictly beats baseline).
    assert any(r["enrolled"] for r in library["regimes"])

    # Full JSON round-trip (the CLI writes this verbatim).
    path = tmp_path / "regime_library.json"
    path.write_text(json.dumps(library, indent=2, sort_keys=True))
    reloaded = json.loads(path.read_text())
    assert reloaded == library


def _write_us_daily_parquet(root, asset, start, n_days):
    """Write a synthetic US daily parquet with a gentle up-drift + oscillation so
    features are well-defined (via app.research.us_regime.ingest.load_us_daily
    round-trip shape)."""
    from pathlib import Path

    rows = []
    price = 100.0
    for i in range(n_days):
        d = start + _timedelta(days=i)
        price *= 1.0 + (0.002 if i % 2 == 0 else -0.001)
        o = price
        c = price * 1.005
        hi = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        rows.append(
            {"date": d, "open": o, "high": hi, "low": lo, "close": c, "volume": 0}
        )
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    Path(root).mkdir(parents=True, exist_ok=True)
    frame.to_parquet(Path(root) / f"{asset}.parquet", index=False)
    return [r["date"] for r in rows]


def test_cli_build_regime_library_writes_json_and_report(tmp_path):
    import json

    from app.gate2.schema import save_artifact
    from app.tools.build_regime_library import main

    us_root = tmp_path / "us_daily"
    us_days = _write_us_daily_parquet(us_root, "SPX", date(2021, 1, 1), 60)

    # Calendar: each surviving US day -> one KR day (gap 1). Feature warmup drops
    # the first few US days; map every day and let conditional filtering align.
    kr_rows = []
    for i, us in enumerate(us_days):
        kr_rows.append(
            {"kr_day": date(2022, 1, 1) + _timedelta(days=i), "us_day": us, "gap_days": 1}
        )
    calendar_path = tmp_path / "kr_us_calendar.parquet"
    pd.DataFrame(kr_rows, columns=["kr_day", "us_day", "gap_days"]).to_parquet(
        calendar_path, index=False
    )

    # Records parquet covering every KR day (one row per KR day; all scores 100).
    rec_rows = []
    for i, row in enumerate(kr_rows):
        rec = {name: 100.0 for name in CONDITION_SCORE_NAMES}
        rec["symbol"] = f"R{i}"
        rec["ts"] = f"{row['kr_day'].isoformat()}T09:{(i % 50) + 5:02d}:00"
        rec["forward_return_bps"] = 15.0
        rec["truncated"] = False
        rec_rows.append(rec)
    from app.research.replay.record_runner import PARQUET_COLUMNS

    records_path = tmp_path / "records_h30.parquet"
    pd.DataFrame(rec_rows, columns=list(PARQUET_COLUMNS)).to_parquet(
        records_path, index=False
    )

    base_path = tmp_path / "base.json"
    save_artifact(_artifact("score_v2_us_w0"), base_path)

    library_out = tmp_path / "regime_library.json"
    report_out = tmp_path / "regime_library_report.md"

    result = main(
        [
            "--us-root",
            str(us_root),
            "--assets",
            "SPX",
            "--calendar",
            str(calendar_path),
            "--records",
            str(records_path),
            "--base-artifact",
            str(base_path),
            "--library-out",
            str(library_out),
            "--report-out",
            str(report_out),
            "--k",
            "3",
            "--random-candidates",
            "8",
        ]
    )

    # Library JSON written + valid schema.
    assert library_out.exists()
    library = json.loads(library_out.read_text())
    assert library["version"] == "regime_lib_v1"
    assert library["k"] == 3
    assert library["assets"] == ["SPX"]
    assert len(library["regimes"]) == 3
    assert result["library"] == library

    # Human report written with the REQUIRED baseline-comparison table.
    assert report_out.exists()
    report = report_out.read_text()
    assert "regime_lib_v1" in report
    # per-regime enrolled/rejected + baseline comparison columns
    assert "enrolled" in report
    assert "baseline" in report.lower()
    # a markdown table with a candidate-vs-baseline comparison row per regime
    assert "| regime_id |" in report or "regime_id" in report


def test_cli_refuses_when_buy_scan_quote_env_set(tmp_path, monkeypatch):
    from app.tools.build_regime_library import main

    monkeypatch.setenv("BUY_SCAN_QUOTE_KIS_ENV", "live")
    with pytest.raises(RuntimeError, match="BUY_SCAN_QUOTE_KIS_ENV"):
        main(
            [
                "--us-root",
                str(tmp_path / "us"),
                "--calendar",
                str(tmp_path / "cal.parquet"),
                "--records",
                str(tmp_path / "rec.parquet"),
                "--base-artifact",
                str(tmp_path / "base.json"),
                "--library-out",
                str(tmp_path / "lib.json"),
                "--report-out",
                str(tmp_path / "rep.md"),
            ]
        )
