"""Read-only lane-pipeline observation over cycle-snapshot telemetry (BP-2).

A reporting **leaf**: it only READS snapshot records and summarizes the lane
telemetry that the pipeline already writes (see
docs/lane_observation_plan_20260704.md §S0 for the real, verified schema). It
must never be imported by a runtime module, and it never mutates anything.

Design constraints grounded in real snapshot data:
- Only flag-ON snapshots (``lane_scheduler_enabled`` truthy) carry lane fields;
  legacy (flag-OFF) sessions have none. The digest classifies and separates them
  rather than silently treating flag-off cycles as "no problems".
- ``blocked_detached`` / ``expired`` are NOT discrete count fields — they are
  DERIVED from ``order_gate_last_decision``, which captures only the last
  decision per cycle. That derivation is surfaced as an honesty label in the
  output, never hidden.
- The reader NEVER raises: hostile input degrades to safe defaults.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

BLOCKED_DETACHED_DECISION = "blocked_detached_handler"
EXPIRED_DECISION = "expired"


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_finite_float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isinf(parsed) or math.isnan(parsed):
        return None
    return parsed


@dataclass(frozen=True)
class LaneObservation:
    """One cycle's lane telemetry, parsed defensively from a snapshot record."""

    flag_on: bool
    order_gate_last_decision: str | None
    order_gate_last_skip_reason: str | None
    order_gate_queue_depth: int
    order_gate_processed_count: int
    quote_age_max_ms: float | None
    quote_age_avg_ms: float | None
    cycle_budget_exceeded: bool
    budget_exceeded_stage: str | None
    buy_scan_exception: str | None
    sell_lane_running: bool
    buy_lane_running: bool

    @property
    def overlap_observed(self) -> bool:
        return bool(self.sell_lane_running and self.buy_lane_running)

    @property
    def blocked_detached(self) -> bool:
        return self.order_gate_last_decision == BLOCKED_DETACHED_DECISION

    @property
    def expired(self) -> bool:
        return self.order_gate_last_decision == EXPIRED_DECISION


def read_lane_observation(record: object) -> LaneObservation:
    """Parse one snapshot record into a LaneObservation. Never raises.

    Non-Mapping input, missing keys, wrong types, and inf/nan all degrade to
    safe defaults (a flag-off observation for non-Mappings).
    """
    if not isinstance(record, Mapping):
        record = {}
    return LaneObservation(
        flag_on=bool(record.get("lane_scheduler_enabled")),
        order_gate_last_decision=_as_str_or_none(record.get("order_gate_last_decision")),
        order_gate_last_skip_reason=_as_str_or_none(record.get("order_gate_last_skip_reason")),
        order_gate_queue_depth=_as_int(record.get("order_gate_queue_depth")),
        order_gate_processed_count=_as_int(record.get("order_gate_processed_count")),
        quote_age_max_ms=_as_finite_float_or_none(record.get("quote_age_max_ms")),
        quote_age_avg_ms=_as_finite_float_or_none(record.get("quote_age_avg_ms")),
        cycle_budget_exceeded=bool(record.get("cycle_budget_exceeded")),
        budget_exceeded_stage=_as_str_or_none(record.get("budget_exceeded_stage")),
        buy_scan_exception=_as_str_or_none(record.get("buy_scan_exception")),
        sell_lane_running=bool(record.get("sell_lane_running")),
        buy_lane_running=bool(record.get("buy_lane_running")),
    )


@dataclass(frozen=True)
class LaneSessionDigest:
    """Session-level aggregation. Lane metrics are computed over flag-ON cycles
    only; flag-OFF cycles are counted separately (they carry no lane data)."""

    total_cycles: int
    flag_on_cycles: int
    flag_off_cycles: int
    decision_counts: dict[str, int] = field(default_factory=dict)
    blocked_detached_cycles: int = 0
    expired_cycles: int = 0
    buy_scan_exception_cycles: int = 0
    budget_exceeded_cycles: int = 0
    overlap_cycles: int = 0
    max_queue_depth: int = 0
    total_processed: int = 0
    quote_age_max_ms: float | None = None
    quote_age_avg_ms: float | None = None


