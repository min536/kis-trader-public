"""Runtime scan pre-gating, normalizer, and planning helpers.

Extracted from app.main Stage 2e-1 through Stage 2e-3.
"""

from __future__ import annotations

import math
from datetime import datetime

from app.core.runtime_budget import (
    api_budget_remaining_quotes as _api_budget_remaining_quotes,
    api_budget_remaining_requests as _api_budget_remaining_requests,
)
from app.scanner.service import build_shallow_scan_candidates


def select_buy_scan_profile(state: dict, settings) -> dict[str, object]:
    profiles = ("momentum", "pullback", "recovery")
    cursor_before = int(state.get("buy_scan_profile_cursor", 0) or 0)
    if settings.buy_scan_profile_rotation_enabled:
        profile = profiles[cursor_before % len(profiles)]
        cursor_after = (cursor_before + 1) % len(profiles)
    else:
        profile = profiles[0]
        cursor_after = cursor_before
    state["buy_scan_profile_cursor"] = cursor_after
    state["buy_scan_last_profile"] = profile
    return {
        "profile": profile,
        "cursor_before": cursor_before,
        "cursor_after": cursor_after,
        "rotation_enabled": bool(settings.buy_scan_profile_rotation_enabled),
    }


def _take_circular_window(
    items: tuple[str, ...],
    *,
    cursor: int,
    count: int,
) -> tuple[tuple[str, ...], int]:
    if not items or count <= 0:
        return (), 0
    normalized_cursor = int(cursor or 0) % len(items)
    selected = [
        items[(normalized_cursor + offset) % len(items)]
        for offset in range(min(count, len(items)))
    ]
    next_cursor = (normalized_cursor + len(selected)) % len(items)
    return tuple(selected), next_cursor


