from typing import Any

from app.auth.token import ApiHttpError
from app.overseas_stock.models import OverseasBalanceAttemptResult, OverseasHolding


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_api_error(exc: Exception) -> tuple[str | None, str]:
    if isinstance(exc, ApiHttpError):
        data = exc.data if isinstance(exc.data, dict) else {}
        code = str(
            data.get("msg_cd")
            or data.get("message")
            or data.get("rt_cd")
            or ""
        ).strip() or None
        message = str(
            data.get("msg1")
            or data.get("message")
            or exc
        ).strip()
        return code, message
    return None, str(exc).strip() or exc.__class__.__name__


def _infer_supported(code: str | None, message: str) -> tuple[bool | None, bool]:
    text = f"{code or ''} {message}".lower()
    unsupported_markers = (
        "미지원",
        "지원하지",
        "not supported",
        "해당없",
        "tr_id",
    )
    if any(marker in text for marker in unsupported_markers):
        return False, True
    if message:
        return None, False
    return None, False


def _is_rate_limited_text(text: str) -> bool:
    lowered = text.lower()
    return "egw00201" in lowered or "초당 거래건수" in lowered or "rate limit" in lowered


def _holding_symbol(row: dict[str, Any]) -> str:
    return str(
        row.get("ovrs_pdno")
        or row.get("pdno")
        or row.get("symb")
        or row.get("rsym")
        or row.get("symbol")
        or row.get("ovrs_item_name")
        or ""
    ).strip()


def _safe_row_preview(row: dict[str, Any]) -> dict[str, Any]:
    preview_keys = (
        "ovrs_pdno",
        "symb",
        "rsym",
        "ovrs_item_name",
        "ovrs_excg_cd",
        "ovrs_cblc_qty",
        "ord_psbl_qty",
        "pchs_avg_pric",
        "ovrs_now_pric",
        "ovrs_stck_evlu_amt",
        "frcr_evlu_pfls_amt",
        "tr_crcy_cd",
    )
    return {
        key: row.get(key)
        for key in preview_keys
        if key in row and row.get(key) not in (None, "", [])
    }


def _response_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "rt_cd": payload.get("rt_cd"),
        "msg_cd": payload.get("msg_cd"),
        "msg1": payload.get("msg1"),
    }


def _normalize_holdings(
    payload: dict[str, Any],
    *,
    market: str,
    exchange_code: str,
    endpoint: str,
    tr_id: str,
) -> tuple[list[OverseasHolding], dict[str, int], list[dict[str, Any]], int]:
    rows = payload.get("output1")
    if not isinstance(rows, list):
        rows = payload.get("output")
    if not isinstance(rows, list):
        return [], {}, [], 0

    holdings: list[OverseasHolding] = []
    filtered_rows: list[dict[str, Any]] = []
    filter_reason_counts: dict[str, int] = {}

    for row in rows:
        if not isinstance(row, dict):
            filter_reason_counts["non_dict_row"] = filter_reason_counts.get("non_dict_row", 0) + 1
            filtered_rows.append({"reason": "non_dict_row", "row": str(row)})
            continue

        symbol = _holding_symbol(row)
        if not symbol:
            filter_reason_counts["missing_symbol"] = filter_reason_counts.get("missing_symbol", 0) + 1
            filtered_rows.append({"reason": "missing_symbol", "row": _safe_row_preview(row)})
            continue

        quantity = None
        for key in ("ovrs_cblc_qty", "cblc_qty", "hldg_qty", "qty", "ord_psbl_qty"):
            quantity = _parse_number(row.get(key))
            if quantity is not None:
                break
        if quantity is None:
            filter_reason_counts["missing_quantity"] = filter_reason_counts.get("missing_quantity", 0) + 1
            filtered_rows.append({"reason": "missing_quantity", "row": _safe_row_preview(row)})
            continue

        avg_price = None
        for key in ("pchs_avg_pric", "avg_unpr3", "frcr_pchs_unpr", "avg_price"):
            avg_price = _parse_number(row.get(key))
            if avg_price is not None:
                break
        current_price = None
        for key in ("ovrs_now_pric", "now_pric2", "last_price", "prpr"):
            current_price = _parse_number(row.get(key))
            if current_price is not None:
                break
        market_value = None
        for key in ("ovrs_stck_evlu_amt", "evlu_amt", "market_value"):
            market_value = _parse_number(row.get(key))
            if market_value is not None:
                break
        unrealized_pnl = None
        for key in ("frcr_evlu_pfls_amt", "evlu_pfls_amt", "unrealized_pnl"):
            unrealized_pnl = _parse_number(row.get(key))
            if unrealized_pnl is not None:
                break
        currency = str(
            row.get("tr_crcy_cd")
            or row.get("crcy_cd")
            or row.get("currency")
            or "USD"
        ).strip() or None
        holdings.append(
            OverseasHolding(
                symbol=symbol,
                market=market,
                exchange_code=str(row.get("ovrs_excg_cd") or exchange_code).strip() or exchange_code,
                quantity=quantity,
                avg_price=avg_price,
                current_price=current_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                currency=currency,
                source_endpoint=endpoint,
                source_tr_id=tr_id,
                raw_identity_fields={
                    "symbol": symbol,
                    "raw_exchange_code": row.get("ovrs_excg_cd"),
                    "name": row.get("ovrs_item_name"),
                    "raw_symbol_alt": row.get("rsym") or row.get("symb"),
                },
            )
        )
    return holdings, filter_reason_counts, filtered_rows, len(rows)


