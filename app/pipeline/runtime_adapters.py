from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.auth.token import ApiHttpError, issue_access_token, is_rate_limit_response
from app.core.error_classification import looks_like_rate_limit_error
from app.core.market_session import get_korean_market_session
from app.core.runtime_budget import (
    api_budget_can_quote,
    api_budget_register_request,
    api_budget_remaining_quotes,
)
from app.core.sell_watch_budget import build_sell_watch_budget_plan
from app.core.sell_watch_cursor import build_sell_watch_cursor_plan
from app.core.time_utils import get_korean_now
from app.domestic_stock.balance import inquire_balance
from app.domestic_stock.quote import inquire_price
from app.execution import calculate_sell_position_sizing
import app.execution.buy_flow as _buy_flow
import app.execution.sell_flow as _sell_flow
from app.market_data.schema import build_market_snapshot
from app.pipeline.intents import BuyIntent, OrderIntent, SellIntent
from app.portfolio.schema import build_portfolio_snapshot
from app.scanner import (
    resolve_mock_buy_price_floor_krw,
    scan_target_symbols,
    select_top_candidate,
    serialize_selection_details,
)
from app.scanner.runtime_scan import apply_buy_runtime_guards_to_scan_results
from app.strategy.sell_decision import (
    build_sell_analysis,
    build_sell_watch_priority,
    select_top_sell_candidate,
)


IntentFactory = Callable[..., BuyIntent | SellIntent | None]
OrderHandler = Callable[[OrderIntent], Any]


@dataclass(frozen=True)
class LaneSchedulerRuntimeAdapters:
    """Production-facing adapter bundle for the lane scheduler.

    The scheduler owns ordering and budget policy. These adapters own the
    translation between existing runtime decisions/wrappers and lane intents.
    """

    buy_symbols: tuple[str, ...] = ()
    execution_token: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)
    quote_prefetch_func: Callable[..., Any] | None = None
    sell_intent_factory: IntentFactory | None = None
    buy_intent_factory: IntentFactory | None = None
    sell_order_handler: OrderHandler | None = None
    buy_order_handler: OrderHandler | None = None
    buy_controller: Any | None = None
    clock: Callable[[], float] | None = None
    now_func: Callable[[], datetime] | None = None
    order_gate_handler_timeout_enabled: bool = True


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_mutable_mapping(value: Any) -> MutableMapping[str, Any]:
    return value if isinstance(value, MutableMapping) else {}


