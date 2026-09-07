"""Slice S1 — full portfolio minute replay (``run_portfolio_replay``).

These tests drive the REAL scanner + REAL ``build_sell_analysis`` through the
replay store seams (same isolation pattern as ``tests/test_scan_driver.py``:
a local copy of the dual live-snapshot redirect + a ``_cycle_snapshots_file``
patch + ``BUY_SCAN_QUOTE_KIS_ENV`` delenv). No network, no broker imports;
research imports runtime pure-calculation modules only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import app.market_data.live_snapshot as live_snapshot
import app.math_models.history as history_module
from app.gate2.schema import default_artifact
from app.research.replay.broker_sim import BrokerSimulator, SimCostParams
from app.research.replay.minute_provider import (
    HistoricalMinuteDataProvider,
    MinuteBar,
)
from app.research.replay.scan_driver import replay_settings
from app.research.replay.sim_runner import (
    SimReplayReport,
    SimTradeRecord,
    _build_report,
    run_portfolio_replay,
)


def _redirect_live_snapshot(monkeypatch, tmp_dir: Path) -> None:
    """Dual-redirect precedent (see tests/test_scan_driver.py): env alone is
    ineffective in a warm process because ``SNAPSHOT_PATH`` is resolved once at
    import time and read as a module global at call time. Redirect BOTH the dir
    env AND the import-cached ``SNAPSHOT_PATH``."""
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(tmp_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", tmp_dir / "live_snapshot.json"
    )


def _isolate_stores(monkeypatch, tmp_path: Path):
    """Replicate the test_scan_driver store-isolation setup and return the
    history JSONL path the driver's ``_cycle_snapshots_file`` patch points at."""
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    _redirect_live_snapshot(monkeypatch, live_dir)
    jsonl = tmp_path / "cycle.jsonl"
    monkeypatch.setattr(history_module, "_cycle_snapshots_file", lambda: jsonl)
    history_module._build_symbol_histories.cache_clear()
    monkeypatch.delenv("BUY_SCAN_QUOTE_KIS_ENV", raising=False)
    return jsonl


def _bar(symbol, ts, o, h, low, c, v):
    return MinuteBar(symbol=symbol, ts=ts, open=o, high=h, low=low, close=c, volume=v)


class _FakeProvider:
    """In-memory provider with the ``ParquetMinuteProvider`` surface
    ``run_portfolio_replay`` reads (load_window / universe_at / volume_rank_at /
    observable_snapshot / prev_close). ``load_window`` is a no-op here because
    all bars are already resident."""

    def __init__(self, bars, prev_closes: dict[str, float]) -> None:
        self._inner = HistoricalMinuteDataProvider(bars)
        self._prev_closes = dict(prev_closes)

    def load_window(self, start_date: date, end_date: date) -> None:  # noqa: ARG002
        return None

    def universe_at(self, t):
        return self._inner.universe_at(t)

    def volume_rank_at(self, t, top_n):
        return self._inner.volume_rank_at(t, top_n)

    def observable_snapshot(self, symbol, t):
        return self._inner.observable_snapshot(symbol, t)

    def prev_close(self, symbol, day):
        return self._prev_closes.get(symbol)


_ONE_MIN = timedelta(minutes=1)


