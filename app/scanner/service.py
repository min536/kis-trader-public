import time
from typing import Any, Mapping

from app.auth.token import ApiHttpError, is_rate_limit_response
from app.auth.settings import Settings
from app.core.costs import (
    calc_buy_fee,
    calc_expected_net_profit_buffer_bps,
    calc_sell_fee,
    calc_sell_tax,
    calc_slippage,
)
from app.core.throttle import get_throttle_metrics_summary
from app.market_data.schema import MarketSnapshot, build_market_snapshot
from app.math_models import build_feature_map, build_feature_vector, summarize_feature_map
from app.math_models.indicator_quality import build_indicator_quality_features
from app.math_models.mean_reversion import build_mean_reversion_features
from app.math_models.portfolio_risk import build_portfolio_risk_features
from app.math_models.price_dynamics import build_price_dynamics_features
from app.math_models.technical import build_technical_features
from app.scanner.models import (
    ScanDiagnostics,
    ShallowScanCandidate,
    SymbolAnalysisResult,
)
from app.scanner.quote_account import (
    BuyScanQuoteContext,
    build_buy_scan_quote_context,
    fetch_buy_scan_price,
)
from app.scanner.scoring import (
    _clamp,
    _effective_buy_min_passed_count,
    _effective_buy_min_score,
    _percent_score,
    _range_strength,
    _rule_equivalent_count,
    _threshold_strength,
    build_analysis_sort_key,
    calculate_selection_score,
    summarize_score_breakdown,
)
from app.scanner.scoring import _score_component_labels
from app.scanner.presentation import (
    _buy_status_letter,
    _intraday_change_pct,
    _rebound_from_low_pct,
    _safe_float,
    _safe_price,
    build_buy_strategy_summary,
    build_candidate_reason,
    build_scan_console_lines,
    build_selection_reason,
    build_shallow_scan_candidates,
    build_universe_console_lines,
    resolve_mock_buy_price_floor_krw,
    select_top_analysis_result,
    select_top_candidate,
    serialize_selection_details,
)
from app.scanner.symbol_names import get_symbol_name
from app.strategy.buy_decision import (
    BuyDecision,
    evaluate_buy_decision,
)


_LAST_SCAN_DIAGNOSTICS = ScanDiagnostics()


