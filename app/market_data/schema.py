from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    current_price: int
    open_price: int
    low_price: int
    prev_day_change_pct: float
    high_price: int = 0
    live_snapshot_available: bool = False
    live_snapshot_updated_at: str | None = None
    live_snapshot_combined_rank: int | None = None
    live_volume_rank: int | None = None
    live_fluctuation_rank: int | None = None
    live_volume_power_rank: int | None = None
    live_ranked_source_count: int = 0


def _require_stripped_text(output: Mapping[str, Any], key: str) -> str:
    value = str(output.get(key, "")).strip()
    if not value:
        raise ValueError(f"현재가 응답에 필수 필드 {key} 가 없습니다.")
    return value


def _parse_int_field(output: Mapping[str, Any], key: str) -> int:
    value = _require_stripped_text(output, key)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"현재가 응답의 {key} 값이 정수가 아닙니다: {value}") from exc


def _parse_float_field(output: Mapping[str, Any], key: str) -> float:
    value = _require_stripped_text(output, key)
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"현재가 응답의 {key} 값이 숫자가 아닙니다: {value}") from exc


def build_market_snapshot(output: Mapping[str, Any]) -> MarketSnapshot:
    symbol = _require_stripped_text(output, "stck_shrn_iscd")
    live_signal = None
    try:
        from app.market_data.live_snapshot import get_live_snapshot_signal

        live_signal = get_live_snapshot_signal(symbol)
    except Exception:
        live_signal = None

    return MarketSnapshot(
        symbol=symbol,
        current_price=_parse_int_field(output, "stck_prpr"),
        open_price=_parse_int_field(output, "stck_oprc"),
        high_price=int(str(output.get("stck_hgpr", "0")).strip() or "0"),
        low_price=_parse_int_field(output, "stck_lwpr"),
        prev_day_change_pct=_parse_float_field(output, "prdy_ctrt"),
        live_snapshot_available=live_signal is not None,
        live_snapshot_updated_at=(None if live_signal is None else live_signal.updated_at),
        live_snapshot_combined_rank=(None if live_signal is None else live_signal.combined_rank),
        live_volume_rank=(None if live_signal is None else live_signal.volume_rank),
        live_fluctuation_rank=(None if live_signal is None else live_signal.fluctuation_rank),
        live_volume_power_rank=(None if live_signal is None else live_signal.volume_power_rank),
        live_ranked_source_count=(0 if live_signal is None else live_signal.ranked_source_count),
    )