def _context_payload(context: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        payload = context.get(key)
        if callable(payload):
            payload = payload()
        if isinstance(payload, Mapping):
            return payload
    return {}


def _payload_text(
    payload: Mapping[str, Any],
    key: str,
    *,
    fallback: str = "",
) -> str:
    value = payload.get(key)
    if value is None:
        return fallback
    return str(value)


@dataclass
class LaneProductionRuntime:
    settings: Any
    scheduler_state: Mapping[str, Any]
    api_budget_state: Mapping[str, Any] | None
    context: MutableMapping[str, Any]
    token: str | None = None
    portfolio_snapshot: Any | None = None
    session_status: Any | None = None
    market_open: bool | None = None
    sell_analysis_results: tuple[Any, ...] = ()
    scan_results: tuple[Any, ...] = ()
    selected_buy_candidate: Any | None = None
    selection_details: dict[str, object] = field(default_factory=dict)

    def ensure_account_context(self) -> None:
        if self.session_status is None:
            session_status = self.context.get("session_status")
            self.session_status = (
                session_status
                if session_status is not None
                else get_korean_market_session()
            )
            explicit_market_open = self.context.get("market_open")
            self.market_open = (
                bool(explicit_market_open)
                if explicit_market_open is not None
                else bool(getattr(self.session_status, "order_allowed", False))
            )
            self.context["session_status"] = self.session_status
            self.context["market_open"] = bool(self.market_open)
            # Observability parity (P1): persist the session object the lane
            # just resolved into the durable scheduler runtime context so the
            # main bridge can map it onto ctx.session_status WITHOUT recomputing
            # it (C2). The scheduler's per-cycle ``context`` dict is ephemeral;
            # ``lane_scheduler_runtime_context`` survives the cycle. Telemetry
            # only — this never touches the order path.
            self._store_session_status_for_bridge(self.session_status)

        if self.token is None:
            token = self.context.get("token") or self.context.get("execution_token")
            self.token = str(token or issue_access_token())
            self.context["token"] = self.token

        if self.portfolio_snapshot is None:
            portfolio_snapshot = self.context.get("portfolio_snapshot")
            if portfolio_snapshot is None:
                balance_data = self.context.get("balance_data")
                if balance_data is None:
                    balance_data = inquire_balance(token=self.token)
                    api_budget_state = _as_mutable_mapping(self.api_budget_state)
                    if api_budget_state:
                        api_budget_register_request(
                            api_budget_state,
                            now=get_korean_now(),
                        )
                if not isinstance(balance_data, Mapping) or balance_data.get("rt_cd") != "0":
                    self.context["account_skipped_reason"] = "balance_failed"
                    raise RuntimeError(f"잔고 조회 실패: {balance_data}")
                portfolio_snapshot = build_portfolio_snapshot(balance_data)
            self.portfolio_snapshot = portfolio_snapshot
            self.context["portfolio_snapshot"] = portfolio_snapshot
            self._store_portfolio_snapshot_for_bridge(portfolio_snapshot)

    def _store_session_status_for_bridge(self, session_status: Any) -> None:
        """Persist the lane's session object into the durable runtime context.

        Mapping only: writes to ``scheduler_state['lane_scheduler_runtime_context']``
        so the main bridge can read the exact object by identity (P1/C2). Never
        raises — an unexpected scheduler_state shape must not break the lane.
        """

        scheduler_state = self.scheduler_state
        if not isinstance(scheduler_state, MutableMapping):
            return
        runtime_context = scheduler_state.get("lane_scheduler_runtime_context")
        if isinstance(runtime_context, MutableMapping):
            runtime_context["session_status"] = session_status

    def _store_portfolio_snapshot_for_bridge(self, portfolio_snapshot: Any) -> None:
        """Expose the lane's already-fetched balance snapshot to finalization."""
        scheduler_state = self.scheduler_state
        if not isinstance(scheduler_state, MutableMapping):
            return
        runtime_context = scheduler_state.get("lane_scheduler_runtime_context")
        if isinstance(runtime_context, MutableMapping):
            runtime_context["portfolio_snapshot"] = portfolio_snapshot

    def store_buy_scan_outcome_for_bridge(
        self,
        *,
        requested_symbols: tuple[str, ...],
        raw_scan_results: tuple[Any, ...],
        scan_results: tuple[Any, ...],
        selected_candidate: Any,
    ) -> None:
        """Persist the lane's BUY-scan outcome telemetry for the main bridge.

        Mapping only (P1 Stage 2): writes into
        ``scheduler_state['lane_scheduler_runtime_context']`` the fields finalize's
        candidate-outcome / funnel builders need — WITHOUT the bulky
        ``selection_details`` (C4). Never raises.
        """

        scheduler_state = self.scheduler_state
        if not isinstance(scheduler_state, MutableMapping):
            return
        runtime_context = scheduler_state.get("lane_scheduler_runtime_context")
        if not isinstance(runtime_context, MutableMapping):
            return
        runtime_context["buy_scan_outcome"] = {
            "requested_symbols": tuple(requested_symbols),
            "raw_scan_results": tuple(raw_scan_results),
            "scan_results": tuple(scan_results),
            "selected_candidate": selected_candidate,
        }

    @property
    def runtime_state(self) -> dict[str, Any]:
        state = self.context.get("state") or self.context.get("runtime_state")
        return state if isinstance(state, dict) else {}

    @property
    def timing_summary(self) -> dict[str, object]:
        timing = self.context.get("timing_summary")
        return timing if isinstance(timing, dict) else {}

    @property
    def effective_buy_settings(self) -> Any:
        return self.context.get("effective_buy_settings") or self.settings

    @property
    def regime_state(self) -> dict[str, object] | None:
        regime_state = self.context.get("regime_state")
        return regime_state if isinstance(regime_state, dict) else None

    @property
    def daily_pnl_brake_state(self) -> dict[str, object] | None:
        brake_state = self.context.get("daily_pnl_brake_state")
        return brake_state if isinstance(brake_state, dict) else None


def _note_runtime_rate_limit(
    runtime: LaneProductionRuntime,
    *,
    source: str,
    exc: BaseException | None = None,
) -> None:
    runtime.context["rate_limit_triggered"] = True
    runtime.context["rate_limit_source"] = source
    if exc is not None:
        runtime.context["rate_limit_error"] = str(exc)


def _sell_watch_ordered_positions(
    *,
    runtime: LaneProductionRuntime,
    held_positions: tuple[Any, ...],
) -> tuple[tuple[Any, ...], int]:
    state = runtime.runtime_state
    total = len(held_positions)
    if total <= 0:
        state["sell_watch_next_start_index"] = 0
        return (), 0

    portfolio_snapshot = runtime.portfolio_snapshot
    priority_rows = [
        (
            position,
            build_sell_watch_priority(
                position=position,
                portfolio_snapshot=portfolio_snapshot,
            ),
        )
        for position in held_positions
    ]
    priority_rows.sort(
        key=lambda item: (
            -float(item[1].get("priority_score", 0.0) or 0.0),
            str(getattr(item[0], "symbol", "")),
        )
    )
    prioritized_positions = tuple(item[0] for item in priority_rows)
    cursor_before = int(state.get("sell_watch_next_start_index", 0) or 0)
    cursor_before %= total
    retry_symbol = str(state.get("sell_watch_retry_symbol") or "").strip()
    recent_partial = bool(
        _as_mapping(runtime.api_budget_state).get("last_sell_watch_partial")
        or state.get("last_sell_watch_partial")
    )
    top_priority_score = float(
        priority_rows[0][1].get("priority_score", 0.0) if priority_rows else 0.0
    )
    cursor_plan = build_sell_watch_cursor_plan(
        symbols=tuple(str(getattr(position, "symbol", "")) for position in prioritized_positions),
        next_start_index=cursor_before,
        retry_symbol=retry_symbol,
        risk_first_override=recent_partial and top_priority_score >= 1.0,
    )
    if cursor_plan.clear_retry_symbol:
        state["sell_watch_retry_symbol"] = None
    ordered = tuple(
        prioritized_positions[index]
        for index in cursor_plan.ordered_indices
    )
    return ordered, int(cursor_plan.start_index)


def _sell_watch_evaluation_limit(
    *,
    runtime: LaneProductionRuntime,
    total_holdings: int,
) -> tuple[int, str | None, str | None]:
    settings = runtime.settings
    api_budget_state = _as_mapping(runtime.api_budget_state)
    configured_quote_limit = int(
        getattr(settings, "api_soft_max_quotes_per_tick", 0) or 0
    )
    if api_budget_state:
        soft_max = int(api_budget_state.get("soft_max_quotes_per_tick", 0) or 0)
        if soft_max <= 0:
            # Unconfigured quote budget -> treat as unlimited.
            remaining_quotes = total_holdings
        else:
            # Configured budget: honor the true remaining count, even when it is
            # fully exhausted (0). Do NOT floor to total_holdings, or an exhausted
            # budget would be mistaken for an unconfigured one.
            remaining_quotes = api_budget_remaining_quotes(dict(api_budget_state))
    else:
        remaining_quotes = configured_quote_limit
        if remaining_quotes <= 0:
            remaining_quotes = total_holdings
    buy_scan_due = bool(runtime.context.get("buy_scan_due", False))
    plan = build_sell_watch_budget_plan(
        total_holdings=total_holdings,
        remaining_requests=total_holdings,
        remaining_quotes=remaining_quotes,
        request_window_size=0,
        soft_request_limit=int(getattr(settings, "api_soft_max_requests_per_second", 1) or 1),
        buy_scan_due=buy_scan_due,
        buy_scan_request_reserve=0,
        buy_scan_quote_reserve=(
            int(getattr(settings, "api_buy_scan_min_quote_reserve", 0) or 0)
            if buy_scan_due
            else 0
        ),
        execution_request_reserve=0,
        recent_partial=bool(api_budget_state.get("last_sell_watch_partial") or False),
        last_rate_limit_source=str(api_budget_state.get("last_rate_limit_source") or ""),
        rate_limit_hits=int(api_budget_state.get("rate_limit_hits", 0) or 0),
        recent_partial_streak=int(
            api_budget_state.get("consecutive_sell_watch_partial_cycles", 0) or 0
        ),
    )
    limit = int(plan.max_evaluations)
    runtime_cap = runtime.scheduler_state.get("effective_sell_watch_max_holdings_per_tick")
    if runtime_cap is not None:
        limit = min(limit, max(1, int(runtime_cap or 0)))
    return max(0, min(total_holdings, limit)), plan.pressure_level, plan.reason


def prepare_lane_scheduler_runtime_context(
    scheduler_state: MutableMapping[str, Any],
    **values: Any,
) -> None:
    runtime_context = scheduler_state.setdefault("lane_scheduler_runtime_context", {})
    if not isinstance(runtime_context, dict):
        return
    for key, value in values.items():
        runtime_context.setdefault(key, value)


def _production_runtime(context: Mapping[str, Any] | None) -> LaneProductionRuntime:
    mutable_context = _as_mutable_mapping(context)
    runtime = mutable_context.get("production_runtime")
    if isinstance(runtime, LaneProductionRuntime):
        return runtime

    settings = mutable_context.get("settings")
    if settings is None:
        raise RuntimeError("lane scheduler production context missing settings")
    scheduler_state = _as_mapping(mutable_context.get("scheduler_state"))
    api_budget_state = _as_mapping(mutable_context.get("api_budget_state"))
    runtime = LaneProductionRuntime(
        settings=settings,
        scheduler_state=scheduler_state,
        api_budget_state=api_budget_state,
        context=mutable_context,
    )
    mutable_context["production_runtime"] = runtime
    return runtime


def _sell_order_payload(
    *,
    runtime: LaneProductionRuntime,
    analysis: Any,
    cycle_id: str,
) -> dict[str, object]:
    settings = runtime.settings
    sell_sizing = calculate_sell_position_sizing(
        trigger=analysis.sell_decision.triggered_rule_name,
        holding_qty=analysis.holding_qty,
        current_price=analysis.market_snapshot.current_price,
        settings=settings,
    )
    sell_log_context = {
        "symbol": analysis.symbol,
        "qty": sell_sizing.recommended_sell_qty,
        "order_type": "market_sell",
        "confirm_buy": getattr(settings, "confirm_buy", "NO"),
        "market_open": bool(runtime.market_open),
        "cycle_id": cycle_id,
    }
    sell_raw_response = {
        "sell_strategy_details": analysis.sell_decision.to_log_payload(),
        "buy_strategy_details": analysis.buy_strategy_result.to_log_payload(),
        "sell_position_sizing": sell_sizing.details,
        "trigger": sell_sizing.sell_trigger,
        "recommended_sell_qty": sell_sizing.recommended_sell_qty,
        "sell_plan": {
            "current_price_krw": analysis.market_snapshot.current_price,
            "qty": sell_sizing.recommended_sell_qty,
            "notional_krw": sell_sizing.recommended_notional_krw,
            "estimated_sell_fee_krw": sell_sizing.details["estimated_sell_fee_krw"],
            "estimated_sell_tax_krw": sell_sizing.details["estimated_sell_tax_krw"],
            "estimated_sell_slippage_krw": sell_sizing.details[
                "estimated_sell_slippage_krw"
            ],
            "estimated_net_proceeds_krw": sell_sizing.details[
                "estimated_net_proceeds_krw"
            ],
        },
    }
    return {
        "state": runtime.runtime_state,
        "settings": settings,
        "token": runtime.token,
        "portfolio_snapshot": runtime.portfolio_snapshot,
        "analysis": analysis,
        "sell_sizing": sell_sizing,
        "sell_log_context": sell_log_context,
        "sell_raw_response": sell_raw_response,
        "market_open": bool(runtime.market_open),
        "session_status": runtime.session_status,
        "cycle_reason": sell_sizing.sell_trigger or "-",
        "cycle_action_label": "매도 주문",
        "api_budget_state": runtime.api_budget_state,
        "flow_context": {"risk_guard_payload": None},
        "print_sell_preview": runtime.context.get("print_sell_preview"),
        "send_order_slack_notification": runtime.context.get(
            "send_order_slack_notification"
        ),
        "wait_for_execution_request_budget": runtime.context.get(
            "wait_for_execution_request_budget"
        ),
    }


def build_sell_intent_from_production(
    *,
    cycle_id: str,
    context: Mapping[str, Any] | None = None,
    budget: Any | None = None,
    **_kwargs: Any,
) -> SellIntent | None:
    runtime = _production_runtime(context)
    mutable_context = runtime.context
    try:
        runtime.ensure_account_context()
    except Exception as exc:
        if looks_like_rate_limit_error(exc):
            _note_runtime_rate_limit(runtime, source="balance", exc=exc)
            mutable_context["sell_skipped_reason"] = "balance_rate_limit"
            return None
        raise
    portfolio_snapshot = runtime.portfolio_snapshot
    held_positions = tuple(getattr(portfolio_snapshot, "held_positions", ()) or ())
    total_holdings = len(held_positions)
    mutable_context["sell_watch_total_holdings"] = total_holdings
    if not held_positions:
        mutable_context["sell_skipped_reason"] = "no_holdings"
        runtime.sell_analysis_results = ()
        return None
    if not runtime.market_open:
        mutable_context["sell_skipped_reason"] = "order_window_closed"
        runtime.sell_analysis_results = ()
        return None

    ordered_positions, cursor_before = _sell_watch_ordered_positions(
        runtime=runtime,
        held_positions=held_positions,
    )
    max_evaluations, pressure_level, plan_reason = _sell_watch_evaluation_limit(
        runtime=runtime,
        total_holdings=total_holdings,
    )
    mutable_context["sell_watch_cursor_before"] = cursor_before
    mutable_context["sell_watch_budget_plan_limit"] = max_evaluations
    mutable_context["sell_watch_budget_plan_pressure_level"] = pressure_level
    mutable_context["sell_watch_budget_plan_reason"] = plan_reason
    if max_evaluations <= 0:
        mutable_context["sell_skipped_reason"] = "sell_watch_budget_limited"
        mutable_context["sell_watch_partial"] = True
        mutable_context["sell_watch_partial_reason"] = (
            plan_reason or "SELL watch quote budget exhausted"
        )
        runtime.sell_analysis_results = ()
        return None

    analyses: list[Any] = []
    skipped_symbols: list[str] = []
    evaluated_symbols: list[str] = []
    api_budget_state = _as_mutable_mapping(runtime.api_budget_state)
    enforce_quote_budget = (
        bool(api_budget_state)
        and int(api_budget_state.get("soft_max_quotes_per_tick", 0) or 0) > 0
    )
    visited_count = 0
    for position in ordered_positions:
        if visited_count >= max_evaluations:
            break
        if enforce_quote_budget and not api_budget_can_quote(api_budget_state, quote_cost=1):
            mutable_context["sell_watch_partial"] = True
            mutable_context["sell_watch_partial_reason"] = (
                plan_reason or "SELL watch quote budget exhausted"
            )
            break
        if api_budget_state:
            api_budget_register_request(
                api_budget_state,
                now=get_korean_now(),
                quote_cost=1,
            )
        visited_count += 1
        try:
            price_data = inquire_price(position.symbol, token=runtime.token)
        except ApiHttpError as exc:
            if looks_like_rate_limit_error(exc):
                runtime.runtime_state["sell_watch_retry_symbol"] = position.symbol
                _note_runtime_rate_limit(runtime, source="sell_watch", exc=exc)
                mutable_context["sell_watch_partial"] = True
                mutable_context["sell_watch_partial_reason"] = (
                    "SELL watch rate limit detected"
                )
                break
            skipped_symbols.append(position.symbol)
            continue
        except Exception as exc:
            if looks_like_rate_limit_error(exc):
                runtime.runtime_state["sell_watch_retry_symbol"] = position.symbol
                _note_runtime_rate_limit(runtime, source="sell_watch", exc=exc)
                mutable_context["sell_watch_partial"] = True
                mutable_context["sell_watch_partial_reason"] = (
                    "SELL watch rate limit detected"
                )
                break
            skipped_symbols.append(position.symbol)
            continue
        if not isinstance(price_data, Mapping) or price_data.get("rt_cd") != "0":
            if isinstance(price_data, Mapping) and is_rate_limit_response(dict(price_data)):
                runtime.runtime_state["sell_watch_retry_symbol"] = position.symbol
                _note_runtime_rate_limit(runtime, source="sell_watch")
                mutable_context["sell_watch_partial"] = True
                mutable_context["sell_watch_partial_reason"] = (
                    "SELL watch rate limit detected"
                )
                break
            skipped_symbols.append(position.symbol)
            continue
        market_snapshot = build_market_snapshot(_as_mapping(price_data.get("output")))
        evaluated_symbols.append(position.symbol)
        analyses.append(
            build_sell_analysis(
                symbol=position.symbol,
                holding_qty=position.holding_qty,
                average_cost=position.average_cost,
                market_snapshot=market_snapshot,
                portfolio_snapshot=portfolio_snapshot,
                settings=runtime.settings,
            )
        )

    runtime.sell_analysis_results = tuple(analyses)
    mutable_context["sell_analysis_results"] = runtime.sell_analysis_results
    mutable_context["sell_evaluated_count"] = len(runtime.sell_analysis_results)
    mutable_context["sell_watch_skipped_symbols"] = tuple(skipped_symbols)
    skipped_tail = tuple(
        str(getattr(position, "symbol", ""))
        for position in ordered_positions[visited_count:]
        if str(getattr(position, "symbol", ""))
    )
    if skipped_tail:
        mutable_context["sell_watch_partial"] = True
        mutable_context["sell_watch_partial_reason"] = (
            mutable_context.get("sell_watch_partial_reason")
            or plan_reason
            or "SELL watch partial budget protection"
        )
    mutable_context["sell_watch_evaluated_symbols"] = tuple(evaluated_symbols)
    mutable_context["sell_watch_skipped_symbols"] = tuple(skipped_symbols) + skipped_tail
    runtime.runtime_state["sell_watch_next_start_index"] = (
        (cursor_before + visited_count) % total_holdings
        if total_holdings > 0 and visited_count > 0
        else cursor_before
    )
    selected = select_top_sell_candidate(runtime.sell_analysis_results)
    if selected is None:
        mutable_context["sell_skipped_reason"] = "no_sell_candidate"
        return None

    payload = _sell_order_payload(
        runtime=runtime,
        analysis=selected,
        cycle_id=cycle_id,
    )
    created_at = get_korean_now()
    ttl_seconds = 5.0
    if budget is not None and callable(getattr(budget, "remaining_seconds", None)):
        ttl_seconds = max(1.0, float(budget.remaining_seconds()))
    return SellIntent(
        intent_id=f"{cycle_id}:sell:{selected.symbol}",
        source_cycle_id=cycle_id,
        source_lane="sell_watch",
        symbol=selected.symbol,
        reason=str(selected.sell_decision.triggered_rule_name or "sell"),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=ttl_seconds),
        payload=payload,
    )