def build_buy_scan_layered_universe(
    *,
    state: dict,
    settings,
    raw_symbols: tuple[str, ...],
    excluded_symbols: tuple[str, ...] | None = None,
) -> dict[str, object]:
    excluded_symbol_set = {
        str(symbol).strip()
        for symbol in tuple(excluded_symbols or ())
        if str(symbol).strip()
    }
    if not raw_symbols:
        return {
            "selected_symbols": (),
            "layer_by_symbol": {},
            "core_symbols": (),
            "rotating_symbols": (),
            "exploration_symbols": (),
            "core_count": 0,
            "rotating_count": 0,
            "exploration_count": 0,
            "selected_core_count": 0,
            "selected_rotating_count": 0,
            "selected_exploration_count": 0,
            "selected_budget": 0,
            "excluded_count": 0,
            "excluded_symbols": (),
            "core_cursor_before": 0,
            "core_cursor_after": 0,
            "rotating_cursor_before": 0,
            "rotating_cursor_after": 0,
            "exploration_cursor_before": 0,
            "exploration_cursor_after": 0,
            "preview": {},
        }

    raw_total = len(raw_symbols)
    core_count = min(raw_total, max(1, int(round(raw_total * settings.buy_scan_core_fraction))))
    core_count = min(core_count, settings.buy_scan_core_max)
    rotating_count = min(
        max(raw_total - core_count, 0),
        int(round(raw_total * settings.buy_scan_rotating_fraction)),
    )
    exploration_count = max(raw_total - core_count - rotating_count, 0)

    core_symbols = tuple(
        symbol
        for symbol in raw_symbols[:core_count]
        if symbol not in excluded_symbol_set
    )
    rotating_symbols = tuple(
        symbol
        for symbol in raw_symbols[core_count : core_count + rotating_count]
        if symbol not in excluded_symbol_set
    )
    exploration_symbols = tuple(
        symbol
        for symbol in raw_symbols[core_count + rotating_count :]
        if symbol not in excluded_symbol_set
    )

    filtered_total = len(core_symbols) + len(rotating_symbols) + len(exploration_symbols)
    selected_budget = min(settings.scan_symbols_max_per_cycle, filtered_total)
    target_core = min(
        len(core_symbols),
        max(0, int(round(selected_budget * settings.buy_scan_core_fraction))),
    )
    if core_symbols and selected_budget > 0 and target_core == 0:
        target_core = 1
    remaining_budget = max(selected_budget - target_core, 0)
    target_rotating = min(
        len(rotating_symbols),
        max(0, int(round(selected_budget * settings.buy_scan_rotating_fraction))),
        remaining_budget,
    )
    remaining_budget = max(selected_budget - target_core - target_rotating, 0)
    target_exploration = min(len(exploration_symbols), remaining_budget)
    refill_budget = max(selected_budget - target_core - target_rotating - target_exploration, 0)

    extra_core = 0
    extra_rotating = 0
    extra_exploration = 0
    for layer_name, available_count, used_count in (
        ("rotating", len(rotating_symbols), target_rotating),
        ("exploration", len(exploration_symbols), target_exploration),
        ("core", len(core_symbols), target_core),
    ):
        if refill_budget <= 0:
            break
        remaining_in_layer = max(available_count - used_count, 0)
        if remaining_in_layer <= 0:
            continue
        take = min(remaining_in_layer, refill_budget)
        if layer_name == "core":
            extra_core += take
        elif layer_name == "rotating":
            extra_rotating += take
        else:
            extra_exploration += take
        refill_budget -= take

    core_cursor_before = int(state.get("buy_scan_core_cursor", 0) or 0)
    if settings.buy_scan_profile_rotation_enabled:
        selected_core, core_cursor_after = _take_circular_window(
            core_symbols,
            cursor=core_cursor_before,
            count=target_core,
        )
        if extra_core > 0:
            extra_selected_core, core_cursor_after = _take_circular_window(
                core_symbols,
                cursor=core_cursor_after,
                count=extra_core,
            )
            selected_core = (*selected_core, *extra_selected_core)
    else:
        selected_core = core_symbols[: target_core + extra_core]
        core_cursor_after = core_cursor_before
    state["buy_scan_core_cursor"] = core_cursor_after

    rotating_cursor_before = int(state.get("buy_scan_rotating_cursor", 0) or 0)
    exploration_cursor_before = int(state.get("buy_scan_exploration_cursor", 0) or 0)
    selected_rotating, rotating_cursor_after = _take_circular_window(
        rotating_symbols,
        cursor=rotating_cursor_before,
        count=target_rotating,
    )
    if extra_rotating > 0:
        extra_selected_rotating, rotating_cursor_after = _take_circular_window(
            rotating_symbols,
            cursor=rotating_cursor_after,
            count=extra_rotating,
        )
        selected_rotating = (*selected_rotating, *extra_selected_rotating)
    selected_exploration, exploration_cursor_after = _take_circular_window(
        exploration_symbols,
        cursor=exploration_cursor_before,
        count=target_exploration,
    )
    if extra_exploration > 0:
        extra_selected_exploration, exploration_cursor_after = _take_circular_window(
            exploration_symbols,
            cursor=exploration_cursor_after,
            count=extra_exploration,
        )
        selected_exploration = (*selected_exploration, *extra_selected_exploration)
    state["buy_scan_rotating_cursor"] = rotating_cursor_after
    state["buy_scan_exploration_cursor"] = exploration_cursor_after

    selected_symbols = tuple(
        dict.fromkeys((*selected_core, *selected_rotating, *selected_exploration))
    )
    layer_by_symbol: dict[str, str] = {}
    for symbol in selected_core:
        layer_by_symbol[symbol] = "core"
    for symbol in selected_rotating:
        layer_by_symbol[symbol] = "rotating"
    for symbol in selected_exploration:
        layer_by_symbol[symbol] = "exploration"

    return {
        "selected_symbols": selected_symbols,
        "layer_by_symbol": layer_by_symbol,
        "core_symbols": core_symbols,
        "rotating_symbols": rotating_symbols,
        "exploration_symbols": exploration_symbols,
        "core_count": len(core_symbols),
        "rotating_count": len(rotating_symbols),
        "exploration_count": len(exploration_symbols),
        "selected_core_count": len(selected_core),
        "selected_rotating_count": len(selected_rotating),
        "selected_exploration_count": len(selected_exploration),
        "selected_budget": selected_budget,
        "excluded_count": len(excluded_symbol_set),
        "excluded_symbols": tuple(sorted(excluded_symbol_set)),
        "core_cursor_before": core_cursor_before,
        "core_cursor_after": core_cursor_after,
        "rotating_cursor_before": rotating_cursor_before,
        "rotating_cursor_after": rotating_cursor_after,
        "exploration_cursor_before": exploration_cursor_before,
        "exploration_cursor_after": exploration_cursor_after,
        "preview": {
            "core": list(selected_core[:5]),
            "rotating": list(selected_rotating[:5]),
            "exploration": list(selected_exploration[:5]),
        },
    }