# Dims that are STRUCTURALLY DEAD (score always 0) under THIS test file's OWN
# settings — not a general-purpose gate2 bypass:
#   - gap_down_quality_score: the regular-session profile these replay settings
#     inherit (scan_driver._REGULAR_SESSION_ENV_PINS) sets
#     buy_rule_enable_gap_down_open=False, which forces
#     gap_down_open_strength_score to 0 (app/scanner/scoring.py) regardless of
#     the bar shape — a genuine production characteristic, not a test-only knob.
#   - volume_rank_score / volume_power_score: every test in this file passes
#     replay_settings(buy_rule_enable_live_volume_rank=False,
#     buy_rule_enable_live_volume_power_rank=False) deliberately (see each
#     test's own docstring — otherwise the flat market-open bar auto-qualifies
#     via the always-#1 live rank), which forces both strength scores to 0.
#   - liquidity_score: has no v1 source at all (app/gate2/adapter.py) and is
#     always None regardless of settings.
# With default weights, these 4 dead dims (combined weight 3.25 of 10.75) drag
# the weight-normalized final_score ceiling for a single-symbol synthetic scan
# down to ~50-60 even when every OTHER dim is maxed — below the sizing tiers
# (60/70/85, app/research/replay/sizing.py) these tests need to reliably clear
# for a non-zero buy. Zeroing their weight here stops them from diluting the
# average; it does NOT change what they measure (still 0 either way) — it only
# stops penalizing a candidate for dims this test suite has already disabled.
# If a future test in this file re-enables one of these rules, the (correct)
# non-zero score for that dim will still be silently dropped by this trimmed
# artifact — use default_artifact()'s weights (or a fresh custom artifact)
# instead of this helper for such a test.
_DEAD_DIMS_UNDER_TEST_SETTINGS = (
    "gap_down_quality_score",
    "volume_rank_score",
    "volume_power_score",
    "liquidity_score",
)


def _low_threshold_artifact():
    """default_artifact but with buy_threshold=0 (so any gate1-pass candidate is
    a gate2 buy) AND the weights of the dims that are structurally dead under
    this test file's OWN settings zeroed out (see
    _DEAD_DIMS_UNDER_TEST_SETTINGS) so they stop diluting the weight-normalized
    final_score average — real candidates then land comfortably inside the
    sizing tiers instead of near the dead-weight-capped ceiling."""
    base = default_artifact()
    weights = dict(base.weights)
    for name in _DEAD_DIMS_UNDER_TEST_SETTINGS:
        weights[name] = 0.0
    return type(base)(
        version=base.version,
        weights=weights,
        buy_threshold=0.0,
        normalization_caps=dict(base.normalization_caps),
    )


