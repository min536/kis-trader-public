"""D0 (docs/dashboard_account_routing_design_20260707.md §D0): deterministic
account × artifact census over filenames + stat metadata only — never reading
data/ contents in full (respects the large-file policy)."""

from __future__ import annotations

import json
from pathlib import Path

from app.tools.account_artifact_census import (
    build_account_census,
    render_census_lines,
)


def _write(path: Path, text: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_census_reports_presence_size_and_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    # Account A: has runtime_state + cycle_snapshots. Missing performance files.
    _write(data_dir / "runtime_state_mock_acct_aaaa.json", '{"x": 1}\n')
    _write(data_dir / "cycle_snapshots_mock_acct_aaaa.jsonl", '{"c": 1}\n')
    _write(logs_dir / "orders_mock_acct_aaaa.jsonl", '{"o": 1}\n')

    census = build_account_census(data_dir=data_dir, logs_dir=logs_dir)
    by_sig = {row["signature"]: row for row in census}
    assert "mock_acct_aaaa" in by_sig
    artifacts = by_sig["mock_acct_aaaa"]["artifacts"]

    assert artifacts["runtime_state"]["exists"] is True
    assert artifacts["runtime_state"]["size_bytes"] > 0
    assert artifacts["cycle_snapshots"]["exists"] is True
    # performance files are absent for this account
    assert artifacts["performance_snapshots"]["exists"] is False
    assert artifacts["performance_summary"]["exists"] is False


def test_census_flags_equity_unavailable_when_perf_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    _write(data_dir / "runtime_state_mock_acct_bbbb.json", '{"x": 1}\n')
    _write(data_dir / "cycle_snapshots_mock_acct_bbbb.jsonl", '{"c": 1}\n')

    census = build_account_census(data_dir=data_dir, logs_dir=logs_dir)
    row = {r["signature"]: r for r in census}["mock_acct_bbbb"]
    # When performance artifacts are absent the census marks the equity source gap.
    assert row["equity_available"] is False
    assert "performance" in (row["equity_gap_reason"] or "").lower()


def test_census_never_reads_full_file_contents(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    _write(data_dir / "runtime_state_mock_acct_cccc.json", '{"x": 1}\n')

    import builtins

    real_open = builtins.open
    opened: list[str] = []

    def _tracking_open(file, *args, **kwargs):  # noqa: ANN001
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _tracking_open)
    build_account_census(data_dir=data_dir, logs_dir=logs_dir)
    # The census must not open the runtime_state file for reading — stat only.
    assert not any("runtime_state_mock_acct_cccc" in p for p in opened)


def test_render_census_lines_is_plaintext_table(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    _write(data_dir / "runtime_state_mock_acct_dddd.json", '{"x": 1}\n')

    census = build_account_census(data_dir=data_dir, logs_dir=logs_dir)
    lines = render_census_lines(census)
    joined = "\n".join(lines)
    assert "mock_acct_dddd" in joined
    assert "runtime_state" in joined
