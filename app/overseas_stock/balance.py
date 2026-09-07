from dataclasses import asdict, replace

from app.auth.settings import get_settings
from app.auth.token import build_auth_headers, issue_access_token, request_json
from app.overseas_stock.balance_parsing import (
    _account_validation_details,
    _attempt_priority,
    _best_attempt,
    _extract_api_error,
    _holding_symbol,
    _infer_supported,
    _interpret_balance_result,
    _is_rate_limited_text,
    _normalize_holdings,
    _parse_number,
    _response_status,
    _safe_row_preview,
    _valid_account_from_status,
)
from app.overseas_stock.balance_attempts import (
    _ACCOUNT_MATRIX_FIXED_CURRENCY,
    _ACCOUNT_MATRIX_FIXED_EXCHANGE,
    _attempt_to_probe_result,
    _build_account_context_candidates,
    _build_balance_account_attempts,
    _build_balance_attempts,
    _currency_candidates,
    _market_candidates,
    _recommended_account_matrix_next_step,
    _recommended_matrix_next_step,
    _tr_id_candidates,
)
from app.overseas_stock.models import (
    OverseasBalanceAttemptResult,
    OverseasBalanceMatrixProbeResult,
    OverseasBalanceProbeResult,
    ProbeAttempt,
)


def _run_single_balance_attempt(
    *,
    attempt: ProbeAttempt,
    symbol: str,
    market: str,
    token: str,
) -> OverseasBalanceAttemptResult:
    settings = get_settings()
    try:
        payload = request_json(
            "GET",
            f"{settings.base_url}{attempt.endpoint}",
            headers=build_auth_headers(token, tr_id=attempt.tr_id),
            params=attempt.params,
            error_label="해외 잔고 조회",
        )
        response_status = _response_status(payload)
        account_valid = _valid_account_from_status(response_status)
        holdings, filter_reason_counts, filtered_rows, raw_row_count = _normalize_holdings(
            payload,
            market=market,
            exchange_code=str(attempt.params.get("OVRS_EXCG_CD") or "").strip(),
            endpoint=attempt.endpoint,
            tr_id=attempt.tr_id,
        )
        matched = next(
            (holding for holding in holdings if holding.symbol.upper() == symbol.upper()),
            None,
        )
        normalized_preview = [asdict(item) for item in holdings[:3]]
        raw_preview = filtered_rows[:3]
        rows = payload.get("output1")
        if not isinstance(rows, list):
            rows = payload.get("output")
        if raw_row_count > 0 and isinstance(rows, list):
            raw_preview = [_safe_row_preview(row) for row in rows[:3] if isinstance(row, dict)]
        normalized_row_count = len(holdings)
        filtered_out_row_count = max(0, raw_row_count - normalized_row_count)
        seen_symbols = [holding.symbol for holding in holdings]
        interpretation = _interpret_balance_result(
            symbol=symbol,
            request_exchange_code=str(attempt.params.get("OVRS_EXCG_CD") or "").strip(),
            response_status=response_status,
            raw_row_count=raw_row_count,
            normalized_row_count=normalized_row_count,
            filtered_out_row_count=filtered_out_row_count,
            matched_holding=matched,
            seen_symbols=seen_symbols,
        )
        diagnostics = [
            f"{attempt.name}: rt_cd={response_status.get('rt_cd') or '-'} msg={response_status.get('msg1') or '-'}",
            f"{attempt.name}: raw_rows={raw_row_count}, normalized={normalized_row_count}, matched={'yes' if matched else 'no'}",
        ]
        if interpretation:
            diagnostics.append(f"{attempt.name}: {interpretation}")
        return OverseasBalanceAttemptResult(
            ok=bool(response_status.get("rt_cd") == "0"),
            account_valid=account_valid,
            supported=True,
            rate_limited=_is_rate_limited_text(f"{response_status.get('msg_cd') or ''} {response_status.get('msg1') or ''}"),
            account_context_source=str(attempt.note or "").replace("account_context_source=", "").strip() or "request_params",
            account_context_used={
                "CANO": attempt.params.get("CANO"),
                "ACNT_PRDT_CD": attempt.params.get("ACNT_PRDT_CD"),
            },
            account_validation_error_detected=_account_validation_details(response_status)[0],
            account_validation_error_code=_account_validation_details(response_status)[1],
            account_validation_error_message=_account_validation_details(response_status)[2],
            diagnostics=diagnostics,
            endpoint_used=attempt.endpoint,
            tr_id_used=attempt.tr_id,
            request_context=dict(attempt.params),
            response_status=response_status,
            raw_row_count=raw_row_count,
            normalized_row_count=normalized_row_count,
            filtered_out_row_count=filtered_out_row_count,
            filter_reason_counts=dict(filter_reason_counts),
            raw_rows_preview=raw_preview,
            normalized_holdings_preview=normalized_preview,
            interpretation=interpretation,
            matched_symbol=matched is not None,
            matched_holding=matched,
        )
    except Exception as exc:
        code, message = _extract_api_error(exc)
        supported, _ = _infer_supported(code, message)
        text = f"{code or ''} {message}"
        interpretation = (
            "rate limited before holdings verification"
            if _is_rate_limited_text(text)
            else "transport/auth failure or no verifiable response"
        )
        return OverseasBalanceAttemptResult(
            ok=False,
            account_valid=None,
            supported=supported,
            rate_limited=_is_rate_limited_text(text),
            account_context_source=str(attempt.note or "").replace("account_context_source=", "").strip() or "request_params",
            account_context_used={
                "CANO": attempt.params.get("CANO"),
                "ACNT_PRDT_CD": attempt.params.get("ACNT_PRDT_CD"),
            },
            account_validation_error_detected=False,
            account_validation_error_code=code,
            account_validation_error_message=message,
            diagnostics=[f"{attempt.name}: {code or '-'} {message}"],
            endpoint_used=attempt.endpoint,
            tr_id_used=attempt.tr_id,
            request_context=dict(attempt.params),
            response_status={"rt_cd": None, "msg_cd": code, "msg1": message},
            interpretation=interpretation,
            matched_symbol=False,
        )