def get_last_scan_diagnostics() -> dict[str, float | int | list[dict[str, float | str]]]:
    return {
        "requested_count": int(_LAST_SCAN_DIAGNOSTICS.requested_count),
        "evaluated_count": int(_LAST_SCAN_DIAGNOSTICS.evaluated_count),
        "quote_request_count": int(_LAST_SCAN_DIAGNOSTICS.quote_request_count),
        "quote_wait_sleep_ms": round(float(_LAST_SCAN_DIAGNOSTICS.quote_wait_sleep_ms), 1),
        "quote_response_ms": round(float(_LAST_SCAN_DIAGNOSTICS.quote_response_ms), 1),
        "quote_parse_ms": round(float(_LAST_SCAN_DIAGNOSTICS.quote_parse_ms), 1),
        "score_calc_ms": round(float(_LAST_SCAN_DIAGNOSTICS.score_calc_ms), 1),
        "candidate_build_ms": round(float(_LAST_SCAN_DIAGNOSTICS.candidate_build_ms), 1),
        "ranking_ms": round(float(_LAST_SCAN_DIAGNOSTICS.ranking_ms), 1),
        "logging_ms": round(float(_LAST_SCAN_DIAGNOSTICS.logging_ms), 1),
        "throttle_sleep_events": int(_LAST_SCAN_DIAGNOSTICS.throttle_sleep_events),
        "throttle_min_sleep_ms": (
            None
            if _LAST_SCAN_DIAGNOSTICS.throttle_min_sleep_ms is None
            else round(float(_LAST_SCAN_DIAGNOSTICS.throttle_min_sleep_ms), 1)
        ),
        "throttle_total_sleep_ms": round(float(_LAST_SCAN_DIAGNOSTICS.throttle_total_sleep_ms), 1),
        "throttle_immediate_pass_count": int(_LAST_SCAN_DIAGNOSTICS.throttle_immediate_pass_count),
        "throttle_average_sleep_ms": round(float(_LAST_SCAN_DIAGNOSTICS.throttle_average_sleep_ms), 1),
        "interrupted_reason": _LAST_SCAN_DIAGNOSTICS.interrupted_reason,
        "rate_limit_triggered": bool(_LAST_SCAN_DIAGNOSTICS.rate_limit_triggered),
        "rate_limit_message": _LAST_SCAN_DIAGNOSTICS.rate_limit_message,
        "rate_limit_partial_stop": bool(_LAST_SCAN_DIAGNOSTICS.rate_limit_partial_stop),
        "rate_limit_partial_stop_symbol": _LAST_SCAN_DIAGNOSTICS.rate_limit_partial_stop_symbol,
        "rate_limit_partial_completed_count": int(
            _LAST_SCAN_DIAGNOSTICS.rate_limit_partial_completed_count
        ),
        "rate_limit_partial_remaining_count": int(
            _LAST_SCAN_DIAGNOSTICS.rate_limit_partial_remaining_count
        ),
        "parse_error_skipped_count": int(
            _LAST_SCAN_DIAGNOSTICS.parse_error_skipped_count
        ),
        "parse_error_skipped_symbols": list(
            _LAST_SCAN_DIAGNOSTICS.parse_error_skipped_symbols
        ),
        "quote_prefetch_missing_count": int(
            _LAST_SCAN_DIAGNOSTICS.quote_prefetch_missing_count
        ),
        "quote_prefetch_missing_symbols": list(
            _LAST_SCAN_DIAGNOSTICS.quote_prefetch_missing_symbols
        ),
        "quote_account_mode": _LAST_SCAN_DIAGNOSTICS.quote_account_mode,
        "quote_account_env": _LAST_SCAN_DIAGNOSTICS.quote_account_env,
        "pure_network_ms": round(float(_LAST_SCAN_DIAGNOSTICS.quote_response_ms), 1),
        "pacing_sleep_ms": round(float(_LAST_SCAN_DIAGNOSTICS.throttle_total_sleep_ms), 1),
        "quote_calc_total_ms": round(
            float(_LAST_SCAN_DIAGNOSTICS.quote_parse_ms)
            + float(_LAST_SCAN_DIAGNOSTICS.score_calc_ms)
            + float(_LAST_SCAN_DIAGNOSTICS.candidate_build_ms),
            1,
        ),
        "sample_symbols": list(_LAST_SCAN_DIAGNOSTICS.sample_symbols[-5:]),
    }


def _looks_like_rate_limit_error(exc: Exception) -> bool:
    text = str(exc)
    if "EGW00201" in text or "초당 거래건수" in text:
        return True
    if isinstance(exc, ApiHttpError):
        return is_rate_limit_response(exc.data)
    return False


def _mark_rate_limit_partial_stop(
    diagnostics: ScanDiagnostics,
    *,
    symbol: str,
    completed_count: int,
    reason: str,
    message: str,
) -> None:
    diagnostics.rate_limit_triggered = True
    diagnostics.interrupted_reason = reason
    diagnostics.rate_limit_message = message
    diagnostics.rate_limit_partial_stop = True
    diagnostics.rate_limit_partial_stop_symbol = symbol
    diagnostics.rate_limit_partial_completed_count = completed_count
    diagnostics.rate_limit_partial_remaining_count = max(
        0,
        diagnostics.requested_count - completed_count,
    )


