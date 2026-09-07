from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from app.core.runtime_budget import (
    api_budget_backoff_active,
    api_budget_can_quote,
    api_budget_transient_backoff_active,
    prune_recent_rate_limit_hits,
    prune_recent_rate_limit_hits as _prune_recent_rate_limit_hits,
)
from app.core.throttle import note_rate_limit
from app.core.time_utils import get_korean_now
from app.notifications.bottleneck import KIS_RATE_LIMIT_BACKOFF
from app.notifications.runtime_alerts import record_bottleneck as _record_bottleneck


def is_due(*, last_run_at: datetime | None, interval_seconds: int, now: datetime) -> bool:
    if last_run_at is None:
        return True
    return (now - last_run_at).total_seconds() >= interval_seconds


def build_cycle_id() -> str:
    return f"{get_korean_now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"


def compute_next_tick_sleep_seconds(
    *,
    tick_started_at: datetime,
    completed_at: datetime,
    base_tick_seconds: int | float,
) -> float:
    elapsed_seconds = max(
        0.0,
        (completed_at - tick_started_at).total_seconds(),
    )
    return max(0.0, float(base_tick_seconds) - elapsed_seconds)


def print_engine_schedule_state(
    *,
    settings,
    sell_check_due: bool,
    buy_scan_due: bool,
    effective_sell_check_interval_seconds: int | None = None,
    effective_buy_scan_interval_seconds: int | None = None,
    last_sell_check_at: datetime | None = None,
    last_buy_scan_at: datetime | None = None,
    scheduler_decision: str | None = None,
    api_budget_state: dict[str, object] | None = None,
    runtime_rate_control: dict[str, object] | None = None,
) -> None:
    print("=== 엔진 스케줄 상태 ===")
    print(f"tick_at={get_korean_now().isoformat()}")
    sell_interval_text = f"{settings.sell_check_interval_seconds}s"
    if (
        effective_sell_check_interval_seconds is not None
        and effective_sell_check_interval_seconds != settings.sell_check_interval_seconds
    ):
        sell_interval_text = (
            f"{settings.sell_check_interval_seconds}s -> "
            f"{effective_sell_check_interval_seconds}s(dynamic)"
        )
    print(
        f"SELL check due: {'YES' if sell_check_due else 'NO'} "
        f"(interval={sell_interval_text})"
    )
    buy_interval_text = f"{settings.buy_scan_interval_seconds}s"
    if (
        effective_buy_scan_interval_seconds is not None
        and effective_buy_scan_interval_seconds != settings.buy_scan_interval_seconds
    ):
        buy_interval_text = (
            f"{settings.buy_scan_interval_seconds}s -> "
            f"{effective_buy_scan_interval_seconds}s(dynamic)"
        )
    print(
        f"BUY scan due: {'YES' if buy_scan_due else 'NO'} "
        f"(interval={buy_interval_text})"
    )
    print(
        "last_sell_check_at="
        f"{last_sell_check_at.isoformat() if last_sell_check_at is not None else '-'}"
    )
    print(
        "last_buy_scan_at="
        f"{last_buy_scan_at.isoformat() if last_buy_scan_at is not None else '-'}"
    )
    if scheduler_decision:
        print(f"scheduler decision={scheduler_decision}")
    if runtime_rate_control:
        print(
            "runtime control="
            f"{runtime_rate_control.get('mode') or 'normal'} | "
            f"reason={runtime_rate_control.get('reason') or '-'} | "
            f"scan_max={runtime_rate_control.get('effective_scan_symbols_max_per_cycle') or settings.scan_symbols_max_per_cycle} | "
            f"deep_eval={runtime_rate_control.get('effective_buy_scan_deep_eval_limit') or settings.buy_scan_deep_eval_limit} | "
            f"sell_cap={runtime_rate_control.get('effective_sell_watch_max_holdings_per_tick') or '-'}"
        )
    if api_budget_state:
        print(
            "api_budget="
            f"requests:{api_budget_state.get('recent_request_count', 0)}/"
            f"{settings.api_soft_max_requests_per_second}, "
            f"quotes:{api_budget_state.get('quotes_used_this_tick', 0)}/"
            f"{settings.api_soft_max_quotes_per_tick}, "
            f"backoff_remaining:{api_budget_state.get('backoff_remaining_seconds', 0)}s, "
            f"transient_backoff:{api_budget_state.get('transient_backoff_remaining_seconds', 0)}s, "
            f"rate_limit_hits:{api_budget_state.get('rate_limit_hits', 0)}, "
            f"rate_limit_hits_10m:{api_budget_state.get('recent_rate_limit_hits_10m', 0)}, "
            f"transient_hits_10m:{api_budget_state.get('recent_transient_error_hits_10m', 0)}, "
            f"backoff_cycles:{api_budget_state.get('consecutive_backoff_cycles', 0)}"
        )
    print()