def test_stop_loss_triggers_next_minute_open_fill_with_cost_inclusive_equity(
    monkeypatch, tmp_path
):
    """S1 test ①: a bought position that crashes past the stop-loss level is sold
    at the NEXT minute bar's open (SELL-first), and the resulting equity equals a
    hand-computed cost-inclusive figure.

    Costs are non-zero on both legs so the hand calc proves cost-inclusiveness.
    Scenario (005930, 2024-06-03):
      - 09:29 tick: sharp pullback-then-partial-recovery -> gate1 candidate ->
        BUY at 09:30 open.
      - 09:32 tick: price has collapsed below the -8% stop -> stop_loss triggers
        -> SELL at 09:33 open.

    Post gate2 score_v2 normalization fix (final_score is now a weight-
    normalized [0, 100] average, see weighted_gate2_score), the 09:29 bar was
    sharpened from the original pullback_pct ~0.7% to 5.0% so
    intraday_pullback_strength_score clears 100 — with _low_threshold_artifact's
    trimmed weights this lands final_score at 72.95 (verified empirically via a
    throwaway instrumentation script reusing this test's own helpers, run
    against these exact bars), comfortably inside the 70-tier
    (score_to_budget_multiplier=1.0, app/research/replay/sizing.py) so the BUY
    still sizes to the qty=10 cap below.
    """
    _isolate_stores(monkeypatch, tmp_path)

    day = date(2024, 6, 3)

    def at(minute):
        return datetime(2024, 6, 3, 9, minute)

    # Single symbol, LIVE BUY RULES OFF (so early flat ticks don't auto-qualify
    # via the always-#1 live rank). 005930 then clears the v1 candidate gate
    # ONLY at 09:29 — a genuine pullback-then-partial-recovery bar (passed_count
    # 4) — and is not a candidate on the flat open (passed_count 1) nor on the
    # monotone crash (rebound fails). Combined with the held-symbol skip, that
    # yields exactly ONE buy and no pyramiding.
    #   09:29 obs: day_open 70000, day_low 66000, current 66500 -> pullback_pct
    #     = (70000-66500)/70000 = 5.00% (clears intraday_pullback_strength_score
    #     = 100) + rebound + controlled_down + range_recovery all pass -> BUY
    #     decision; fill = next bar open (09:30) = 66500.
    #   avg cost with 10 bps buy slippage = 66500 * 1.001 = 66566.5
    #   crash: 09:32 obs close 61200 -> gross = 61200/66566.5-1 = -8.06% <= -8 ->
    #     stop_loss -> SELL fill = next bar open (09:33) = 60000.
    bars = [
        _bar("005930", at(0), 70000, 70000, 70000, 70000, 50_000),
        _bar("005930", at(29), 70000, 70000, 66000, 66500, 60_000),
        _bar("005930", at(30), 66500, 66500, 66100, 66200, 60_000),
        _bar("005930", at(31), 65000, 65000, 64000, 64200, 90_000),
        _bar("005930", at(32), 63000, 63000, 61000, 61200, 100_000),
        _bar("005930", at(33), 60000, 60000, 59000, 59100, 120_000),
        _bar("005930", at(34), 59000, 59050, 58800, 58850, 110_000),
        _bar("005930", at(35), 58800, 58850, 58500, 58600, 40_000),
    ]
    provider = _FakeProvider(bars, prev_closes={"005930": 70000.0})

    costs = SimCostParams(
        buy_fee_bps=0.0,
        sell_fee_bps=0.0,
        sell_tax_bps=0.0,
        buy_slippage_bps=10.0,
        sell_slippage_bps=10.0,
    )
    settings = replay_settings(
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
    )
    initial_cash = 1_000_000.0

    report = run_portfolio_replay(
        artifact=_low_threshold_artifact(),
        provider=provider,
        dates=[day],
        settings=settings,
        costs=costs,
        initial_cash=initial_cash,
    )

    assert isinstance(report, SimReplayReport)
    # one buy + one stop-loss sell
    assert report.trade_count == 2

    # Hand calc ------------------------------------------------------------
    # BUY: fill ref = 09:30 open = 66500, slippage 10bps -> fill_price 66566.5.
    #   final_score 72.95 -> multiplier 1.0 (70-tier) -> budget = min(1_000_000,
    #   cash, 1_000_000) = 1_000_000
    #   qty = min(1_000_000 // 66566.5, 10) = min(15, 10) = 10
    buy_fill = 66500.0 * (1 + 10.0 / 1e4)
    qty = 10
    cash_after_buy = initial_cash - buy_fill * qty
    # SELL: fill ref = 09:33 open = 60000, slippage 10bps -> fill_price 59940.0.
    sell_fill = 60000.0 * (1 - 10.0 / 1e4)
    cash_after_sell = cash_after_buy + sell_fill * qty
    # position closed -> final equity == cash (no marks).
    final_equity = report.equity_curve[-1][1]
    assert final_equity == pytest.approx(cash_after_sell, rel=0, abs=1e-6)
    # The executed sell was classified as a stop_loss.
    sell_trades = [tr for tr in report.trades if tr.side == "SELL"]
    assert len(sell_trades) == 1
    assert sell_trades[0].trigger == "stop_loss"
    assert sell_trades[0].fill_price == pytest.approx(sell_fill, abs=1e-6)
    # The full-replay sell populates its OWN per-trade pnl (proceeds - cost
    # basis, cost-inclusive on both legs); buys carry None.
    assert sell_trades[0].pnl == pytest.approx(
        (sell_fill - buy_fill) * qty, abs=1e-6
    )
    assert sell_trades[0].pnl < 0
    buy_trades = [tr for tr in report.trades if tr.side == "BUY"]
    assert buy_trades[0].pnl is None


