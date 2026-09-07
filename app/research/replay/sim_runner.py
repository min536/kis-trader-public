"""Slice S1 — full portfolio minute replay for finalist artifacts.

Runs the production cycle semantics (plan §2.5) over replay minute data:

* iterate each trading day's 1-minute ticks (09:01-15:30, the R1 tick window);
* **SELL first** — for every held symbol, build the observable market snapshot
  from a synthesized KIS payload (F1) and run the REAL
  ``app.strategy.sell_decision.build_sell_analysis``; a triggered symbol is sold
  at the NEXT minute bar's open (``_bar_open_at_or_after`` reused from R1's
  ``record_runner``; if no next same-day bar exists, at the last same-day close);
* **BUY** — drive the REAL scanner via ``run_scan_at`` (F2), score every
  gate1-pass candidate with ``weighted_gate2_score`` over the artifact, take the
  TOP-1 whose final score >= ``artifact.buy_threshold`` (ties: ascending symbol),
  size it with ``score_to_budget_multiplier`` + ``apply_budget_caps``, and buy at
  the next minute bar's open — AT MOST ONE buy per tick;
* mark-to-market and ``record_equity`` at the tick.

No forced end-of-day liquidation (overnight holds mirror production). Day-loop
mechanics mirror ``record_runner``: one reused ``ReplayHistoryWriter`` (with
``reseed_for_new_day`` between days) and one ``ReplayLiveSnapshotWriter``;
``provider.load_window(day, day)`` per day.

``intrabar_mode="stop_first"`` is the conservative intrabar rule: when a single
minute bar's low/high straddle BOTH the stop-loss and take-profit levels, the
symbol is judged a STOP (sold) regardless of the ``build_sell_analysis`` verdict.
Default mode leaves that verdict as-is.

Leaf research module: stdlib + runtime pure-calculation siblings + replay
research siblings only. No ``get_settings`` / broker imports; no ``tests`` import.
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.gate2.adapter import condition_scores_from_v1
from app.gate2.schema import ScoreV2Artifact
from app.gate2.score_v2 import weighted_gate2_score
from app.market_data.schema import build_market_snapshot
from app.portfolio.schema import build_portfolio_snapshot
from app.research.replay.broker_sim import BrokerSimulator, SimCostParams
from app.research.replay.payload_synth import synth_quote_payload
from app.research.replay.record_runner import _bar_open_at_or_after
from app.research.replay.scan_driver import (
    ReplayHistoryWriter,
    ReplayLiveSnapshotWriter,
    run_scan_at,
)
from app.research.replay.sizing import apply_budget_caps, score_to_budget_multiplier
from app.strategy.sell_decision import build_sell_analysis

_ONE_MINUTE = timedelta(minutes=1)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SimTradeRecord:
    """One executed fill (internal detail; not part of the required schema but
    exposed so callers/tests can inspect trigger classification).

    ``pnl`` is THIS trade's own realized pnl (``proceeds - cost_basis``) for
    SELL records; ``None`` for BUY records. win_rate classifies each sell by
    this per-trade figure, never by the symbol's cumulative pnl.
    """

    symbol: str
    side: str
    ts: str
    qty: int
    fill_price: float
    trigger: str | None
    pnl: float | None = None


@dataclass(frozen=True)
class SimReplayReport:
    """Summary of a portfolio replay (plan §7 S1)."""

    total_return_pct: float
    mdd_pct: float
    trade_count: int
    win_rate: float
    equity_curve: list[tuple[str, float]]
    per_symbol_pnl_top: list[tuple[str, float]]
    per_symbol_pnl_bottom: list[tuple[str, float]]
    initial_cash: float = 0.0
    final_equity: float = 0.0
    trades: tuple[SimTradeRecord, ...] = field(default_factory=tuple)
    partial: bool = False
    completed_days: int | None = None
    requested_days: int | None = None

    def to_dict(self) -> dict:
        """JSON-serializable view of the report."""
        return {
            "total_return_pct": self.total_return_pct,
            "mdd_pct": self.mdd_pct,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "initial_cash": self.initial_cash,
            "final_equity": self.final_equity,
            "equity_curve": [[d, e] for d, e in self.equity_curve],
            "per_symbol_pnl_top": [[s, p] for s, p in self.per_symbol_pnl_top],
            "per_symbol_pnl_bottom": [[s, p] for s, p in self.per_symbol_pnl_bottom],
            "trades": [
                {
                    "symbol": tr.symbol,
                    "side": tr.side,
                    "ts": tr.ts,
                    "qty": tr.qty,
                    "fill_price": tr.fill_price,
                    "trigger": tr.trigger,
                    "pnl": tr.pnl,
                }
                for tr in self.trades
            ],
            "partial": self.partial,
            "completed_days": self.completed_days,
            "requested_days": self.requested_days,
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _hhmm_to_time(hhmm: str) -> time:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return time(h, m)


def _iter_tick_minutes(day: date, open_t: time, close_t: time):
    t = datetime.combine(day, open_t)
    end = datetime.combine(day, close_t)
    while t <= end:
        yield t
        t += _ONE_MINUTE


def _last_close_on_day(provider, symbol: str, close_dt: datetime) -> float | None:
    obs = provider.observable_snapshot(symbol, close_dt)
    if obs is None:
        return None
    return float(obs.close)


def _next_open_or_last_close(
    provider, symbol: str, t: datetime, close_dt: datetime
) -> float | None:
    """Fill reference = open of the first same-day bar with ``bar_ts >= t+1min``;
    if the session ends before any such bar, fall back to the last same-day
    close (plan §2.5 fill convention)."""
    fill = _bar_open_at_or_after(provider, symbol, t + _ONE_MINUTE, close_dt)
    if fill is not None:
        return fill
    return _last_close_on_day(provider, symbol, close_dt)


def _portfolio_from_sim(sim: BrokerSimulator, obs_by_symbol: dict) -> object:
    """Minimal KIS balance-response shape from the sim's holdings marked at the
    current observations, fed to the REAL ``build_portfolio_snapshot`` so the
    sell rules read the production portfolio shape."""
    output1 = []
    total_market_value = 0
    for symbol, position in sim.positions.items():
        obs = obs_by_symbol.get(symbol)
        current_price = int(obs.close) if obs is not None else int(position.avg_price)
        qty = int(position.qty)
        market_value = current_price * qty
        cost_value = int(position.avg_price) * qty
        total_market_value += market_value
        output1.append(
            {
                "pdno": symbol,
                "hldg_qty": str(qty),
                "pchs_avg_pric": str(int(position.avg_price)),
                "prpr": str(current_price),
                "evlu_amt": str(market_value),
                "evlu_pfls_amt": str(market_value - cost_value),
                "evlu_pfls_rt": (
                    f"{(current_price / position.avg_price - 1) * 100:.2f}"
                    if position.avg_price
                    else "0.0"
                ),
            }
        )
    cash = int(sim.cash)
    return build_portfolio_snapshot(
        {
            "rt_cd": "0",
            "output1": output1,
            "output2": [
                {
                    "dnca_tot_amt": str(cash),
                    "prvs_rcdl_excc_amt": str(cash),
                    "nxdy_excc_amt": str(cash),
                    "tot_evlu_amt": str(cash + total_market_value),
                }
            ],
        }
    )


def _observations_for(provider, symbols, t: datetime) -> dict:
    return {symbol: provider.observable_snapshot(symbol, t) for symbol in symbols}


def _market_snapshot_for(obs, prev_close):
    """Synthesize the F1 payload for ``obs`` and parse it through the REAL
    ``build_market_snapshot`` so the sell rules see the exact runtime shape."""
    payload = synth_quote_payload(obs, prev_close=prev_close) if obs is not None else None
    if payload is None:
        return None
    return build_market_snapshot(payload["output"])


def _intrabar_stop_first_breach(
    obs, average_cost: float, *, stop_loss_pct: float, take_profit_pct: float
) -> bool:
    """True when this bar's low/high straddle BOTH the stop and take levels
    derived from ``average_cost`` (gross-pnl basis mirrors ``evaluate_stop_loss``
    / ``evaluate_take_profit`` when ``use_cost_aware_pnl`` is off)."""
    if obs is None or average_cost <= 0:
        return False
    stop_price = average_cost * (1.0 + stop_loss_pct / 100.0)
    take_price = average_cost * (1.0 + take_profit_pct / 100.0)
    return float(obs.low) <= stop_price and float(obs.high) >= take_price


def _run_sell_phase(
    *,
    sim: BrokerSimulator,
    provider,
    settings,
    t: datetime,
    close_dt: datetime,
    intrabar_mode: str,
    trades: list,
    realized_by_symbol: dict,
) -> None:
    held_symbols = tuple(sim.positions)
    if not held_symbols:
        return
    obs_by_symbol = _observations_for(provider, held_symbols, t)
    portfolio_snapshot = _portfolio_from_sim(sim, obs_by_symbol)
    for symbol in held_symbols:
        position = sim.positions.get(symbol)
        if position is None:
            continue
        obs = obs_by_symbol.get(symbol)
        prev_close = provider.prev_close(symbol, t.date())
        market_snapshot = _market_snapshot_for(obs, prev_close)
        if market_snapshot is None:
            continue
        analysis = build_sell_analysis(
            symbol=symbol,
            holding_qty=int(position.qty),
            average_cost=int(position.avg_price),
            market_snapshot=market_snapshot,
            portfolio_snapshot=portfolio_snapshot,
            settings=settings,
        )
        decision = analysis.sell_decision
        trigger = decision.triggered_rule_name if decision.should_attempt_sell else None
        should_sell = decision.should_attempt_sell
        if intrabar_mode == "stop_first" and _intrabar_stop_first_breach(
            obs,
            float(position.avg_price),
            stop_loss_pct=float(settings.sell_stop_loss_pct),
            take_profit_pct=float(settings.sell_take_profit_pct),
        ):
            should_sell = True
            trigger = "stop_loss"
        if not should_sell:
            continue
        fill_ref = _next_open_or_last_close(provider, symbol, t, close_dt)
        if fill_ref is None:
            continue
        qty = int(position.qty)
        cost_basis = position.avg_price * qty
        fill = sim.sell(symbol, qty, fill_ref, t)
        if fill is None:
            continue
        proceeds = fill.fill_price * qty - fill.fee - fill.tax
        trade_pnl = proceeds - cost_basis
        realized_by_symbol[symbol] = (
            realized_by_symbol.get(symbol, 0.0) + trade_pnl
        )
        trades.append(
            SimTradeRecord(
                symbol=symbol,
                side="SELL",
                ts=t.isoformat(),
                qty=qty,
                fill_price=fill.fill_price,
                trigger=trigger,
                pnl=trade_pnl,
            )
        )


def _select_buy_candidate(results, artifact: ScoreV2Artifact, *, held: frozenset):
    """Top-1 v1 buy candidate whose weighted gate2 score >= buy_threshold (ties
    broken by ascending symbol). Returns ``(result, final_score)`` or ``None``.

    Gate2 is layered ON TOP of the unchanged v1 buy decision: only symbols the
    real scanner already accepts (``result.candidate is True``) are eligible, so
    the replay never buys a name v1 rejects (runtime score_v1 stays authoritative
    — plan §0). The gate2 score is the additional re-ranking / threshold filter.

    Already-held symbols are skipped: production never re-buys a name it already
    holds on the very next cycle (the buy flow's holding / rebuy-cooldown guards),
    so ``run_portfolio_replay`` mirrors that to avoid degenerate per-tick
    pyramiding of one symbol (plan §2.5 "매수 1건").
    """
    scored: list[tuple[float, str, object]] = []
    for result in results:
        if result.symbol in held:
            continue
        if not bool(getattr(result, "candidate", False)):
            continue
        scores = condition_scores_from_v1(
            result.score_components, artifact.normalization_caps
        )
        final = weighted_gate2_score(scores, artifact.weights).final_score
        if final >= artifact.buy_threshold:
            scored.append((final, result.symbol, result))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, _symbol, best_result = scored[0]
    return best_result, best_score


def _size_and_buy(
    *,
    sim: BrokerSimulator,
    result,
    final_score: float,
    provider,
    settings,
    t: datetime,
    close_dt: datetime,
    trades: list,
) -> None:
    fill_ref = _next_open_or_last_close(provider, result.symbol, t, close_dt)
    if fill_ref is None or fill_ref <= 0:
        return
    max_budget = float(settings.buy_max_budget_per_trade_krw)
    multiplier = score_to_budget_multiplier(final_score)
    raw_budget = max_budget * multiplier
    budget = apply_budget_caps(raw_budget, cash=float(sim.cash), max_budget=max_budget)
    if budget <= 0:
        return
    qty = int(budget // fill_ref)
    qty = min(qty, int(settings.buy_max_qty_per_trade))
    if qty <= 0:
        return
    fill = sim.buy(result.symbol, qty, fill_ref, t)
    if fill is None:
        return
    trades.append(
        SimTradeRecord(
            symbol=result.symbol,
            side="BUY",
            ts=t.isoformat(),
            qty=qty,
            fill_price=fill.fill_price,
            trigger="buy",
        )
    )


def _mark_prices(provider, sim: BrokerSimulator, t: datetime) -> dict:
    marks: dict[str, float] = {}
    for symbol in sim.positions:
        obs = provider.observable_snapshot(symbol, t)
        if obs is not None:
            marks[symbol] = float(obs.close)
    return marks


def _build_report(
    *,
    sim: BrokerSimulator,
    initial_cash: float,
    day_end_equity: list[tuple[str, float]],
    trades: list,
    realized_by_symbol: dict,
    partial: bool = False,
    completed_days: int | None = None,
    requested_days: int | None = None,
) -> SimReplayReport:
    final_equity = day_end_equity[-1][1] if day_end_equity else initial_cash
    total_return_pct = (
        (final_equity / initial_cash - 1.0) * 100.0 if initial_cash else 0.0
    )
    # Max drawdown over the full per-tick equity path (peak-to-trough).
    peak = float("-inf")
    mdd = 0.0
    for _ts, equity in sim.equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (equity - peak) / peak * 100.0
            mdd = min(mdd, drawdown)
    trade_count = len(trades)
    sells = [tr for tr in trades if tr.side == "SELL"]
    # Each sell is classified by ITS OWN realized pnl — a symbol sold twice
    # (one win, one loss) must count as one of each, so never classify by the
    # symbol-cumulative pnl (that sum is only for the top/bottom ranking below).
    wins = sum(1 for tr in sells if (tr.pnl or 0) > 0)
    win_rate = (wins / len(sells)) if sells else 0.0

    ranked = sorted(realized_by_symbol.items(), key=lambda item: item[1], reverse=True)
    top = [(s, p) for s, p in ranked[:5]]
    bottom = [(s, p) for s, p in sorted(ranked, key=lambda item: item[1])[:5]]

    return SimReplayReport(
        total_return_pct=total_return_pct,
        mdd_pct=mdd,
        trade_count=trade_count,
        win_rate=win_rate,
        equity_curve=day_end_equity,
        per_symbol_pnl_top=top,
        per_symbol_pnl_bottom=bottom,
        initial_cash=initial_cash,
        final_equity=final_equity,
        trades=tuple(trades),
        partial=partial,
        completed_days=completed_days,
        requested_days=requested_days,
    )


def run_portfolio_replay(
    *,
    artifact: ScoreV2Artifact,
    provider,
    dates,
    settings,
    costs: SimCostParams,
    initial_cash: float,
    intrabar_mode: str = "none",
    session_open: str = "09:01",
    session_close: str = "15:30",
    history_flush_ticks: int = 390,
    on_day_end=None,
    should_stop=None,
) -> SimReplayReport:
    """Full portfolio minute replay (plan §7 S1). See module docstring.

    ``history_flush_ticks`` batches the history writer's debug-JSONL flush
    (default 390 ≈ once per session day). The scanner reads the in-memory
    replay buffer, so the flush cadence never affects replay results — but the
    writer's own default (1 = every tick) rewrites ~3k records per tick and
    dominated Stage B wall time before this was wired.

    ``on_day_end: Callable[[int, date, float], None] | None`` fires only for a
    FULLY completed day (day_index, day, that day's closing equity) — a day
    truncated by ``should_stop`` never fires it (S2, docs/slack_backtest_speed_
    timeout_delegation_20260712.md §5).

    ``should_stop: Callable[[], bool] | None`` is polled at two points: the
    start of each day (before ``load_window`` — a True here stops immediately,
    that day never starts) and the start of each tick (before the SELL phase —
    a True here appends a partial ``(day.isoformat(), last_equity)`` entry then
    stops). Neither kwarg perturbs the replay result when omitted or when
    ``should_stop`` never returns True (equivalence pin, S2 red ③).
    """
    dates = list(dates)
    open_t = _hhmm_to_time(session_open)
    close_t = _hhmm_to_time(session_close)
    sim = BrokerSimulator(initial_cash=initial_cash, costs=costs)
    trades: list[SimTradeRecord] = []
    realized_by_symbol: dict[str, float] = {}
    day_end_equity: list[tuple[str, float]] = []
    stopped = False
    completed_days_count = 0

    with contextlib.ExitStack() as stack:
        tmp_path = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="gate2_sim_"))
        )
        live_dir = tmp_path / "live"
        live_dir.mkdir()
        live_writer = ReplayLiveSnapshotWriter(live_dir)
        history_writer = ReplayHistoryWriter(
            tmp_path / "cycle_snapshots.jsonl",
            history_refresh_ticks=history_flush_ticks,
        )

        for day_index, day in enumerate(dates):
            if should_stop is not None and should_stop():
                stopped = True
                break
            provider.load_window(day, day)
            if day_index > 0:
                history_writer.reseed_for_new_day()
            close_dt = datetime.combine(day, close_t)
            last_equity = sim.equity(_mark_prices(provider, sim, close_dt))
            day_stopped_mid_tick = False
            for t in _iter_tick_minutes(day, open_t, close_t):
                if should_stop is not None and should_stop():
                    day_end_equity.append((day.isoformat(), last_equity))
                    day_stopped_mid_tick = True
                    stopped = True
                    break
                # ① SELL first.
                _run_sell_phase(
                    sim=sim,
                    provider=provider,
                    settings=settings,
                    t=t,
                    close_dt=close_dt,
                    intrabar_mode=intrabar_mode,
                    trades=trades,
                    realized_by_symbol=realized_by_symbol,
                )
                # ② BUY (top-1 gate2 candidate; at most one buy per tick).
                results = run_scan_at(
                    t=t,
                    provider=provider,
                    sim=sim,
                    settings=settings,
                    history_writer=history_writer,
                    live_writer=live_writer,
                )
                selection = _select_buy_candidate(
                    results, artifact, held=frozenset(sim.positions)
                )
                if selection is not None:
                    best_result, best_score = selection
                    _size_and_buy(
                        sim=sim,
                        result=best_result,
                        final_score=best_score,
                        provider=provider,
                        settings=settings,
                        t=t,
                        close_dt=close_dt,
                        trades=trades,
                    )
                # ③ record equity (mark held positions at current close).
                marks = _mark_prices(provider, sim, t)
                sim.record_equity(t, marks)
                last_equity = sim.equity(marks)
            if day_stopped_mid_tick:
                break
            day_end_equity.append((day.isoformat(), last_equity))
            completed_days_count += 1
            if on_day_end is not None:
                on_day_end(day_index, day, last_equity)

    return _build_report(
        sim=sim,
        initial_cash=initial_cash,
        day_end_equity=day_end_equity,
        trades=trades,
        realized_by_symbol=realized_by_symbol,
        partial=stopped,
        completed_days=completed_days_count,
        requested_days=len(dates),
    )
