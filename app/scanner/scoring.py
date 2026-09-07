from app.auth.settings import Settings
from app.market_data.schema import MarketSnapshot
from app.strategy.buy_decision import BuyDecision, estimate_prev_close
from app.strategy.schema import StrategyEvaluationResult


def _effective_buy_min_score(*, settings: Settings, selection_layer: str) -> float:
    if selection_layer == "core":
        return float(settings.buy_min_score_core)
    return float(settings.buy_min_score)


def _effective_buy_min_passed_count(
    *,
    settings: Settings,
    strategy_result: BuyDecision,
) -> int:
    live_rule_enabled = any(
        result.enabled and result.strategy_name.startswith("live_")
        for result in strategy_result.rule_results
    )
    return int(settings.buy_min_passed_count) + (1 if live_rule_enabled else 0)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _percent_score(value: float) -> float:
    return round(_clamp(value) * 100.0, 2)


def _threshold_strength(value: float, full_score_at: float) -> float:
    if full_score_at <= 0:
        return 0.0
    return _percent_score(value / full_score_at)


def _range_strength(
    value: float,
    lower: float,
    upper: float,
    *,
    soft_margin: float,
) -> float:
    if upper <= lower:
        return 0.0
    if lower <= value <= upper:
        return 100.0
    margin = max(float(soft_margin), 0.01)
    if value < lower:
        return _percent_score(1.0 - ((lower - value) / margin))
    return _percent_score(1.0 - ((value - upper) / margin))


def _rule_equivalent_count(
    *,
    result: StrategyEvaluationResult,
    strength_score: float,
) -> float:
    if not result.enabled:
        return 0.0
    if result.passed:
        return 1.0
    return _clamp(float(strength_score) / 100.0)