def _build_feature_payload(
    *,
    snapshot: MarketSnapshot,
    strategy_result: BuyDecision,
    score_components: dict[str, float],
    cost_metrics: dict[str, float | int],
    technical_features: Mapping[str, object] | None,
    indicator_quality_features: Mapping[str, object] | None,
    mean_reversion_features: Mapping[str, object] | None,
    portfolio_risk_features: Mapping[str, object] | None,
    price_dynamics_features: Mapping[str, object] | None,
    final_score: float,
) -> tuple[
    dict[str, dict[str, float | None]],
    dict[str, float | None],
    dict[str, str],
    str,
]:
    enabled_count = max(int(strategy_result.enabled_count), 1)
    feature_map = build_feature_map(
        base_quality_features={
            "passed_count": float(strategy_result.passed_count),
            "signal_quality_count": score_components.get("signal_quality_count"),
            "enabled_count": float(strategy_result.enabled_count),
            "pass_ratio": round(float(strategy_result.passed_count) / enabled_count, 4),
            "soft_pass_ratio": round(
                float(
                    score_components.get(
                        "signal_quality_count",
                        strategy_result.passed_count,
                    )
                    or 0.0
                )
                / enabled_count,
                4,
            ),
            "trend_quality": score_components.get("trend_quality_score"),
            "momentum_quality": score_components.get("momentum_quality_score"),
            "price_efficiency": score_components.get("price_efficiency_score"),
            "intraday_pullback_strength_score": score_components.get(
                "intraday_pullback_strength_score"
            ),
            "rebound_from_low_strength_score": score_components.get(
                "rebound_from_low_strength_score"
            ),
            "controlled_down_strength_score": score_components.get(
                "controlled_down_strength_score"
            ),
            "gap_down_open_strength_score": score_components.get(
                "gap_down_open_strength_score"
            ),
            "range_recovery_strength_score": score_components.get(
                "range_recovery_strength_score"
            ),
            "intraday_pullback_bonus": score_components.get("intraday_pullback_bonus"),
            "rebound_from_low_bonus": score_components.get("rebound_from_low_bonus"),
            "controlled_down_bonus": score_components.get("controlled_down_bonus"),
            "gap_down_open_bonus": score_components.get("gap_down_open_bonus"),
            "range_recovery_bonus": score_components.get("range_recovery_bonus"),
            "gap_up_open_pct": score_components.get("gap_up_open_pct"),
            "pullback_pct": score_components.get("pullback_pct"),
            "rebound_pct": score_components.get("rebound_pct"),
            "prev_day_change_pct": snapshot.prev_day_change_pct,
        },
        cost_features={
            "expected_fee_krw": cost_metrics.get("expected_fee_krw"),
            "expected_tax_krw": cost_metrics.get("expected_tax_krw"),
            "expected_slippage_krw": cost_metrics.get("expected_slippage_krw"),
            "expected_total_cost_krw": cost_metrics.get("expected_total_cost_krw"),
            "expected_cost_bps": cost_metrics.get("expected_cost_bps"),
            "cost_quality_score": cost_metrics.get("cost_quality_score"),
            "expected_cost_penalty": cost_metrics.get("expected_cost_penalty"),
            "net_edge_bps": cost_metrics.get("net_edge_bps"),
        },
        technical_features=technical_features,
        indicator_quality_features=indicator_quality_features,
        mean_reversion_features=mean_reversion_features,
        portfolio_risk_features=portfolio_risk_features,
        price_dynamics_features=price_dynamics_features,
        final_score_components={
            "final_score": final_score,
            "base_score_after_quality": final_score,
            "overheat_penalty": score_components.get("overheat_penalty"),
            "pullback_exhaustion_penalty": score_components.get("pullback_exhaustion_penalty"),
            "mean_reversion_bonus": score_components.get("mean_reversion_bonus"),
            "overextension_penalty": score_components.get("overextension_penalty"),
        },
    )
    feature_vector = build_feature_vector(feature_map)
    feature_summaries = summarize_feature_map(feature_map)
    math_score_summary = (
        f"base={feature_summaries['base_quality_features']} | "
        f"cost={feature_summaries['cost_features']} | "
        f"tech={feature_summaries['technical_features']} | "
        f"indicator_shadow={feature_summaries['indicator_quality_features']} | "
        f"mean_rev={feature_summaries['mean_reversion_features']} | "
        f"portfolio_risk={feature_summaries['portfolio_risk_features']} | "
        f"price_dyn={feature_summaries['price_dynamics_features']}"
    )
    return feature_map, feature_vector, feature_summaries, math_score_summary


