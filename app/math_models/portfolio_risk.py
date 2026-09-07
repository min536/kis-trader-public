import math
from typing import Any

from app.math_models.history import get_recent_symbol_price_history


def _mean(values: list[float]) -> float:
    return math.fsum(values) / len(values)


def _pstdev(values: list[float]) -> float:
    # fsum-based population stdev: statistics.pstdev's Fraction exact-sum kernel
    # is prohibitively slow on the replay's per-pair call volume (S4-2/B3).
    mean_value = math.fsum(values) / len(values)
    return math.sqrt(
        math.fsum((value - mean_value) ** 2 for value in values) / len(values)
    )


def _returns_by_timestamp(history: list[dict[str, Any]]) -> dict[str, float]:
    returns: dict[str, float] = {}
    previous_price: float | None = None
    for item in history:
        timestamp = str(item.get("timestamp", "")).strip()
        price = float(item.get("price", 0) or 0)
        if not timestamp or price <= 0:
            continue
        if previous_price and previous_price > 0:
            returns[timestamp] = (price / previous_price) - 1.0
        previous_price = price
    return returns


def _aligned_return_pairs(
    left_returns: dict[str, float],
    right_returns: dict[str, float],
) -> tuple[list[float], list[float]]:
    common_timestamps = sorted(set(left_returns) & set(right_returns))
    return (
        [left_returns[timestamp] for timestamp in common_timestamps],
        [right_returns[timestamp] for timestamp in common_timestamps],
    )


