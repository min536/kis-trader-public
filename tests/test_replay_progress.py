"""Slice S2 — replay progress callback + SIGTERM tick-boundary partial results.

Sim-runner-side behavior tests for ``run_portfolio_replay``'s new ``on_day_end``
/ ``should_stop`` kw-only hooks
(docs/slack_backtest_speed_timeout_delegation_20260712.md §5). The fixture
mirrors ``tests/test_sim_runner.py``'s store-isolation pattern (dual
live-snapshot redirect + ``_cycle_snapshots_file`` patch +
``BUY_SCAN_QUOTE_KIS_ENV`` delenv) but uses an EMPTY-universe provider so the
tick loop's SELL/BUY phases are trivial no-ops — these tests exercise the
day/tick LOOP INSTRUMENTATION (on_day_end / should_stop / partial bookkeeping),
not scan/strategy behavior (already covered by tests/test_sim_runner.py's S1
suite).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import app.market_data.live_snapshot as live_snapshot
import app.math_models.history as history_module
from app.gate2.schema import default_artifact
from app.research.replay.broker_sim import SimCostParams
from app.research.replay.scan_driver import replay_settings
from app.research.replay.sim_runner import run_portfolio_replay


def _isolate_stores(monkeypatch, tmp_path: Path) -> None:
    """Replicate tests/test_sim_runner.py's store-isolation setup so an
    (empty-universe) replay scan never touches real project data/logs paths."""
    live_dir = tmp_path / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(live_dir))
    monkeypatch.setattr(live_snapshot, "SNAPSHOT_PATH", live_dir / "live_snapshot.json")
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()
    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)


class _EmptyUniverseProvider:
    """Zero-symbol provider — the tick loop's SELL/BUY phases become no-ops (no
    held positions, no candidates), isolating these tests to the day/tick loop
    mechanics. Tracks ``load_window`` calls so a test can assert a day whose
    should_stop fired at the day-loop boundary never starts."""

    def __init__(self) -> None:
        self.load_window_calls: list[tuple[date, date]] = []

    def load_window(self, start_date: date, end_date: date) -> None:
        self.load_window_calls.append((start_date, end_date))

    def universe_at(self, t: datetime) -> tuple[str, ...]:
        return ()

    def volume_rank_at(self, t: datetime, top_n: int) -> tuple[str, ...]:
        return ()

    def observable_snapshot(self, symbol: str, t: datetime):
        return None

    def prev_close(self, symbol: str, day: date):
        return None


_TWO_DAYS = [date(2024, 6, 3), date(2024, 6, 4)]


def _run(monkeypatch, tmp_path: Path, *, on_day_end=None, should_stop=None, dates=None):
    _isolate_stores(monkeypatch, tmp_path)
    provider = _EmptyUniverseProvider()
    settings = replay_settings(
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
    )
    costs = SimCostParams(0.0, 0.0, 0.0, 0.0, 0.0)
    report = run_portfolio_replay(
        artifact=default_artifact(),
        provider=provider,
        dates=list(dates if dates is not None else _TWO_DAYS),
        settings=settings,
        costs=costs,
        initial_cash=1_000_000.0,
        session_open="09:01",
        session_close="09:05",
        on_day_end=on_day_end,
        should_stop=should_stop,
    )
    return report, provider


def test_on_day_end_receives_one_call_per_completed_day(monkeypatch, tmp_path):
    """S2 red (1): a synthetic 2-day fixture with no should_stop must call
    on_day_end exactly twice, once per completed day in order, with the day
    index/date/day-end-equity that day produced."""
    calls: list[tuple[int, date, float]] = []

    def _capture(day_index: int, day: date, last_equity: float) -> None:
        calls.append((day_index, day, last_equity))

    report, _provider = _run(monkeypatch, tmp_path, on_day_end=_capture)

    assert calls == [
        (0, date(2024, 6, 3), 1_000_000.0),
        (1, date(2024, 6, 4), 1_000_000.0),
    ]
    assert report.trade_count == 0
    assert report.partial is False
    assert report.completed_days == 2
    assert report.requested_days == 2


def test_should_stop_mid_first_day_yields_partial_report(monkeypatch, tmp_path):
    """S2 red (2): should_stop is False for the day-loop-start check (day 0
    starts) and for the first two tick-loop-start checks (09:01, 09:02 run to
    completion), then True at the tick-loop-start check for 09:03 — aborting
    BEFORE that tick's SELL phase. Day 0 never reaches its normal completion
    path (no on_day_end call for it), day 1's load_window is never called, and
    the report is partial with a single (day-0) equity_curve entry."""
    calls: list[tuple[int, date, float]] = []
    call_count = 0

    def _stop_after_two_ticks() -> bool:
        nonlocal call_count
        call_count += 1
        # call #1 = day-loop-start check (day 0) -> False (day 0 starts).
        # call #2/#3 = tick-loop-start checks for 09:01/09:02 -> False (both
        # ticks run to completion). call #4 = tick-loop-start check for 09:03
        # -> True (abort before that tick's SELL phase).
        return call_count > 3

    def _capture(day_index: int, day: date, last_equity: float) -> None:
        calls.append((day_index, day, last_equity))

    report, provider = _run(
        monkeypatch,
        tmp_path,
        on_day_end=_capture,
        should_stop=_stop_after_two_ticks,
    )

    assert report.partial is True
    assert report.completed_days == 0
    assert report.requested_days == 2
    assert report.equity_curve == [("2024-06-03", 1_000_000.0)]
    assert provider.load_window_calls == [(date(2024, 6, 3), date(2024, 6, 3))]
    assert calls == []  # a partial day never reaches the on_day_end call.


def test_default_call_and_noop_callbacks_produce_identical_report(monkeypatch, tmp_path):
    """S2 red (3) equivalence pin: a default call (on_day_end/should_stop both
    omitted) and an explicit no-op-callback call (on_day_end=no-op,
    should_stop=lambda: False) must produce byte-identical to_dict() output —
    the callbacks are pure side-effect hooks that never perturb the replay
    result. This is the safety pin for run_gate2_weight_search.py's existing
    run_portfolio_replay call site, which passes neither kwarg."""
    default_report, _ = _run(monkeypatch, tmp_path)
    explicit_report, _ = _run(
        monkeypatch,
        tmp_path,
        on_day_end=lambda *_a: None,
        should_stop=lambda: False,
    )

    assert default_report.to_dict() == explicit_report.to_dict()