def _estimate_buy_cost_metrics(
    *,
    snapshot: MarketSnapshot,
    gross_expected_profit_krw: int,
    settings: Settings,
) -> dict[str, float | int]:
    notional_krw = max(int(snapshot.current_price) * max(int(settings.qty), 1), 0)
    if notional_krw <= 0:
        return {
            "expected_fee_krw": 0,
            "expected_tax_krw": 0,
            "expected_slippage_krw": 0,
            "expected_total_cost_krw": 0,
            "expected_cost_bps": 0.0,
            "cost_quality_score": 0.0,
            "expected_cost_penalty": 0.0,
            "net_edge_bps": 0.0,
            "expected_buy_slippage_bps": 0.0,
            "expected_sell_slippage_bps": 0.0,
        }

    volatility_adjustment = min(abs(float(snapshot.prev_day_change_pct or 0.0)) * 0.25, 4.0)
    price_adjustment = 0.0
    if snapshot.current_price >= 200_000:
        price_adjustment = 1.5
    elif snapshot.current_price >= 100_000:
        price_adjustment = 0.75

    notional_adjustment = 0.0
    if notional_krw >= max(int(settings.buy_max_budget_per_trade_krw), 1):
        notional_adjustment = 1.0
    elif notional_krw >= max(int(settings.buy_max_budget_per_trade_krw * 0.6), 1):
        notional_adjustment = 0.4

    expected_buy_slippage_bps = max(
        float(settings.buy_slippage_bps),
        float(settings.expected_slippage_bps_base) + volatility_adjustment + price_adjustment + notional_adjustment,
    )
    expected_sell_slippage_bps = max(
        float(settings.sell_slippage_bps),
        float(settings.expected_slippage_bps_base) + (volatility_adjustment * 0.8),
    )

    expected_fee_krw = calc_buy_fee(notional_krw, settings.buy_fee_bps) + calc_sell_fee(
        notional_krw,
        settings.sell_fee_bps,
    )
    expected_tax_krw = calc_sell_tax(notional_krw, settings.sell_tax_bps)
    expected_slippage_krw = calc_slippage(
        notional_krw,
        expected_buy_slippage_bps,
    ) + calc_slippage(
        notional_krw,
        expected_sell_slippage_bps,
    )
    expected_total_cost_krw = expected_fee_krw + expected_tax_krw + expected_slippage_krw
    expected_cost_bps = round((expected_total_cost_krw / notional_krw) * 10_000, 2)
    gross_edge_bps = (max(int(gross_expected_profit_krw), 0) / notional_krw) * 10_000
    net_edge_bps = round(gross_edge_bps - expected_cost_bps, 2)

    block_bps = max(float(settings.expected_cost_block_bps), 1.0)
    cost_quality_score = round(max(0.0, (block_bps - expected_cost_bps) / block_bps) * 0.35, 2)
    expected_cost_penalty = round(max(0.0, expected_cost_bps - (block_bps * 0.55)) / 10.0, 2)

    return {
        "expected_fee_krw": int(expected_fee_krw),
        "expected_tax_krw": int(expected_tax_krw),
        "expected_slippage_krw": int(expected_slippage_krw),
        "expected_total_cost_krw": int(expected_total_cost_krw),
        "expected_cost_bps": expected_cost_bps,
        "cost_quality_score": cost_quality_score,
        "expected_cost_penalty": expected_cost_penalty,
        "net_edge_bps": net_edge_bps,
        "expected_buy_slippage_bps": round(expected_buy_slippage_bps, 2),
        "expected_sell_slippage_bps": round(expected_sell_slippage_bps, 2),
    }


