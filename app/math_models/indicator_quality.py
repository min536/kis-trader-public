"""Shadow indicator quality features for buy-candidate analysis."""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from app.market_data.schema import MarketSnapshot
from app.math_models.history import (
    append_current_snapshot_observation,
    get_recent_symbol_price_history,
)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _score(value: float, weight: float) -> float:
    return round(_clamp(value) * weight, 2)


def _percentile_rank(values: list[float], current: float) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value <= current) / len(values)


def _pct_change(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return ((current / previous) - 1.0) * 100.0


def _rsi(prices: list[float], period: int = 14) -> float | None:
    if len(prices) <= period:
        return None
    changes = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
    recent_changes = changes[-period:]
    gains = [max(change, 0.0) for change in recent_changes]
    losses = [abs(min(change, 0.0)) for change in recent_changes]
    avg_gain = mean(gains)
    avg_loss = mean(losses)
    if avg_gain <= 0 and avg_loss <= 0:
        return 50.0
    if avg_loss <= 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _rolling_band_widths(prices: list[float], window: int) -> list[float]:
    widths: list[float] = []
    if len(prices) < window:
        return widths
    for end in range(window, len(prices) + 1):
        window_prices = prices[end - window:end]
        center = mean(window_prices)
        if center <= 0:
            continue
        deviation = pstdev(window_prices)
        widths.append((4.0 * deviation / center) * 100.0)
    return widths


def _approx_true_range_pct_rows(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    previous_close: float | None = None
    for row in rows:
        try:
            close = float(row.get("price", 0) or 0)
            open_price = float(row.get("open_price", 0) or 0)
            low_price = float(row.get("low_price", 0) or 0)
        except (TypeError, ValueError):
            previous_close = None
            continue
        if close <= 0:
            previous_close = None
            continue
        high = max(close, open_price if open_price > 0 else close)
        low = min(close, low_price if low_price > 0 else close)
        if previous_close is None or previous_close <= 0:
            true_range = max(high - low, 0.0)
        else:
            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        values.append((true_range / close) * 100.0 if close > 0 else 0.0)
        previous_close = close
    return values


def build_indicator_quality_features(
    *,
    symbol: str,
    snapshot: MarketSnapshot,
    score_components: dict[str, float],
    window: int = 20,
    min_observations: int = 15,
) -> dict[str, Any]:
    history = get_recent_symbol_price_history(symbol, limit=max(window * 3, 60))
    history = append_current_snapshot_observation(
        history,
        symbol=symbol,
        snapshot=snapshot,
    )
    price_items = [item for item in history if float(item.get("price", 0) or 0) > 0]
    prices = [float(item["price"]) for item in price_items]

    empty = {
        "rsi_value": None,
        "rsi_recovery_score": 0.0,
        "rsi_overheat_penalty": 0.0,
        "bollinger_band_position": None,
        "bollinger_rebound_score": 0.0,
        "bollinger_upper_penalty": 0.0,
        "bollinger_band_width_pct": None,
        "bollinger_width_percentile": None,
        "bollinger_squeeze_score": 0.0,
        "bollinger_expansion_penalty": 0.0,
        "sma_short": None,
        "sma_medium": None,
        "sma_support_score": 0.0,
        "sma_breakdown_penalty": 0.0,
        "atrp_pct": None,
        "atrp_balance_score": 0.0,
        "atrp_volatility_penalty": 0.0,
        "indicator_quality_summary": "지표 품질 데이터 부족",
        "data_sufficient": False,
        "observation_count": len(prices),
    }
    if len(prices) < min_observations:
        return empty

    current_price = float(snapshot.current_price)
    if current_price <= 0:
        return empty

    rebound_signal = float(score_components.get("rebound_from_low_bonus", 0.0) or 0.0)
    recovery_signal = float(score_components.get("range_recovery_bonus", 0.0) or 0.0)
    rebound_strength = float(
        score_components.get("rebound_from_low_strength_score", 0.0) or 0.0
    )
    recovery_strength = float(
        score_components.get("range_recovery_strength_score", 0.0) or 0.0
    )
    intraday_signal_strength = max(
        rebound_signal * 200.0,
        recovery_signal * 200.0,
        rebound_strength,
        recovery_strength,
    )
    has_intraday_recovery = intraday_signal_strength >= 50.0

    rsi_value = _rsi(prices)
    rsi_recovery_score = 0.0
    rsi_overheat_penalty = 0.0
    if rsi_value is not None:
        if 35.0 <= rsi_value <= 60.0 and has_intraday_recovery:
            rsi_recovery_score = _score(1.0 - abs(rsi_value - 45.0) / 25.0, 0.18)
        elif 30.0 <= rsi_value < 35.0 and has_intraday_recovery:
            rsi_recovery_score = _score((rsi_value - 30.0) / 5.0, 0.10)
        elif 60.0 < rsi_value <= 70.0 and float(
            score_components.get("trend_quality_score", 0.0) or 0.0
        ) > 0.4:
            rsi_recovery_score = 0.05
        if rsi_value > 70.0:
            rsi_overheat_penalty = _score((rsi_value - 70.0) / 20.0, 0.18)
        elif rsi_value < 25.0 and not has_intraday_recovery:
            rsi_overheat_penalty = _score((25.0 - rsi_value) / 25.0, 0.12)

    window_prices = prices[-window:]
    center = mean(window_prices)
    deviation = pstdev(window_prices)
    upper_band = center + (2.0 * deviation)
    lower_band = center - (2.0 * deviation)
    band_width = upper_band - lower_band
    band_position = None
    if band_width > 0:
        band_position = _clamp((current_price - lower_band) / band_width)

    bollinger_rebound_score = 0.0
    bollinger_upper_penalty = 0.0
    if band_position is not None:
        if band_position <= 0.40 and has_intraday_recovery:
            bollinger_rebound_score = _score((0.40 - band_position) / 0.40, 0.18)
        elif band_position <= 0.55 and recovery_signal > 0:
            bollinger_rebound_score = 0.06
        if band_position >= 0.92:
            bollinger_upper_penalty = _score((band_position - 0.92) / 0.08, 0.12)

    band_width_pct = ((band_width / center) * 100.0) if center > 0 else None
    band_widths = _rolling_band_widths(prices, window)
    width_percentile = (
        _percentile_rank(band_widths, band_width_pct)
        if band_width_pct is not None and len(band_widths) >= 5
        else None
    )
    recent_return_pct = (
        _pct_change(prices[-1], prices[-4]) if len(prices) >= 4 else None
    )
    bollinger_squeeze_score = 0.0
    bollinger_expansion_penalty = 0.0
    if width_percentile is not None:
        if width_percentile <= 0.30 and (
            has_intraday_recovery
            or float(recent_return_pct or 0.0) > 0.0
        ):
            bollinger_squeeze_score = _score((0.30 - width_percentile) / 0.30, 0.12)
        elif width_percentile >= 0.90:
            bollinger_expansion_penalty = _score((width_percentile - 0.90) / 0.10, 0.08)

    short_window = min(5, len(prices))
    medium_window = min(20, len(prices))
    sma_short = mean(prices[-short_window:])
    sma_medium = mean(prices[-medium_window:])
    previous_sma_medium = (
        mean(prices[-medium_window - 1:-1])
        if len(prices) > medium_window
        else sma_medium
    )
    medium_slope_pct = _pct_change(sma_medium, previous_sma_medium) or 0.0
    distance_to_medium_pct = _pct_change(current_price, sma_medium) or 0.0
    sma_support_score = 0.0
    sma_breakdown_penalty = 0.0
    if current_price >= sma_short >= sma_medium and medium_slope_pct >= 0:
        sma_support_score = 0.18
    elif (
        abs(distance_to_medium_pct) <= 1.2
        and medium_slope_pct >= -0.05
        and has_intraday_recovery
    ):
        sma_support_score = _score(1.0 - abs(distance_to_medium_pct) / 1.2, 0.14)
    elif current_price > sma_medium and sma_short >= sma_medium:
        sma_support_score = 0.08
    if current_price < sma_medium and medium_slope_pct < -0.10:
        sma_breakdown_penalty = _score(abs(distance_to_medium_pct) / 2.5, 0.14)

    trp_values = _approx_true_range_pct_rows(price_items[-15:])
    atrp_pct = mean(trp_values[-14:]) if trp_values else None
    atrp_balance_score = 0.0
    atrp_volatility_penalty = 0.0
    if atrp_pct is not None:
        if 0.20 <= atrp_pct <= 2.50:
            atrp_balance_score = _score(1.0 - abs(atrp_pct - 1.00) / 1.50, 0.12)
        elif 2.50 < atrp_pct <= 3.50 and has_intraday_recovery:
            atrp_balance_score = 0.04
        if atrp_pct > 4.0:
            atrp_volatility_penalty = _score((atrp_pct - 4.0) / 4.0, 0.16)

    summary_parts: list[str] = []
    if rsi_recovery_score > 0:
        summary_parts.append("RSI 회복권")
    if bollinger_rebound_score > 0:
        summary_parts.append("Bollinger 하단 반등")
    if bollinger_squeeze_score > 0:
        summary_parts.append("Bollinger squeeze")
    if sma_support_score > 0:
        summary_parts.append("SMA 지지")
    if atrp_balance_score > 0:
        summary_parts.append("ATRP 균형")
    if rsi_overheat_penalty or bollinger_upper_penalty or atrp_volatility_penalty:
        summary_parts.append("지표 리스크")

    return {
        "rsi_value": round(rsi_value, 2) if rsi_value is not None else None,
        "rsi_recovery_score": round(rsi_recovery_score, 2),
        "rsi_overheat_penalty": round(rsi_overheat_penalty, 2),
        "bollinger_band_position": (
            round(band_position, 3) if band_position is not None else None
        ),
        "bollinger_rebound_score": round(bollinger_rebound_score, 2),
        "bollinger_upper_penalty": round(bollinger_upper_penalty, 2),
        "bollinger_band_width_pct": (
            round(band_width_pct, 3) if band_width_pct is not None else None
        ),
        "bollinger_width_percentile": (
            round(width_percentile, 3) if width_percentile is not None else None
        ),
        "bollinger_squeeze_score": round(bollinger_squeeze_score, 2),
        "bollinger_expansion_penalty": round(bollinger_expansion_penalty, 2),
        "sma_short": round(sma_short, 2),
        "sma_medium": round(sma_medium, 2),
        "sma_support_score": round(sma_support_score, 2),
        "sma_breakdown_penalty": round(sma_breakdown_penalty, 2),
        "atrp_pct": round(atrp_pct, 3) if atrp_pct is not None else None,
        "atrp_balance_score": round(atrp_balance_score, 2),
        "atrp_volatility_penalty": round(atrp_volatility_penalty, 2),
        "indicator_quality_summary": (
            " / ".join(summary_parts) if summary_parts else "지표 품질 중립"
        ),
        "data_sufficient": True,
        "observation_count": len(prices),
    }
