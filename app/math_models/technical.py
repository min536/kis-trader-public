from typing import Any

from app.math_models.history import (
    append_current_snapshot_observation,
    get_recent_symbol_price_history,
)

TECHNICAL_HISTORY_LIMIT = 3000
MIN_TECHNICAL_HISTORY_OBSERVATIONS = 12
FULL_TECHNICAL_HISTORY_OBSERVATIONS = 26


def _calc_ema(prices: list[float], window: int) -> list[float]:
    if not prices or window <= 0:
        return []
    alpha = 2.0 / (window + 1.0)
    emas = [prices[0]]
    for p in prices[1:]:
        emas.append(p * alpha + emas[-1] * (1.0 - alpha))
    return emas


def build_technical_features(
    symbol: str,
    *,
    current_snapshot: object | None = None,
) -> dict[str, Any]:
    history = get_recent_symbol_price_history(symbol, limit=TECHNICAL_HISTORY_LIMIT)
    history = append_current_snapshot_observation(
        history,
        symbol=symbol,
        snapshot=current_snapshot,
    )
    prices = [float(item.get("price", 0) or 0) for item in history if float(item.get("price", 0) or 0) > 0]
    observation_count = len(prices)

    if observation_count < MIN_TECHNICAL_HISTORY_OBSERVATIONS:
        return {
            "trend_alignment_score": 0.0,
            "macd_momentum_score": 0.0,
            "macd_value": None,
            "macd_signal": None,
            "macd_histogram": None,
            "short_ma": None,
            "medium_ma": None,
            "technical_summary": "기술적분석 데이터부족",
            "history_observation_count": observation_count,
            "history_usable": False,
            "history_full_confidence": False,
            "history_confidence": 0.0,
        }

    confidence = min(
        max(float(observation_count), float(MIN_TECHNICAL_HISTORY_OBSERVATIONS))
        / float(FULL_TECHNICAL_HISTORY_OBSERVATIONS),
        1.0,
    )

    # Trend Alignment (using 9-period and 21-period EMA for intraday snapshot trend)
    ema9 = _calc_ema(prices, 9)
    ema21 = _calc_ema(prices, 21)
    
    current_price = prices[-1]
    curr_ema9 = ema9[-1]
    curr_ema21 = ema21[-1]
    
    trend_score = 0.0
    if current_price > curr_ema21:
        trend_score += 0.10
    if curr_ema9 > curr_ema21:
        trend_score += 0.15
    if len(ema21) >= 2 and ema21[-1] > ema21[-2]:
        trend_score += 0.10
    trend_score *= confidence

    # MACD (12, 26, 9)
    ema12 = _calc_ema(prices, 12)
    ema26 = _calc_ema(prices, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = _calc_ema(macd_line, 9)
    
    curr_macd = macd_line[-1]
    curr_signal = signal_line[-1]
    curr_hist = curr_macd - curr_signal
    prev_hist = (macd_line[-2] - signal_line[-2]) if len(signal_line) > 1 else 0.0
    
    macd_score = 0.0
    if curr_hist > 0:
        macd_score += 0.10
        if curr_hist > prev_hist:
            macd_score += 0.10
    elif curr_hist < 0 and curr_hist > prev_hist:
        macd_score += 0.05
    macd_score *= confidence
        
    summary_parts = []
    if trend_score >= 0.25:
        summary_parts.append("추세정배열")
    elif trend_score == 0:
        summary_parts.append("추세역배열")
        
    if macd_score >= 0.20:
        summary_parts.append("MACD강세")
    elif macd_score > 0:
        summary_parts.append("MACD호전")
    if confidence < 1.0:
        summary_parts.append("저신뢰")
    
    summary = " ".join(summary_parts) if summary_parts else "특이동향없음"

    return {
        "trend_alignment_score": round(trend_score, 2),
        "macd_momentum_score": round(macd_score, 2),
        "macd_value": round(curr_macd, 2),
        "macd_signal": round(curr_signal, 2),
        "macd_histogram": round(curr_hist, 2),
        "short_ma": round(curr_ema9, 2),
        "medium_ma": round(curr_ema21, 2),
        "technical_summary": summary,
        "history_observation_count": observation_count,
        "history_usable": True,
        "history_full_confidence": observation_count >= FULL_TECHNICAL_HISTORY_OBSERVATIONS,
        "history_confidence": round(confidence, 2),
    }