def build_buy_intent_from_production(
    *,
    cycle_id: str,
    context: Mapping[str, Any] | None = None,
    prefetch_result: Any | None = None,
    budget: Any | None = None,
    **_kwargs: Any,
) -> BuyIntent | None:
    runtime = _production_runtime(context)
    mutable_context = runtime.context
    try:
        runtime.ensure_account_context()
    except Exception as exc:
        if looks_like_rate_limit_error(exc):
            _note_runtime_rate_limit(runtime, source="balance", exc=exc)
            mutable_context["buy_skipped_reason"] = "balance_rate_limit"
            return None
        raise
    if prefetch_result is None:
        mutable_context["buy_skipped_reason"] = "quote_prefetch_missing"
        return None

    price_data_by_symbol = dict(getattr(prefetch_result, "price_data_by_symbol", {}) or {})
    if not price_data_by_symbol:
        mutable_context["buy_skipped_reason"] = "quote_prefetch_missing"
        return None

    symbols = tuple(price_data_by_symbol)
    raw_scan_results = scan_target_symbols(
        settings=runtime.settings,
        token=runtime.token or "",
        portfolio_snapshot=runtime.portfolio_snapshot,
        symbols=symbols,
        price_data_by_symbol=price_data_by_symbol,
        allow_inline_quote_fetch=False,
    )
    scan_results = apply_buy_runtime_guards_to_scan_results(
        raw_scan_results,
        state=runtime.runtime_state,
        settings=runtime.effective_buy_settings,
        portfolio_snapshot=runtime.portfolio_snapshot,
        regime_state=runtime.regime_state,
        daily_pnl_brake_state=runtime.daily_pnl_brake_state,
    )
    selected = select_top_candidate(
        scan_results,
        min_price_krw=resolve_mock_buy_price_floor_krw(runtime.settings),
    )
    selection_details = serialize_selection_details(
        selected_result=selected,
        results=scan_results,
    )
    selection_details["requested_universe_count"] = len(symbols)
    selection_details["evaluated_count"] = len(scan_results)
    selection_details["top_candidate_limit"] = getattr(
        runtime.settings,
        "buy_scan_top_k_candidates",
        None,
    )
    runtime.scan_results = tuple(scan_results)
    runtime.selected_buy_candidate = selected
    runtime.selection_details = selection_details
    # P1 Stage 2 observability parity: persist the scan telemetry the lane
    # genuinely computed into the durable scheduler runtime context so the main
    # bridge can regenerate finalize's candidate_outcomes (C5) and BUY funnel
    # cycle_stats. NOTE (C4): selection_details is deliberately NOT persisted here
    # — it carries per-symbol raw scan payloads (feature_map/feature_vector/…)
    # that build_cycle_snapshot embeds untruncated. requested_symbols == the flat
    # scanned universe, so ALL scanned symbols are "layer-selected" (C5: no
    # fabricated universe_layered_out). Telemetry only — no order-path effect.
    runtime.store_buy_scan_outcome_for_bridge(
        requested_symbols=symbols,
        raw_scan_results=tuple(raw_scan_results),
        scan_results=runtime.scan_results,
        selected_candidate=selected,
    )
    mutable_context["scan_results"] = runtime.scan_results
    mutable_context["selected_candidate"] = selected
    mutable_context["selection_details"] = selection_details
    mutable_context["buy_scan_evaluated_count"] = len(scan_results)
    if selected is None:
        mutable_context["buy_skipped_reason"] = "no_buy_candidate"
        return None

    created_at = get_korean_now()
    ttl_seconds = 5.0
    if budget is not None and callable(getattr(budget, "remaining_seconds", None)):
        ttl_seconds = max(1.0, float(budget.remaining_seconds()))
    ttl_seconds = min(
        ttl_seconds,
        float(getattr(runtime.settings, "buy_scan_total_budget_seconds", 25.0) or 25.0),
    )
    payload = {
        "state": runtime.runtime_state,
        "settings": runtime.settings,
        "effective_buy_settings": runtime.effective_buy_settings,
        "token": runtime.token,
        "portfolio_snapshot": runtime.portfolio_snapshot,
        "selected_candidate": selected,
        "scan_results": runtime.scan_results,
        "sell_analysis_results": runtime.sell_analysis_results,
        "selection_details": selection_details,
        "regime_state": runtime.regime_state,
        "daily_pnl_brake_state": runtime.daily_pnl_brake_state,
        "market_open": bool(runtime.market_open),
        "session_status": runtime.session_status,
        "order_type": "market_buy",
        "cycle_id": cycle_id,
        "sell_check_due": bool(mutable_context.get("sell_check_due", False)),
        "sell_watch_partial": bool(mutable_context.get("sell_watch_partial", False)),
        "sell_watch_partial_reason": mutable_context.get("sell_watch_partial_reason"),
        "api_budget_state": runtime.api_budget_state,
        "timing_summary": runtime.timing_summary,
        "rate_limit_source": mutable_context.get("rate_limit_source"),
        "flow_context": {},
        "send_order_slack_notification": mutable_context.get(
            "send_order_slack_notification"
        ),
        "wait_for_execution_request_budget": mutable_context.get(
            "wait_for_execution_request_budget"
        ),
    }
    quote_age_ms = getattr(prefetch_result, "elapsed_ms", None)
    return BuyIntent(
        intent_id=f"{cycle_id}:buy:{selected.symbol}",
        source_cycle_id=cycle_id,
        source_lane="buy_scan",
        symbol=selected.symbol,
        reason=str(selection_details.get("selection_reason") or "buy candidate"),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=ttl_seconds),
        payload=payload,
        quote_age_ms=None if quote_age_ms is None else float(quote_age_ms),
        candidate_score=float(getattr(selected, "score", 0.0) or 0.0),
    )