def _symbol_returns_cached(
    scan_cache: dict[tuple[str, int], tuple[list[dict[str, Any]], dict[str, float]]] | None,
    symbol: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if scan_cache is None:
        history = get_recent_symbol_price_history(symbol, limit=limit)
        return history, _returns_by_timestamp(history)
    cache_key = (symbol, limit)
    cached = scan_cache.get(cache_key)
    if cached is not None:
        return cached
    history = get_recent_symbol_price_history(symbol, limit=limit)
    returns = _returns_by_timestamp(history)
    scan_cache[cache_key] = (history, returns)
    return history, returns


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 4 or len(right) < 4 or len(left) != len(right):
        return None
    left_mean = _mean(left)
    right_mean = _mean(right)
    numerator = sum((l - left_mean) * (r - right_mean) for l, r in zip(left, right))
    left_var = sum((l - left_mean) ** 2 for l in left)
    right_var = sum((r - right_mean) ** 2 for r in right)
    denominator = math.sqrt(left_var * right_var)
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def build_portfolio_risk_features(
    *,
    candidate_symbol: str,
    current_price: int,
    portfolio_snapshot,
    candidate_qty: int = 1,
    history_window: int = 20,
    min_observations: int = 6,
    scan_cache: dict[tuple[str, int], tuple[list[dict[str, Any]], dict[str, float]]] | None = None,
) -> dict[str, Any]:
    held_positions = tuple(getattr(portfolio_snapshot, "held_positions", ()) or ())
    comparable_positions = [position for position in held_positions if position.symbol != candidate_symbol]
    if not comparable_positions:
        return {
            "portfolio_avg_correlation": None,
            "portfolio_max_correlation": None,
            "variance_increase_estimate": None,
            "portfolio_correlation_penalty": 0.0,
            "variance_increase_penalty": 0.0,
            "diversification_bonus": 0.0,
            "portfolio_risk_summary": "포트폴리오 비교 대상 부족",
            "data_sufficient": False,
            "observation_count": 0,
        }

    candidate_history, candidate_return_pairs = _symbol_returns_cached(
        scan_cache, candidate_symbol, max(history_window * 3, 30)
    )
    if len(candidate_history) < min_observations:
        return {
            "portfolio_avg_correlation": None,
            "portfolio_max_correlation": None,
            "variance_increase_estimate": None,
            "portfolio_correlation_penalty": 0.0,
            "variance_increase_penalty": 0.0,
            "diversification_bonus": 0.0,
            "portfolio_risk_summary": "포트폴리오 위험 데이터 부족",
            "data_sufficient": False,
            "observation_count": len(candidate_history),
        }

    correlations: list[float] = []
    weighted_covariances: list[tuple[float, float]] = []
    candidate_returns_all = list(candidate_return_pairs.values())
    if len(candidate_returns_all) < min_observations:
        return {
            "portfolio_avg_correlation": None,
            "portfolio_max_correlation": None,
            "variance_increase_estimate": None,
            "portfolio_correlation_penalty": 0.0,
            "variance_increase_penalty": 0.0,
            "diversification_bonus": 0.0,
            "portfolio_risk_summary": "포트폴리오 위험 데이터 부족",
            "data_sufficient": False,
            "observation_count": len(candidate_returns_all),
        }

    candidate_std = _pstdev(candidate_returns_all) if len(candidate_returns_all) >= 2 else 0.0
    holdings_market_value = sum(int(position.market_value or 0) for position in comparable_positions)
    operating_equity = holdings_market_value + int(getattr(portfolio_snapshot, "cash_orderable", 0) or 0)
    if operating_equity <= 0:
        operating_equity = max(holdings_market_value, 1)

    current_portfolio_var = 0.0
    for position in comparable_positions:
        holding_history, holding_returns = _symbol_returns_cached(
            scan_cache, position.symbol, max(history_window * 3, 30)
        )
        left, right = _aligned_return_pairs(candidate_return_pairs, holding_returns)
        corr = _correlation(left, right)
        if corr is None:
            continue
        correlations.append(corr)
        holding_returns = right
        holding_std = _pstdev(holding_returns) if len(holding_returns) >= 2 else 0.0
        weight = max(0.0, float(position.market_value or 0) / max(holdings_market_value, 1))
        current_portfolio_var += (weight ** 2) * (holding_std ** 2)
        weighted_covariances.append((weight, corr * holding_std * candidate_std))

    if not correlations:
        return {
            "portfolio_avg_correlation": None,
            "portfolio_max_correlation": None,
            "variance_increase_estimate": None,
            "portfolio_correlation_penalty": 0.0,
            "variance_increase_penalty": 0.0,
            "diversification_bonus": 0.0,
            "portfolio_risk_summary": "포트폴리오 위험 비교 데이터 부족",
            "data_sufficient": False,
            "observation_count": len(candidate_returns_all),
        }

    avg_corr = _mean(correlations)
    max_corr = max(correlations)
    candidate_weight = min(max((int(current_price) * max(int(candidate_qty), 1)) / max(operating_equity, 1), 0.01), 0.08)
    avg_cov = sum(weight * covariance for weight, covariance in weighted_covariances)
    new_portfolio_var = (
        ((1.0 - candidate_weight) ** 2) * current_portfolio_var
        + (candidate_weight ** 2) * (candidate_std ** 2)
        + 2.0 * candidate_weight * (1.0 - candidate_weight) * avg_cov
    )
    variance_increase = None
    if current_portfolio_var > 0:
        variance_increase = (new_portfolio_var - current_portfolio_var) / current_portfolio_var

    correlation_penalty = max(0.0, avg_corr - 0.55) * 0.6 + max(0.0, max_corr - 0.75) * 0.4
    variance_penalty = max(0.0, float(variance_increase or 0.0) - 0.05) * 2.5
    diversification_bonus = 0.15 if avg_corr < 0.2 and (variance_increase is None or variance_increase <= 0.02) else 0.0

    if avg_corr >= 0.7 or (variance_increase is not None and variance_increase >= 0.1):
        summary = "보유 종목과 유사도 높음"
    elif avg_corr <= 0.2 and diversification_bonus > 0:
        summary = "분산효과 양호"
    elif variance_increase is not None and variance_increase > 0.05:
        summary = "포트폴리오 위험 증가"
    else:
        summary = "포트폴리오 위험 중립"

    return {
        "portfolio_avg_correlation": round(avg_corr, 3),
        "portfolio_max_correlation": round(max_corr, 3),
        "variance_increase_estimate": round(float(variance_increase), 4) if variance_increase is not None else None,
        "portfolio_correlation_penalty": round(correlation_penalty, 2),
        "variance_increase_penalty": round(variance_penalty, 2),
        "diversification_bonus": round(diversification_bonus, 2),
        "portfolio_risk_summary": summary,
        "data_sufficient": True,
        "observation_count": len(candidate_returns_all),
    }