def build_buy_scan_shallow_plan(
    *,
    state: dict,
    settings,
    profile: str,
    symbols: tuple[str, ...],
    layer_by_symbol: dict[str, str],
) -> dict[str, object]:
    shallow_candidates = build_shallow_scan_candidates(
        symbols=symbols,
        profile=profile,
        layer_by_symbol=layer_by_symbol,
        cached_snapshots=state.get("recent_market_snapshots_by_symbol") or {},
        max_cache_age_seconds=int(getattr(settings, "live_snapshot_ttl_seconds", 420) or 420),
    )
    _shallow_top_k = int(getattr(settings, "buy_scan_shallow_top_k", 0) or 0)
    _shallow_cap_original_count = len(shallow_candidates)
    _shallow_cap_applied = False
    if _shallow_top_k > 0 and len(shallow_candidates) > _shallow_top_k:
        shallow_candidates = shallow_candidates[:_shallow_top_k]
        _shallow_cap_applied = True
    deep_eval_limit = min(settings.buy_scan_deep_eval_limit, len(shallow_candidates))
    exploration_quota_target = min(
        len([item for item in shallow_candidates if item.layer != "core"]),
        int(math.ceil(deep_eval_limit * settings.buy_scan_exploration_ratio)),
    )
    if exploration_quota_target == 0 and deep_eval_limit > 1 and any(
        item.layer != "core" for item in shallow_candidates
    ):
        exploration_quota_target = 1
    exploration_rows = [item for item in shallow_candidates if item.layer != "core"]
    selected_symbols: list[str] = []
    quota_selected_symbols: set[str] = set()
    exploration_quota_used = 0
    for item in exploration_rows[:exploration_quota_target]:
        if item.symbol not in selected_symbols:
            selected_symbols.append(item.symbol)
            quota_selected_symbols.add(item.symbol)
            exploration_quota_used += 1
    for item in shallow_candidates:
        if item.symbol in selected_symbols:
            continue
        selected_symbols.append(item.symbol)
        if len(selected_symbols) >= deep_eval_limit:
            break
    core_rescue_applied = False
    core_rescue_selected_symbol: str | None = None
    core_rescue_selected_score: float | None = None
    core_rescue_replaced_symbol: str | None = None
    core_rescue_reason: str | None = None
    shortlist_layers = [str(layer_by_symbol.get(symbol) or "core") for symbol in selected_symbols]
    if deep_eval_limit > 0 and selected_symbols and "core" not in shortlist_layers:
        top_core_candidate = next(
            (
                item
                for item in shallow_candidates
                if item.layer == "core" and item.symbol not in selected_symbols
            ),
            None,
        )
        replace_idx = next(
            (
                idx
                for idx in range(len(selected_symbols) - 1, -1, -1)
                if selected_symbols[idx] not in quota_selected_symbols
                and str(layer_by_symbol.get(selected_symbols[idx]) or "core") != "core"
            ),
            None,
        )
        if top_core_candidate is not None and replace_idx is not None:
            core_rescue_applied = True
            core_rescue_selected_symbol = top_core_candidate.symbol
            core_rescue_selected_score = round(float(top_core_candidate.shallow_score or 0.0), 4)
            core_rescue_replaced_symbol = selected_symbols[replace_idx]
            selected_symbols[replace_idx] = top_core_candidate.symbol
            core_rescue_reason = (
                "core가 shortlist에서 0건이라 quota 밖 비-core 1개를 "
                "최상위 core로 교체해 deep-eval 기회를 보존했습니다."
            )
    shortlist_symbols = tuple(selected_symbols[:deep_eval_limit])
    shortlist_preview = [
        {
            "symbol": item.symbol,
            "name": item.name,
            "layer": item.layer,
            "profile": item.profile,
            "shallow_score": round(float(item.shallow_score or 0.0), 3),
            "summary": item.summary,
            "snapshot_available": bool(item.snapshot_available),
        }
        for item in shallow_candidates[: settings.buy_scan_shallow_top_k]
    ]
    return {
        "profile": profile,
        "candidates": shallow_candidates,
        "ranked_count": len(shallow_candidates),
        "deep_eval_limit": deep_eval_limit,
        "exploration_quota_target": exploration_quota_target,
        "exploration_quota_used": exploration_quota_used,
        "shortlist_symbols": shortlist_symbols,
        "shortlist_preview": shortlist_preview,
        "core_rescue_applied": core_rescue_applied,
        "core_rescue_selected_symbol": core_rescue_selected_symbol,
        "core_rescue_selected_score": core_rescue_selected_score,
        "core_rescue_replaced_symbol": core_rescue_replaced_symbol,
        "core_rescue_reason": core_rescue_reason,
        "selected_layers": {
            symbol: str(layer_by_symbol.get(symbol) or "core")
            for symbol in shortlist_symbols
        },
        "shallow_cap_applied": _shallow_cap_applied,
        "shallow_cap_original_count": _shallow_cap_original_count,
        "shallow_cap_limit": _shallow_top_k,
    }