def calculate_selection_score(
    *,
    snapshot: MarketSnapshot,
    strategy_result: BuyDecision,
    rebound_from_low_pct: float,
    controlled_down_day_min: float,
    controlled_down_day_max: float,
    gap_down_open_min_pct: float,
    gap_down_open_max_pct: float,
    range_recovery_min_ratio: float,
) -> tuple[float, dict[str, float]]:
    strategy_map = {
        result.strategy_name: result
        for result in strategy_result.rule_results
    }

    pullback_pct = 0.0
    if snapshot.open_price > 0:
        pullback_pct = max(
            (snapshot.open_price - snapshot.current_price) / snapshot.open_price * 100,
            0.0,
        )

    rebound_pct = 0.0
    if snapshot.low_price > 0:
        rebound_pct = max(
            (snapshot.current_price - snapshot.low_price) / snapshot.low_price * 100,
            0.0,
        )
    rebound_threshold_pct = max(float(rebound_from_low_pct) * 100.0, 0.0)
    rebound_from_low_strength_score = _threshold_strength(
        rebound_pct,
        rebound_threshold_pct,
    )

    controlled_down_bonus = 0.0
    rate_span = controlled_down_day_max - controlled_down_day_min
    controlled_down_strength_score = _range_strength(
        float(snapshot.prev_day_change_pct or 0.0),
        controlled_down_day_min,
        controlled_down_day_max,
        soft_margin=max(rate_span * 0.25, 1.0),
    )
    if (
        strategy_map["controlled_down_day"].enabled
        and strategy_map["controlled_down_day"].passed
    ):
        if rate_span > 0:
            controlled_down_bonus = min(
                max(
                    (snapshot.prev_day_change_pct - controlled_down_day_min) / rate_span,
                    0.0,
                ),
                1.0,
            ) * 0.5

    intraday_pullback_bonus = 0.0
    intraday_pullback_strength_score = _threshold_strength(pullback_pct, 5.0)
    if (
        strategy_map["intraday_pullback"].enabled
        and strategy_map["intraday_pullback"].passed
    ):
        intraday_pullback_bonus = min(pullback_pct / 5.0, 1.0) * 0.5

    rebound_from_low_bonus = 0.0
    if (
        strategy_map["rebound_from_low"].enabled
        and strategy_map["rebound_from_low"].passed
    ):
        extra_rebound_pct = max(rebound_pct - (rebound_from_low_pct * 100), 0.0)
        rebound_from_low_bonus = min(extra_rebound_pct / 3.0, 1.0) * 0.5

    gap_down_open_pct = 0.0
    prev_close_estimate = estimate_prev_close(snapshot)
    if prev_close_estimate and prev_close_estimate > 0:
        gap_down_open_pct = max(
            (prev_close_estimate - snapshot.open_price) / prev_close_estimate * 100,
            0.0,
        )

    gap_down_open_bonus = 0.0
    gap_span = gap_down_open_max_pct - gap_down_open_min_pct
    gap_down_open_strength_score = _range_strength(
        gap_down_open_pct,
        gap_down_open_min_pct,
        gap_down_open_max_pct,
        soft_margin=max(gap_span * 0.25, 0.5),
    )
    if (
        strategy_map["gap_down_open"].enabled
        and strategy_map["gap_down_open"].passed
    ):
        if gap_span > 0:
            gap_down_open_bonus = min(
                max((gap_down_open_pct - gap_down_open_min_pct) / gap_span, 0.0),
                1.0,
            ) * 0.5

    range_recovery_ratio = 0.0
    intraday_range = snapshot.open_price - snapshot.low_price
    if intraday_range > 0:
        range_recovery_ratio = max(
            (snapshot.current_price - snapshot.low_price) / intraday_range,
            0.0,
        )

    range_recovery_bonus = 0.0
    range_recovery_strength_score = _threshold_strength(
        range_recovery_ratio,
        range_recovery_min_ratio,
    )
    if (
        strategy_map["range_recovery"].enabled
        and strategy_map["range_recovery"].passed
    ):
        extra_recovery_ratio = max(range_recovery_ratio - range_recovery_min_ratio, 0.0)
        range_recovery_bonus = min(extra_recovery_ratio / 0.8, 1.0) * 0.5

    trend_quality_score = 0.0
    if strategy_map["controlled_down_day"].passed:
        trend_quality_score += 0.35
    if strategy_map["range_recovery"].passed:
        trend_quality_score += 0.35
    if snapshot.current_price <= snapshot.open_price:
        trend_quality_score += 0.15

    momentum_quality_score = 0.0
    if rebound_pct > 0:
        momentum_quality_score += min(rebound_pct / 2.5, 0.45)
    if strategy_map["rebound_from_low"].passed:
        momentum_quality_score += 0.2
    if strategy_map["intraday_pullback"].passed:
        momentum_quality_score += 0.15

    price_efficiency_score = 0.0
    if intraday_range > 0:
        price_efficiency_score += min(range_recovery_ratio, 1.0) * 0.3
        if snapshot.current_price >= snapshot.low_price and snapshot.current_price <= snapshot.open_price:
            price_efficiency_score += 0.15

    gap_up_open_pct = 0.0
    if prev_close_estimate and prev_close_estimate > 0:
        gap_up_open_pct = max(
            (snapshot.open_price - prev_close_estimate) / prev_close_estimate * 100,
            0.0,
        )
    overheat_penalty = 0.0
    if snapshot.prev_day_change_pct > 3.0:
        overheat_penalty += min((snapshot.prev_day_change_pct - 3.0) / 4.0, 0.45)
    if gap_up_open_pct > 1.0:
        overheat_penalty += min((gap_up_open_pct - 1.0) / 3.0, 0.35)
    if rebound_pct > 4.0 and snapshot.current_price > snapshot.open_price:
        overheat_penalty += 0.2

    pullback_exhaustion_penalty = 0.0
    if pullback_pct > 5.0 and not strategy_map["range_recovery"].passed:
        pullback_exhaustion_penalty += min((pullback_pct - 5.0) / 5.0, 0.35)

    if not strategy_map["intraday_pullback"].enabled:
        intraday_pullback_strength_score = 0.0
    if not strategy_map["rebound_from_low"].enabled:
        rebound_from_low_strength_score = 0.0
    if not strategy_map["controlled_down_day"].enabled:
        controlled_down_strength_score = 0.0
    if not strategy_map["gap_down_open"].enabled:
        gap_down_open_strength_score = 0.0
    if not strategy_map["range_recovery"].enabled:
        range_recovery_strength_score = 0.0

    passed_count = strategy_result.passed_count
    live_volume_rank_strength_score = (
        100.0 if strategy_map["live_volume_rank"].passed else 0.0
    )
    live_volume_power_rank_strength_score = (
        100.0 if strategy_map["live_volume_power_rank"].passed else 0.0
    )
    soft_passed_count = round(
        _rule_equivalent_count(
            result=strategy_map["intraday_pullback"],
            strength_score=intraday_pullback_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["rebound_from_low"],
            strength_score=rebound_from_low_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["controlled_down_day"],
            strength_score=controlled_down_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["gap_down_open"],
            strength_score=gap_down_open_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["range_recovery"],
            strength_score=range_recovery_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["live_volume_rank"],
            strength_score=live_volume_rank_strength_score,
        )
        + _rule_equivalent_count(
            result=strategy_map["live_volume_power_rank"],
            strength_score=live_volume_power_rank_strength_score,
        ),
        2,
    )
    score = round(
        soft_passed_count
        + intraday_pullback_bonus
        + rebound_from_low_bonus
        + controlled_down_bonus
        + gap_down_open_bonus
        + range_recovery_bonus
        + trend_quality_score
        + momentum_quality_score
        + price_efficiency_score
        - overheat_penalty
        - pullback_exhaustion_penalty,
        2,
    )
    return (
        score,
        {
            "passed_count_base": float(passed_count),
            "pullback_pct": round(pullback_pct, 2),
            "rebound_pct": round(rebound_pct, 2),
            "gap_down_open_pct": round(gap_down_open_pct, 2),
            "range_recovery_ratio": round(range_recovery_ratio, 2),
            "soft_passed_count_base": soft_passed_count,
            "signal_quality_count": soft_passed_count,
            "intraday_pullback_strength_score": round(intraday_pullback_strength_score, 2),
            "rebound_from_low_strength_score": round(rebound_from_low_strength_score, 2),
            "controlled_down_strength_score": round(controlled_down_strength_score, 2),
            "gap_down_open_strength_score": round(gap_down_open_strength_score, 2),
            "range_recovery_strength_score": round(range_recovery_strength_score, 2),
            "live_volume_rank_strength_score": round(live_volume_rank_strength_score, 2),
            "live_volume_power_rank_strength_score": round(
                live_volume_power_rank_strength_score,
                2,
            ),
            "intraday_pullback_bonus": round(intraday_pullback_bonus, 2),
            "rebound_from_low_bonus": round(rebound_from_low_bonus, 2),
            "controlled_down_bonus": round(controlled_down_bonus, 2),
            "gap_down_open_bonus": round(gap_down_open_bonus, 2),
            "range_recovery_bonus": round(range_recovery_bonus, 2),
            "trend_quality_score": round(trend_quality_score, 2),
            "momentum_quality_score": round(momentum_quality_score, 2),
            "price_efficiency_score": round(price_efficiency_score, 2),
            "gap_up_open_pct": round(gap_up_open_pct, 2),
            "overheat_penalty": round(overheat_penalty, 2),
            "pullback_exhaustion_penalty": round(pullback_exhaustion_penalty, 2),
        },
    )