def test_at_most_one_buy_per_cycle(monkeypatch, tmp_path):
    """S1 test ②: when MULTIPLE symbols clear the candidate gate on the same
    tick, at most ONE buy is executed that tick (production "사이클당 매수 1건",
    plan §2.5). Two symbols both form a pullback-recovery candidate at 09:29 ->
    exactly one buy at 09:29, and no tick ever has more than one buy.
    """
    _isolate_stores(monkeypatch, tmp_path)
    day = date(2024, 6, 3)

    def at(minute):
        return datetime(2024, 6, 3, 9, minute)

    # 005930 and 000660 BOTH: flat open (not a candidate), then a pullback-recover
    # bar at 09:29 (candidate). Live buy rules off so candidacy is the pattern.
    bars = [
        _bar("005930", at(0), 70000, 70000, 70000, 70000, 50_000),
        _bar("005930", at(29), 70000, 70000, 69000, 69500, 60_000),
        _bar("005930", at(30), 69500, 69600, 69400, 69550, 40_000),
        _bar("000660", at(0), 50000, 50000, 50000, 50000, 55_000),
        _bar("000660", at(29), 50000, 50000, 49300, 49650, 65_000),
        _bar("000660", at(30), 49650, 49700, 49500, 49680, 45_000),
    ]
    provider = _FakeProvider(
        bars, prev_closes={"005930": 70000.0, "000660": 50000.0}
    )
    settings = replay_settings(
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
    )
    costs = SimCostParams(
        buy_fee_bps=0.0,
        sell_fee_bps=0.0,
        sell_tax_bps=0.0,
        buy_slippage_bps=0.0,
        sell_slippage_bps=0.0,
    )

    report = run_portfolio_replay(
        artifact=_low_threshold_artifact(),
        provider=provider,
        dates=[day],
        settings=settings,
        costs=costs,
        initial_cash=5_000_000.0,
    )

    buys = [tr for tr in report.trades if tr.side == "BUY"]
    # both symbols were candidates at 09:29, but only one buy landed that tick.
    buys_at_0929 = [tr for tr in buys if tr.ts == at(29).isoformat()]
    assert len(buys_at_0929) == 1
    # never more than one buy on any single tick.
    per_tick: dict[str, int] = {}
    for tr in buys:
        per_tick[tr.ts] = per_tick.get(tr.ts, 0) + 1
    assert max(per_tick.values()) == 1
    # the one 09:29 buy is one of the two candidates (top-1 by gate2 score).
    assert buys_at_0929[0].symbol in {"005930", "000660"}


def _straddle_scenario(monkeypatch, tmp_path):
    """Shared fixture for the stop_first test: buy at 09:30 open=70000
    (avg_cost 70000, no slippage), then a single 09:30 bar whose low/high
    straddle BOTH the -8% stop (64400) and the +10% take (77000) while closing
    flat at 70000 (so a close-based verdict triggers neither)."""
    _isolate_stores(monkeypatch, tmp_path)
    day = date(2024, 6, 3)

    def at(minute):
        return datetime(2024, 6, 3, 9, minute)

    bars = [
        _bar("005930", at(0), 71000, 71000, 71000, 71000, 50_000),
        _bar("005930", at(29), 71000, 71000, 69500, 70200, 60_000),
        # straddle bar: buy fills at open 70000; low 64000 <= stop 64400 and
        # high 77500 >= take 77000; close flat 70000 (no close-based trigger).
        _bar("005930", at(30), 70000, 77500, 64000, 70000, 90_000),
        _bar("005930", at(31), 70000, 70050, 69950, 70000, 40_000),
    ]
    provider = _FakeProvider(bars, prev_closes={"005930": 71000.0})
    settings = replay_settings(
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
        sell_take_profit_pct=10.0,
    )
    costs = SimCostParams(
        buy_fee_bps=0.0,
        sell_fee_bps=0.0,
        sell_tax_bps=0.0,
        buy_slippage_bps=0.0,
        sell_slippage_bps=0.0,
    )
    return provider, settings, costs, day, at


