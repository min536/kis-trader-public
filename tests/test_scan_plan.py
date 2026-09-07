from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import app.scanner.runtime_scan as origin
from app.scanner import scan_plan


def _settings(**overrides):
    values = {
        "buy_scan_profile_rotation_enabled": True,
        "buy_scan_core_fraction": 0.5,
        "buy_scan_core_max": 3,
        "buy_scan_rotating_fraction": 0.25,
        "scan_symbols_max_per_cycle": 4,
        "buy_scan_deep_eval_limit": 2,
        "buy_scan_exploration_ratio": 0.5,
        "buy_scan_shallow_top_k": 3,
        "live_snapshot_ttl_seconds": 420,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --- Pin tests: facade re-exports must be the same object as scan_plan's ---


def test_pin_select_buy_scan_profile() -> None:
    assert getattr(origin, "select_buy_scan_profile") is getattr(
        scan_plan, "select_buy_scan_profile"
    )


def test_pin_take_circular_window() -> None:
    assert getattr(origin, "_take_circular_window") is getattr(
        scan_plan, "_take_circular_window"
    )


def test_pin_build_buy_scan_layered_universe() -> None:
    assert getattr(origin, "build_buy_scan_layered_universe") is getattr(
        scan_plan, "build_buy_scan_layered_universe"
    )


def test_pin_build_buy_scan_shallow_plan() -> None:
    assert getattr(origin, "build_buy_scan_shallow_plan") is getattr(
        scan_plan, "build_buy_scan_shallow_plan"
    )


def test_pin_build_buy_scan_deep_eval_symbols() -> None:
    assert getattr(origin, "build_buy_scan_deep_eval_symbols") is getattr(
        scan_plan, "build_buy_scan_deep_eval_symbols"
    )


def test_pin_cap_buy_scan_deep_eval_symbols_for_api_budget() -> None:
    assert getattr(origin, "cap_buy_scan_deep_eval_symbols_for_api_budget") is getattr(
        scan_plan, "cap_buy_scan_deep_eval_symbols_for_api_budget"
    )


# --- _take_circular_window: literal cases including wrap-around ---


def test_take_circular_window_empty_and_zero_count() -> None:
    assert scan_plan._take_circular_window((), cursor=0, count=3) == ((), 0)
    assert scan_plan._take_circular_window(("A", "B", "C"), cursor=1, count=0) == ((), 0)


def test_take_circular_window_wraps_past_end() -> None:
    assert scan_plan._take_circular_window(
        ("A", "B", "C", "D"), cursor=3, count=3
    ) == (("D", "A", "B"), 2)


def test_take_circular_window_count_exceeds_length_caps_at_length() -> None:
    assert scan_plan._take_circular_window(("A", "B"), cursor=0, count=5) == (
        ("A", "B"),
        0,
    )


# --- select_buy_scan_profile: all rotation branches ---


def test_select_buy_scan_profile_rotation_momentum() -> None:
    state = {"buy_scan_profile_cursor": 0}
    result = scan_plan.select_buy_scan_profile(
        state, _settings(buy_scan_profile_rotation_enabled=True)
    )
    assert result == {
        "profile": "momentum",
        "cursor_before": 0,
        "cursor_after": 1,
        "rotation_enabled": True,
    }
    assert state["buy_scan_profile_cursor"] == 1
    assert state["buy_scan_last_profile"] == "momentum"


def test_select_buy_scan_profile_rotation_pullback() -> None:
    state = {"buy_scan_profile_cursor": 1}
    result = scan_plan.select_buy_scan_profile(
        state, _settings(buy_scan_profile_rotation_enabled=True)
    )
    assert result == {
        "profile": "pullback",
        "cursor_before": 1,
        "cursor_after": 2,
        "rotation_enabled": True,
    }
    assert state["buy_scan_last_profile"] == "pullback"


def test_select_buy_scan_profile_rotation_recovery_wraps() -> None:
    state = {"buy_scan_profile_cursor": 2}
    result = scan_plan.select_buy_scan_profile(
        state, _settings(buy_scan_profile_rotation_enabled=True)
    )
    assert result == {
        "profile": "recovery",
        "cursor_before": 2,
        "cursor_after": 0,
        "rotation_enabled": True,
    }
    assert state["buy_scan_profile_cursor"] == 0
    assert state["buy_scan_last_profile"] == "recovery"


def test_select_buy_scan_profile_no_rotation_keeps_cursor() -> None:
    state = {"buy_scan_profile_cursor": 5}
    result = scan_plan.select_buy_scan_profile(
        state, _settings(buy_scan_profile_rotation_enabled=False)
    )
    assert result == {
        "profile": "momentum",
        "cursor_before": 5,
        "cursor_after": 5,
        "rotation_enabled": False,
    }
    assert state["buy_scan_profile_cursor"] == 5
    assert state["buy_scan_last_profile"] == "momentum"


# --- build_buy_scan_deep_eval_symbols: literal cases ---


def test_build_buy_scan_deep_eval_symbols_none_returns_empty() -> None:
    assert scan_plan.build_buy_scan_deep_eval_symbols(shallow_plan=None) == ()


def test_build_buy_scan_deep_eval_symbols_no_rescue_returns_shortlist() -> None:
    assert scan_plan.build_buy_scan_deep_eval_symbols(
        shallow_plan={"shortlist_symbols": ("A", "B")}
    ) == ("A", "B")


def test_build_buy_scan_deep_eval_symbols_rescued_already_front_unchanged() -> None:
    assert scan_plan.build_buy_scan_deep_eval_symbols(
        shallow_plan={
            "shortlist_symbols": ("A", "B"),
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "A",
        }
    ) == ("A", "B")


def test_build_buy_scan_deep_eval_symbols_promotes_rescued_symbol() -> None:
    assert scan_plan.build_buy_scan_deep_eval_symbols(
        shallow_plan={
            "shortlist_symbols": ("B", "A", "C"),
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "A",
        }
    ) == ("A", "B", "C")


def test_build_buy_scan_deep_eval_symbols_rescued_not_in_shortlist_unchanged() -> None:
    assert scan_plan.build_buy_scan_deep_eval_symbols(
        shallow_plan={
            "shortlist_symbols": ("B", "C"),
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "Z",
        }
    ) == ("B", "C")


# --- cap_buy_scan_deep_eval_symbols_for_api_budget: hand api_budget_state dicts ---

_CAP_NOW = datetime(2026, 4, 25, 10, 0, 0)


def test_cap_for_api_budget_ample_budget_no_cap() -> None:
    capped, metadata = scan_plan.cap_buy_scan_deep_eval_symbols_for_api_budget(
        symbols=("AAA", "BBB", "CCC"),
        api_budget_state={
            "quotes_used_this_tick": 0,
            "soft_max_quotes_per_tick": 10,
            "recent_requests": [],
            "soft_max_requests_per_second": 10,
        },
        now=_CAP_NOW,
    )
    assert capped == ("AAA", "BBB", "CCC")
    assert metadata == {
        "budget_cap_applied": False,
        "budget_cap_reasons": [],
        "quote_budget_cap_applied": False,
        "request_budget_cap_applied": False,
        "quote_budget_remaining_before_scan": 10,
        "request_budget_remaining_before_scan": 10,
        "execution_request_reserve": 0,
        "min_scan_request_floor": 0,
        "request_budget_floor_applied": False,
        "execution_request_reserve_relaxed_by": 0,
        "request_budget_available_for_scan": 10,
        "quote_budget_original_count": 3,
        "budget_capped_count": 3,
    }


def test_cap_for_api_budget_insufficient_quote_budget_caps() -> None:
    capped, metadata = scan_plan.cap_buy_scan_deep_eval_symbols_for_api_budget(
        symbols=("AAA", "BBB", "CCC", "DDD"),
        api_budget_state={
            "quotes_used_this_tick": 3,
            "soft_max_quotes_per_tick": 5,
            "recent_requests": [],
            "soft_max_requests_per_second": 10,
        },
        now=_CAP_NOW,
    )
    assert capped == ("AAA", "BBB")
    assert metadata == {
        "budget_cap_applied": True,
        "budget_cap_reasons": ["quote_budget"],
        "quote_budget_cap_applied": True,
        "request_budget_cap_applied": False,
        "quote_budget_remaining_before_scan": 2,
        "request_budget_remaining_before_scan": 10,
        "execution_request_reserve": 0,
        "min_scan_request_floor": 0,
        "request_budget_floor_applied": False,
        "execution_request_reserve_relaxed_by": 0,
        "request_budget_available_for_scan": 10,
        "quote_budget_original_count": 4,
        "budget_capped_count": 2,
    }


def test_cap_for_api_budget_floor_relaxes_reserve() -> None:
    capped, metadata = scan_plan.cap_buy_scan_deep_eval_symbols_for_api_budget(
        symbols=("AAA", "BBB", "CCC"),
        api_budget_state={
            "quotes_used_this_tick": 0,
            "soft_max_quotes_per_tick": 10,
            "recent_requests": [],
            "soft_max_requests_per_second": 4,
        },
        now=_CAP_NOW,
        execution_request_reserve=4,
        min_scan_request_floor=2,
    )
    assert capped == ("AAA", "BBB")
    assert metadata == {
        "budget_cap_applied": True,
        "budget_cap_reasons": ["request_budget"],
        "quote_budget_cap_applied": False,
        "request_budget_cap_applied": True,
        "quote_budget_remaining_before_scan": 10,
        "request_budget_remaining_before_scan": 4,
        "execution_request_reserve": 4,
        "min_scan_request_floor": 2,
        "request_budget_floor_applied": True,
        "execution_request_reserve_relaxed_by": 2,
        "request_budget_available_for_scan": 2,
        "quote_budget_original_count": 3,
        "budget_capped_count": 2,
    }


# --- build_buy_scan_layered_universe: hand input -> full layer composition ---


def test_build_buy_scan_layered_universe_empty_raw_symbols() -> None:
    universe = scan_plan.build_buy_scan_layered_universe(
        state={},
        settings=_settings(),
        raw_symbols=(),
        excluded_symbols=None,
    )
    assert universe == {
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


def test_build_buy_scan_layered_universe_splits_layers_and_advances_cursors() -> None:
    state = {
        "buy_scan_core_cursor": 1,
        "buy_scan_rotating_cursor": 0,
        "buy_scan_exploration_cursor": 0,
    }
    universe = scan_plan.build_buy_scan_layered_universe(
        state=state,
        settings=_settings(
            buy_scan_core_fraction=0.5,
            buy_scan_core_max=3,
            buy_scan_rotating_fraction=0.25,
            scan_symbols_max_per_cycle=4,
            buy_scan_profile_rotation_enabled=True,
        ),
        raw_symbols=("A", "B", "C", "D", "E", "F"),
        excluded_symbols=("D",),
    )
    assert universe == {
        "selected_symbols": ("B", "C", "E", "F"),
        "layer_by_symbol": {
            "B": "core",
            "C": "core",
            "E": "rotating",
            "F": "exploration",
        },
        "core_symbols": ("A", "B", "C"),
        "rotating_symbols": ("E",),
        "exploration_symbols": ("F",),
        "core_count": 3,
        "rotating_count": 1,
        "exploration_count": 1,
        "selected_core_count": 2,
        "selected_rotating_count": 1,
        "selected_exploration_count": 1,
        "selected_budget": 4,
        "excluded_count": 1,
        "excluded_symbols": ("D",),
        "core_cursor_before": 1,
        "core_cursor_after": 0,
        "rotating_cursor_before": 0,
        "rotating_cursor_after": 0,
        "exploration_cursor_before": 0,
        "exploration_cursor_after": 0,
        "preview": {
            "core": ["B", "C"],
            "rotating": ["E"],
            "exploration": ["F"],
        },
    }
    assert state["buy_scan_core_cursor"] == 0
    assert state["buy_scan_rotating_cursor"] == 0
    assert state["buy_scan_exploration_cursor"] == 0


# --- build_buy_scan_shallow_plan: hand input -> layering, order, cap, rescue ---


def test_build_buy_scan_shallow_plan_orders_and_quota_without_cap() -> None:
    plan = scan_plan.build_buy_scan_shallow_plan(
        state={"recent_market_snapshots_by_symbol": {}},
        settings=_settings(
            buy_scan_shallow_top_k=4,
            buy_scan_deep_eval_limit=3,
            buy_scan_exploration_ratio=0.5,
        ),
        profile="momentum",
        symbols=("AAA", "BBB", "CCC", "DDD"),
        layer_by_symbol={
            "AAA": "core",
            "BBB": "rotating",
            "CCC": "exploration",
            "DDD": "core",
        },
    )
    candidate_order = [(c.symbol, c.layer, c.shallow_score) for c in plan["candidates"]]
    assert candidate_order == [
        ("BBB", "rotating", 0.35),
        ("CCC", "exploration", 0.3),
        ("AAA", "core", 0.15),
        ("DDD", "core", 0.15),
    ]
    assert plan["profile"] == "momentum"
    assert plan["ranked_count"] == 4
    assert plan["deep_eval_limit"] == 3
    assert plan["exploration_quota_target"] == 2
    assert plan["exploration_quota_used"] == 2
    assert plan["shortlist_symbols"] == ("BBB", "CCC", "AAA")
    assert plan["selected_layers"] == {
        "BBB": "rotating",
        "CCC": "exploration",
        "AAA": "core",
    }
    assert plan["core_rescue_applied"] is False
    assert plan["core_rescue_selected_symbol"] is None
    assert plan["core_rescue_selected_score"] is None
    assert plan["core_rescue_replaced_symbol"] is None
    assert plan["core_rescue_reason"] is None
    assert plan["shallow_cap_applied"] is False
    assert plan["shallow_cap_original_count"] == 4
    assert plan["shallow_cap_limit"] == 4
    preview = [
        (p["symbol"], p["layer"], p["shallow_score"]) for p in plan["shortlist_preview"]
    ]
    assert preview == [
        ("BBB", "rotating", 0.35),
        ("CCC", "exploration", 0.3),
        ("AAA", "core", 0.15),
        ("DDD", "core", 0.15),
    ]


def test_build_buy_scan_shallow_plan_applies_core_rescue() -> None:
    plan = scan_plan.build_buy_scan_shallow_plan(
        state={"recent_market_snapshots_by_symbol": {}},
        settings=_settings(
            buy_scan_shallow_top_k=5,
            buy_scan_deep_eval_limit=2,
            buy_scan_exploration_ratio=0.1,
        ),
        profile="momentum",
        symbols=("AAA", "BBB", "CCC"),
        layer_by_symbol={"AAA": "core", "BBB": "rotating", "CCC": "exploration"},
    )
    assert plan["shortlist_symbols"] == ("BBB", "AAA")
    assert plan["core_rescue_applied"] is True
    assert plan["core_rescue_selected_symbol"] == "AAA"
    assert plan["core_rescue_selected_score"] == 0.15
    assert plan["core_rescue_replaced_symbol"] == "CCC"
    assert plan["core_rescue_reason"] == (
        "core가 shortlist에서 0건이라 quota 밖 비-core 1개를 "
        "최상위 core로 교체해 deep-eval 기회를 보존했습니다."
    )
    assert plan["exploration_quota_target"] == 1
    assert plan["exploration_quota_used"] == 1
    assert plan["selected_layers"] == {"BBB": "rotating", "AAA": "core"}
    assert plan["shallow_cap_applied"] is False
    assert plan["shallow_cap_original_count"] == 3
    assert plan["shallow_cap_limit"] == 5


def test_build_buy_scan_shallow_plan_applies_shallow_cap() -> None:
    plan = scan_plan.build_buy_scan_shallow_plan(
        state={"recent_market_snapshots_by_symbol": {}},
        settings=_settings(
            buy_scan_shallow_top_k=2,
            buy_scan_deep_eval_limit=1,
            buy_scan_exploration_ratio=1.0,
        ),
        profile="recovery",
        symbols=("AAA", "BBB", "CCC"),
        layer_by_symbol={"AAA": "core", "BBB": "rotating", "CCC": "exploration"},
    )
    candidate_order = [(c.symbol, c.layer, c.shallow_score) for c in plan["candidates"]]
    assert candidate_order == [
        ("BBB", "rotating", 0.35),
        ("CCC", "exploration", 0.3),
    ]
    assert plan["ranked_count"] == 2
    assert plan["deep_eval_limit"] == 1
    assert plan["exploration_quota_target"] == 1
    assert plan["exploration_quota_used"] == 1
    assert plan["shortlist_symbols"] == ("BBB",)
    assert plan["core_rescue_applied"] is False
    assert plan["shallow_cap_applied"] is True
    assert plan["shallow_cap_original_count"] == 3
    assert plan["shallow_cap_limit"] == 2