def build_buy_scan_deep_eval_symbols(
    *,
    shallow_plan: dict[str, object] | None,
) -> tuple[str, ...]:
    plan = shallow_plan if isinstance(shallow_plan, dict) else {}
    shortlist_symbols = tuple(plan.get("shortlist_symbols") or ())
    if not shortlist_symbols or not bool(plan.get("core_rescue_applied")):
        return shortlist_symbols

    rescued_symbol = str(plan.get("core_rescue_selected_symbol") or "").strip()
    if not rescued_symbol or rescued_symbol not in shortlist_symbols:
        return shortlist_symbols
    if shortlist_symbols[0] == rescued_symbol:
        return shortlist_symbols

    # Keep shortlist membership fixed while giving the rescued core row
    # an earlier deep-eval chance if rate-limit pressure stops the loop mid-way.
    return (rescued_symbol,) + tuple(
        symbol for symbol in shortlist_symbols if symbol != rescued_symbol
    )


def cap_buy_scan_deep_eval_symbols_for_api_budget(
    *,
    symbols: tuple[str, ...],
    api_budget_state: dict[str, object],
    now: datetime,
    execution_request_reserve: int = 0,
    min_scan_request_floor: int = 0,
) -> tuple[tuple[str, ...], dict[str, object]]:
    remaining_quotes = _api_budget_remaining_quotes(api_budget_state)
    remaining_requests = _api_budget_remaining_requests(api_budget_state, now=now)
    request_reserve = max(0, int(execution_request_reserve or 0))
    reserved_scan_requests = max(0, remaining_requests - request_reserve)
    scan_floor = min(
        len(symbols),
        remaining_requests,
        max(0, int(min_scan_request_floor or 0)),
    )
    available_scan_requests = max(reserved_scan_requests, scan_floor)
    cap = min(len(symbols), remaining_quotes, available_scan_requests)
    capped_symbols = tuple(symbols[:cap])
    cap_reasons: list[str] = []
    if remaining_quotes < len(symbols):
        cap_reasons.append("quote_budget")
    if available_scan_requests < len(symbols):
        cap_reasons.append("request_budget")
    return capped_symbols, {
        "budget_cap_applied": len(capped_symbols) < len(symbols),
        "budget_cap_reasons": cap_reasons,
        "quote_budget_cap_applied": remaining_quotes < len(symbols),
        "request_budget_cap_applied": available_scan_requests < len(symbols),
        "quote_budget_remaining_before_scan": remaining_quotes,
        "request_budget_remaining_before_scan": remaining_requests,
        "execution_request_reserve": request_reserve,
        "min_scan_request_floor": scan_floor,
        "request_budget_floor_applied": available_scan_requests > reserved_scan_requests,
        "execution_request_reserve_relaxed_by": max(
            0,
            available_scan_requests - reserved_scan_requests,
        ),
        "request_budget_available_for_scan": available_scan_requests,
        "quote_budget_original_count": len(symbols),
        "budget_capped_count": len(capped_symbols),
    }
