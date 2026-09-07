from __future__ import annotations

import json

from tests.fixtures.autotuner_session_summaries import (
    healthy_session_summary,
    w1_holdout_session_summaries,
    write_session_summary_files,
)


def test_healthy_session_summary_matches_operational_metric_aliases() -> None:
    summary = healthy_session_summary(
        session_id="mock_20260609",
        api_call_count=814,
        buy_scan_cycles=42,
        sell_check_cycles=167,
    )

    assert summary == {
        "session_id": "mock_20260609",
        "metrics": {
            "api_call_count": 814,
            "rate_limit_hits": 0,
            "runtime_errors": 0,
            "broker_errors": 0,
            "order_rejections": 0,
            "buy_scan_cycles": 42,
            "sell_check_cycles": 167,
            "skipped_buy_scan_cadence": 0,
        },
    }


def test_w1_holdout_session_fixture_splits_train_and_holdout() -> None:
    fixture = w1_holdout_session_summaries()

    assert sorted(fixture) == ["holdout", "train"]
    assert [item["session_id"] for item in fixture["train"]] == [
        "mock_20260609_stage2",
        "mock_20260610_stage2",
    ]
    assert [item["session_id"] for item in fixture["holdout"]] == [
        "mock_20260611_holdout",
        "mock_20260612_holdout",
    ]
    assert all(
        item["metrics"]["runtime_errors"] == 0
        for group in fixture.values()
        for item in group
    )


def test_write_session_summary_files_creates_ordered_json_files(tmp_path) -> None:
    summaries = [
        healthy_session_summary(session_id="b"),
        healthy_session_summary(session_id="a"),
    ]

    paths = write_session_summary_files(tmp_path, summaries)

    assert [path.name for path in paths] == ["session_001_b.json", "session_002_a.json"]
    assert json.loads(paths[0].read_text(encoding="utf-8"))["session_id"] == "b"
