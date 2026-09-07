"""S5a-1 (docs/slack_backtest_native_pipeline_design_20260707.md §4.1): the
native backtest CLI orchestration — assemble provider/artifact from the manifest,
apply the freshness gate, run the replay, emit a RESULT line + summary JSON.

Uses injected backtest_fn / provider_factory so it never needs a real parquet
store; the freshness/window logic is exercised with touch-only partitions."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.tools import run_native_backtest as rnb


def _manifest(tmp_path: Path, parquet_dir: str = "pq") -> Path:
    m = tmp_path / "paths.json"
    m.write_text(
        json.dumps({"parquet_out_dir": parquet_dir, "artifact_out": "absent.json"}),
        encoding="utf-8",
    )
    return m


def _partitions(root: Path, days: list[str]) -> None:
    for day in days:
        d = root / f"date={day}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "part.parquet").write_bytes(b"PAR1")


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        initial_capital=100_000_000.0,
        final_equity=110_000_000.0,
        total_return_pct=10.0,
        mdd_pct=-4.2,
        trade_count=12,
        win_rate=0.5833,
        day_count=3,
        equity_curve=(("2020-01-02", 100_000_000.0),),
        report={"ok": True},
    )


def _run(tmp_path, **overrides):
    captured = {}

    def fake_backtest_fn(*, artifact, provider, dates, settings, initial_capital, **kw):
        captured["dates"] = list(dates)
        captured["initial_capital"] = initial_capital
        captured["provider"] = provider
        return _fake_result()

    def fake_provider_factory(**kw):
        captured["provider_kwargs"] = kw
        return object()

    kwargs = dict(
        manifest_path=_manifest(tmp_path),
        base_dir=tmp_path,
        symbols=("012450",),
        initial_capital=100_000_000.0,
        max_days=60,
        _backtest_fn=fake_backtest_fn,
        _provider_factory=fake_provider_factory,
        _settings=SimpleNamespace(),
    )
    kwargs.update(overrides)
    summary = rnb.run_native_backtest(**kwargs)
    return summary, captured


def test_summary_carries_core_metrics(tmp_path: Path) -> None:
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03", "2020-01-06"])
    summary, captured = _run(tmp_path)

    assert summary["total_return_pct"] == 10.0
    assert summary["mdd_pct"] == -4.2
    assert summary["trade_count"] == 12
    assert summary["day_count"] == 3
    assert captured["dates"] == [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
    assert captured["initial_capital"] == 100_000_000.0


def test_window_adjusted_when_request_out_of_range(tmp_path: Path) -> None:
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03"])
    summary, _ = _run(
        tmp_path, start=date(2010, 1, 1), end=date(2030, 1, 1)
    )
    assert summary["window_adjusted"] is True
    assert summary["window"]["start"] == "2020-01-02"
    assert summary["window"]["end"] == "2020-01-03"


def test_truncates_to_max_days_and_flags(tmp_path: Path) -> None:
    days = [f"2020-01-{d:02d}" for d in range(1, 11)]  # 10 partitions
    _partitions(tmp_path / "pq", days)
    summary, captured = _run(tmp_path, max_days=3)

    assert summary["truncated"] is True
    assert len(captured["dates"]) == 3
    # keeps the most recent 3 partitions
    assert captured["dates"][-1] == date(2020, 1, 10)
    assert captured["dates"][0] == date(2020, 1, 8)


def test_plan_only_reports_window_without_running(tmp_path: Path) -> None:
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03", "2020-01-06"])
    summary, captured = _run(tmp_path, plan_only=True)

    assert summary["ok"] is True
    assert summary["plan_only"] is True
    assert summary["planned_day_count"] == 3
    assert summary["window"]["end"] == "2020-01-06"
    # the replay backend must NOT have been invoked
    assert "dates" not in captured


def test_error_when_no_partitions(tmp_path: Path) -> None:
    (tmp_path / "pq").mkdir()
    summary, _ = _run(tmp_path)
    assert summary["ok"] is False
    assert "no parquet" in summary["error"].lower()


def test_format_result_line(tmp_path: Path) -> None:
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03"])
    summary, _ = _run(tmp_path)
    line = rnb.format_result_line(summary)
    assert line.startswith("RESULT:")
    assert "return=10.0%" in line
    assert "trades=12" in line


def test_format_result_line_partial_flag() -> None:
    """S2 red (4a): a partial summary must surface a
    ``partial_{completed}/{requested}d`` flag in the same bracket as the
    existing window_clamped/truncated flags."""
    summary = {
        "ok": True,
        "total_return_pct": 5.0,
        "mdd_pct": -2.0,
        "trade_count": 3,
        "win_rate": 0.5,
        "day_count": 2,
        "partial": True,
        "completed_days": 2,
        "requested_days": 5,
    }
    line = rnb.format_result_line(summary)
    assert "[partial_2/5d]" in line


def test_progress_prints_day_lines_via_on_day_end(tmp_path: Path, capsys) -> None:
    """S2 red (4b): progress=True wires an on_day_end printer into the
    backtest_fn call; the injected stub drives it once per date and each
    printed line must match the exact ``progress: day i/n ...`` format."""
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03", "2020-01-06"])

    def fake_backtest_fn(
        *, artifact, provider, dates, settings, initial_capital, on_day_end=None, **kw
    ):
        assert on_day_end is not None
        for i, day in enumerate(dates):
            on_day_end(i, day, 100_000_000.0 + i)
        return _fake_result()

    _summary, _captured = _run(
        tmp_path, _backtest_fn=fake_backtest_fn, progress=True
    )

    printed = capsys.readouterr().out
    lines = [ln for ln in printed.splitlines() if ln.startswith("progress:")]
    assert lines == [
        "progress: day 1/3 2020-01-02 equity=100,000,000 elapsed=0s",
        "progress: day 2/3 2020-01-03 equity=100,000,001 elapsed=0s",
        "progress: day 3/3 2020-01-06 equity=100,000,002 elapsed=0s",
    ]


def test_main_writes_summary_json(tmp_path: Path, monkeypatch, capsys) -> None:
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03"])
    manifest = _manifest(tmp_path)
    out = tmp_path / "out.json"

    monkeypatch.setattr(rnb, "_default_backtest_fn", None)
    monkeypatch.setattr(rnb, "get_settings", lambda: SimpleNamespace())

    def fake_backtest_fn(*, artifact, provider, dates, settings, initial_capital, **kw):
        return _fake_result()

    monkeypatch.setattr(rnb, "run_backtest", fake_backtest_fn)
    monkeypatch.setattr(
        rnb, "build_parquet_provider", lambda **kw: object()
    )

    rc = rnb.main(
        [
            "--manifest",
            str(manifest),
            "--base-dir",
            str(tmp_path),
            "--symbols",
            "012450",
            "--out",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert rc == 0
    assert out.exists()
    saved = json.loads(out.read_text())
    assert saved["total_return_pct"] == 10.0
    assert "RESULT:" in printed


def test_main_sigterm_handler_sets_should_stop_and_prints_message(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """S2 (bonus coverage): main() registers a SIGTERM handler that flips an
    internal stop flag (observed via the should_stop callable forwarded into
    backtest_fn) and prints the stop message — verified WITHOUT sending a real
    OS signal (signal.signal itself is monkeypatched to capture the handler
    instead of registering it with the OS)."""
    _partitions(tmp_path / "pq", ["2020-01-02", "2020-01-03"])
    manifest = _manifest(tmp_path)

    captured_registration: dict = {}

    def fake_signal_signal(signalnum, handler):
        captured_registration["signalnum"] = signalnum
        captured_registration["handler"] = handler

    monkeypatch.setattr(rnb.signal, "signal", fake_signal_signal)
    monkeypatch.setattr(rnb, "_default_backtest_fn", None)
    monkeypatch.setattr(rnb, "get_settings", lambda: SimpleNamespace())

    captured_should_stop: dict = {}

    def fake_backtest_fn(
        *, artifact, provider, dates, settings, initial_capital, should_stop=None, **kw
    ):
        captured_should_stop["fn"] = should_stop
        return _fake_result()

    monkeypatch.setattr(rnb, "run_backtest", fake_backtest_fn)
    monkeypatch.setattr(rnb, "build_parquet_provider", lambda **kw: object())

    rc = rnb.main(
        ["--manifest", str(manifest), "--base-dir", str(tmp_path), "--symbols", "012450"]
    )

    assert rc == 0
    assert captured_registration["signalnum"] == rnb.signal.SIGTERM
    should_stop_fn = captured_should_stop["fn"]
    assert should_stop_fn() is False

    # Simulate signal delivery by invoking the captured handler directly (no
    # real OS signal is sent).
    captured_registration["handler"](rnb.signal.SIGTERM, None)

    assert should_stop_fn() is True
    printed = capsys.readouterr().out
    assert "signal: SIGTERM — stopping at next tick boundary" in printed
