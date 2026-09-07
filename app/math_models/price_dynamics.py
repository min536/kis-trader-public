"""Price dynamics features."""

from __future__ import annotations

from statistics import mean
from typing import Any

from app.market_data.schema import MarketSnapshot
from app.math_models.history import get_recent_symbol_price_history


def _ols_slope(values: list[float]) -> float | None:
    count = len(values)
    if count < 3:
        return None
    x_mean = (count - 1) / 2.0
    y_mean = mean(values)
    numerator = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    if denominator <= 0:
        return None
    return numerator / denominator


def _percentile_rank(history_prices: list[float], current_price: float) -> float:
    if not history_prices:
        return 0.5
    return sum(1 for price in history_prices if price <= current_price) / len(history_prices)


def build_price_dynamics_features(
    *,
    symbol: str,
    snapshot: MarketSnapshot,
    score_components: dict[str, float],
    window: int = 20,
    min_observations: int = 6,
) -> dict[str, Any]:
    history = get_recent_symbol_price_history(symbol, limit=max(window * 3, 30))
    price_items = [item for item in history if float(item.get("price", 0) or 0) > 0]
    prices = [float(item["price"]) for item in price_items]

    if len(prices) < min_observations:
        return {
            "hist_percentile_rank": None,
            "hist_range_position": None,
            "hist_range_bonus": 0.0,
            "hist_range_penalty": 0.0,
            "price_velocity_pct": None,
            "velocity_deceleration_pct": None,
            "velocity_bonus": 0.0,
            "velocity_penalty": 0.0,
            "hist_avg_pullback_pct": None,
            "pullback_depth_ratio": None,
            "pullback_depth_bonus": 0.0,
            "price_dynamics_summary": "가격 이력 데이터 부족",
            "data_sufficient": False,
            "observation_count": len(prices),
        }

    current_price = float(snapshot.current_price)
    window_prices = prices[-window:]
    mean_price = mean(window_prices)
    if mean_price <= 0:
        mean_price = max(current_price, 1.0)

    rebound_bonus = float(score_components.get("rebound_from_low_bonus", 0.0) or 0.0)
    recovery_bonus = float(score_components.get("range_recovery_bonus", 0.0) or 0.0)
    intraday_signal = rebound_bonus > 0 or recovery_bonus > 0

    hist_min = min(window_prices)
    hist_max = max(window_prices)
    hist_range = hist_max - hist_min
    hist_range_position = (
        max(0.0, min(1.0, (current_price - hist_min) / hist_range))
        if hist_range > 0
        else 0.5
    )
    percentile_rank = _percentile_rank(window_prices, current_price)

    hist_range_bonus = 0.0
    if percentile_rank <= 0.20 and intraday_signal:
        hist_range_bonus = 0.20
    elif percentile_rank <= 0.35 and intraday_signal:
        hist_range_bonus = 0.10

    hist_range_penalty = 0.0
    if percentile_rank >= 0.90:
        hist_range_penalty = round(min((percentile_rank - 0.90) / 0.10 * 0.20, 0.20), 2)
    elif percentile_rank >= 0.80:
        hist_range_penalty = round(min((percentile_rank - 0.80) / 0.10 * 0.10, 0.10), 2)

    recent_count = min(8, len(prices))
    older_count = min(16, len(prices))
    recent_prices = prices[-recent_count:]
    older_prices = prices[-older_count:-recent_count] if older_count > recent_count else []

    recent_slope = _ols_slope(recent_prices)
    older_slope = _ols_slope(older_prices) if len(older_prices) >= 3 else None

    price_velocity_pct: float | None = None
    velocity_deceleration_pct: float | None = None
    if recent_slope is not None:
        price_velocity_pct = round((recent_slope / mean_price) * 100, 4)
        if older_slope is not None:
            older_velocity = (older_slope / mean_price) * 100
            velocity_deceleration_pct = round(price_velocity_pct - older_velocity, 4)

    velocity_bonus = 0.0
    velocity_penalty = 0.0
    if price_velocity_pct is not None:
        if (
            price_velocity_pct < -0.05
            and velocity_deceleration_pct is not None
            and velocity_deceleration_pct > 0
        ):
            velocity_bonus = round(min(velocity_deceleration_pct * 0.5, 0.15), 2)
        elif price_velocity_pct > 0.5:
            velocity_bonus = round(min((price_velocity_pct - 0.5) * 0.08, 0.10), 2)
        if price_velocity_pct < -0.30:
            velocity_penalty = round(min(abs(price_velocity_pct + 0.30) * 0.40, 0.20), 2)

    open_items = [
        item for item in price_items[-window:]
        if float(item.get("open_price", 0) or 0) > 0
    ]
    historical_pullbacks = [
        max((float(item["open_price"]) - float(item["price"])) / float(item["open_price"]) * 100, 0.0)
        for item in open_items
    ]
    today_pullback_pct = float(score_components.get("pullback_pct", 0.0) or 0.0)
    historical_avg_pullback = mean(historical_pullbacks) if historical_pullbacks else None
    pullback_depth_ratio: float | None = None
    pullback_depth_bonus = 0.0
    if historical_avg_pullback is not None and historical_avg_pullback > 0.05 and today_pullback_pct > 0:
        pullback_depth_ratio = round(today_pullback_pct / historical_avg_pullback, 3)
        if 1.2 <= pullback_depth_ratio <= 3.0 and intraday_signal:
            pullback_depth_bonus = round(min((pullback_depth_ratio - 1.2) / 1.8 * 0.10, 0.10), 2)

    summary_parts: list[str] = []
    if percentile_rank <= 0.25:
        summary_parts.append("역사적 저점 근방")
    elif percentile_rank >= 0.85:
        summary_parts.append("역사적 고점 근방")

    if price_velocity_pct is not None:
        if (
            price_velocity_pct < -0.05
            and velocity_deceleration_pct is not None
            and velocity_deceleration_pct > 0
        ):
            summary_parts.append("낙폭 둔화 중")
        elif price_velocity_pct < -0.30:
            summary_parts.append("하락 지속")
        elif price_velocity_pct > 0.30:
            summary_parts.append("상승 모멘텀")

    if pullback_depth_ratio is not None and pullback_depth_ratio >= 1.2:
        summary_parts.append(f"평소 대비 {pullback_depth_ratio:.1f}x 눌림")

    return {
        "hist_percentile_rank": round(percentile_rank, 3),
        "hist_range_position": round(hist_range_position, 3),
        "hist_range_bonus": round(hist_range_bonus, 2),
        "hist_range_penalty": round(hist_range_penalty, 2),
        "price_velocity_pct": price_velocity_pct,
        "velocity_deceleration_pct": velocity_deceleration_pct,
        "velocity_bonus": round(velocity_bonus, 2),
        "velocity_penalty": round(velocity_penalty, 2),
        "hist_avg_pullback_pct": (
            round(historical_avg_pullback, 3) if historical_avg_pullback is not None else None
        ),
        "pullback_depth_ratio": pullback_depth_ratio,
        "pullback_depth_bonus": round(pullback_depth_bonus, 2),
        "price_dynamics_summary": " / ".join(summary_parts) if summary_parts else "가격 동학 중립",
        "data_sufficient": True,
        "observation_count": len(prices),
    }