def parse_hhmm_window(window_text: str) -> tuple[int, int] | None:
    text = str(window_text or "").strip()
    if "-" not in text:
        return None
    start_text, end_text = (part.strip() for part in text.split("-", 1))
    try:
        start_hour, start_minute = (int(part) for part in start_text.split(":", 1))
        end_hour, end_minute = (int(part) for part in end_text.split(":", 1))
    except (TypeError, ValueError):
        return None
    return (start_hour * 60 + start_minute, end_hour * 60 + end_minute)


def within_hhmm_window(*, now: datetime, window_text: str) -> bool:
    parsed = parse_hhmm_window(window_text)
    if parsed is None:
        return False
    start_minutes, end_minutes = parsed
    current_minutes = now.hour * 60 + now.minute
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def build_scheduler_tick_decision(
    *,
    sell_check_due: bool,
    buy_scan_due: bool,
    api_budget_state: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    if sell_check_due and buy_scan_due:
        scheduler_decision = "SELL_PRIORITY_WITH_BUY"
    elif sell_check_due:
        scheduler_decision = "SELL_ONLY_DUE"
    elif buy_scan_due:
        scheduler_decision = "BUY_ONLY_DUE"
    else:
        scheduler_decision = "IDLE_WAIT"

    if api_budget_transient_backoff_active(api_budget_state, now=now):
        return {
            "decision": "API_TRANSIENT_BACKOFF_WAIT",
            "sell_check_due": False,
            "buy_scan_due": False,
            "skip_cycle": True,
        }

    if api_budget_backoff_active(api_budget_state, now=now):
        rate_limit_source = str(
            api_budget_state.get("last_rate_limit_source") or ""
        ).strip()
        # Balance is fetched before sell_watch; draining this backoff would
        # immediately retry the same endpoint at the cooldown boundary.
        if rate_limit_source == "balance":
            return {
                "decision": "API_BACKOFF_WAIT",
                "sell_check_due": False,
                "buy_scan_due": False,
                "skip_cycle": True,
            }
        return {
            "decision": "API_BACKOFF_WAIT",
            "sell_check_due": sell_check_due,
            "buy_scan_due": False,
            "skip_cycle": not sell_check_due,
        }

    if sell_check_due and buy_scan_due and not api_budget_can_quote(
        api_budget_state,
        quote_cost=1,
    ):
        return {
            "decision": "SELL_PRIORITY_DEFER_BUY_BUDGET",
            "sell_check_due": sell_check_due,
            "buy_scan_due": False,
            "skip_cycle": False,
        }

    return {
        "decision": scheduler_decision,
        "sell_check_due": sell_check_due,
        "buy_scan_due": buy_scan_due,
        "skip_cycle": False,
    }


def build_runtime_rate_control(
    *,
    settings,
    api_budget_state: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    recent_rate_limit_hits = prune_recent_rate_limit_hits(api_budget_state, now=now)
    backoff_active = api_budget_backoff_active(api_budget_state, now=now)
    consecutive_backoff_cycles = int(api_budget_state.get("consecutive_backoff_cycles", 0) or 0)
    if backoff_active:
        consecutive_backoff_cycles += 1
    else:
        consecutive_backoff_cycles = 0
    api_budget_state["consecutive_backoff_cycles"] = consecutive_backoff_cycles

    degraded_until = api_budget_state.get("degraded_mode_until")
    degraded_active = isinstance(degraded_until, datetime) and degraded_until > now
    degraded_reason = str(api_budget_state.get("degraded_mode_reason") or "").strip()

    if settings.degraded_mode_enabled and not degraded_active:
        trigger_reasons: list[str] = []
        if (
            settings.degraded_mode_rate_limit_hits_in_10m > 0
            and len(recent_rate_limit_hits) >= settings.degraded_mode_rate_limit_hits_in_10m
        ):
            trigger_reasons.append(
                f"rate_limit_hits_10m={len(recent_rate_limit_hits)}"
            )
        if (
            settings.degraded_mode_consecutive_backoff_cycles > 0
            and consecutive_backoff_cycles >= settings.degraded_mode_consecutive_backoff_cycles
        ):
            trigger_reasons.append(
                f"consecutive_backoff_cycles={consecutive_backoff_cycles}"
            )
        if trigger_reasons:
            degraded_until = now + timedelta(seconds=settings.degraded_mode_duration_seconds)
            api_budget_state["degraded_mode_until"] = degraded_until
            degraded_reason = " / ".join(trigger_reasons)
            api_budget_state["degraded_mode_reason"] = degraded_reason
            degraded_active = True
            print(
                "[info] degraded mode activated | "
                f"until={degraded_until.isoformat()} | reason={degraded_reason}"
            )
    elif not degraded_active:
        api_budget_state["degraded_mode_until"] = None
        api_budget_state["degraded_mode_reason"] = None
        degraded_reason = ""

    midday_active = bool(settings.adaptive_midday_enabled) and within_hhmm_window(
        now=now,
        window_text=settings.adaptive_midday_window,
    )

    effective_sell_interval = int(settings.sell_check_interval_seconds)
    effective_buy_interval = int(settings.buy_scan_interval_seconds)
    effective_scan_symbols_max = int(settings.scan_symbols_max_per_cycle)
    effective_buy_scan_deep_eval_limit = int(settings.buy_scan_deep_eval_limit)
    effective_sell_watch_max_holdings_per_tick: int | None = None
    mode = "normal"
    reasons: list[str] = []

    if midday_active:
        mode = "midday"
        reasons.append(f"window={settings.adaptive_midday_window}")
        effective_sell_interval = max(
            effective_sell_interval,
            int(settings.adaptive_midday_sell_check_interval_seconds),
        )
        effective_buy_interval = max(
            effective_buy_interval,
            int(settings.adaptive_midday_buy_scan_interval_seconds),
        )
        effective_scan_symbols_max = min(
            effective_scan_symbols_max,
            int(settings.adaptive_midday_scan_symbols_max_per_cycle),
        )
        effective_buy_scan_deep_eval_limit = min(
            effective_buy_scan_deep_eval_limit,
            int(settings.adaptive_midday_buy_scan_deep_eval_limit),
        )

    if degraded_active:
        mode = "degraded"
        reasons.append(degraded_reason or "sustained_rate_limit")
        effective_sell_interval = max(
            effective_sell_interval,
            int(settings.degraded_mode_sell_check_interval_seconds),
        )
        effective_buy_interval = max(
            effective_buy_interval,
            int(settings.degraded_mode_buy_scan_interval_seconds),
        )
        effective_scan_symbols_max = min(
            effective_scan_symbols_max,
            int(settings.degraded_mode_scan_symbols_max_per_cycle),
        )
        effective_buy_scan_deep_eval_limit = min(
            effective_buy_scan_deep_eval_limit,
            int(settings.degraded_mode_buy_scan_deep_eval_limit),
        )
        effective_sell_watch_max_holdings_per_tick = int(
            settings.degraded_mode_sell_watch_max_holdings_per_tick
        )

    effective_shallow_top_k = min(
        int(settings.buy_scan_shallow_top_k),
        max(effective_scan_symbols_max, 1),
    )
    effective_buy_scan_deep_eval_limit = min(
        max(1, effective_buy_scan_deep_eval_limit),
        effective_shallow_top_k,
    )

    return {
        "mode": mode,
        "reason": " + ".join(reason for reason in reasons if reason) or "base_defaults",
        "midday_active": midday_active,
        "degraded_active": degraded_active,
        "recent_rate_limit_hits_10m": len(recent_rate_limit_hits),
        "consecutive_backoff_cycles": consecutive_backoff_cycles,
        "effective_sell_check_interval_seconds": effective_sell_interval,
        "effective_buy_scan_interval_seconds": effective_buy_interval,
        "effective_scan_symbols_max_per_cycle": effective_scan_symbols_max,
        "effective_buy_scan_shallow_top_k": effective_shallow_top_k,
        "effective_buy_scan_deep_eval_limit": effective_buy_scan_deep_eval_limit,
        "effective_sell_watch_max_holdings_per_tick": effective_sell_watch_max_holdings_per_tick,
    }


# Source-text guard: tests/test_main_rate_limit_body_sources.py inspects this body
# for the KIS_RATE_LIMIT_BACKOFF / _record_bottleneck literals — keep them verbatim.
def note_rate_limit_backoff(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    source: str | None = None,
) -> None:
    recent_hits = _prune_recent_rate_limit_hits(api_budget_state, now=now)
    recent_hits.append(now)
    api_budget_state["recent_rate_limit_hit_times"] = recent_hits
    api_budget_state["last_rate_limit_source"] = (
        str(source or "").strip() or None
    )
    hits = int(api_budget_state.get("rate_limit_hits", 0)) + 1
    api_budget_state["rate_limit_hits"] = hits
    base_backoff = int(api_budget_state.get("backoff_seconds_on_rate_limit", 60) or 60)
    # Exponential backoff: 60s → 120s → 240s → 480s → 600s(max) on consecutive hits.
    # KIS enforces session-level bans that outlast our 60s internal backoff when
    # rate limits are triggered repeatedly.  Doubling the wait on each consecutive
    # hit gives KIS time to lift the ban before we retry.
    backoff_seconds = min(base_backoff * (2 ** min(hits - 1, 4)), 600)
    api_budget_state["backoff_until"] = now + timedelta(seconds=backoff_seconds)
    print(
        f"[info] rate limit backoff scheduled | hits={hits} | backoff={backoff_seconds}s"
    )
    note_rate_limit(source=source)
    _record_bottleneck(
        KIS_RATE_LIMIT_BACKOFF,
        context={"hits": hits, "backoff_s": backoff_seconds, "source": source or "unknown"},
    )


def run_session_loop(
    *,
    settings,
    build_api_budget_state: Callable[..., dict[str, object]],
    build_runtime_rate_control: Callable[..., dict[str, object]],
    compute_effective_sell_check_interval_seconds: Callable[..., int],
    is_due_callback: Callable[..., bool],
    build_scheduler_tick_decision: Callable[..., dict[str, object]],
    api_budget_transient_backoff_remaining_seconds: Callable[..., float],
    api_budget_backoff_remaining_seconds: Callable[..., float],
    run_cycle: Callable[..., None],
    emit_status: Callable[..., None],
    record_main_loop_exception_if_needed: Callable[[Exception], None],
    get_now: Callable[[], datetime],
    sleep: Callable[[float], None],
    print_message: Callable[..., None],
    record_cycle_health: Callable[[Exception | None], None] | None = None,
) -> None:
    base_tick_seconds = max(
        1,
        min(
            settings.run_interval_seconds,
            settings.sell_check_interval_seconds,
            settings.buy_scan_interval_seconds,
        ),
    )
    last_sell_check_at: datetime | None = None
    last_buy_scan_at: datetime | None = None
    api_budget_state = build_api_budget_state(settings)

    while True:
        now = get_now()
        api_budget_state["quotes_used_this_tick"] = 0
        runtime_rate_control = build_runtime_rate_control(
            settings=settings,
            api_budget_state=api_budget_state,
            now=now,
        )
        effective_sell_check_interval_seconds = compute_effective_sell_check_interval_seconds(
            base_interval_seconds=int(
                runtime_rate_control["effective_sell_check_interval_seconds"]
            ),
            total_holdings=int(api_budget_state.get("last_sell_watch_total_holdings", 0) or 0),
            recent_partial=bool(api_budget_state.get("last_sell_watch_partial") or False),
            last_rate_limit_source=str(api_budget_state.get("last_rate_limit_source") or ""),
            recent_partial_streak=int(
                api_budget_state.get("consecutive_sell_watch_partial_cycles", 0) or 0
            ),
        )
        sell_check_due = is_due_callback(
            last_run_at=last_sell_check_at,
            interval_seconds=effective_sell_check_interval_seconds,
            now=now,
        )
        buy_scan_due = is_due_callback(
            last_run_at=last_buy_scan_at,
            interval_seconds=int(
                runtime_rate_control["effective_buy_scan_interval_seconds"]
            ),
            now=now,
        )
        scheduler_tick = build_scheduler_tick_decision(
            sell_check_due=sell_check_due,
            buy_scan_due=buy_scan_due,
            api_budget_state=api_budget_state,
            now=now,
        )
        scheduler_decision = str(scheduler_tick["decision"])
        sell_check_due = bool(scheduler_tick["sell_check_due"])
        buy_scan_due = bool(scheduler_tick["buy_scan_due"])

        if not sell_check_due and not buy_scan_due:
            if scheduler_tick.get("skip_cycle"):
                if scheduler_decision == "API_TRANSIENT_BACKOFF_WAIT":
                    backoff_remaining = api_budget_transient_backoff_remaining_seconds(
                        api_budget_state,
                        now=now,
                    )
                    source_text = str(
                        api_budget_state.get("last_transient_error_source") or "unknown"
                    )
                    print_message(
                        "[info] API transient backoff active: skip engine cycle"
                        f" | source={source_text}"
                        f" | remaining={round(backoff_remaining, 1)}s"
                    )
                else:
                    backoff_remaining = api_budget_backoff_remaining_seconds(
                        api_budget_state,
                        now=now,
                    )
                    print_message(
                        "[info] API backoff active: skip engine cycle"
                        f" | remaining={round(backoff_remaining, 1)}s"
                    )
            sleep(base_tick_seconds)
            continue

        try:
            cycle_settings = replace(
                settings,
                sell_check_interval_seconds=int(
                    runtime_rate_control["effective_sell_check_interval_seconds"]
                ),
                buy_scan_interval_seconds=int(
                    runtime_rate_control["effective_buy_scan_interval_seconds"]
                ),
                scan_symbols_max_per_cycle=int(
                    runtime_rate_control["effective_scan_symbols_max_per_cycle"]
                ),
                buy_scan_shallow_top_k=int(
                    runtime_rate_control["effective_buy_scan_shallow_top_k"]
                ),
                buy_scan_deep_eval_limit=int(
                    runtime_rate_control["effective_buy_scan_deep_eval_limit"]
                ),
            )
            run_cycle(
                cycle_settings,
                sell_check_due=sell_check_due,
                buy_scan_due=buy_scan_due,
                scheduler_state={
                    "last_sell_check_at": last_sell_check_at,
                    "last_buy_scan_at": last_buy_scan_at,
                    "decision": scheduler_decision,
                    "sell_check_due": sell_check_due,
                    "buy_scan_due": buy_scan_due,
                    "effective_sell_check_interval_seconds": effective_sell_check_interval_seconds,
                    "effective_buy_scan_interval_seconds": int(
                        runtime_rate_control["effective_buy_scan_interval_seconds"]
                    ),
                    "effective_sell_watch_max_holdings_per_tick": runtime_rate_control.get(
                        "effective_sell_watch_max_holdings_per_tick"
                    ),
                    "runtime_rate_control": runtime_rate_control,
                },
                api_budget_state=api_budget_state,
            )
            if record_cycle_health is not None:
                record_cycle_health(None)
        except Exception as exc:
            emit_status(
                "DEGRADED",
                f"사이클 실행 중 예외가 발생했지만 다음 사이클을 계속 진행합니다: {exc}",
            )
            record_main_loop_exception_if_needed(exc)
            if record_cycle_health is not None:
                record_cycle_health(exc)
        completed_at = get_now()
        if sell_check_due:
            last_sell_check_at = now
        if buy_scan_due:
            last_buy_scan_at = now
        next_tick_sleep_seconds = compute_next_tick_sleep_seconds(
            tick_started_at=now,
            completed_at=completed_at,
            base_tick_seconds=base_tick_seconds,
        )
        if next_tick_sleep_seconds > 0:
            print_message(
                f"{round(next_tick_sleep_seconds, 1)}초 후 다음 엔진 tick을 시작합니다."
                f" (target={base_tick_seconds}s)"
            )
        else:
            print_message(
                "다음 엔진 tick을 바로 시작합니다."
                f" (target={base_tick_seconds}s)"
            )
        print_message()
        if next_tick_sleep_seconds > 0:
            sleep(next_tick_sleep_seconds)
