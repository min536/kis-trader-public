"""Tests for app/dashboard/data_loader.py _safe_read_jsonl tail-based loading."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from app.dashboard import data_loader
from app.dashboard.data_loader import _load_research_data, _safe_read_jsonl


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_safe_read_jsonl_no_limit():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    records = [{"i": i} for i in range(50)]
    _write_jsonl(path, records)

    result, err = _safe_read_jsonl(path)
    assert err is None
    assert len(result) == 50
    assert result[0] == {"i": 0}
    assert result[-1] == {"i": 49}
    path.unlink()


def test_safe_read_jsonl_max_lines_returns_tail():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    records = [{"i": i} for i in range(100)]
    _write_jsonl(path, records)

    result, err = _safe_read_jsonl(path, max_lines=10)
    assert err is None
    assert len(result) == 10
    assert result[0] == {"i": 90}
    assert result[-1] == {"i": 99}
    path.unlink()


def test_safe_read_jsonl_max_lines_larger_than_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    records = [{"i": i} for i in range(5)]
    _write_jsonl(path, records)

    result, err = _safe_read_jsonl(path, max_lines=1000)
    assert err is None
    assert len(result) == 5
    path.unlink()


def test_safe_read_jsonl_missing_file():
    result, err = _safe_read_jsonl(Path("/nonexistent/file.jsonl"))
    assert result == []
    assert err == "파일 없음"


def test_safe_read_jsonl_parse_error_skipped():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    with path.open("w") as fh:
        fh.write('{"a": 1}\n')
        fh.write("not valid json\n")
        fh.write('{"b": 2}\n')

    result, err = _safe_read_jsonl(path, max_lines=10)
    assert len(result) == 2
    assert "파싱 실패" in err
    path.unlink()


def test_safe_read_jsonl_empty_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    path.write_text("")

    result, err = _safe_read_jsonl(path)
    assert result == []
    assert err is None
    path.unlink()


def test_safe_read_jsonl_reports_full_read_limit_without_tail():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    path.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    with mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}):
        result, err = _safe_read_jsonl(path)

    assert result == []
    assert err is not None
    assert "읽기 제한 초과" in err
    path.unlink()


def test_safe_read_jsonl_tail_mode_bypasses_full_file_size_limit():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    _write_jsonl(path, [{"i": i, "payload": "x" * 20} for i in range(20)])

    with mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}):
        result, err = _safe_read_jsonl(path, max_lines=2)

    assert err is None
    assert result == [
        {"i": 18, "payload": "x" * 20},
        {"i": 19, "payload": "x" * 20},
    ]
    path.unlink()


def test_safe_read_jsonl_tail_mode_skips_oversized_lines_without_dropping_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"i": 1}) + "\n")
        fh.write(json.dumps({"i": 2, "payload": "x" * 200}) + "\n")
        fh.write(json.dumps({"i": 3}) + "\n")

    with mock.patch.dict(os.environ, {"KIS_LOCAL_LINE_MAX_BYTES": "80"}):
        result, err = _safe_read_jsonl(path, max_lines=10)

    assert result == [{"i": 1}, {"i": 3}]
    assert err is not None
    assert "읽기 제한 초과 라인 생략: 1건" in err
    path.unlink()


def test_safe_read_jsonl_honors_explicit_max_line_bytes_headroom():
    """D1: an oversized line under the widened cap is read, not skipped.

    The default per-line cap is 1MB; a caller passing max_line_bytes above the
    line size must read the line through the tail window (this is the fix that
    lets the dashboard read pre-fix bloated snapshot/order lines).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)
    big_payload = "x" * 200
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"i": 1}) + "\n")
        fh.write(json.dumps({"i": 2, "payload": big_payload}) + "\n")

    with mock.patch.dict(os.environ, {"KIS_LOCAL_LINE_MAX_BYTES": "80"}):
        # Without headroom the 200-byte line is skipped; with an explicit cap
        # above the line size it is read.
        skipped, skipped_err = _safe_read_jsonl(path, max_lines=10)
        read_through, read_err = _safe_read_jsonl(
            path, max_lines=10, max_line_bytes=8_000_000
        )

    assert skipped == [{"i": 1}]
    assert "읽기 제한 초과 라인 생략: 1건" in (skipped_err or "")
    assert read_through == [{"i": 1}, {"i": 2, "payload": big_payload}]
    assert read_err is None
    path.unlink()


def test_research_loader_flattens_baseline_metrics_and_loads_latest_native_backtest(
    tmp_path, monkeypatch
):
    snapshot_dir = tmp_path / "research" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "snapshot_20260801.json").write_text(
        json.dumps(
            {
                "as_of_date": "20260801",
                "backtest_baselines": {
                    "core": {
                        "performance": {
                            "sharpe": 1.25,
                            "total_return": 7.5,
                            "max_drawdown": 3.2,
                            "win_rate": 54.0,
                            "trade_count": 88,
                        },
                        "staleness": {
                            "end_date": "2026-08-01",
                            "days_old": 0,
                            "is_stale": False,
                        },
                    }
                },
                "snapshot_meta": {"stale_baselines": []},
            }
        ),
        encoding="utf-8",
    )
    native_dir = tmp_path / "reports" / "native_backtest"
    native_dir.mkdir(parents=True)
    (native_dir / "native_backtest_20260901_150000.json").write_text(
        json.dumps({"ok": True, "plan_only": True, "planned_day_count": 5}),
        encoding="utf-8",
    )
    (native_dir / "native_backtest_20260901_150100.json").write_text(
        json.dumps(
            {
                "ok": True,
                "window": {"start": "2025-06-10", "end": "2025-06-16"},
                "day_count": 5,
                "total_return_pct": 1.4172,
                "mdd_pct": -2.4926,
                "trade_count": 275,
                "win_rate": 0.534,
                "final_equity": 101_417_218,
                "artifact_version": "base@thr55",
                "partial": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(data_loader, "_PROJECT_ROOT", tmp_path)

    research = _load_research_data()

    baseline = research["baselines"][0]
    assert baseline["sharpe"] == 1.25
    assert baseline["total_return"] == 7.5
    assert baseline["max_drawdown"] == 3.2
    assert baseline["win_rate"] == 54.0
    assert baseline["trade_count"] == 88
    assert baseline["is_stale"] is True
    assert baseline["days_old"] > 7
    assert research["snapshot_meta"]["stale_baselines"] == ["core"]
    assert research["native_backtest"]["trade_count"] == 275
    assert research["native_backtest"]["source_file"].endswith("150100.json")