def _analyze_symbol_with_metrics(
    *,
    symbol: str,
    token: str,
    settings: Settings,
    portfolio_snapshot=None,
    selection_layer: str = "",  # "core" 이면 core 전용 required_pass_count 적용
    quote_context: BuyScanQuoteContext | None = None,
    price_data: dict[str, Any] | None = None,
    scan_cache: dict | None = None,
) -> tuple[SymbolAnalysisResult, dict[str, float | int | str]]:
    api_started_perf = time.perf_counter()
    if price_data is None:
        price_data = (
            fetch_buy_scan_price(symbol, context=quote_context)
            if quote_context is not None
            else fetch_buy_scan_price(
                symbol,
                context=BuyScanQuoteContext(
                    mode="execution_account",
                    env="",
                    token=token,
                ),
            )
        )
    api_elapsed_ms = (time.perf_counter() - api_started_perf) * 1000
    if price_data.get("rt_cd") != "0":
        raise RuntimeError(f"현재가 조회 실패({symbol}): {price_data}")

    parse_started_perf = time.perf_counter()
    market_snapshot = build_market_snapshot(price_data["output"])
    parse_elapsed_ms = (time.perf_counter() - parse_started_perf) * 1000

    calc_started_perf = time.perf_counter()
    effective_required_pass_count = (
        settings.buy_rule_required_pass_count_core
        if selection_layer == "core"
        else settings.buy_rule_required_pass_count
    )
    effective_min_score = _effective_buy_min_score(
        settings=settings,
        selection_layer=selection_layer,
    )
    strategy_result = evaluate_buy_decision(
        symbol=symbol,
        qty=settings.qty,
        snapshot=market_snapshot,
        enable_intraday_pullback=settings.buy_rule_enable_intraday_pullback,
        enable_rebound_from_low=settings.buy_rule_enable_rebound_from_low,
        enable_controlled_down_day=settings.buy_rule_enable_controlled_down_day,
        enable_gap_down_open=settings.buy_rule_enable_gap_down_open,
        enable_range_recovery=settings.buy_rule_enable_range_recovery,
        enable_live_volume_rank=settings.buy_rule_enable_live_volume_rank,
        enable_live_volume_power_rank=settings.buy_rule_enable_live_volume_power_rank,
        rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
        controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
        controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
        gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
        gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
        range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
        required_pass_count=effective_required_pass_count,
        selection_layer=selection_layer,
    )
    effective_min_passed_count = _effective_buy_min_passed_count(
        settings=settings,
        strategy_result=strategy_result,
    )
    score, score_components = calculate_selection_score(
        snapshot=market_snapshot,
        strategy_result=strategy_result,
        rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
        controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
        controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
        gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
        gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
        range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
    )
    buffer_metrics = calc_expected_net_profit_buffer_bps(
        entry_price_krw=market_snapshot.current_price,
        target_exit_price_krw=max(
            market_snapshot.open_price,
            market_snapshot.current_price,
        ),
        qty=max(settings.qty, 1),
        settings=settings,
    )
    net_profit_buffer_bps = float(buffer_metrics["net_expected_profit_bps"])
    passes_profit_buffer = (
        not settings.use_cost_aware_pnl
        or net_profit_buffer_bps >= settings.min_net_profit_buffer_bps
    )
    cost_metrics = _estimate_buy_cost_metrics(
        snapshot=market_snapshot,
        gross_expected_profit_krw=int(buffer_metrics["gross_expected_profit_krw"]),
        settings=settings,
    )
    mean_reversion_features = build_mean_reversion_features(
        symbol=symbol,
        snapshot=market_snapshot,
        score_components=score_components,
    )
    portfolio_risk_features = build_portfolio_risk_features(
        candidate_symbol=symbol,
        current_price=market_snapshot.current_price,
        portfolio_snapshot=portfolio_snapshot,
        candidate_qty=max(settings.qty, 1),
        scan_cache=scan_cache,
    )
    technical_features = build_technical_features(
        symbol,
        current_snapshot=market_snapshot,
    )
    indicator_quality_features = build_indicator_quality_features(
        symbol=symbol,
        snapshot=market_snapshot,
        score_components=score_components,
    )
    price_dynamics_features = build_price_dynamics_features(
        symbol=symbol,
        snapshot=market_snapshot,
        score_components=score_components,
    )
    score_components = {
        **score_components,
        "mean_reversion_bonus": float(mean_reversion_features["mean_reversion_bonus"]),
        "overextension_penalty": float(mean_reversion_features["overextension_penalty"]),
        "diversification_bonus": float(portfolio_risk_features["diversification_bonus"]),
        "portfolio_correlation_penalty": float(portfolio_risk_features["portfolio_correlation_penalty"]),
        "variance_increase_penalty": float(portfolio_risk_features["variance_increase_penalty"]),
        "cost_quality_score": float(cost_metrics["cost_quality_score"]),
        "expected_cost_penalty": float(cost_metrics["expected_cost_penalty"]),
        "trend_alignment_score": float(technical_features.get("trend_alignment_score", 0.0)),
        "macd_momentum_score": float(technical_features.get("macd_momentum_score", 0.0)),
        "hist_range_bonus": float(price_dynamics_features["hist_range_bonus"]),
        "hist_range_penalty": float(price_dynamics_features["hist_range_penalty"]),
        "velocity_bonus": float(price_dynamics_features["velocity_bonus"]),
        "velocity_penalty": float(price_dynamics_features["velocity_penalty"]),
        "pullback_depth_bonus": float(price_dynamics_features["pullback_depth_bonus"]),
    }
    score = round(
        score
        + float(mean_reversion_features["mean_reversion_bonus"])
        + float(portfolio_risk_features["diversification_bonus"])
        - float(mean_reversion_features["overextension_penalty"])
        - float(portfolio_risk_features["portfolio_correlation_penalty"])
        - float(portfolio_risk_features["variance_increase_penalty"])
        + float(cost_metrics["cost_quality_score"])
        - float(cost_metrics["expected_cost_penalty"])
        + float(price_dynamics_features["hist_range_bonus"])
        + float(price_dynamics_features["velocity_bonus"])
        + float(price_dynamics_features["pullback_depth_bonus"])
        - float(price_dynamics_features["hist_range_penalty"])
        - float(price_dynamics_features["velocity_penalty"])
        + float(technical_features.get("trend_alignment_score", 0.0))
        + float(technical_features.get("macd_momentum_score", 0.0)),
        2,
    )
    signal_quality_count = float(
        score_components.get("signal_quality_count", strategy_result.passed_count)
        or 0.0
    )
    base_candidate = (
        max(float(strategy_result.passed_count), signal_quality_count)
        >= effective_min_passed_count
        and score >= effective_min_score
        and passes_profit_buffer
    )
    cost_block_reason = None
    if base_candidate and settings.use_cost_aware_pnl:
        if float(cost_metrics["expected_cost_bps"]) > settings.expected_cost_block_bps:
            cost_block_reason = "expected_cost_too_high"
        elif float(cost_metrics["net_edge_bps"]) < settings.min_net_edge_bps:
            cost_block_reason = "net_edge_too_low"
    score_highlights, score_penalties, score_summary = summarize_score_breakdown(
        score_components
    )
    feature_map, feature_vector, feature_summaries, math_score_summary = _build_feature_payload(
        snapshot=market_snapshot,
        strategy_result=strategy_result,
        score_components=score_components,
        cost_metrics=cost_metrics,
        technical_features=technical_features,
        indicator_quality_features=indicator_quality_features,
        mean_reversion_features=mean_reversion_features,
        portfolio_risk_features=portfolio_risk_features,
        price_dynamics_features=price_dynamics_features,
        final_score=score,
    )
    candidate = base_candidate and cost_block_reason is None
    score_calc_elapsed_ms = (time.perf_counter() - calc_started_perf) * 1000

    build_started_perf = time.perf_counter()
    result = SymbolAnalysisResult(
        symbol=symbol,
        name=get_symbol_name(symbol),
        market_snapshot=market_snapshot,
        strategy_result=strategy_result,
        passed_count=strategy_result.passed_count,
        signal_quality_count=signal_quality_count,
        enabled_count=strategy_result.enabled_count,
        candidate=candidate,
        final_reason=build_candidate_reason(
            passed_count=strategy_result.passed_count,
            min_passed_count=effective_min_passed_count,
            signal_quality_count=signal_quality_count,
            score=score,
            min_score=effective_min_score,
            net_profit_buffer_bps=net_profit_buffer_bps,
            min_net_profit_buffer_bps=settings.min_net_profit_buffer_bps,
            passes_profit_buffer=passes_profit_buffer,
            use_cost_aware_pnl=settings.use_cost_aware_pnl,
            expected_cost_bps=float(cost_metrics["expected_cost_bps"]),
            expected_cost_block_bps=settings.expected_cost_block_bps,
            net_edge_bps=float(cost_metrics["net_edge_bps"]),
            min_net_edge_bps=settings.min_net_edge_bps,
            cost_block_reason=cost_block_reason,
        ),
        passed_pattern=build_buy_strategy_summary(strategy_result),
        score=score,
        net_profit_buffer_bps=net_profit_buffer_bps,
        passes_profit_buffer=passes_profit_buffer,
        score_components=score_components,
        score_highlights=score_highlights,
        score_penalties=score_penalties,
        score_summary=score_summary,
        expected_fee_krw=int(cost_metrics["expected_fee_krw"]),
        expected_tax_krw=int(cost_metrics["expected_tax_krw"]),
        expected_slippage_krw=int(cost_metrics["expected_slippage_krw"]),
        expected_total_cost_krw=int(cost_metrics["expected_total_cost_krw"]),
        expected_cost_bps=float(cost_metrics["expected_cost_bps"]),
        cost_quality_score=float(cost_metrics["cost_quality_score"]),
        expected_cost_penalty=float(cost_metrics["expected_cost_penalty"]),
        net_edge_bps=float(cost_metrics["net_edge_bps"]),
        cost_block_reason=cost_block_reason,
        feature_map=feature_map,
        feature_vector=feature_vector,
        feature_summaries=feature_summaries,
        math_score_summary=math_score_summary,
        mean_reversion_zscore=mean_reversion_features["mean_reversion_zscore"],
        reversion_quality_score=mean_reversion_features["reversion_quality_score"],
        overextension_penalty=mean_reversion_features["overextension_penalty"],
        ou_half_life_estimate=mean_reversion_features["ou_half_life_estimate"],
        mean_reversion_summary=mean_reversion_features["mean_reversion_summary"],
        portfolio_avg_correlation=portfolio_risk_features["portfolio_avg_correlation"],
        portfolio_max_correlation=portfolio_risk_features["portfolio_max_correlation"],
        variance_increase_estimate=portfolio_risk_features["variance_increase_estimate"],
        portfolio_risk_summary=portfolio_risk_features["portfolio_risk_summary"],
        portfolio_correlation_penalty=portfolio_risk_features["portfolio_correlation_penalty"],
        variance_increase_penalty=portfolio_risk_features["variance_increase_penalty"],
        hist_percentile_rank=price_dynamics_features["hist_percentile_rank"],
        price_velocity_pct=price_dynamics_features["price_velocity_pct"],
        price_dynamics_summary=price_dynamics_features["price_dynamics_summary"],
        sort_key=build_analysis_sort_key(
            passed_count=strategy_result.passed_count,
            score=score,
            symbol=symbol,
        ),
    )
    candidate_build_elapsed_ms = (time.perf_counter() - build_started_perf) * 1000
    return result, {
        "symbol": symbol,
        "api_ms": round(api_elapsed_ms, 1),
        "parse_ms": round(parse_elapsed_ms, 1),
        "score_calc_ms": round(score_calc_elapsed_ms, 1),
        "candidate_build_ms": round(candidate_build_elapsed_ms, 1),
    }