def probe_overseas_balance_matrix(
    *,
    symbol: str,
    market: str,
    token: str | None = None,
) -> OverseasBalanceMatrixProbeResult:
    settings = get_settings()
    token = token or issue_access_token()
    mock = "openapivts" in settings.base_url.lower()
    attempts = _build_balance_attempts(symbol, market, mock=mock, matrix=True)
    attempt_results = [
        _run_single_balance_attempt(
            attempt=attempt,
            symbol=symbol,
            market=market,
            token=token,
        )
        for attempt in attempts
    ]
    best = _best_attempt(attempt_results)
    any_valid = any(item.account_valid is True for item in attempt_results)
    any_raw_rows = any(int(item.raw_row_count) > 0 for item in attempt_results)
    any_symbol_match = any(bool(item.matched_symbol) for item in attempt_results)
    any_supported_false = any(item.supported is False for item in attempt_results)
    return OverseasBalanceMatrixProbeResult(
        ok=any_valid,
        supported=False if any_supported_false and not any_valid else None if not any_valid else True,
        diagnostics=[
            f"attempts={len(attempt_results)}",
            f"any_valid_account_context={'yes' if any_valid else 'no'}",
            f"any_raw_rows={'yes' if any_raw_rows else 'no'}",
            f"any_symbol_match={'yes' if any_symbol_match else 'no'}",
        ],
        attempts=attempt_results,
        best_attempt=best,
        any_valid_account_context=any_valid,
        any_raw_rows=any_raw_rows,
        any_symbol_match=any_symbol_match,
        recommended_next_step=_recommended_matrix_next_step(
            OverseasBalanceMatrixProbeResult(
                ok=any_valid,
                supported=None,
                attempts=attempt_results,
                best_attempt=best,
                any_valid_account_context=any_valid,
                any_raw_rows=any_raw_rows,
                any_symbol_match=any_symbol_match,
            )
        ),
    )


