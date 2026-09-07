from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

_IMPORT_ENV = {
    "KIS_TRADER_DISABLE_DOTENV": "1",
    "KIS_TRADER_DISABLE_CREDENTIAL_FILES": "1",
    "KIS_APP_KEY": "test-key",
    "KIS_APP_SECRET": "test-secret",
    "KIS_BASE_URL": "https://openapivts.koreainvestment.com:9443",
    "KIS_CANO": "00000000",
    "KIS_ACNT_PRDT_CD": "01",
}
_ORIGINAL_IMPORT_ENV = {key: os.environ.get(key) for key in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

from app import main as main_module
from app.auth.token import ApiHttpError
from app.core.market_session import MarketSessionStatus
from app.market_data.schema import MarketSnapshot
from app.pipeline import BUY_SCAN_LANE_CONTROLLER, BuyScanLaneController
import app.pipeline.buy_lane as buy_lane_module
from app.pipeline.buy_lane import BuyLane, LiveQuoteLane
from app.pipeline.cycle_budget import LaneBudget
from app.pipeline.intents import BuyIntent
import app.pipeline.lane_scheduler as scheduler_module
import app.pipeline.runtime_adapters as adapter_module
from app.scanner.models import SymbolAnalysisResult
from app.scanner.quote_account import BuyScanQuotePrefetchResult
from app.strategy.buy_decision import BuyDecision
from app.strategy.schema import BuyDecisionSummary, StrategyEvaluationResult

for _key, _value in _ORIGINAL_IMPORT_ENV.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _Settings(SimpleNamespace):
    def __getattr__(self, name: str):
        defaults = {
            "run_mode": "mock",
            "confirm_buy": "NO",
            "lane_scheduler_enabled": True,
            "order_gate_enabled": True,
            "enable_sell_guard_selftest": False,
            "enable_sell_test_scenarios": False,
            "sell_test_mode": "off",
            "session_cycle_hard_budget_seconds": 60.0,
            "sell_check_interval_seconds": 30,
            "buy_scan_interval_seconds": 60,
            "buy_scan_min_remaining_budget_seconds": 5.0,
            "buy_scan_quote_prefetch_deadline_seconds": 18.0,
            "buy_scan_quote_request_timeout_seconds": 2.0,
            "buy_scan_quote_max_attempts": 1,
            "buy_scan_total_budget_seconds": 25.0,
            "scan_symbols_max_per_cycle": 200,
            "target_symbols": ("005930",),
            "buy_scan_top_k_candidates": 5,
            "enable_daily_pnl_brake": False,
            "buy_max_budget_per_trade_krw": 1_000_000,
            "buy_max_account_exposure_pct": 100.0,
            "buy_max_qty_per_trade": 10,
            "rebuy_cooldown_minutes": 0,
            "same_symbol_max_buys_per_day": 10,
            "buy_daily_max_order_submissions": 10,
            "buy_enable_risk_guards": False,
            "sell_daily_max_order_submissions": 10,
            "sell_daily_max_notional_krw": 10_000_000,
            "sell_enable": True,
            "sell_stop_loss_pct": 5.0,
            "sell_take_profit_pct": 50.0,
            "sell_trailing_stop_pct": 50.0,
            "sell_rule_enable_live_leadership_loss": False,
            "sell_rule_enable_live_power_breakdown": False,
            "sell_exit_required_pass_count": 1,
            "block_resell_symbols_sold_today": False,
            "enable_sell_cooldown": False,
            "allow_one_sell_trigger_per_symbol_per_day": False,
            "sell_blocked_cooldown_minutes": 0,
            "order_cooldown_minutes": 0,
            "buy_fee_bps": 0.0,
            "buy_slippage_bps": 0.0,
            "sell_fee_bps": 0.0,
            "sell_tax_bps": 0.0,
            "sell_slippage_bps": 0.0,
            "use_cost_aware_pnl": False,
            "qty": 1,
            "buy_block_on_blocked_preview": False,
            "enable_rebalance_sell": False,
            "strict_sell_first": False,
            "target_symbols_source": "test",
            "target_symbols_raw": "005930",
            "target_symbols_split_items": ("005930",),
        }
        if name in defaults:
            return defaults[name]
        return False


def _settings(**overrides: object) -> _Settings:
    return _Settings(**overrides)


def _runtime_state() -> dict[str, object]:
    return {
        "recent_orders": [],
        "buy_entries_by_symbol_today": {},
        "last_buy_entry_at_by_symbol": {},
        "buy_attempted_symbols_today": [],
        "buy_blocked_symbols_today": [],
        "blocked_buy_symbols_today": [],
        "buy_cooldown_blocked_symbols_today": [],
        "buy_untradable_symbols_today": [],
        "sell_triggered_symbols_today": [],
        "sell_blocked_symbols_today": [],
        "blocked_sell_symbols_today": [],
        "sell_cooldown_blocked_symbols_today": [],
        "symbols_sold_today": [],
        "symbols_bought_today": [],
    }


def _session() -> MarketSessionStatus:
    return MarketSessionStatus(
        session="REGULAR",
        order_allowed=True,
        reason="regular",
        buy_block_action=None,
        sell_block_action=None,
    )


def _balance_response() -> dict[str, object]:
    return {
        "rt_cd": "0",
        "output1": [
            {
                "pdno": "000660",
                "prdt_name": "SK하이닉스",
                "hldg_qty": "10",
                "pchs_avg_pric": "10000",
                "prpr": "9000",
                "evlu_amt": "90000",
                "evlu_pfls_amt": "-10000",
                "evlu_pfls_rt": "-10.0",
            }
        ],
        "output2": [
            {
                "dnca_tot_amt": "1000000",
                "prvs_rcdl_excc_amt": "1000000",
                "nxdy_excc_amt": "1000000",
                "tot_evlu_amt": "1090000",
            }
        ],
    }


def _balance_response_many(count: int) -> dict[str, object]:
    return {
        **_balance_response(),
        "output1": [
            {
                "pdno": f"{index:06d}",
                "prdt_name": f"보유{index}",
                "hldg_qty": "10",
                "pchs_avg_pric": "10000",
                "prpr": "9000",
                "evlu_amt": "90000",
                "evlu_pfls_amt": "-10000",
                "evlu_pfls_rt": "-10.0",
            }
            for index in range(count)
        ],
    }


def _quote_response(symbol: str = "000660") -> dict[str, object]:
    return {
        "rt_cd": "0",
        "output": {
            "stck_shrn_iscd": symbol,
            "stck_prpr": "9000",
            "stck_oprc": "10000",
            "stck_hgpr": "10100",
            "stck_lwpr": "8900",
            "prdy_ctrt": "-10.0",
        },
    }


def _prefetch_result(symbols: tuple[str, ...]) -> BuyScanQuotePrefetchResult:
    return BuyScanQuotePrefetchResult(
        price_data_by_symbol={
            symbol: {
                "rt_cd": "0",
                "output": {
                    "stck_shrn_iscd": symbol,
                    "stck_prpr": "10000",
                    "stck_oprc": "9900",
                    "stck_hgpr": "10100",
                    "stck_lwpr": "9800",
                    "prdy_ctrt": "1.0",
                },
            }
            for symbol in symbols
        },
        requested_symbols=symbols,
        completed_symbols=symbols,
        failed_symbols=(),
        quote_account_mode="read_only_quote_account",
        quote_account_env="live",
        elapsed_ms=100.0,
        quote_response_ms=100.0,
        throttle_sleep_ms=0.0,
        skipped_deadline_symbols=(),
        budget_skipped_symbols=(),
        deadline_hit=False,
        deadline_seconds=18.0,
        success_ratio=1.0,
        request_timeout_seconds=2.0,
        max_attempts=1,
        timeout_count=0,
    )


def _buy_result(symbol: str = "005930", *, candidate: bool = True) -> SymbolAnalysisResult:
    snapshot = MarketSnapshot(
        symbol=symbol,
        current_price=10000,
        open_price=9900,
        low_price=9800,
        prev_day_change_pct=1.0,
    )
    decision = BuyDecision(
        summary=BuyDecisionSummary(
            should_attempt_buy=candidate,
            passed_count=1 if candidate else 0,
            enabled_count=1,
            required_pass_count=1,
            final_reason="candidate" if candidate else "reject",
            passed_strategy_names=("rule",) if candidate else (),
            total_count=1,
        ),
        evaluation_results=(
            StrategyEvaluationResult("rule", True, candidate, "ok"),
        ),
    )
    return SymbolAnalysisResult(
        symbol=symbol,
        name="삼성전자",
        market_snapshot=snapshot,
        strategy_result=decision,
        passed_count=1 if candidate else 0,
        signal_quality_count=1.0 if candidate else 0.0,
        enabled_count=1,
        candidate=candidate,
        final_reason="candidate" if candidate else "reject",
        passed_pattern="Y" if candidate else "N",
        score=100.0 if candidate else 0.0,
        net_profit_buffer_bps=100.0,
        passes_profit_buffer=True,
        score_components={},
        score_highlights=("rule",) if candidate else (),
        score_penalties=(),
        score_summary="score",
        expected_fee_krw=0,
        expected_tax_krw=0,
        expected_slippage_krw=0,
        expected_total_cost_krw=0,
        expected_cost_bps=0.0,
        cost_quality_score=1.0,
        expected_cost_penalty=0.0,
        net_edge_bps=100.0,
        cost_block_reason=None,
        feature_map={},
        feature_vector={},
        feature_summaries={},
        math_score_summary="math",
        mean_reversion_zscore=None,
        reversion_quality_score=None,
        overextension_penalty=None,
        ou_half_life_estimate=None,
        mean_reversion_summary=None,
        portfolio_avg_correlation=None,
        portfolio_max_correlation=None,
        variance_increase_estimate=None,
        portfolio_risk_summary=None,
        portfolio_correlation_penalty=None,
        variance_increase_penalty=None,
        hist_percentile_rank=None,
        price_velocity_pct=None,
        price_dynamics_summary=None,
        sort_key=(0 if candidate else 1, -100.0 if candidate else 0.0, symbol),
    )


def _patch_boundaries(
    monkeypatch,
    *,
    orders: list[str],
    clock: _FakeClock | None = None,
    buy_candidate: bool = True,
    sell_flow_delay: float = 0.0,
    mock_order_flows: bool = True,
) -> None:
    for key, value in _IMPORT_ENV.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(adapter_module, "get_korean_market_session", _session)
    monkeypatch.setattr(adapter_module, "issue_access_token", lambda: "dry-token")
    monkeypatch.setattr(adapter_module, "inquire_balance", lambda token=None: _balance_response())

    def inquire_price(symbol: str, token: str | None = None):
        if clock is not None:
            clock.advance(0.01)
        return _quote_response(symbol)

    monkeypatch.setattr(adapter_module, "inquire_price", inquire_price)
    monkeypatch.setattr(
        scheduler_module,
        "_prefetch_buy_scan_prices",
        lambda *, symbols, **_kwargs: _prefetch_result(tuple(symbols)),
    )
    monkeypatch.setattr(
        adapter_module,
        "scan_target_symbols",
        lambda **_kwargs: (_buy_result(candidate=buy_candidate),),
    )

    if mock_order_flows:
        def sell_flow(**kwargs):
            if sell_flow_delay:
                time.sleep(sell_flow_delay)
            orders.append(f"SELL:{kwargs['analysis'].symbol}")
            return True

        def buy_flow(**kwargs):
            orders.append(f"BUY:{kwargs['selected_candidate'].symbol}")
            return SimpleNamespace(
                execution_snapshot=None,
                position_sizing=None,
                sell_position_sizing=None,
                buy_risk_guard_payload=None,
                sell_risk_guard_payload=None,
                rebalance_buy_preview=None,
                quality_rebalance_preview=None,
                active_sell_analysis=None,
                rebalance_selection_ms=None,
                execution_tail_backoff_drain_ms=0.0,
                rate_limit_source=None,
            )

        monkeypatch.setattr(adapter_module._sell_flow, "run_sell_order_flow", sell_flow)
        monkeypatch.setattr(adapter_module._buy_flow, "run_buy_order_flow", buy_flow)
        return

    def no_order_api(*_args, **_kwargs):
        raise AssertionError("final order API must not be called in preview tests")

    def sell_log_event(**kwargs):
        orders.append(f"SELL_LOG:{kwargs.get('action')}")

    def buy_log_event(**kwargs):
        orders.append(f"BUY_LOG:{kwargs.get('action')}")

    monkeypatch.setattr(adapter_module._sell_flow, "sell_market", no_order_api)
    monkeypatch.setattr(adapter_module._buy_flow, "buy_market", no_order_api)
    monkeypatch.setattr(adapter_module._sell_flow, "log_order_event", sell_log_event)
    monkeypatch.setattr(adapter_module._buy_flow, "log_order_event", buy_log_event)
    monkeypatch.setattr(
        "app.risk.guards.count_today_sell_order_submissions",
        lambda strict=False: 0,
    )
    monkeypatch.setattr(
        "app.risk.guards.count_today_buy_order_submissions",
        lambda strict=False: 0,
    )


def test_run_lane_scheduler_cycle_uses_real_default_adapters_without_hooks(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    scheduler_state = {
        "cycle_id": "no-hook",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert "lane_scheduler_hooks" not in scheduler_state
    assert result.sell_result.intent is not None
    assert result.buy_result.intent is not None
    assert result.telemetry["adapter_source"] == "default"
    assert result.telemetry["handler_source"] == "default"


def test_no_hook_scheduler_caps_sell_watch_quotes_to_budget(monkeypatch) -> None:
    orders: list[str] = []
    quote_calls: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)
    monkeypatch.setattr(
        adapter_module,
        "inquire_balance",
        lambda token=None: _balance_response_many(38),
    )

    def inquire_price(symbol: str, token: str | None = None):
        quote_calls.append(symbol)
        return _quote_response(symbol)

    monkeypatch.setattr(adapter_module, "inquire_price", inquire_price)

    api_budget_state = {
        "recent_requests": [],
        "quotes_used_this_tick": 0,
        "soft_max_quotes_per_tick": 20,
        "soft_max_requests_per_second": 4,
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(
            api_soft_max_quotes_per_tick=20,
            api_soft_max_requests_per_second=4,
            api_buy_scan_min_quote_reserve=4,
        ),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state={
            "cycle_id": "sell-cap",
            "lane_scheduler_runtime_context": {
                "state": _runtime_state(),
                "timing_summary": {},
            },
        },
        api_budget_state=api_budget_state,
    )

    assert len(quote_calls) == 13
    assert result.telemetry["sell_evaluated_count"] == 13
    assert result.telemetry["sell_watch_total_holdings"] == 38
    assert result.telemetry["sell_watch_partial"] is True
    assert result.telemetry["sell_watch_budget_plan_limit"] == 13
    assert api_budget_state["quotes_used_this_tick"] == 13


def test_no_hook_scheduler_exhausted_quote_budget_limits_sell_watch(monkeypatch) -> None:
    """F5: a fully-exhausted configured quote budget must NOT be treated like an
    unconfigured budget. Zero quotes remaining -> zero evaluations and a
    'sell_watch_budget_limited' skip, not a fake full budget."""
    orders: list[str] = []
    quote_calls: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)
    monkeypatch.setattr(
        adapter_module,
        "inquire_balance",
        lambda token=None: _balance_response_many(38),
    )

    def inquire_price(symbol: str, token: str | None = None):
        quote_calls.append(symbol)
        return _quote_response(symbol)

    monkeypatch.setattr(adapter_module, "inquire_price", inquire_price)

    api_budget_state = {
        "recent_requests": [],
        "quotes_used_this_tick": 20,
        "soft_max_quotes_per_tick": 20,
        "soft_max_requests_per_second": 4,
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(
            api_soft_max_quotes_per_tick=20,
            api_soft_max_requests_per_second=4,
            api_buy_scan_min_quote_reserve=4,
        ),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state={
            "cycle_id": "sell-exhausted",
            "lane_scheduler_runtime_context": {
                "state": _runtime_state(),
                "timing_summary": {},
            },
        },
        api_budget_state=api_budget_state,
    )

    assert quote_calls == []
    assert result.telemetry["sell_watch_budget_plan_limit"] == 0
    assert result.sell_result.skipped_reason == "sell_watch_budget_limited"
    assert result.telemetry["sell_watch_partial"] is True


def test_no_hook_scheduler_sell_watch_rate_limit_does_not_escape(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    def rate_limited_quote(symbol: str, token: str | None = None):
        raise ApiHttpError(
            "현재가 조회 HTTP 500: {'msg_cd': 'EGW00201', 'msg1': '초당 거래건수를 초과하였습니다.'}",
            500,
            {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
        )

    monkeypatch.setattr(adapter_module, "inquire_price", rate_limited_quote)
    api_budget_state = {
        "recent_requests": [],
        "quotes_used_this_tick": 0,
        "soft_max_quotes_per_tick": 20,
        "soft_max_requests_per_second": 4,
    }

    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(
            api_soft_max_quotes_per_tick=20,
            api_soft_max_requests_per_second=4,
            api_buy_scan_min_quote_reserve=4,
        ),
        sell_check_due=True,
        buy_scan_due=False,
        scheduler_state={
            "cycle_id": "sell-rate-limit",
            "lane_scheduler_runtime_context": {
                "state": _runtime_state(),
                "timing_summary": {},
            },
        },
        api_budget_state=api_budget_state,
    )

    assert orders == []
    assert result.telemetry["rate_limit_triggered"] is True
    assert result.telemetry["rate_limit_source"] == "sell_watch"
    assert result.sell_result.skipped_reason == "no_sell_candidate"


def test_run_lane_scheduler_cycle_default_handlers_run_guarded_preview_flows(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders, mock_order_flows=False)

    scheduler_state = {
        "cycle_id": "guarded-preview",
        "lane_scheduler_runtime_context": {"state": _runtime_state(), "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert "lane_scheduler_hooks" not in scheduler_state
    assert result.telemetry["adapter_source"] == "default"
    assert result.telemetry["handler_source"] == "default"
    assert result.telemetry["order_gate_order"] == ("SELL:000660", "BUY:005930")
    assert "SELL_LOG:sell_preview_only" in orders
    assert "BUY_LOG:blocked_confirm_buy_off" in orders


def test_run_lane_scheduler_cycle_sell_only_uses_default_sell_flow(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    scheduler_state = {
        "cycle_id": "sell-only",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=False,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert "lane_scheduler_hooks" not in scheduler_state
    assert result.sell_result.intent is not None
    assert result.buy_result.intent is None
    assert result.buy_result.skipped_reason == "cadence_not_reached"
    assert orders == ["SELL:000660"]


def test_run_lane_scheduler_cycle_sell_intent_has_ttl_expiry(monkeypatch) -> None:
    """S2 Change A: the production SELL intent factory must set an ``expires_at``
    TTL (like the BUY factory) so a stale analysis-based SELL cannot stay valid
    indefinitely."""
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    scheduler_state = {
        "cycle_id": "sell-ttl",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=False,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    sell_intent = result.sell_result.intent
    assert sell_intent is not None
    assert sell_intent.expires_at is not None
    assert sell_intent.expires_at > sell_intent.created_at


def test_buy_intent_dropped_by_expired_budget_gets_gate_decision() -> None:
    """S2 Change B: when the cycle budget expires between buy-lane completion and
    order-gate assembly, the fresh BuyIntent must NOT be silently dropped — it is
    handed to the gate, which records an explicit ``budget_exceeded`` decision."""
    clock = _FakeClock()

    def prefetch_func(*, symbols, **_kwargs):
        return _prefetch_result(tuple(symbols))

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return None

    def buy_intent_factory(*, cycle_id, prefetch_result, **_kwargs):
        symbol = tuple(prefetch_result.price_data_by_symbol)[0]
        intent = BuyIntent(
            intent_id=f"{cycle_id}:buy:{symbol}",
            source_cycle_id=cycle_id,
            source_lane="buy_scan",
            symbol=symbol,
            reason="buy candidate",
            created_at=datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc),
        )
        # Exhaust the hard budget *after* the intent is built but before the
        # scheduler assembles ready_intents / calls the order gate.
        clock.advance(120.0)
        return intent

    scheduler_state = {
        "cycle_id": "buy-budget-expired",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "clock": clock,
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(session_cycle_hard_budget_seconds=60.0),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.intent is not None
    buy_decisions = [
        decision
        for decision in result.order_gate_result.decisions
        if decision.intent_type == "BUY"
    ]
    assert len(buy_decisions) == 1
    assert buy_decisions[0].status == "budget_exceeded"


def test_run_lane_scheduler_cycle_buy_only_uses_default_buy_flow(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    scheduler_state = {
        "cycle_id": "buy-only",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=False,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert "lane_scheduler_hooks" not in scheduler_state
    assert result.sell_result.intent is None
    assert result.sell_result.skipped_reason == "cadence_not_reached"
    assert result.buy_result.intent is not None
    assert orders == ["BUY:005930"]


def test_buy_only_cycle_propagates_buy_factory_rate_limit_to_telemetry(monkeypatch) -> None:
    """F3(b): buy-only cycle — buy factory writes rate-limit keys to the shared
    context; scheduler telemetry (read from the original context) must see them."""

    def prefetch_func(*, symbols, **_kwargs):
        return _prefetch_result(tuple(symbols))

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return None

    def buy_intent_factory(*, cycle_id, context, **_kwargs):
        context["rate_limit_triggered"] = True
        context["rate_limit_source"] = "balance"
        context["buy_skipped_reason"] = "balance_rate_limit"
        return None

    scheduler_state = {
        "cycle_id": "buy-only-rate-limit",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=False,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.telemetry["rate_limit_triggered"] is True
    assert result.telemetry["rate_limit_source"] == "balance"
    assert result.buy_result.skipped_reason == "balance_rate_limit"


def test_buy_intent_payload_reflects_sell_watch_partial_from_shared_context(monkeypatch) -> None:
    """F6: the production buy factory must read sell_watch_partial from the shared
    context (marked by the sell lane in the same cycle), not hardcode it False."""
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    def sell_intent_factory(*, cycle_id, context, **_kwargs):
        context["sell_watch_partial"] = True
        context["sell_watch_partial_reason"] = "test partial"
        return None

    scheduler_state = {
        "cycle_id": "partial-buy",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "sell_intent_factory": sell_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.intent is not None
    payload = result.buy_result.intent.payload
    assert payload["sell_watch_partial"] is True
    assert payload["sell_watch_partial_reason"] == "test partial"


def test_full_cycle_no_candidate_reports_no_buy_candidate_skip_reason(monkeypatch) -> None:
    """F3(a): with sell running first, the buy factory writes buy_skipped_reason
    into the shared context; BuyLane.run must surface it (not fall back to
    'no_buy_intent')."""
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders, buy_candidate=False)

    scheduler_state = {
        "cycle_id": "no-candidate",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.intent is None
    assert result.buy_result.skipped_reason == "no_buy_candidate"


def test_expired_budget_during_sell_lane_reports_cycle_budget_exceeded(monkeypatch) -> None:
    """F4: when the hard budget is exhausted during the sell lane, the buy skip
    reason must be 'cycle_budget_exceeded' (not 'cadence_not_reached'), and the
    quote prefetch must never run."""
    clock = _FakeClock()

    def prefetch_func(**_kwargs):
        raise AssertionError("prefetch must not run after budget expiry")

    def sell_intent_factory(*, cycle_id, **_kwargs):
        clock.advance(0.11)  # past the 0.05s hard budget
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        raise AssertionError("buy factory must not run after budget expiry")

    scheduler_state = {
        "cycle_id": "budget-expired",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "clock": clock,
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(session_cycle_hard_budget_seconds=0.05),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.due is True
    assert result.buy_result.skipped_reason == "cycle_budget_exceeded"


def test_run_lane_scheduler_cycle_low_budget_skips_buy_without_prefetch(monkeypatch) -> None:
    orders: list[str] = []
    clock = _FakeClock()
    _patch_boundaries(monkeypatch, orders=orders, clock=clock)
    monkeypatch.setattr(scheduler_module.time, "perf_counter", clock)

    def slow_inquire_price(symbol: str, token: str | None = None):
        clock.advance(59.95)
        return _quote_response(symbol)

    monkeypatch.setattr(adapter_module, "inquire_price", slow_inquire_price)
    monkeypatch.setattr(
        scheduler_module,
        "_prefetch_buy_scan_prices",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("prefetch skipped")),
    )

    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state={
            "cycle_id": "low-budget",
            "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        },
        api_budget_state={},
    )

    assert result.sell_result.intent is not None
    assert result.buy_result.intent is None
    assert result.buy_result.skipped_reason == "cycle_budget_low"
    assert result.telemetry["budget_skip_stage"] == "before_buy_scan"
    assert orders == ["SELL:000660"]


def test_run_lane_scheduler_cycle_slow_sell_handler_times_out_by_default(monkeypatch) -> None:
    orders: list[str] = []
    clock = _FakeClock()
    handler_started = threading.Event()
    release_handler = threading.Event()
    handler_finished = threading.Event()
    _patch_boundaries(monkeypatch, orders=orders, clock=clock)
    monkeypatch.setattr(scheduler_module.time, "perf_counter", clock)

    def blocking_sell_flow(**kwargs):
        handler_started.set()
        release_handler.wait(timeout=1.0)
        orders.append(f"SELL:{kwargs['analysis'].symbol}")
        handler_finished.set()
        return True

    monkeypatch.setattr(
        adapter_module._sell_flow,
        "run_sell_order_flow",
        blocking_sell_flow,
    )

    try:
        result = scheduler_module.run_lane_scheduler_cycle(
            _settings(session_cycle_hard_budget_seconds=0.06),
            sell_check_due=True,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "slow-sell",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
            },
            api_budget_state={},
        )

        assert handler_started.wait(timeout=0.2)
        assert result.telemetry["handler_source"] == "default"
        assert result.sell_result.intent is not None
        assert result.buy_result.intent is None
        assert result.buy_result.skipped_reason == "cycle_budget_low"
        assert result.order_gate_result.last_decision is not None
        assert result.order_gate_result.last_decision.status == "handler_timeout"
        assert (
            result.telemetry["order_gate_budget_exceeded_stage"]
            == "order_gate_handler_timeout"
        )
        assert result.telemetry["budget_skip_stage"] == "before_buy_scan"
        assert orders == []
    finally:
        release_handler.set()
        if handler_started.is_set():
            assert handler_finished.wait(timeout=1.0)


def test_run_lane_scheduler_cycle_stale_worker_telemetry_without_hooks(monkeypatch) -> None:
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)
    BUY_SCAN_LANE_CONTROLLER.reset_for_tests()
    future: Future = Future()

    class FakeExecutor:
        def submit(self, *_args, **_kwargs):
            return future

        def shutdown(self, **_kwargs) -> None:
            return None

    BUY_SCAN_LANE_CONTROLLER.start_quote_prefetch(
        scan_id="stuck-1",
        symbols=("005930",),
        settings=_settings(),
        execution_token="dry-token",
        now=datetime(2026, 6, 28, 8, 59, tzinfo=timezone.utc),
        executor_factory=lambda **_kwargs: FakeExecutor(),
        max_worker_ttl_seconds=0.0,
    )

    try:
        result = scheduler_module.run_lane_scheduler_cycle(
            _settings(),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "stale-worker",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
            },
            api_budget_state={},
        )
    finally:
        BUY_SCAN_LANE_CONTROLLER.reset_for_tests()

    assert result.buy_result.skipped_reason == "previous_scan_worker_stale"
    assert result.telemetry["worker_ttl_expired"] is True
    assert result.telemetry["stale_worker_recovered"] is True
    assert result.telemetry["buy_scan_guard_state"]["stale"] is True


def test_main_run_cycle_no_hook_scheduler_persists_normal_state(monkeypatch) -> None:
    orders: list[str] = []
    saved_states: list[dict[str, object]] = []
    built_snapshots: list[dict[str, object]] = []
    _patch_boundaries(monkeypatch, orders=orders)

    monkeypatch.setattr(main_module, "sync_account_scope_meta", lambda settings: {})
    monkeypatch.setattr(
        main_module,
        "get_account_scope_context",
        lambda settings=None: {
            "account_signature": "mock-test",
            "account_environment": "mock",
            "masked_account_display": "0000-01",
        },
    )
    monkeypatch.setattr(main_module, "load_runtime_state", lambda: {})
    monkeypatch.setattr(main_module, "save_runtime_state", lambda state: saved_states.append(dict(state)) or True)
    monkeypatch.setattr(main_module, "_write_slack_runtime_status_snapshot", lambda **_kwargs: True)
    monkeypatch.setattr(main_module, "_run_cycle_market_data_quality_sentinel", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_resolve_benchmark_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "_build_buy_cycle_funnel_stats", lambda **_kwargs: {})
    monkeypatch.setattr(main_module, "_build_buy_candidate_outcome_records", lambda **_kwargs: [])
    monkeypatch.setattr(main_module, "append_candidate_outcomes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "append_cycle_stats", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "build_daily_summary", lambda: {})
    monkeypatch.setattr(main_module, "build_daily_summary_console_lines", lambda _summary: [])
    monkeypatch.setattr(main_module, "build_cycle_stats_daily_summary", lambda: {})
    monkeypatch.setattr(main_module, "build_cycle_stats_console_lines", lambda _summary: [])
    monkeypatch.setattr(main_module, "get_runtime_state_path", lambda settings=None: "runtime.json")
    monkeypatch.setattr(main_module, "get_cycle_snapshots_path", lambda settings=None: "cycle.jsonl")
    monkeypatch.setattr(main_module, "_print_runtime_mode", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_applied_settings", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_test_mode", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_cycle_header", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_engine_schedule_state", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "_print_cycle_timing", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "_print_api_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "_print_sell_metrics", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "_print_buy_scan_metrics", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "_print_runtime_state_summary", lambda *_args, **_kwargs: None)

    def fake_build_cycle_snapshot(**kwargs):
        built_snapshots.append(kwargs)
        return {
            "timing_summary": dict(kwargs["timing_summary"]),
            "scheduler_state": dict(kwargs["scheduler_state"]),
        }

    monkeypatch.setattr(main_module, "build_cycle_snapshot", fake_build_cycle_snapshot)
    monkeypatch.setattr(main_module, "persist_cycle_snapshot", lambda snapshot: True)

    scheduler_state = {"decision": "NO_HOOK_TEST"}
    main_module.run_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert "lane_scheduler_hooks" not in scheduler_state
    assert orders == ["SELL:000660", "BUY:005930"]
    assert scheduler_state["lane_scheduler_entered"] is True
    assert saved_states
    assert saved_states[-1]["lane_scheduler_enabled"] is True
    assert saved_states[-1]["last_timing_summary"]["adapter_source"] == "default"
    assert saved_states[-1]["last_timing_summary"]["handler_source"] == "default"
    assert saved_states[-1]["last_timing_summary"]["order_gate_order"] == (
        "SELL:000660",
        "BUY:005930",
    )
    assert built_snapshots
    assert built_snapshots[-1]["timing_summary"]["lane_scheduler_enabled"] is True


def _fresh_buy_lane(prefetch_func) -> tuple[BuyLane, BuyScanLaneController]:
    controller = BuyScanLaneController(clock=_FakeClock())
    quote_lane = LiveQuoteLane(prefetch_func=prefetch_func)
    buy_lane = BuyLane(controller=controller, quote_lane=quote_lane, intent_factory=None)
    return buy_lane, controller


def _run_buy_lane(buy_lane: BuyLane):
    clock = _FakeClock()
    budget = LaneBudget(started_at=0.0, hard_budget_seconds=60.0, clock=clock)
    return buy_lane.run(
        due=True,
        cycle_id="f7",
        symbols=("005930",),
        settings=_settings(),
        execution_token="dry-token",
        budget=budget,
        now=datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc),
        context={},
    )


def test_buy_lane_reraises_keyboard_interrupt_and_releases_guard() -> None:
    """F7: BuyLane.run must NOT swallow KeyboardInterrupt/SystemExit; it re-raises
    while still releasing the scan guard via the finally block."""

    def prefetch_func(**_kwargs):
        raise KeyboardInterrupt("ctrl-c during scan")

    buy_lane, controller = _fresh_buy_lane(prefetch_func)

    with pytest.raises(KeyboardInterrupt):
        _run_buy_lane(buy_lane)

    assert controller.guard.running is False


def test_buy_lane_converts_runtime_error_to_prefetch_exception() -> None:
    """F7 companion: a plain Exception still becomes a 'prefetch_exception' result
    (not re-raised) and releases the guard."""

    def prefetch_func(**_kwargs):
        raise RuntimeError("boom")

    buy_lane, controller = _fresh_buy_lane(prefetch_func)

    result = _run_buy_lane(buy_lane)

    assert result.skipped_reason == "prefetch_exception"
    assert controller.guard.running is False


def test_s4_overlap_flag_off_never_starts_async_prefetch(monkeypatch) -> None:
    """S4 t1: with ``buy_scan_prefetch_overlap_enabled`` at its default (False),
    the scheduler must take the byte-for-byte synchronous path — it must NEVER
    call the async controller ``start_quote_prefetch`` — and the produced order
    decisions are identical to today."""
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    def _forbidden_start(*_args, **_kwargs):
        raise AssertionError(
            "start_quote_prefetch must NOT be called when overlap flag is off"
        )

    monkeypatch.setattr(
        BuyScanLaneController, "start_quote_prefetch", _forbidden_start
    )

    scheduler_state = {
        "cycle_id": "s4-off",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.sell_result.intent is not None
    assert result.buy_result.intent is not None
    assert result.telemetry["order_gate_order"] == ("SELL:000660", "BUY:005930")
    assert orders == ["SELL:000660", "BUY:005930"]


def test_s4_overlap_flag_on_prefetch_starts_before_sell_eval(monkeypatch) -> None:
    """S4 t2: with overlap enabled, the background BUY quote prefetch must START
    before the SELL lane finishes evaluating (real overlap), and the cycle still
    produces the BUY intent. Evidence: the prefetch worker sets its start event
    and the SELL factory observes it already set; total elapsed < sync-sum."""
    BUY_SCAN_LANE_CONTROLLER.reset_for_tests()
    prefetch_entered = threading.Event()
    prefetch_release = threading.Event()
    observations: dict[str, object] = {}

    def prefetch_func(*, symbols, **_kwargs):
        prefetch_entered.set()
        # Simulate quote-lane latency overlapping with SELL evaluation.
        prefetch_release.wait(timeout=1.0)
        return _prefetch_result(tuple(symbols))

    def sell_intent_factory(*, cycle_id, **_kwargs):
        # The prefetch worker must already be in flight while SELL evaluates.
        observations["prefetch_in_flight_during_sell"] = prefetch_entered.wait(
            timeout=1.0
        )
        prefetch_release.set()
        return None

    def buy_intent_factory(*, cycle_id, prefetch_result, **_kwargs):
        symbol = tuple(prefetch_result.price_data_by_symbol)[0]
        return BuyIntent(
            intent_id=f"{cycle_id}:buy:{symbol}",
            source_cycle_id=cycle_id,
            source_lane="buy_scan",
            symbol=symbol,
            reason="buy candidate",
            created_at=datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc),
        )

    scheduler_state = {
        "cycle_id": "s4-on-overlap",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    try:
        result = scheduler_module.run_lane_scheduler_cycle(
            _settings(buy_scan_prefetch_overlap_enabled=True),
            sell_check_due=True,
            buy_scan_due=True,
            scheduler_state=scheduler_state,
            api_budget_state={},
        )
    finally:
        prefetch_release.set()
        BUY_SCAN_LANE_CONTROLLER.reset_for_tests()

    assert observations["prefetch_in_flight_during_sell"] is True
    assert result.buy_result.intent is not None
    assert result.buy_result.intent.symbol == "005930"
    assert float(result.telemetry["buy_quote_prefetch_join_wait_ms"]) >= 0.0


def test_s4_overlap_flag_on_join_timeout_detaches_and_blocks_next_cycle(monkeypatch) -> None:
    """S4 t3: with overlap on, if the background prefetch never completes within
    the budget-derived join timeout, the cycle produces NO BuyIntent, the guard
    stays held (worker detached), telemetry marks the detach, and the NEXT cycle
    is skipped with ``previous_scan_running`` (controller timeout semantics reused,
    not reimplemented)."""
    clock = _FakeClock()
    never_future: Future = Future()

    class FakeExecutor:
        def submit(self, *_args, **_kwargs):
            return never_future

        def shutdown(self, **_kwargs) -> None:
            return None

    class _StuckExecutorController(BuyScanLaneController):
        """Injects a never-resolving worker so the scheduler's own
        ``start_quote_prefetch`` call produces a join timeout (the default
        ``executor_factory`` is bound at def-time, so patching the module symbol
        would not reach it — override the call site instead)."""

        def start_quote_prefetch(self, **kwargs):
            kwargs.setdefault("executor_factory", lambda **_kw: FakeExecutor())
            # Large TTL so the detached worker stays "running" (not TTL-stale)
            # across both cycles -> next cycle sees previous_scan_running.
            kwargs.setdefault("max_worker_ttl_seconds", 10_000.0)
            return super().start_quote_prefetch(**kwargs)

    controller = _StuckExecutorController()

    def sell_intent_factory(*, cycle_id, **_kwargs):
        # Burn the cycle budget down to just above the low-budget floor (5s) so
        # the buy lane still takes the overlap-join branch but the budget-derived
        # join timeout is ~0.05s -> the never-resolving future times out fast.
        clock.advance(54.95)
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        raise AssertionError("buy factory must not run on a timed-out prefetch")

    try:
        result = scheduler_module.run_lane_scheduler_cycle(
            _settings(
                buy_scan_prefetch_overlap_enabled=True,
                session_cycle_hard_budget_seconds=60.0,
            ),
            sell_check_due=True,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "s4-timeout-1",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
                "lane_scheduler_hooks": {
                    "clock": clock,
                    "buy_controller": controller,
                    "sell_intent_factory": sell_intent_factory,
                    "buy_intent_factory": buy_intent_factory,
                },
            },
            api_budget_state={},
        )

        assert result.buy_result.intent is None
        assert result.telemetry["buy_quote_prefetch_worker_detached"] is True
        assert controller.running is True

        # Second cycle: guard still held by the detached worker -> skipped.
        result2 = scheduler_module.run_lane_scheduler_cycle(
            _settings(
                buy_scan_prefetch_overlap_enabled=True,
                session_cycle_hard_budget_seconds=60.0,
            ),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "s4-timeout-2",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
                "lane_scheduler_hooks": {
                    "clock": clock,
                    "buy_controller": controller,
                },
            },
            api_budget_state={},
        )
        assert result2.buy_result.intent is None
        assert result2.buy_result.skipped_reason == "previous_scan_running"
    finally:
        never_future.set_result(None)
        controller.reset_for_tests()


def test_s5_buy_scan_exception_surfaced_in_telemetry(monkeypatch) -> None:
    """S5 item 1: when the buy lane catches a prefetch exception,
    ``buy_result.exception`` is set; the scheduler telemetry must surface it as a
    string under ``buy_scan_exception``. A normal cycle keeps the key at None."""

    def raising_prefetch_func(**_kwargs):
        raise RuntimeError("prefetch blew up")

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        raise AssertionError("buy factory must not run after a prefetch exception")

    scheduler_state = {
        "cycle_id": "s5-buy-exc",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "prefetch_func": raising_prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.skipped_reason == "prefetch_exception"
    assert result.buy_result.exception is not None
    assert "prefetch blew up" in str(result.telemetry["buy_scan_exception"])


def test_s5_buy_scan_exception_none_on_normal_cycle(monkeypatch) -> None:
    """S5 item 1 companion: a normal successful cycle leaves ``buy_scan_exception``
    at None (no buy-lane exception occurred)."""
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    scheduler_state = {
        "cycle_id": "s5-buy-exc-none",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert result.buy_result.intent is not None
    assert result.telemetry["buy_scan_exception"] is None


def test_s4_overlap_flag_on_expired_budget_never_starts_prefetch(monkeypatch) -> None:
    """S4 t4: with overlap enabled but the cycle budget already exhausted, the
    background prefetch must NOT be started (0 calls) and the buy lane is skipped
    with ``cycle_budget_exceeded`` — consistent with existing budget handling."""
    start_calls: list[dict[str, object]] = []
    real_start = BuyScanLaneController.start_quote_prefetch

    def spy_start(self, **kwargs):
        start_calls.append(dict(kwargs))
        return real_start(self, **kwargs)

    monkeypatch.setattr(BuyScanLaneController, "start_quote_prefetch", spy_start)

    class _ExpiredAfterStartClock:
        """Reads as 0.0 when the budget captures ``started_at``, then jumps past
        the hard deadline on every subsequent read so the budget is already
        expired by the time the overlap pre-check runs (before the SELL lane)."""

        def __init__(self) -> None:
            self._calls = 0

        def __call__(self) -> float:
            self._calls += 1
            return 0.0 if self._calls == 1 else 1_000.0

    def prefetch_func(**_kwargs):
        raise AssertionError("prefetch must not run when budget is exhausted")

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        raise AssertionError("buy factory must not run when budget is exhausted")

    scheduler_state = {
        "cycle_id": "s4-expired",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "clock": _ExpiredAfterStartClock(),
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(
            buy_scan_prefetch_overlap_enabled=True,
            session_cycle_hard_budget_seconds=60.0,
        ),
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    assert start_calls == []
    assert result.buy_result.intent is None
    assert result.buy_result.skipped_reason == "cycle_budget_exceeded"


def test_s4_overlap_budget_expires_during_sell_drains_started_prefetch(monkeypatch) -> None:
    """S4 correctness gap: with overlap on, a prefetch is STARTED before the SELL
    lane; if the budget then expires DURING the SELL lane, the buy branch skips
    with ``cycle_budget_exceeded`` — but it must first DRAIN the already-started
    prefetch. With a never-resolving worker, the zero-timeout drain marks it
    detached in telemetry (instead of leaking silently), and the next cycle
    correctly sees ``previous_scan_running``."""
    clock = _FakeClock()
    never_future: Future = Future()

    class FakeExecutor:
        def submit(self, *_args, **_kwargs):
            return never_future

        def shutdown(self, **_kwargs) -> None:
            return None

    class _StuckExecutorController(BuyScanLaneController):
        def start_quote_prefetch(self, **kwargs):
            kwargs.setdefault("executor_factory", lambda **_kw: FakeExecutor())
            kwargs.setdefault("max_worker_ttl_seconds", 10_000.0)
            return super().start_quote_prefetch(**kwargs)

    controller = _StuckExecutorController()

    def sell_intent_factory(*, cycle_id, **_kwargs):
        # Blow past the hard budget during SELL so branch 1 (cycle_budget_exceeded)
        # fires after the prefetch was already started.
        clock.advance(120.0)
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        raise AssertionError("buy factory must not run after budget expiry")

    try:
        result = scheduler_module.run_lane_scheduler_cycle(
            _settings(
                buy_scan_prefetch_overlap_enabled=True,
                session_cycle_hard_budget_seconds=60.0,
            ),
            sell_check_due=True,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "s4-drain-1",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
                "lane_scheduler_hooks": {
                    "clock": clock,
                    "buy_controller": controller,
                    "sell_intent_factory": sell_intent_factory,
                    "buy_intent_factory": buy_intent_factory,
                },
            },
            api_budget_state={},
        )

        assert result.buy_result.intent is None
        assert result.buy_result.skipped_reason == "cycle_budget_exceeded"
        # The started prefetch was drained: the never-resolving worker is marked
        # detached in this cycle's telemetry (not left silently in-flight).
        assert result.telemetry["buy_quote_prefetch_worker_detached"] is True
        assert controller.running is True

        # Next cycle: guard still held by the detached worker -> skipped.
        result2 = scheduler_module.run_lane_scheduler_cycle(
            _settings(
                buy_scan_prefetch_overlap_enabled=True,
                session_cycle_hard_budget_seconds=60.0,
            ),
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={
                "cycle_id": "s4-drain-2",
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
                "lane_scheduler_hooks": {
                    "clock": clock,
                    "buy_controller": controller,
                },
            },
            api_budget_state={},
        )
        assert result2.buy_result.intent is None
        assert result2.buy_result.skipped_reason == "previous_scan_running"
    finally:
        never_future.set_result(None)
        controller.reset_for_tests()


def test_quote_age_uses_per_symbol_collection_timestamps_when_present(monkeypatch) -> None:
    """C-2: with collected_monotonic_by_symbol populated, quote_age_* telemetry
    reflects real per-symbol ages against the scheduler clock — not elapsed_ms."""
    import dataclasses

    clock = _FakeClock()

    def prefetch_func(*, symbols, **_kwargs):
        clock.now = 10.0
        return dataclasses.replace(
            _prefetch_result(tuple(symbols)),
            collected_monotonic_by_symbol={"005930": 9.0},
        )

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return None

    def buy_intent_factory(*, cycle_id, **_kwargs):
        return None

    scheduler_state = {
        "cycle_id": "quote-age-real",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        "lane_scheduler_hooks": {
            "clock": clock,
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
        },
    }
    result = scheduler_module.run_lane_scheduler_cycle(
        _settings(),
        sell_check_due=False,
        buy_scan_due=True,
        scheduler_state=scheduler_state,
        api_budget_state={},
    )

    # single symbol collected at t=9.0, telemetry computed at t=10.0 -> 1000ms
    assert result.telemetry["quote_age_max_ms"] == 1000.0
    assert result.telemetry["quote_age_avg_ms"] == 1000.0


def test_detached_handler_block_sends_operator_warning(monkeypatch) -> None:
    """C-3 (§S5-③): when the gate refuses intents because a detached order
    handler is alive, the scheduler sends one operator Slack warning through
    the injected ops notifier; a normal cycle sends none."""
    from concurrent.futures import Future
    from types import SimpleNamespace as _NS
    from unittest import mock as _mock

    import app.pipeline.order_gate as order_gate_module

    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)

    notifier = _mock.Mock()
    notifier.send.return_value = _NS(status="sent")

    def run(cycle_id: str):
        return scheduler_module.run_lane_scheduler_cycle(
            _settings(),
            sell_check_due=True,
            buy_scan_due=False,
            scheduler_state={
                "cycle_id": cycle_id,
                "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
                "lane_scheduler_hooks": {
                    "context": {"ops_slack_notifier": notifier},
                },
            },
            api_budget_state={},
        )

    order_gate_module.reset_detached_handlers_for_tests()
    try:
        stuck = Future()  # never resolves -> permanently "running"
        order_gate_module._register_detached_handler(
            intent=_NS(intent_id="prev:sell:000660", intent_type="SELL", symbol="000660"),
            future=stuck,
        )
        blocked = run("blocked-cycle")
        assert (
            blocked.telemetry["order_gate_blocked_detached_count"] > 0
        )
        assert blocked.telemetry["detached_handler_warning_sent"] is True
        notifier.send.assert_called_once()
        assert notifier.send.call_args.args[0] == "order_gate_blocked"
        assert "SELL:000660" in notifier.send.call_args.args[1]
    finally:
        order_gate_module.reset_detached_handlers_for_tests()

    notifier.send.reset_mock()
    normal = run("normal-cycle")
    assert normal.telemetry["order_gate_blocked_detached_count"] == 0
    assert normal.telemetry["detached_handler_warning_sent"] is False
    notifier.send.assert_not_called()
