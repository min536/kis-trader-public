"""P0-c: EOD tools must degrade gracefully (exit 0, empty-but-valid output +
warning) when candidate_outcomes_<account>_<date>.jsonl is absent but cycle
snapshots/stats exist. All fixtures use tmp_path — never the real data/logs tree.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from app.tools import (
    analyze_signal_dataset,
    analyze_signal_outcome_dataset,
    export_signal_dataset,
    export_signal_outcome_dataset,
    live_health_check,
)

_ACCOUNT = "mock_acct_test"
_DATE = "20260703"


def _patch_roots(monkeypatch, tmp_path):
    """Point every module's project root at an isolated tmp tree with empty
    logs/ + data/ (no candidate_outcomes file present)."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(live_health_check, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_signal_dataset, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(analyze_signal_dataset, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(export_signal_outcome_dataset, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(analyze_signal_outcome_dataset, "_PROJECT_ROOT", tmp_path)


def test_build_health_summary_tolerates_missing_outcomes(tmp_path, monkeypatch, capsys):
    _patch_roots(monkeypatch, tmp_path)
    # Also neutralise the downstream builders' own project roots so they read
    # from the empty tmp tree, not the real repo.
    import app.tools.analyze_budget_bottlenecks as bottlenecks
    import app.tools.operational_day_diagnostics as opsdiag
    import app.auth.settings as settings_mod

    monkeypatch.setattr(bottlenecks, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(opsdiag, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(settings_mod, "PROJECT_ROOT", tmp_path, raising=False)

    summary = live_health_check.build_health_summary(
        account=_ACCOUNT, date=_DATE, session="REGULAR"
    )
    assert isinstance(summary, dict)
    assert summary["candidate_rows"] == 0
    err = capsys.readouterr().err
    assert "candidate_outcomes" in err.lower()


def test_export_signal_dataset_main_exits_zero_when_no_rows(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    out = tmp_path / "signals.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "export_signal_dataset",
            "--date", _DATE,
            "--account", _ACCOUNT,
            "--output", str(out),
        ],
    )
    export_signal_dataset.main()  # must not raise SystemExit(nonzero)
    # Empty-but-valid CSV: header written, zero data rows.
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "symbol" in text.splitlines()[0]


def test_analyze_signal_dataset_main_exits_zero_on_empty_dataset(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    # A header-only (empty) signal dataset, as export would produce.
    empty = tmp_path / "logs" / f"signal_dataset_{_ACCOUNT}_{_DATE}.csv"
    empty.write_text("ts,symbol,selection_bucket,stage_reached\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["analyze_signal_dataset", "--file", str(empty)],
    )
    analyze_signal_dataset.main()  # must not raise SystemExit(nonzero)


def test_export_signal_outcome_dataset_main_exits_zero_when_no_rows(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    empty = tmp_path / "logs" / f"signal_dataset_{_ACCOUNT}_{_DATE}.csv"
    empty.write_text("ts,symbol\n", encoding="utf-8")
    out = tmp_path / "outcomes.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "export_signal_outcome_dataset",
            "--file", str(empty),
            "--account", _ACCOUNT,
            "--output", str(out),
        ],
    )
    export_signal_outcome_dataset.main()  # must not raise SystemExit(nonzero)
    assert out.exists()


def test_analyze_signal_outcome_dataset_main_exits_zero_on_empty(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    empty = tmp_path / "logs" / f"signal_outcomes_{_ACCOUNT}_{_DATE}.csv"
    empty.write_text("ts,symbol,entry_price\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["analyze_signal_outcome_dataset", "--file", str(empty)],
    )
    analyze_signal_outcome_dataset.main()  # must not raise SystemExit(nonzero)