def build_analysis_sort_key(
    *,
    passed_count: int,
    score: float,
    symbol: str,
) -> tuple[int, float, str]:
    return (-passed_count, -score, symbol)


def _score_component_labels() -> dict[str, str]:
    return {
        "trend_quality_score": "trend_quality",
        "momentum_quality_score": "momentum_quality",
        "price_efficiency_score": "price_efficiency",
        "mean_reversion_bonus": "mean_reversion",
        "diversification_bonus": "diversification",
        "cost_quality_score": "cost_quality",
        "intraday_pullback_bonus": "intraday_pullback",
        "rebound_from_low_bonus": "rebound_from_low",
        "controlled_down_bonus": "controlled_down_day",
        "gap_down_open_bonus": "gap_down_open",
        "range_recovery_bonus": "range_recovery",
        "overheat_penalty": "overheat_context",
        "overextension_penalty": "overextension",
        "pullback_exhaustion_penalty": "pullback_exhaustion",
        "portfolio_correlation_penalty": "portfolio_similarity",
        "variance_increase_penalty": "variance_increase",
        "expected_cost_penalty": "expected_cost",
        "hist_range_bonus": "hist_range_low",
        "velocity_bonus": "velocity_decel",
        "pullback_depth_bonus": "deep_pullback",
        "hist_range_penalty": "hist_range_high",
        "velocity_penalty": "velocity_freefall",
        "trend_alignment_score": "trend_alignment",
        "macd_momentum_score": "macd_momentum",
    }


def summarize_score_breakdown(score_components: dict[str, float]) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    labels = _score_component_labels()
    positives: list[tuple[str, float]] = []
    negatives: list[tuple[str, float]] = []
    for key, label in labels.items():
        value = float(score_components.get(key, 0.0) or 0.0)
        if "penalty" in key:
            if value > 0:
                negatives.append((label, value))
        elif value > 0:
            positives.append((label, value))

    positives = sorted(positives, key=lambda item: item[1], reverse=True)
    negatives = sorted(negatives, key=lambda item: item[1], reverse=True)
    top_positives = tuple(item[0] for item in positives[:3])
    top_negatives = tuple(item[0] for item in negatives[:2])

    positive_text = ", ".join(top_positives) if top_positives else "뚜렷한 가산 요인 없음"
    negative_text = ", ".join(top_negatives) if top_negatives else "주요 감점 요인 없음"
    summary = f"가산: {positive_text} | 감점: {negative_text}"
    return top_positives, top_negatives, summary