def default_sell_order_handler(intent: OrderIntent) -> Any:
    payload = _as_mapping(intent.payload)
    return _sell_flow.run_sell_order_flow(
        state=payload["state"],
        settings=payload["settings"],
        token=str(payload.get("token") or ""),
        portfolio_snapshot=payload["portfolio_snapshot"],
        analysis=payload["analysis"],
        sell_sizing=payload["sell_sizing"],
        sell_log_context=dict(_as_mapping(payload.get("sell_log_context"))),
        sell_raw_response=dict(_as_mapping(payload.get("sell_raw_response"))),
        market_open=bool(payload.get("market_open")),
        session_status=payload["session_status"],
        cycle_reason=str(payload.get("cycle_reason") or "-"),
        cycle_action_label=str(payload.get("cycle_action_label") or "매도 주문"),
        is_rebalance=bool(payload.get("is_rebalance", False)),
        flow_context=payload.get("flow_context"),
        api_budget_state=payload.get("api_budget_state"),
        print_sell_preview=payload.get("print_sell_preview"),
        send_order_slack_notification=payload.get("send_order_slack_notification"),
        wait_for_execution_request_budget=payload.get(
            "wait_for_execution_request_budget"
        ),
    )


def _default_sell_order_flow_from_kwargs(**kwargs: Any) -> Any:
    payload = {
        **kwargs,
        "print_sell_preview": kwargs.get("print_sell_preview"),
        "send_order_slack_notification": kwargs.get("send_order_slack_notification"),
        "wait_for_execution_request_budget": kwargs.get(
            "wait_for_execution_request_budget"
        ),
    }
    intent = SellIntent(
        intent_id=f"{kwargs.get('cycle_id', 'lane')}:sell:{getattr(kwargs.get('analysis'), 'symbol', '-')}",
        source_cycle_id=str(kwargs.get("cycle_id") or "lane"),
        source_lane="sell_watch",
        symbol=str(getattr(kwargs.get("analysis"), "symbol", "-")),
        reason=str(kwargs.get("cycle_reason") or "sell"),
        created_at=get_korean_now(),
        payload=payload,
    )
    return default_sell_order_handler(intent)