def _valid_account_from_status(response_status: dict[str, Any]) -> bool | None:
    rt_cd = str(response_status.get("rt_cd") or "").strip()
    msg_cd = str(response_status.get("msg_cd") or "").strip().upper()
    msg1 = str(response_status.get("msg1") or "").strip()
    text = f"{msg_cd} {msg1}".upper()
    if rt_cd == "0":
        return True
    if "INVALID_CHECK_ACNO" in text or "OPSQ2000" in text:
        return False
    return None


def _account_validation_details(response_status: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    msg_cd = str(response_status.get("msg_cd") or "").strip() or None
    msg1 = str(response_status.get("msg1") or "").strip() or None
    text = f"{msg_cd or ''} {msg1 or ''}".upper()
    detected = "INVALID_CHECK_ACNO" in text or "OPSQ2000" in text
    return detected, msg_cd, msg1


def _interpret_balance_result(
    *,
    symbol: str,
    request_exchange_code: str,
    response_status: dict[str, Any],
    raw_row_count: int,
    normalized_row_count: int,
    filtered_out_row_count: int,
    matched_holding: OverseasHolding | None,
    seen_symbols: list[str],
) -> str:
    rt_cd = str(response_status.get("rt_cd") or "").strip()
    msg_cd = str(response_status.get("msg_cd") or "").strip()
    msg1 = str(response_status.get("msg1") or "").strip()
    text = f"{msg_cd} {msg1}"

    if rt_cd and rt_cd != "0":
        if "INVALID_CHECK_ACNO" in text or "OPSQ2000" in text:
            return "account validation failed (INVALID_CHECK_ACNO)"
        if _is_rate_limited_text(text):
            return "rate limited before holdings verification"
        if "미지원" in text.lower() or "not supported" in text.lower():
            return "endpoint unsupported"
        return f"endpoint responded with error rt_cd={rt_cd}; holdings result not trustworthy"

    if matched_holding is not None:
        return "holdings present"
    if raw_row_count == 0:
        return "endpoint succeeded and returned no holdings rows"
    if normalized_row_count == 0 and filtered_out_row_count > 0:
        return "raw holdings rows existed but none normalized"
    if normalized_row_count > 0 and symbol.upper() not in {item.upper() for item in seen_symbols}:
        possible_mismatch = any(
            request_exchange_code != (holding_exchange or request_exchange_code)
            for holding_exchange in []
        )
        if possible_mismatch:
            return "possible market mismatch; verify exchange parameter"
        return "valid account with rows but symbol mismatch"
    return "valid account but holdings interpretation is unclear"


def _attempt_priority(attempt: OverseasBalanceAttemptResult) -> tuple[int, int, int, int]:
    account_valid = bool(attempt.account_valid)
    rt_cd = str(attempt.response_status.get("rt_cd") or "").strip()
    if account_valid and attempt.matched_symbol:
        base = 6
    elif account_valid and attempt.raw_row_count > 0:
        base = 5
    elif account_valid:
        base = 4
    elif attempt.account_valid is False:
        base = 3
    elif not attempt.rate_limited and attempt.supported is not False:
        base = 2
    else:
        base = 1
    return (
        base,
        1 if rt_cd == "0" else 0,
        int(attempt.raw_row_count),
        int(attempt.normalized_row_count),
    )


def _best_attempt(attempts: list[OverseasBalanceAttemptResult]) -> OverseasBalanceAttemptResult | None:
    if not attempts:
        return None
    return max(attempts, key=_attempt_priority)
