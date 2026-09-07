from typing import Any

from app.auth.settings import get_settings
from app.auth.token import (
    build_auth_headers,
    issue_access_token,
    issue_hashkey,
    request_json,
)
from app.core.throttle import wait_for_request_slots

# hashkey POST + order POST land in the same KIS per-key second, so the pair must
# be admitted as one unit (docs/todo_20260710.md §A-2).
_ORDER_REQUEST_SLOTS = 2


def _order_market(
    *,
    symbol: str,
    qty: int,
    token: str | None,
    tr_id: str,
    error_label: str,
) -> dict[str, Any]:
    settings = get_settings()
    token = token or issue_access_token()

    url = f"{settings.base_url}/uapi/domestic-stock/v1/trading/order-cash"
    payload = {
        "CANO": settings.cano,
        "ACNT_PRDT_CD": settings.acnt_prdt_cd,
        "PDNO": symbol,
        "ORD_DVSN": "01",
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0",
    }

    wait_for_request_slots(_ORDER_REQUEST_SLOTS)

    hashkey = issue_hashkey(payload, token=token)
    headers = build_auth_headers(
        token=token,
        tr_id=tr_id,
        hashkey=hashkey,
    )

    return request_json(
        "POST",
        url,
        headers=headers,
        payload=payload,
        error_label=error_label,
    )


def buy_market(symbol: str, qty: int, token: str | None = None) -> dict[str, Any]:
    return _order_market(
        symbol=symbol,
        qty=qty,
        token=token,
        tr_id="VTTC0012U",
        error_label="시장가 매수",
    )


def sell_market(symbol: str, qty: int, token: str | None = None) -> dict[str, Any]:
    return _order_market(
        symbol=symbol,
        qty=qty,
        token=token,
        tr_id="VTTC0011U",
        error_label="시장가 매도",
    )