def test_stop_first_intrabar_double_breach_is_judged_a_stop(monkeypatch, tmp_path):
    """S1 test ③: a single minute bar that breaches BOTH the stop and the take
    is judged conservatively as a STOP under ``intrabar_mode="stop_first"`` —
    the position is sold (trigger stop_loss) at the next minute's open — whereas
    the default mode (close-based verdict) does NOT sell that flat-closing bar.
    """
    provider, settings, costs, day, at = _straddle_scenario(monkeypatch, tmp_path)

    stop_first = run_portfolio_replay(
        artifact=_low_threshold_artifact(),
        provider=provider,
        dates=[day],
        settings=settings,
        costs=costs,
        initial_cash=1_000_000.0,
        intrabar_mode="stop_first",
    )
    sells = [tr for tr in stop_first.trades if tr.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].trigger == "stop_loss"
    # sold at the NEXT minute open (09:31 open = 70000).
    assert sells[0].ts == at(30).isoformat()
    assert sells[0].fill_price == pytest.approx(70000.0, abs=1e-6)


def test_default_mode_does_not_sell_flat_closing_double_breach(monkeypatch, tmp_path):
    """S1 test ③ (contrast): the SAME straddle bar under the default intrabar
    mode leaves the ``build_sell_analysis`` verdict as-is; the flat close
    triggers no sell rule, so no sell happens."""
    provider, settings, costs, day, _at = _straddle_scenario(monkeypatch, tmp_path)

    default = run_portfolio_replay(
        artifact=_low_threshold_artifact(),
        provider=provider,
        dates=[day],
        settings=settings,
        costs=costs,
        initial_cash=1_000_000.0,
        intrabar_mode="none",
    )
    assert [tr for tr in default.trades if tr.side == "SELL"] == []


def test_report_to_dict_schema_is_json_serializable(monkeypatch, tmp_path):
    """S1 test ④: ``SimReplayReport.to_dict`` exposes the required fields
    (total_return_pct, mdd_pct, trade_count, win_rate, day-downsampled
    equity_curve, per-symbol pnl top/bottom up to 5) and is JSON-serializable.
    """
    import json

    _isolate_stores(monkeypatch, tmp_path)
    day = date(2024, 6, 3)

    def at(minute):
        return datetime(2024, 6, 3, 9, minute)

    # A buy-then-stop-loss run so the report carries a trade, a realized pnl and
    # a non-trivial equity curve.
    bars = [
        _bar("005930", at(0), 70000, 70000, 70000, 70000, 50_000),
        _bar("005930", at(29), 70000, 70000, 69000, 69500, 60_000),
        _bar("005930", at(30), 69500, 69500, 69100, 69200, 60_000),
        _bar("005930", at(31), 68000, 68000, 67000, 67200, 90_000),
        _bar("005930", at(32), 66000, 66000, 64000, 64200, 100_000),
        _bar("005930", at(33), 63000, 63000, 62000, 62100, 120_000),
        _bar("005930", at(34), 62000, 62050, 61800, 61850, 110_000),
    ]
    provider = _FakeProvider(bars, prev_closes={"005930": 70000.0})
    settings = replay_settings(
        buy_rule_enable_live_volume_rank=False,
        buy_rule_enable_live_volume_power_rank=False,
    )
    costs = SimCostParams(
        buy_fee_bps=5.0,
        sell_fee_bps=5.0,
        sell_tax_bps=20.0,
        buy_slippage_bps=10.0,
        sell_slippage_bps=10.0,
    )

    report = run_portfolio_replay(
        artifact=_low_threshold_artifact(),
        provider=provider,
        dates=[day],
        settings=settings,
        costs=costs,
        initial_cash=1_000_000.0,
    )
    payload = report.to_dict()

    # Required keys present.
    for key in (
        "total_return_pct",
        "mdd_pct",
        "trade_count",
        "win_rate",
        "equity_curve",
        "per_symbol_pnl_top",
        "per_symbol_pnl_bottom",
    ):
        assert key in payload

    assert isinstance(payload["total_return_pct"], float)
    assert isinstance(payload["trade_count"], int)
    assert payload["trade_count"] == 2  # one buy + one stop-loss sell
    assert 0.0 <= payload["win_rate"] <= 1.0
    assert payload["mdd_pct"] <= 0.0  # drawdown is non-positive

    # equity_curve is a day-downsampled list of [date-iso, equity].
    assert payload["equity_curve"] == [["2024-06-03", pytest.approx(report.final_equity)]]
    assert len(payload["equity_curve"]) == 1

    # per-symbol pnl lists are capped at 5 and contain the traded symbol.
    assert len(payload["per_symbol_pnl_top"]) <= 5
    assert len(payload["per_symbol_pnl_bottom"]) <= 5
    assert payload["per_symbol_pnl_top"][0][0] == "005930"

    # Fully JSON-serializable (no datetimes / dataclasses leak through).
    dumped = json.dumps(payload)
    assert json.loads(dumped)["trade_count"] == 2


