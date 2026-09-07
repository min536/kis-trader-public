from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.pipeline.runtime_adapters import (
    apply_lane_scheduler_state_overrides,
    prepare_lane_scheduler_runtime_context,
)
from app.pipeline.lane_scheduler import run_lane_scheduler_cycle


@dataclass(frozen=True)
class LaneSchedulerMainBridgeResult:
    final_overrides: dict[str, object]
    sell_watch_skipped_reason: str | None
    buy_scan_skipped_reason: str | None
    sell_status_text: str
    buy_status_text: str
    # P1 observability parity: the lane's own session object (identity-mapped
    # from scheduler_state['lane_scheduler_runtime_context']['session_status']),
    # or None when the lane never resolved a session this cycle.
    session_status: Any = None
    # Account snapshot already fetched by the lane production runtime. Mapping
    # it by identity lets the common finalize path persist performance without
    # issuing another balance request.
    portfolio_snapshot: Any = None
    # P1 Stage 2: the lane's BUY-scan outcome telemetry
    # (requested_symbols/raw_scan_results/scan_results/selected_candidate), or
    # None when the lane produced no scan this cycle. Deliberately excludes the
    # bulky selection_details (C4).
    buy_scan_outcome: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LaneSchedulerRuntimeTelemetry:
    enabled: bool
    sell_lane_running: bool
    buy_lane_running: bool
    buy_lane_previous_scan_id: str | None
    buy_lane_previous_started_at: Any
    sell_watch_skipped_reason: str | None
    order_gate_queue_depth: int
    order_gate_processed_count: int
    order_gate_last_decision: str | None
    order_gate_last_intent_type: str | None
    order_gate_last_symbol: str | None
    order_gate_last_skip_reason: str | None
    quote_age_max_ms: float | None
    quote_age_avg_ms: float | None
    suspected_shared_rate_bucket: bool
    cycle_budget_exceeded: bool
    cycle_budget_ms: float
    budget_exceeded_stage: str | None

    def timing_fields(self) -> dict[str, object]:
        return {
            "lane_scheduler_enabled": bool(self.enabled),
            "sell_lane_running": bool(self.sell_lane_running),
            "buy_lane_running": bool(self.buy_lane_running),
            "buy_lane_previous_scan_id": self.buy_lane_previous_scan_id,
            "buy_lane_previous_started_at": (
                self.buy_lane_previous_started_at.isoformat()
                if isinstance(self.buy_lane_previous_started_at, datetime)
                else self.buy_lane_previous_started_at
            ),
            "sell_watch_skipped_reason": self.sell_watch_skipped_reason,
            "order_gate_queue_depth": int(self.order_gate_queue_depth),
            "order_gate_processed_count": int(self.order_gate_processed_count),
            "order_gate_last_decision": self.order_gate_last_decision,
            "order_gate_last_intent_type": self.order_gate_last_intent_type,
            "order_gate_last_symbol": self.order_gate_last_symbol,
            "order_gate_last_skip_reason": self.order_gate_last_skip_reason,
            "quote_age_max_ms": (
                None
                if self.quote_age_max_ms is None
                else round(float(self.quote_age_max_ms), 1)
            ),
            "quote_age_avg_ms": (
                None
                if self.quote_age_avg_ms is None
                else round(float(self.quote_age_avg_ms), 1)
            ),
            "suspected_shared_rate_bucket": bool(self.suspected_shared_rate_bucket),
            "cycle_budget_exceeded": bool(self.cycle_budget_exceeded),
            "cycle_budget_ms": round(float(self.cycle_budget_ms), 1),
            "budget_exceeded_stage": self.budget_exceeded_stage,
        }

    def state_fields(self, *, timing_summary: Mapping[str, object]) -> dict[str, object]:
        return {
            "lane_scheduler_enabled": bool(self.enabled),
            "sell_lane_running": bool(self.sell_lane_running),
            "buy_lane_running": bool(self.buy_lane_running),
            "buy_lane_previous_scan_id": self.buy_lane_previous_scan_id,
            "buy_lane_previous_started_at": timing_summary.get(
                "buy_lane_previous_started_at"
            ),
            "order_gate_queue_depth": int(self.order_gate_queue_depth),
            "order_gate_processed_count": int(self.order_gate_processed_count),
            "order_gate_last_decision": self.order_gate_last_decision,
            "order_gate_last_intent_type": self.order_gate_last_intent_type,
            "order_gate_last_symbol": self.order_gate_last_symbol,
            "order_gate_last_skip_reason": self.order_gate_last_skip_reason,
            "quote_age_max_ms": timing_summary.get("quote_age_max_ms"),
            "quote_age_avg_ms": timing_summary.get("quote_age_avg_ms"),
            "suspected_shared_rate_bucket": bool(self.suspected_shared_rate_bucket),
            "cycle_budget_exceeded": bool(self.cycle_budget_exceeded),
            "cycle_budget_ms": round(float(self.cycle_budget_ms), 1),
            "budget_exceeded_stage": self.budget_exceeded_stage,
        }


def _status_text(*, processed: bool, skipped_reason: str | None, fallback: str) -> str:
    if processed:
        return "processed"
    return f"skipped({skipped_reason or fallback})"