def default_buy_order_handler(intent: OrderIntent) -> Any:
    payload = _as_mapping(intent.payload)
    api_budget_state = payload.get("api_budget_state")
    if not isinstance(api_budget_state, dict):
        api_budget_state = {}
    timing_summary = payload.get("timing_summary")
    if not isinstance(timing_summary, dict):
        timing_summary = {}
    flow_context = payload.get("flow_context")
    if not isinstance(flow_context, dict):
        flow_context = {}
    return _buy_flow.run_buy_order_flow(
        state=payload["state"],
        settings=payload["settings"],
        effective_buy_settings=payload["effective_buy_settings"],
        token=str(payload.get("token") or ""),
        portfolio_snapshot=payload["portfolio_snapshot"],
        selected_candidate=payload["selected_candidate"],
        scan_results=payload["scan_results"],
        sell_analysis_results=payload["sell_analysis_results"],
        selection_details=payload["selection_details"],
        regime_state=payload.get("regime_state"),
        daily_pnl_brake_state=payload.get("daily_pnl_brake_state"),
        market_open=bool(payload.get("market_open")),
        session_status=payload["session_status"],
        order_type=str(payload.get("order_type") or "market_buy"),
        cycle_id=str(payload.get("cycle_id") or intent.source_cycle_id),
        sell_check_due=bool(payload.get("sell_check_due")),
        sell_watch_partial=bool(payload.get("sell_watch_partial")),
        sell_watch_partial_reason=payload.get("sell_watch_partial_reason"),
        api_budget_state=api_budget_state,
        timing_summary=timing_summary,
        rate_limit_source=payload.get("rate_limit_source"),
        run_sell_order_flow=_default_sell_order_flow_from_kwargs,
        send_order_slack_notification=payload.get("send_order_slack_notification"),
        wait_for_execution_request_budget=payload.get(
            "wait_for_execution_request_budget"
        ),
        flow_context=flow_context,
    )