def aggregate_lane_observations(records: object) -> LaneSessionDigest:
    """Aggregate an iterable of snapshot records into a LaneSessionDigest.

    Never raises: each record is parsed via the never-raise reader.
    """
    try:
        record_list = list(records)  # type: ignore[arg-type]
    except TypeError:
        record_list = []

    total = len(record_list)
    flag_on = 0
    flag_off = 0
    decision_counts: dict[str, int] = {}
    blocked_detached = 0
    expired = 0
    exceptions = 0
    budget_exceeded = 0
    overlap = 0
    max_queue = 0
    total_processed = 0
    quote_maxes: list[float] = []
    quote_avgs: list[float] = []

    for record in record_list:
        obs = read_lane_observation(record)
        if not obs.flag_on:
            flag_off += 1
            continue
        flag_on += 1
        if obs.order_gate_last_decision is not None:
            decision_counts[obs.order_gate_last_decision] = (
                decision_counts.get(obs.order_gate_last_decision, 0) + 1
            )
        if obs.blocked_detached:
            blocked_detached += 1
        if obs.expired:
            expired += 1
        if obs.buy_scan_exception is not None:
            exceptions += 1
        if obs.cycle_budget_exceeded:
            budget_exceeded += 1
        if obs.overlap_observed:
            overlap += 1
        if obs.order_gate_queue_depth > max_queue:
            max_queue = obs.order_gate_queue_depth
        total_processed += obs.order_gate_processed_count
        if obs.quote_age_max_ms is not None:
            quote_maxes.append(obs.quote_age_max_ms)
        if obs.quote_age_avg_ms is not None:
            quote_avgs.append(obs.quote_age_avg_ms)

    return LaneSessionDigest(
        total_cycles=total,
        flag_on_cycles=flag_on,
        flag_off_cycles=flag_off,
        decision_counts=decision_counts,
        blocked_detached_cycles=blocked_detached,
        expired_cycles=expired,
        buy_scan_exception_cycles=exceptions,
        budget_exceeded_cycles=budget_exceeded,
        overlap_cycles=overlap,
        max_queue_depth=max_queue,
        total_processed=total_processed,
        quote_age_max_ms=max(quote_maxes) if quote_maxes else None,
        quote_age_avg_ms=(sum(quote_avgs) / len(quote_avgs)) if quote_avgs else None,
    )


def _fmt_ms(value: float | None) -> str:
    return "N/A (측정 불가)" if value is None else f"{value:.1f}ms"


def format_lane_digest_console(digest: LaneSessionDigest) -> list[str]:
    """Human-readable digest lines. Honesty labels live in the body, not docs."""
    lines = [
        "── Lane pipeline 관찰 다이제스트 ──",
        f"관찰 {digest.total_cycles} 세션(사이클): "
        f"flag-on {digest.flag_on_cycles} · flag-off(레거시) {digest.flag_off_cycles}",
        "  ※ flag-off(레거시) 세션은 레인 텔레메트리가 없어 아래 지표에서 제외됨",
    ]
    if digest.flag_on_cycles == 0:
        lines.append("  (flag-on 세션 없음 — 레인 지표 산출 불가)")
        return lines
    lines += [
        f"blocked_detached: {digest.blocked_detached_cycles} "
        f"(파생 — last-decision 기반, 사이클당 최종 결정만 포착)",
        f"expired: {digest.expired_cycles} (파생 — 동일 한계)",
        f"buy_scan_exception: {digest.buy_scan_exception_cycles}",
        f"budget_exceeded: {digest.budget_exceeded_cycles}",
        f"overlap(양 레인 동시 실행): {digest.overlap_cycles}",
        f"order_gate: max_queue={digest.max_queue_depth} · total_processed={digest.total_processed}",
        f"quote_age: max={_fmt_ms(digest.quote_age_max_ms)} · "
        f"avg={_fmt_ms(digest.quote_age_avg_ms)}",
    ]
    if digest.decision_counts:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(digest.decision_counts.items()))
        lines.append(f"decision 분포: {parts}")
    return lines


def format_lane_digest_json(digest: LaneSessionDigest) -> dict[str, object]:
    """JSON-safe digest. quote_age None → null; never emits inf/nan."""
    return {
        "total_cycles": digest.total_cycles,
        "flag_on_cycles": digest.flag_on_cycles,
        "flag_off_cycles": digest.flag_off_cycles,
        "flag_off_excluded_from_lane_metrics": True,
        "decision_counts": dict(digest.decision_counts),
        "blocked_detached_cycles": digest.blocked_detached_cycles,
        "blocked_detached_is_derived": True,
        "expired_cycles": digest.expired_cycles,
        "buy_scan_exception_cycles": digest.buy_scan_exception_cycles,
        "budget_exceeded_cycles": digest.budget_exceeded_cycles,
        "overlap_cycles": digest.overlap_cycles,
        "max_queue_depth": digest.max_queue_depth,
        "total_processed": digest.total_processed,
        "quote_age_max_ms": digest.quote_age_max_ms,
        "quote_age_avg_ms": digest.quote_age_avg_ms,
    }
