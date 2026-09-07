from typing import Any

from app.auth.settings import get_settings
from app.auth.token import build_auth_headers, issue_access_token, request_json


def inquire_orderable_cash(
    symbol: str,
    price: str,
    token: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    token = token or issue_access_token()

    url = f"{settings.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    headers = build_auth_headers(token=token, tr_id="VTTC8908R")
    params = {
        "CANO": settings.cano,
        "ACNT_PRDT_CD": settings.acnt_prdt_cd,
        "PDNO": symbol,
        "ORD_UNPR": str(price),
        "ORD_DVSN": "00",
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }

    return request_json(
        "GET",
        url,
        headers=headers,
        params=params,
        error_label="매수가능조회",
        error_context=f"params={params}",
    )