def build_sell_intent_from_context(
    *,
    cycle_id: str,
    context: Mapping[str, Any] | None = None,
    **_kwargs: Any,
) -> SellIntent | None:
    payload = _context_payload(
        _as_mapping(context),
        "sell_intent_payload",
        "sell_order_payload",
    )
    if not payload:
        return None
    symbol = _payload_text(payload, "symbol")
    if not symbol:
        analysis = payload.get("analysis")
        symbol = str(getattr(analysis, "symbol", "") or "")
    if not symbol:
        return None
    now_func = payload.get("now_func")
    created_at = now_func() if callable(now_func) else get_korean_now()
    return SellIntent(
        intent_id=_payload_text(payload, "intent_id", fallback=f"{cycle_id}:sell:{symbol}"),
        source_cycle_id=cycle_id,
        source_lane=_payload_text(payload, "source_lane", fallback="sell_watch"),
        symbol=symbol,
        reason=_payload_text(payload, "reason", fallback="sell"),
        created_at=created_at,
        payload=payload,
    )


def build_buy_intent_from_context(
    *,
    cycle_id: str,
    context: Mapping[str, Any] | None = None,
    prefetch_result: Any | None = None,
    **_kwargs: Any,
) -> BuyIntent | None:
    payload = _context_payload(
        _as_mapping(context),
        "buy_intent_payload",
        "buy_order_payload",
    )
    if not payload:
        return None
    symbol = _payload_text(payload, "symbol")
    if not symbol:
        candidate = payload.get("selected_candidate")
        symbol = str(getattr(candidate, "symbol", "") or "")
    if not symbol:
        return None
    now_func = payload.get("now_func")
    created_at = now_func() if callable(now_func) else get_korean_now()
    ttl_seconds = float(payload.get("ttl_seconds", 5.0) or 5.0)
    quote_age_ms = payload.get("quote_age_ms")
    if quote_age_ms is None and prefetch_result is not None:
        quote_age_ms = getattr(prefetch_result, "elapsed_ms", None)
    candidate_score = payload.get("candidate_score")
    candidate = payload.get("selected_candidate")
    if candidate_score is None and candidate is not None:
        candidate_score = getattr(candidate, "score", None)
    return BuyIntent(
        intent_id=_payload_text(payload, "intent_id", fallback=f"{cycle_id}:buy:{symbol}"),
        source_cycle_id=cycle_id,
        source_lane=_payload_text(payload, "source_lane", fallback="buy_scan"),
        symbol=symbol,
        reason=_payload_text(payload, "reason", fallback="buy candidate"),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=max(1.0, ttl_seconds)),
        payload=payload,
        quote_age_ms=None if quote_age_ms is None else float(quote_age_ms),
        candidate_score=(
            None if candidate_score is None else float(candidate_score)
        ),
    )


