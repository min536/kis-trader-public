from dataclasses import replace
from typing import Any

from app.auth.settings import get_settings
from app.auth.token import ApiHttpError, build_auth_headers, issue_access_token, request_json
from app.overseas_stock.models import OverseasQuote, OverseasQuoteProbeResult, ProbeAttempt


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


def _market_candidates(market: str) -> tuple[str, ...]:
    normalized = market.strip().upper()
    if normalized == "US":
        return ("NAS", "NASD")
    return (normalized,)


def _build_quote_attempts(symbol: str, market: str, *, mock: bool) -> list[ProbeAttempt]:
    attempts: list[ProbeAttempt] = []
    tr_ids = ("HHDFS00000300",)
    endpoints = (
        "/uapi/overseas-price/v1/quotations/price",
        "/uapi/overseas-price/v1/quotations/price-detail",
    )
    for endpoint in endpoints:
        for exchange in _market_candidates(market):
            for tr_id in tr_ids:
                attempts.append(
                    ProbeAttempt(
                        name=f"quote:{exchange}:{tr_id}",
                        endpoint=endpoint,
                        tr_id=tr_id,
                        params={
                            "AUTH": "",
                            "EXCD": exchange,
                            "SYMB": symbol,
                        },
                        note="mock" if mock else "live_or_unknown",
                    )
                )
    return attempts


def _normalize_quote_payload(
    payload: dict[str, Any],
    *,
    symbol: str,
    market: str,
    exchange_code: str,
    endpoint: str,
    tr_id: str,
) -> OverseasQuote | None:
    output = payload.get("output")
    if not isinstance(output, dict):
        output = payload.get("output1")
    if not isinstance(output, dict):
        return None

    last_price = None
    for key in (
        "last",
        "last_price",
        "ovrs_nmix_prpr",
        "ovrs_now_pric",
        "stck_prpr",
        "prpr",
    ):
        last_price = _parse_number(output.get(key))
        if last_price is not None:
            break

    change = None
    for key in ("diff", "change", "ovrs_prdy_vrss", "prdy_vrss"):
        change = _parse_number(output.get(key))
        if change is not None:
            break

    change_pct = None
    for key in ("rate", "change_rate", "ovrs_prdy_ctrt", "prdy_ctrt"):
        change_pct = _parse_number(output.get(key))
        if change_pct is not None:
            break

    currency = str(
        output.get("tr_crcy_cd")
        or output.get("crcy_cd")
        or output.get("currency")
        or "USD"
    ).strip() or None
    as_of = str(
        output.get("xymd")
        or output.get("date")
        or output.get("as_of")
        or ""
    ).strip() or None

    if last_price is None and change is None and change_pct is None:
        return None

    return OverseasQuote(
        symbol=symbol,
        market=market,
        exchange_code=exchange_code,
        last_price=last_price,
        change=change,
        change_pct=change_pct,
        currency=currency,
        as_of=as_of,
        source_endpoint=endpoint,
        source_tr_id=tr_id,
        raw_identity_fields={
            "symbol": str(output.get("symb") or output.get("rsym") or symbol).strip() or symbol,
            "exchange_code": str(output.get("excd") or output.get("ovrs_excg_cd") or exchange_code).strip() or exchange_code,
            "name": str(output.get("hts_kor_isnm") or output.get("ovrs_item_name") or "").strip() or None,
        },
    )


def get_overseas_quote(
    *,
    symbol: str,
    market: str,
    token: str | None = None,
) -> OverseasQuoteProbeResult:
    settings = get_settings()
    token = token or issue_access_token()
    mock = "openapivts" in settings.base_url.lower()
    attempts = _build_quote_attempts(symbol, market, mock=mock)
    diagnostics: list[str] = []
    can_verify_mock_support = False

    for attempt in attempts:
        try:
            payload = request_json(
                "GET",
                f"{settings.base_url}{attempt.endpoint}",
                headers=build_auth_headers(token, tr_id=attempt.tr_id),
                params=attempt.params,
                error_label="해외 시세 조회",
            )
            normalized = _normalize_quote_payload(
                payload,
                symbol=symbol,
                market=market,
                exchange_code=str(attempt.params.get("EXCD") or "").strip(),
                endpoint=attempt.endpoint,
                tr_id=attempt.tr_id,
            )
            if normalized is None:
                diagnostics.append(
                    f"{attempt.name}: 응답은 받았지만 해외 시세 필드를 해석하지 못했습니다."
                )
                continue
            success_attempts = list(attempts)
            success_attempts[attempts.index(attempt)] = replace(attempt, ok=True)
            return OverseasQuoteProbeResult(
                ok=True,
                supported=True,
                can_verify_mock_support=True,
                diagnostics=diagnostics
                + [f"{attempt.name}: 해외 시세 조회 성공"],
                quote=normalized,
                attempts=success_attempts,
                endpoint_used=attempt.endpoint,
                tr_id_used=attempt.tr_id,
                request_context=dict(attempt.params),
                raw=payload,
            )
        except Exception as exc:
            code, message = _extract_api_error(exc)
            supported, verifiable = _infer_supported(code, message)
            can_verify_mock_support = can_verify_mock_support or verifiable
            diagnostics.append(
                f"{attempt.name}: {code or '-'} {message}"
            )
            attempts[attempts.index(attempt)] = replace(
                attempt,
                ok=False,
                error_code=code,
                error_message=message,
            )
            if supported is False:
                return OverseasQuoteProbeResult(
                    ok=False,
                    supported=False,
                    can_verify_mock_support=can_verify_mock_support,
                    diagnostics=diagnostics,
                    attempts=attempts,
                    endpoint_used=attempt.endpoint,
                    tr_id_used=attempt.tr_id,
                    request_context=dict(attempt.params),
                    raw={"error_code": code, "error_message": message},
                )

    return OverseasQuoteProbeResult(
        ok=False,
        supported=None,
        can_verify_mock_support=can_verify_mock_support,
        diagnostics=diagnostics or ["해외 시세 조회를 검증하지 못했습니다."],
        attempts=attempts,
        endpoint_used=None,
        tr_id_used=None,
        request_context={},
        raw=None,
    )


def probe_overseas_quote(
    *,
    symbol: str,
    market: str,
    token: str | None = None,
) -> OverseasQuoteProbeResult:
    return get_overseas_quote(symbol=symbol, market=market, token=token)
