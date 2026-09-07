import time
from typing import Any

from app.auth.settings import get_settings
from app.auth.token import build_auth_headers, issue_access_token, request_json
from app.portfolio.schema import PortfolioSnapshot

_BALANCE_PAGE_LIMIT = 10
# Bumped 0.35 → 0.6 on 2026-04-23 after KIS EGW00201 spikes during multi-page
# balance pulls. 0.35s is too tight for KIS's 1-second per-key window when the
# pagination loop overlaps with the next cycle's orderable/quote calls — a
# slightly slower per-page pace eliminates that overlap entirely.
_BALANCE_CONTINUATION_SLEEP_SECONDS = 0.6


def is_symbol_already_holding(portfolio_snapshot: PortfolioSnapshot, symbol: str) -> bool:
    return portfolio_snapshot.has_position_for(symbol)


def inquire_balance(token: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    token = token or issue_access_token()

    url = f"{settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
    headers = build_auth_headers(token=token, tr_id="VTTC8434R")
    params = {
        "CANO": settings.cano,
        "ACNT_PRDT_CD": settings.acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    merged_output1: list[dict[str, Any]] = []
    summary_output2: list[dict[str, Any]] = []
    last_response: dict[str, Any] | None = None

    for _ in range(_BALANCE_PAGE_LIMIT):
        response = request_json(
            "GET",
            url,
            headers=headers,
            params=params,
            error_label="잔고 조회",
        )
        last_response = response
        if response.get("rt_cd") != "0":
            return response

        page_positions = response.get("output1")
        if isinstance(page_positions, list):
            merged_output1.extend(
                item for item in page_positions if isinstance(item, dict)
            )

        page_summary = response.get("output2")
        if isinstance(page_summary, list) and page_summary:
            summary_output2 = page_summary

        next_fk = str(response.get("ctx_area_fk100") or "").strip()
        next_nk = str(response.get("ctx_area_nk100") or "").strip()
        if not next_fk and not next_nk:
            break

        params["CTX_AREA_FK100"] = next_fk
        params["CTX_AREA_NK100"] = next_nk
        time.sleep(_BALANCE_CONTINUATION_SLEEP_SECONDS)

    if last_response is None:
        return {}

    return {
        **last_response,
        "output1": merged_output1,
        "output2": summary_output2,
    }