def delegate_order_handler_from_payload(intent: OrderIntent) -> Any:
    payload = _as_mapping(intent.payload)
    handler = (
        payload.get("order_handler")
        or payload.get("handler")
        or payload.get(f"{intent.intent_type.lower()}_order_handler")
    )
    if not callable(handler):
        raise RuntimeError("missing_payload")
    kwargs = payload.get("order_kwargs")
    if isinstance(kwargs, Mapping):
        return handler(**dict(kwargs))
    return handler(intent)


def _adapters_from_mapping(
    payload: Mapping[str, Any],
) -> LaneSchedulerRuntimeAdapters:
    return LaneSchedulerRuntimeAdapters(
        buy_symbols=tuple(payload.get("buy_symbols") or ()),
        execution_token=str(payload.get("execution_token") or ""),
        context=_as_mapping(payload.get("context")),
        quote_prefetch_func=payload.get("quote_prefetch_func")
        or payload.get("prefetch_func"),
        sell_intent_factory=payload.get("sell_intent_factory"),
        buy_intent_factory=payload.get("buy_intent_factory"),
        sell_order_handler=payload.get("sell_order_handler"),
        buy_order_handler=payload.get("buy_order_handler"),
        buy_controller=payload.get("buy_controller"),
        clock=payload.get("clock"),
        now_func=payload.get("now_func"),
        order_gate_handler_timeout_enabled=bool(
            payload.get("order_gate_handler_timeout_enabled", True)
        ),
    )


