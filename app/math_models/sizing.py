import math
from typing import Any


def build_math_sizing_overlay(candidate) -> dict[str, Any]:
    mean_reversion_multiplier = 1.0
    portfolio_multiplier = 1.0
    cost_multiplier = 1.0
    reasons: list[str] = []

    zscore = candidate.mean_reversion_zscore
    reversion_quality = float(candidate.reversion_quality_score or 0.0)
    if zscore is not None:
        if zscore >= 1.6:
            mean_reversion_multiplier *= 0.72
            reasons.append("평균 대비 과열로 예산 축소")
        elif zscore >= 0.9:
            mean_reversion_multiplier *= 0.85
            reasons.append("평균 대비 다소 고평가")
        elif -1.4 <= zscore <= -0.2 and reversion_quality > 0:
            mean_reversion_multiplier *= 1.05
            reasons.append("눌림 후 회복 구간")
        elif zscore <= -2.0 and reversion_quality <= 0:
            mean_reversion_multiplier *= 0.88
            reasons.append("과매도지만 회복 확인 약함")

    avg_corr = candidate.portfolio_avg_correlation
    variance_increase = candidate.variance_increase_estimate
    if avg_corr is not None:
        if avg_corr >= 0.75 or (variance_increase is not None and variance_increase >= 0.12):
            portfolio_multiplier *= 0.65
            reasons.append("포트폴리오 유사도 높아 수량 축소")
        elif avg_corr >= 0.55 or (variance_increase is not None and variance_increase >= 0.06):
            portfolio_multiplier *= 0.8
            reasons.append("포트폴리오 위험 증가 우려")
        elif str(candidate.portfolio_risk_summary or "") == "분산효과 양호":
            portfolio_multiplier *= 1.05
            reasons.append("분산효과 양호")

    expected_cost_penalty = float(candidate.expected_cost_penalty or 0.0)
    net_edge_bps = float(candidate.net_edge_bps or 0.0)
    if expected_cost_penalty > 0:
        cost_multiplier *= max(0.7, 1.0 - min(expected_cost_penalty * 0.08, 0.28))
        reasons.append("비용 품질 반영")
    if net_edge_bps < 20:
        cost_multiplier *= 0.92
        reasons.append("순우위가 작아 예산 보수화")

    effective_multiplier = max(
        0.35,
        min(1.1, mean_reversion_multiplier * portfolio_multiplier * cost_multiplier),
    )
    summary = (
        ", ".join(reasons[:3])
        if reasons
        else "수학적 보정 없이 기본 수량 유지"
    )
    return {
        "sizing_mean_reversion_multiplier": round(mean_reversion_multiplier, 2),
        "sizing_portfolio_multiplier": round(portfolio_multiplier, 2),
        "sizing_cost_multiplier": round(cost_multiplier, 2),
        "effective_math_multiplier": round(effective_multiplier, 2),
        "math_sizing_reasons": reasons,
        "math_sizing_summary": summary,
    }


def apply_math_overlay_to_limits(
    *,
    max_budget_per_trade_krw: int,
    max_account_exposure_pct: float,
    max_qty_per_trade: int,
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    multiplier = float((overlay or {}).get("effective_math_multiplier", 1.0) or 1.0)
    return {
        "max_budget_per_trade_krw": max(1, int(round(max_budget_per_trade_krw * multiplier))),
        "max_account_exposure_pct": max(0.1, round(max_account_exposure_pct * multiplier, 2)),
        "max_qty_per_trade": max(1, int(math.floor(max_qty_per_trade * max(multiplier, 0.34)))),
        "effective_math_multiplier": round(multiplier, 2),
    }