def run_lane_scheduler_main_bridge(
    settings: Any,
    *,
    cycle_id: str,
    sell_check_due: bool,
    buy_scan_due: bool,
    scheduler_state: dict[str, object],
    api_budget_state: dict[str, object],
    state: dict[str, object],
    timing_summary: dict[str, object],
    send_order_slack_notification: Callable[..., Any] | None,
    wait_for_execution_request_budget: Callable[..., Any] | None,
) -> LaneSchedulerMainBridgeResult:
    scheduler_state.setdefault("cycle_id", cycle_id)
    prepare_lane_scheduler_runtime_context(
        scheduler_state,
        state=state,
        timing_summary=timing_summary,
        send_order_slack_notification=send_order_slack_notification,
        wait_for_execution_request_budget=wait_for_execution_request_budget,
    )
    lane_result = run_lane_scheduler_cycle(
        settings,
        sell_check_due=sell_check_due,
        buy_scan_due=buy_scan_due,
        scheduler_state=scheduler_state,
        api_budget_state=api_budget_state,
    )
    final_overrides = dict(lane_result.telemetry)
    timing_summary.update(final_overrides)
    scheduler_state["lane_scheduler_entered"] = True
    sell_watch_skipped_reason = lane_result.sell_result.skipped_reason
    buy_scan_skipped_reason = lane_result.buy_result.skipped_reason
    # P1/C2: read the exact session object the lane stored during its own
    # account-context setup. No recompute here — if the lane never resolved a
    # session (e.g. both lanes not due), this stays None and ctx.session_status
    # stays None.
    runtime_context = scheduler_state.get("lane_scheduler_runtime_context")
    session_status = (
        runtime_context.get("session_status")
        if isinstance(runtime_context, Mapping)
        else None
    )
    portfolio_snapshot = (
        runtime_context.get("portfolio_snapshot")
        if isinstance(runtime_context, Mapping)
        else None
    )
    buy_scan_outcome = (
        runtime_context.get("buy_scan_outcome")
        if isinstance(runtime_context, Mapping)
        else None
    )
    if not isinstance(buy_scan_outcome, Mapping):
        buy_scan_outcome = None
    return LaneSchedulerMainBridgeResult(
        final_overrides=final_overrides,
        sell_watch_skipped_reason=sell_watch_skipped_reason,
        buy_scan_skipped_reason=buy_scan_skipped_reason,
        sell_status_text=_status_text(
            processed=lane_result.sell_result.intent is not None,
            skipped_reason=sell_watch_skipped_reason,
            fallback="no_sell_intent",
        ),
        buy_status_text=_status_text(
            processed=lane_result.buy_result.intent is not None,
            skipped_reason=buy_scan_skipped_reason,
            fallback="no_buy_intent",
        ),
        session_status=session_status,
        portfolio_snapshot=portfolio_snapshot,
        buy_scan_outcome=buy_scan_outcome,
    )


def apply_lane_buy_scan_outcome_to_context(
    ctx: Any,
    buy_scan_outcome: Mapping[str, Any] | None,
) -> None:
    """Map the lane's BUY-scan outcome telemetry onto the run_cycle context.

    P1 Stage 2 (telemetry only). Populates ONLY the finalize builder inputs the
    lane genuinely computed:

    * ``ctx.requested_buy_symbols`` — the flat scanned universe;
    * ``ctx.raw_scan_results`` / ``ctx.scan_results`` — the pre-/post-guard scan
      result objects (embedded truncated in the snapshot, safe);
    * ``ctx.selected_candidate`` — the chosen buy candidate;
    * ``ctx.buy_scan_layered_universe['selected_symbols']`` — set to the scanned
      symbols so the candidate-outcome builder does NOT fabricate a
      ``universe_layered_out`` stage for symbols the lane actually scanned (C5).

    Deliberately does NOT touch ``ctx.selection_details`` — the lane's
    selection_details carries per-symbol raw payloads that the cycle_snapshot
    embeds untruncated (C4). ``pre_gating`` / ``shallow_plan`` are left at their
    empty defaults because the lane runs a flat scan with no such stages.
    """

    if not isinstance(buy_scan_outcome, Mapping):
        return
    requested_symbols = tuple(buy_scan_outcome.get("requested_symbols") or ())
    ctx.requested_buy_symbols = requested_symbols
    ctx.raw_scan_results = tuple(buy_scan_outcome.get("raw_scan_results") or ())
    ctx.scan_results = tuple(buy_scan_outcome.get("scan_results") or ())
    ctx.selected_candidate = buy_scan_outcome.get("selected_candidate")
    # Flat universe: every scanned symbol is "layer-selected" (C5). Mirror the
    # existing dict shape so downstream layer_by_symbol lookups stay total.
    layered = dict(getattr(ctx, "buy_scan_layered_universe", None) or {})
    layered["selected_symbols"] = requested_symbols
    ctx.buy_scan_layered_universe = layered


def merge_lane_scheduler_timing_summary(
    timing_summary: dict[str, object],
    telemetry: LaneSchedulerRuntimeTelemetry,
    *,
    final_overrides: Mapping[str, object] | None = None,
) -> None:
    timing_summary.update(telemetry.timing_fields())
    if final_overrides:
        timing_summary.update(final_overrides)


def merge_lane_scheduler_runtime_state(
    state: dict[str, Any],
    *,
    timing_summary: Mapping[str, object],
    telemetry: LaneSchedulerRuntimeTelemetry,
    final_overrides: Mapping[str, object] | None = None,
) -> None:
    state.update(telemetry.state_fields(timing_summary=timing_summary))
    if final_overrides:
        apply_lane_scheduler_state_overrides(state, final_overrides)
