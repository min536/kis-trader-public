import math
from statistics import mean, pstdev
from typing import Any

from app.market_data.schema import MarketSnapshot
from app.math_models.history import get_recent_symbol_price_history


def _half_life_from_prices(prices: list[float]) -> float | None:
    if len(prices) < 6:
        return None
    mu = mean(prices)
    deviations = [price - mu for price in prices]
    x = deviations[:-1]
    y = deviations[1:]
    if len(x) < 3:
        return None

    x_mean = mean(x)
    y_mean = mean(y)
    numerator = sum((x_i - x_mean) * (y_i - y_mean) for x_i, y_i in zip(x, y))
    denominator = sum((x_i - x_mean) ** 2 for x_i in x)
    if denominator <= 0:
        return None
    phi = numerator / denominator
    if phi <= 0 or phi >= 1:
        return None
    try:
        return round(math.log(2) / -math.log(phi), 2)
    except (ValueError, ZeroDivisionError):
        return None


def build_mean_reversion_features(
    *,
    symbol: str,
    snapshot: MarketSnapshot,
    score_components: dict[str, float],
    window: int = 20,
    min_observations: int = 6,
) -> dict[str, Any]:
    history = get_recent_symbol_price_history(symbol, limit=max(window * 3, 30))
    prices = [float(item.get("price", 0) or 0) for item in history if float(item.get("price", 0) or 0) > 0]
    if len(prices) < min_observations:
        return {
            "mean_reversion_zscore": None,
            "distance_from_rolling_mean_pct": None,
            "reversion_quality_score": 0.0,
            "mean_reversion_bonus": 0.0,
            "overextension_penalty": 0.0,
            "ou_half_life_estimate": None,
            "volatility_adjusted_pullback": None,
            "mean_reversion_summary": "평균 복귀 데이터 부족",
            "data_sufficient": False,
            "history_observation_count": len(prices),
        }

    window_prices = prices[-window:]
    rolling_mean = mean(window_prices)
    rolling_std = pstdev(window_prices)
    if rolling_std <= 0:
        return {
            "mean_reversion_zscore": None,
            "distance_from_rolling_mean_pct": None,
            "reversion_quality_score": 0.0,
            "mean_reversion_bonus": 0.0,
            "overextension_penalty": 0.0,
            "ou_half_life_estimate": _half_life_from_prices(window_prices),
            "volatility_adjusted_pullback": None,
            "mean_reversion_summary": "평균 복귀 분산 부족",
            "data_sufficient": False,
            "history_observation_count": len(prices),
        }

    zscore = (float(snapshot.current_price) - rolling_mean) / rolling_std
    distance_pct = ((float(snapshot.current_price) / rolling_mean) - 1.0) * 100 if rolling_mean > 0 else None
    rebound_bonus = float(score_components.get("rebound_from_low_bonus", 0.0) or 0.0)
    recovery_bonus = float(score_components.get("range_recovery_bonus", 0.0) or 0.0)
    trend_quality = float(score_components.get("trend_quality_score", 0.0) or 0.0)

    reversion_quality_score = 0.0
    if -1.4 <= zscore <= -0.2 and (rebound_bonus > 0 or recovery_bonus > 0):
        reversion_quality_score += 0.25
    elif -0.6 <= zscore <= 0.6 and trend_quality > 0.3:
        reversion_quality_score += 0.12

    overextension_penalty = 0.0
    if zscore >= 1.4:
        overextension_penalty += min((zscore - 1.4) * 0.18, 0.45)
    if zscore <= -2.0 and rebound_bonus <= 0 and recovery_bonus <= 0:
        overextension_penalty += min((abs(zscore) - 2.0) * 0.12, 0.18)

    volatility_adjusted_pullback = round((abs(zscore) / max(rolling_std, 1.0)) * 100, 2)
    half_life = _half_life_from_prices(window_prices)

    if zscore >= 1.4:
        summary = "평균 대비 과열"
    elif -1.4 <= zscore <= -0.2 and reversion_quality_score > 0:
        summary = "적절한 눌림"
    elif zscore <= -1.6 and reversion_quality_score <= 0:
        summary = "평균 복귀 매력 낮음"
    else:
        summary = "평균 복귀 관점 양호"

    return {
        "mean_reversion_zscore": round(zscore, 3),
        "distance_from_rolling_mean_pct": round(distance_pct, 2) if distance_pct is not None else None,
        "reversion_quality_score": round(reversion_quality_score, 2),
        "mean_reversion_bonus": round(reversion_quality_score, 2),
        "overextension_penalty": round(overextension_penalty, 2),
        "ou_half_life_estimate": half_life,
        "volatility_adjusted_pullback": volatility_adjusted_pullback,
        "mean_reversion_summary": summary,
        "data_sufficient": True,
        "history_observation_count": len(prices),
    }