def probe_overseas_balance_account_matrix(
    *,
    symbol: str,
    market: str,
    token: str | None = None,
) -> OverseasBalanceMatrixProbeResult:
    settings = get_settings()
    token = token or issue_access_token()
    mock = "openapivts" in settings.base_url.lower()
    attempts, account_candidates = _build_balance_account_attempts(market=market, mock=mock)
    attempt_results = [
        _run_single_balance_attempt(
            attempt=attempt,
            symbol=symbol,
            market=market,
            token=token,
        )
        for attempt in attempts
    ]
    best = _best_attempt(attempt_results)
    any_valid = any(item.account_valid is True for item in attempt_results)
    any_raw_rows = any(int(item.raw_row_count) > 0 for item in attempt_results)
    any_symbol_match = any(bool(item.matched_symbol) for item in attempt_results)
    validation_failures = [
        {
            "source": item.account_context_source,
            "cano": (item.account_context_used or {}).get("CANO"),
            "acnt_prdt_cd": (item.account_context_used or {}).get("ACNT_PRDT_CD"),
            "tr_id": item.tr_id_used,
            "code": item.account_validation_error_code,
            "message": item.account_validation_error_message,
        }
        for item in attempt_results
        if item.account_validation_error_detected
    ]
    any_supported_false = any(item.supported is False for item in attempt_results)
    return OverseasBalanceMatrixProbeResult(
        ok=any_valid,
        supported=False if any_supported_false and not any_valid else None if not any_valid else True,
        diagnostics=[
            f"settings account={settings.cano}/{settings.acnt_prdt_cd}",
            f"attempts={len(attempt_results)}",
            f"any_valid_account_context={'yes' if any_valid else 'no'}",
            f"account_validation_failures={len(validation_failures)}",
        ],
        attempts=attempt_results,
        best_attempt=best,
        any_valid_account_context=any_valid,
        any_raw_rows=any_raw_rows,
        any_symbol_match=any_symbol_match,
        account_context_candidates=account_candidates,
        account_validation_failures=validation_failures,
        recommended_next_step=_recommended_account_matrix_next_step(
            OverseasBalanceMatrixProbeResult(
                ok=any_valid,
                supported=None,
                attempts=attempt_results,
                best_attempt=best,
                any_valid_account_context=any_valid,
                any_raw_rows=any_raw_rows,
                any_symbol_match=any_symbol_match,
                account_context_candidates=account_candidates,
                account_validation_failures=validation_failures,
            )
        ),
    )


def probe_overseas_balance(
    *,
    symbol: str,
    market: str,
    token: str | None = None,
) -> OverseasBalanceProbeResult:
    settings = get_settings()
    token = token or issue_access_token()
    mock = "openapivts" in settings.base_url.lower()
    attempts = _build_balance_attempts(symbol, market, mock=mock, matrix=False)
    attempt_results = [
        _run_single_balance_attempt(
            attempt=attempt,
            symbol=symbol,
            market=market,
            token=token,
        )
        for attempt in attempts
    ]
    best = _best_attempt(attempt_results)
    if best is None:
        return OverseasBalanceProbeResult(
            ok=False,
            supported=None,
            can_verify_mock_support=False,
            account_valid=None,
            rate_limited=False,
            diagnostics=["해외 잔고 조회를 검증하지 못했습니다."],
            interpretation="transport/auth failure or no verifiable response",
        )
    can_verify_mock_support = any(
        item.account_valid is not None or item.supported is False
        for item in attempt_results
    )
    supported = False if any(item.supported is False for item in attempt_results) and best.account_valid is not True else True if best.account_valid is True else None
    result = _attempt_to_probe_result(
        best,
        supported=supported,
        can_verify_mock_support=can_verify_mock_support,
    )
    result.diagnostics = [diag for item in attempt_results for diag in item.diagnostics[:2]]
    result.attempts = [
        replace(
            ProbeAttempt(
                name=f"{item.request_context.get('OVRS_EXCG_CD', '-')}"
                f":{item.tr_id_used or '-'}"
                f":{item.request_context.get('TR_CRCY_CD', '-') or 'EMPTY'}",
                endpoint=item.endpoint_used or "",
                tr_id=item.tr_id_used or "",
                params={str(k): str(v) for k, v in item.request_context.items()},
            ),
            ok=item.ok,
            error_code=str(item.response_status.get("msg_cd") or "").strip() or None,
            error_message=str(item.response_status.get("msg1") or "").strip() or None,
        )
        for item in attempt_results
    ]
    return result