def test_history_writer_flush_cadence_is_batched(monkeypatch):
    """Stage-B cost (V1 ④ 실측): ``run_portfolio_replay`` must create its
    ``ReplayHistoryWriter`` with a BATCHED ``history_refresh_ticks`` (default
    390 ≈ once per session day). The JSONL is a debug artifact since the
    in-memory history seam — flushing every tick (writer default 1) rewrites
    ~3k records per tick and dominated Stage B wall time (14h+ per finalist)."""
    from app.research.replay import sim_runner

    captured: dict = {}
    real_writer = sim_runner.ReplayHistoryWriter

    def spy(path, **kwargs):
        captured.update(kwargs)
        return real_writer(path, **kwargs)

    monkeypatch.setattr(sim_runner, "ReplayHistoryWriter", spy)
    report = sim_runner.run_portfolio_replay(
        artifact=default_artifact(),
        provider=None,
        dates=[],
        settings=None,
        costs=SimCostParams(
            buy_fee_bps=0.0,
            sell_fee_bps=0.0,
            sell_tax_bps=0.0,
            buy_slippage_bps=0.0,
            sell_slippage_bps=0.0,
        ),
        initial_cash=1_000_000.0,
    )
    assert report.trade_count == 0
    assert captured.get("history_refresh_ticks") == 390


def test_win_rate_classifies_each_sell_by_its_own_trade_pnl():
    """Coordinator-review fix: win_rate must classify each SELL by that trade's
    OWN realized pnl, not the symbol's cumulative pnl. Same symbol sold twice —
    a +15,000 win then (after a re-buy at higher cost) a -90,000 loss, so the
    symbol-cumulative sum is NEGATIVE: cumulative classification would call both
    sells losses (win_rate 0.0); per-trade classification gives exactly 0.5.
    """
    sim = BrokerSimulator(
        initial_cash=1_000_000.0,
        costs=SimCostParams(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    trades = [
        SimTradeRecord(
            symbol="005930", side="BUY", ts="2024-06-03T09:29:00",
            qty=10, fill_price=69500.0, trigger="buy", pnl=None,
        ),
        SimTradeRecord(
            symbol="005930", side="SELL", ts="2024-06-03T09:40:00",
            qty=10, fill_price=71000.0, trigger="take_profit", pnl=15000.0,
        ),
        SimTradeRecord(
            symbol="005930", side="BUY", ts="2024-06-03T10:00:00",
            qty=10, fill_price=71000.0, trigger="buy", pnl=None,
        ),
        SimTradeRecord(
            symbol="005930", side="SELL", ts="2024-06-03T10:30:00",
            qty=10, fill_price=62000.0, trigger="stop_loss", pnl=-90000.0,
        ),
    ]
    realized_by_symbol = {"005930": 15000.0 - 90000.0}  # net NEGATIVE

    report = _build_report(
        sim=sim,
        initial_cash=1_000_000.0,
        day_end_equity=[("2024-06-03", 925_000.0)],
        trades=trades,
        realized_by_symbol=realized_by_symbol,
    )

    # one winning sell + one losing sell on the SAME symbol -> exactly 0.5.
    assert report.win_rate == 0.5
    # each trade row carries its OWN pnl (None for buys) through to_dict.
    rows = report.to_dict()["trades"]
    assert [row["pnl"] for row in rows] == [None, 15000.0, None, -90000.0]
    # per-symbol ranking still uses the cumulative realized pnl (unchanged).
    assert report.per_symbol_pnl_bottom[0] == ("005930", -75000.0)
