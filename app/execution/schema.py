from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionSnapshot:
    symbol: str
    orderable_cash: int
    orderable_qty: int
    current_price: int
    expected_notional_krw: int


def _parse_int(value: Any, *, field_name: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"주문 가능 조회 응답에 {field_name} 값이 없습니다.")

    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(
            f"주문 가능 조회 응답의 {field_name} 값이 정수가 아닙니다: {text}"
        ) from exc


def build_execution_snapshot(
    *,
    symbol: str,
    current_price: int,
    qty: int,
    orderable_output: Mapping[str, Any],
) -> ExecutionSnapshot:
    orderable_cash = _parse_int(
        orderable_output.get("ord_psbl_cash"),
        field_name="ord_psbl_cash",
    )
    orderable_qty = _parse_int(
        orderable_output.get("nrcvb_buy_qty"),
        field_name="nrcvb_buy_qty",
    )

    return ExecutionSnapshot(
        symbol=str(symbol).strip(),
        orderable_cash=orderable_cash,
        orderable_qty=orderable_qty,
        current_price=int(current_price),
        expected_notional_krw=int(current_price) * int(qty),
    )