def build_default_lane_runtime_adapters(
    settings: Any,
    *,
    scheduler_state: Mapping[str, Any] | None = None,
    api_budget_state: Mapping[str, Any] | None = None,
) -> LaneSchedulerRuntimeAdapters:
    """Build the production default scheduler adapters.

    Tests may monkeypatch this builder, but the scheduler does not require
    ``lane_scheduler_hooks`` for its default enabled path.
    """

    state = _as_mapping(scheduler_state)
    configured = (
        state.get("lane_scheduler_runtime_adapters")
        or state.get("lane_scheduler_adapters")
    )
    if isinstance(configured, LaneSchedulerRuntimeAdapters):
        return configured
    if isinstance(configured, Mapping):
        return _adapters_from_mapping(configured)

    runtime_context = _as_mapping(state.get("lane_scheduler_runtime_context"))
    context = {
        "settings": settings,
        "scheduler_state": state,
        "api_budget_state": api_budget_state,
        **dict(runtime_context),
    }
    return LaneSchedulerRuntimeAdapters(
        buy_symbols=tuple(getattr(settings, "target_symbols", ()) or ()),
        execution_token=str(state.get("execution_token") or ""),
        context=context,
        sell_intent_factory=build_sell_intent_from_production,
        buy_intent_factory=build_buy_intent_from_production,
        sell_order_handler=default_sell_order_handler,
        buy_order_handler=default_buy_order_handler,
        order_gate_handler_timeout_enabled=True,
    )


def apply_lane_scheduler_state_overrides(
    state: dict[str, Any],
    telemetry: Mapping[str, Any],
) -> None:
    """Mirror scheduler telemetry into runtime_state compatibility keys."""

    state["last_buy_scan_requested_count"] = int(
        telemetry.get("buy_scan_requested_count", 0) or 0
    )
    state["last_buy_scan_evaluated_count"] = int(
        telemetry.get("buy_scan_evaluated_count", 0) or 0
    )
    state["last_sell_evaluated_count"] = int(
        telemetry.get("sell_evaluated_count", 0) or 0
    )
    state["last_buy_scan_skipped_reason"] = telemetry.get("buy_scan_skipped_reason")
    state["last_buy_quote_prefetch_request_count"] = int(
        telemetry.get("buy_quote_prefetch_request_count", 0) or 0
    )
    state["last_buy_quote_prefetch_deadline_hit"] = bool(
        telemetry.get("buy_quote_prefetch_deadline_hit", False)
    )
    state["last_buy_quote_prefetch_success_ratio"] = round(
        float(telemetry.get("buy_quote_prefetch_success_ratio", 0.0) or 0.0),
        4,
    )
    state["buy_quote_prefetch_request_timeout_seconds"] = round(
        float(telemetry.get("buy_quote_prefetch_request_timeout_seconds", 0.0) or 0.0),
        3,
    )
    state["buy_quote_prefetch_max_attempts"] = int(
        telemetry.get("buy_quote_prefetch_max_attempts", 0) or 0
    )
    state["buy_quote_prefetch_timeout_count"] = int(
        telemetry.get("buy_quote_prefetch_timeout_count", 0) or 0
    )
    state["buy_quote_prefetch_budget_skipped"] = int(
        telemetry.get("buy_quote_prefetch_budget_skipped", 0) or 0
    )
    state["buy_quote_prefetch_worker_detached"] = bool(
        telemetry.get("buy_quote_prefetch_worker_detached", False)
    )
    state["buy_quote_prefetch_cleanup_nonblocking"] = bool(
        telemetry.get("buy_quote_prefetch_cleanup_nonblocking", False)
    )
    state["buy_quote_prefetch_future_done"] = bool(
        telemetry.get("buy_quote_prefetch_future_done", True)
    )
    state["buy_scan_guard_released"] = bool(
        telemetry.get("buy_scan_guard_released", False)
    )
    state["buy_scan_guard_release_reason"] = telemetry.get(
        "buy_scan_guard_release_reason"
    )
    state["lane_scheduler_enabled"] = bool(
        telemetry.get("lane_scheduler_enabled", False)
    )
    state["sell_lane_running"] = bool(telemetry.get("sell_lane_running", False))
    state["buy_lane_running"] = bool(telemetry.get("buy_lane_running", False))
    state["buy_lane_previous_scan_id"] = telemetry.get("buy_lane_previous_scan_id")
    state["order_gate_queue_depth"] = int(
        telemetry.get("order_gate_queue_depth", 0) or 0
    )
    state["order_gate_processed_count"] = int(
        telemetry.get("order_gate_processed_count", 0) or 0
    )
    state["order_gate_last_decision"] = telemetry.get("order_gate_last_decision")
    state["order_gate_last_intent_type"] = telemetry.get(
        "order_gate_last_intent_type"
    )
    state["order_gate_last_symbol"] = telemetry.get("order_gate_last_symbol")
    state["order_gate_last_skip_reason"] = telemetry.get(
        "order_gate_last_skip_reason"
    )
    state["quote_age_max_ms"] = telemetry.get("quote_age_max_ms")
    state["quote_age_avg_ms"] = telemetry.get("quote_age_avg_ms")
    state["rate_limit_triggered"] = bool(telemetry.get("rate_limit_triggered", False))
    state["rate_limit_source"] = telemetry.get("rate_limit_source")
    state["cycle_budget_exceeded"] = bool(
        telemetry.get("cycle_budget_exceeded", False)
    )
    state["cycle_budget_ms"] = round(
        float(telemetry.get("cycle_budget_seconds", 0.0) or 0.0) * 1000.0,
        1,
    )
    state["budget_exceeded_stage"] = telemetry.get("budget_exceeded_stage")