def analyze_symbol(
    *,
    symbol: str,
    token: str,
    settings: Settings,
    portfolio_snapshot=None,
    selection_layer: str = "",
) -> SymbolAnalysisResult:
    result, _ = _analyze_symbol_with_metrics(
        symbol=symbol,
        token=token,
        settings=settings,
        portfolio_snapshot=portfolio_snapshot,
        selection_layer=selection_layer,
    )
    return result


def scan_target_symbols(
    *,
    settings: Settings,
    token: str,
    portfolio_snapshot=None,
    symbols: tuple[str, ...] | None = None,
    layer_by_symbol: Mapping[str, str] | None = None,
    price_data_by_symbol: Mapping[str, dict[str, Any]] | None = None,
    allow_inline_quote_fetch: bool = True,
) -> tuple[SymbolAnalysisResult, ...]:
    global _LAST_SCAN_DIAGNOSTICS

    raw_scan_symbols = tuple(
        symbols or settings.target_symbols[: settings.scan_symbols_max_per_cycle]
    )
    scan_symbols = tuple(
        dict.fromkeys(
            str(symbol).strip()
            for symbol in raw_scan_symbols
            if str(symbol).strip()
        )
    )
    results: list[SymbolAnalysisResult] = []
    diagnostics = ScanDiagnostics(
        requested_count=len(scan_symbols)
    )
    _layer_map: Mapping[str, str] = layer_by_symbol or {}
    quote_context = build_buy_scan_quote_context(
        settings=settings,
        execution_token=token,
    )
    diagnostics.quote_account_mode = quote_context.mode
    diagnostics.quote_account_env = quote_context.env
    prefetched_price_data = (
        price_data_by_symbol if isinstance(price_data_by_symbol, Mapping) else {}
    )
    scan_cache: dict = {}
    for symbol in scan_symbols:
        if not allow_inline_quote_fetch and symbol not in prefetched_price_data:
            diagnostics.quote_prefetch_missing_count += 1
            diagnostics.quote_prefetch_missing_symbols.append(symbol)
            diagnostics.interrupted_reason = (
                diagnostics.interrupted_reason or "quote_prefetch_missing"
            )
            continue
        throttle_before = get_throttle_metrics_summary()
        try:
            result, symbol_metrics = _analyze_symbol_with_metrics(
                symbol=symbol,
                token=token,
                settings=settings,
                portfolio_snapshot=portfolio_snapshot,
                selection_layer=str(_layer_map.get(symbol) or ""),
                quote_context=quote_context,
                price_data=prefetched_price_data.get(symbol),
                scan_cache=scan_cache,
            )
        except ApiHttpError as exc:
            if _looks_like_rate_limit_error(exc):
                _mark_rate_limit_partial_stop(
                    diagnostics,
                    symbol=symbol,
                    completed_count=len(results),
                    reason="rate_limit",
                    message=str(exc),
                )
                break
            raise
        except RuntimeError as exc:
            if _looks_like_rate_limit_error(exc):
                _mark_rate_limit_partial_stop(
                    diagnostics,
                    symbol=symbol,
                    completed_count=len(results),
                    reason="rate_limit_parse_skip",
                    message=str(exc),
                )
                break
            raise
        except ValueError as exc:
            if _looks_like_rate_limit_error(exc):
                _mark_rate_limit_partial_stop(
                    diagnostics,
                    symbol=symbol,
                    completed_count=len(results),
                    reason="rate_limit_parse_skip",
                    message=str(exc),
                )
                break
            # KIS occasionally returns an empty/malformed output block for a
            # single symbol (e.g. missing stck_shrn_iscd). Skip that symbol
            # instead of aborting the whole scan cycle.
            diagnostics.parse_error_skipped_count += 1
            diagnostics.parse_error_skipped_symbols.append(symbol)
            print(
                f"[warn] scan skip on parse error symbol={symbol} error={exc}"
            )
            continue
        throttle_after = get_throttle_metrics_summary()
        sleep_elapsed_ms = max(
            float(throttle_after.get("total_sleep_ms", 0.0) or 0.0)
            - float(throttle_before.get("total_sleep_ms", 0.0) or 0.0),
            0.0,
        )
        diagnostics.quote_request_count += 1
        diagnostics.quote_wait_sleep_ms += sleep_elapsed_ms
        diagnostics.quote_response_ms += float(symbol_metrics["api_ms"])
        diagnostics.quote_parse_ms += float(symbol_metrics["parse_ms"])
        diagnostics.score_calc_ms += float(symbol_metrics["score_calc_ms"])
        diagnostics.candidate_build_ms += float(symbol_metrics["candidate_build_ms"])
        diagnostics.throttle_sleep_events += max(
            int(throttle_after.get("sleep_events", 0) or 0)
            - int(throttle_before.get("sleep_events", 0) or 0),
            0,
        )
        diagnostics.throttle_total_sleep_ms += sleep_elapsed_ms
        diagnostics.throttle_immediate_pass_count += max(
            int(throttle_after.get("immediate_pass_count", 0) or 0)
            - int(throttle_before.get("immediate_pass_count", 0) or 0),
            0,
        )
        new_min_sleep = throttle_after.get("min_sleep_ms")
        old_min_sleep = throttle_before.get("min_sleep_ms")
        if new_min_sleep is not None and new_min_sleep != old_min_sleep:
            diagnostics.throttle_min_sleep_ms = float(new_min_sleep)
        if diagnostics.throttle_sleep_events > 0:
            diagnostics.throttle_average_sleep_ms = (
                float(diagnostics.throttle_total_sleep_ms)
                / float(diagnostics.throttle_sleep_events)
            )
        diagnostics.sample_symbols.append(
            {
                "symbol": symbol,
                "sleep_ms": round(sleep_elapsed_ms, 1),
                "api_ms": float(symbol_metrics["api_ms"]),
                "calc_ms": round(
                    float(symbol_metrics["parse_ms"])
                    + float(symbol_metrics["score_calc_ms"])
                    + float(symbol_metrics["candidate_build_ms"]),
                    1,
                ),
            }
        )
        results.append(result)
    diagnostics.evaluated_count = len(results)
    if diagnostics.rate_limit_partial_stop and diagnostics.rate_limit_partial_completed_count == 0:
        diagnostics.rate_limit_partial_completed_count = len(results)
        diagnostics.rate_limit_partial_remaining_count = max(
            0,
            diagnostics.requested_count - len(results),
        )
    _LAST_SCAN_DIAGNOSTICS = diagnostics
    return tuple(results)
