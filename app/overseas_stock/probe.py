import json
from pathlib import Path

from app.auth.settings import PROJECT_ROOT, get_settings
from app.auth.token import issue_access_token
from app.core.time_utils import get_korean_now
from app.overseas_stock.balance import probe_overseas_balance
from app.overseas_stock.models import (
    OverseasBalanceMatrixProbeResult,
    OverseasBalanceProbeResult,
    OverseasHolding,
    OverseasProbeSummary,
    OverseasQuote,
    OverseasQuoteProbeResult,
)
from app.overseas_stock.balance import (
    probe_overseas_balance_account_matrix,
    probe_overseas_balance_matrix,
)
from app.overseas_stock.quote import probe_overseas_quote


def _mask_account(cano: str) -> str:
    text = str(cano or "").strip()
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:4]}{'*' * max(0, len(text) - 6)}{text[-2:]}"


def _environment_label(base_url: str) -> str:
    lowered = base_url.lower()
    if "openapivts" in lowered or "vts" in lowered:
        return "mock"
    if lowered:
        return "live_or_unknown"
    return "unknown"


def _mock_support_label(
    *,
    environment: str,
    quote_ok: bool,
    balance_ok: bool,
    quote_supported: bool | None,
    balance_supported: bool | None,
    verifiable: bool,
) -> str:
    if environment != "mock":
        return "unclear"
    if quote_ok or balance_ok:
        return "verified"
    if quote_supported is False or balance_supported is False:
        return "unsupported"
    if verifiable:
        return "unclear"
    return "unclear"


def _recommended_next_step(summary: OverseasProbeSummary) -> str:
    quote = summary.quote_probe
    balance = summary.balance_probe
    matrix = summary.balance_probe_matrix
    account_matrix = summary.balance_account_matrix
    if account_matrix is not None:
        return str(account_matrix.recommended_next_step or "balance account matrix 결과를 기준으로 다음 단계를 정리하세요.")
    if matrix is not None:
        return str(matrix.recommended_next_step or "balance matrix 결과를 기준으로 다음 단계를 정리하세요.")
    if balance and balance.ok and balance.interpretation == "endpoint succeeded and returned no holdings rows":
        return "해외 잔고 endpoint는 응답했지만 현재 요청 exchange/통화 기준으로는 보유 rows가 없습니다. broker 화면의 시장코드와 계좌 구분을 먼저 대조하세요."
    if balance and balance.ok and balance.interpretation == "raw holdings rows existed but none normalized":
        return "raw holdings rows는 왔지만 normalization에서 걸러졌습니다. raw_rows_preview와 filter_reason_counts를 기준으로 필드 매핑을 보강하세요."
    if balance and balance.ok and balance.interpretation == "possible market mismatch; verify exchange parameter":
        return "exchange parameter가 실제 보유 시장과 다를 수 있습니다. OVRS_EXCG_CD 후보와 broker 화면 시장을 우선 대조하세요."
    if quote and quote.ok and balance and balance.ok:
        return "다음 단계로 해외 quote adapter 정리와 잔고 reconciliation 토대를 붙이는 것이 좋습니다."
    if quote and quote.ok and balance and not balance.ok:
        return "해외 시세는 확인됐고 잔고 쪽만 불확실합니다. balance endpoint/TR 조합을 먼저 확정하세요."
    if balance and balance.ok and quote and not quote.ok:
        return "해외 잔고는 확인됐고 시세 쪽만 불확실합니다. quote endpoint 파라미터/시장코드를 우선 재검증하세요."
    if summary.mock_support == "unsupported":
        return "현재 환경의 해외 mock read 지원이 없을 수 있습니다. 실계좌 read-only 테스트 가능 여부를 먼저 확인하세요."
    return "현재 결과만으로는 해외 mock 지원을 확정하기 어렵습니다. API 응답 코드와 TR 조합을 추가 검증하세요."


def _artifact_path() -> Path:
    return PROJECT_ROOT / "data" / "overseas_probe_latest.json"


def _quote_preview(quote: OverseasQuote | None) -> dict | None:
    if quote is None:
        return None
    return {
        "symbol": quote.symbol,
        "market": quote.market,
        "exchange_code": quote.exchange_code,
        "last_price": quote.last_price,
        "change": quote.change,
        "change_pct": quote.change_pct,
        "currency": quote.currency,
        "endpoint_used": quote.source_endpoint,
        "tr_id_used": quote.source_tr_id,
    }


def _holding_preview(holding: OverseasHolding) -> dict:
    return {
        "symbol": holding.symbol,
        "market": holding.market,
        "exchange_code": holding.exchange_code,
        "quantity": holding.quantity,
        "avg_price": holding.avg_price,
        "current_price": holding.current_price,
        "market_value": holding.market_value,
        "unrealized_pnl": holding.unrealized_pnl,
        "currency": holding.currency,
        "raw_identity_fields": holding.raw_identity_fields,
    }


def _artifact_payload(summary: OverseasProbeSummary) -> dict:
    payload = summary.to_dict()
    quote_probe = payload.get("quote_probe") or {}
    if isinstance(quote_probe, dict):
        quote_probe["raw"] = None
        quote_probe["quote_preview"] = _quote_preview(summary.quote_probe.quote) if summary.quote_probe else None
    balance_probe = payload.get("balance_probe") or {}
    if isinstance(balance_probe, dict):
        balance_probe["raw"] = None
        balance_probe["normalized_holdings_preview"] = [
            _holding_preview(item)
            for item in (summary.balance_probe.holdings[:3] if summary.balance_probe else [])
        ]
        if summary.balance_probe and summary.balance_probe.matched_holding is not None:
            balance_probe["matched_holding_preview"] = _holding_preview(summary.balance_probe.matched_holding)
    matrix_probe = payload.get("balance_probe_matrix") or {}
    if isinstance(matrix_probe, dict):
        matrix = summary.balance_probe_matrix
        best = matrix.best_attempt if matrix is not None else None
        matrix_probe["attempts"] = [
            {
                "endpoint_used": item.endpoint_used,
                "tr_id_used": item.tr_id_used,
                "request_context": item.request_context,
                "response_status": item.response_status,
                "raw_row_count": item.raw_row_count,
                "normalized_row_count": item.normalized_row_count,
                "filtered_out_row_count": item.filtered_out_row_count,
                "interpretation": item.interpretation,
                "matched_symbol": item.matched_symbol,
                "diagnostics": item.diagnostics[:3],
            }
            for item in (matrix.attempts if matrix is not None else [])
        ]
        matrix_probe["best_attempt"] = (
            {
                "endpoint_used": best.endpoint_used,
                "tr_id_used": best.tr_id_used,
                "request_context": best.request_context,
                "response_status": best.response_status,
                "raw_row_count": best.raw_row_count,
                "normalized_row_count": best.normalized_row_count,
                "filtered_out_row_count": best.filtered_out_row_count,
                "interpretation": best.interpretation,
                "matched_symbol": best.matched_symbol,
                "diagnostics": best.diagnostics[:3],
            }
            if best is not None
            else None
        )
        matrix_probe["balance_probe_best_attempt"] = matrix_probe["best_attempt"]
        matrix_probe["balance_probe_best_interpretation"] = best.interpretation if best is not None else None
        matrix_probe["balance_probe_any_valid_account_context"] = bool(matrix.any_valid_account_context) if matrix is not None else False
        matrix_probe["balance_probe_any_raw_rows"] = bool(matrix.any_raw_rows) if matrix is not None else False
        matrix_probe["balance_probe_any_symbol_match"] = bool(matrix.any_symbol_match) if matrix is not None else False
    account_matrix_probe = payload.get("balance_account_matrix") or {}
    if isinstance(account_matrix_probe, dict):
        account_matrix = summary.balance_account_matrix
        best = account_matrix.best_attempt if account_matrix is not None else None
        account_matrix_probe["attempts"] = [
            {
                "account_context_source": item.account_context_source,
                "account_context_used": item.account_context_used,
                "account_validation_error_detected": item.account_validation_error_detected,
                "account_validation_error_code": item.account_validation_error_code,
                "account_validation_error_message": item.account_validation_error_message,
                "endpoint_used": item.endpoint_used,
                "tr_id_used": item.tr_id_used,
                "request_context": item.request_context,
                "response_status": item.response_status,
                "raw_row_count": item.raw_row_count,
                "normalized_row_count": item.normalized_row_count,
                "interpretation": item.interpretation,
                "matched_symbol": item.matched_symbol,
            }
            for item in (account_matrix.attempts if account_matrix is not None else [])
        ]
        account_matrix_probe["balance_account_best_attempt"] = (
            {
                "account_context_source": best.account_context_source,
                "account_context_used": best.account_context_used,
                "account_validation_error_detected": best.account_validation_error_detected,
                "account_validation_error_code": best.account_validation_error_code,
                "account_validation_error_message": best.account_validation_error_message,
                "endpoint_used": best.endpoint_used,
                "tr_id_used": best.tr_id_used,
                "request_context": best.request_context,
                "response_status": best.response_status,
                "raw_row_count": best.raw_row_count,
                "normalized_row_count": best.normalized_row_count,
                "interpretation": best.interpretation,
                "matched_symbol": best.matched_symbol,
            }
            if best is not None
            else None
        )
        account_matrix_probe["balance_account_any_valid_context"] = (
            bool(account_matrix.any_valid_account_context) if account_matrix is not None else False
        )
        account_matrix_probe["balance_account_validation_failures"] = (
            list(account_matrix.account_validation_failures) if account_matrix is not None else []
        )
        account_matrix_probe["balance_account_best_interpretation"] = (
            best.interpretation if best is not None else None
        )
        payload["balance_account_best_attempt"] = account_matrix_probe["balance_account_best_attempt"]
        payload["balance_account_any_valid_context"] = account_matrix_probe["balance_account_any_valid_context"]
        payload["balance_account_validation_failures"] = account_matrix_probe["balance_account_validation_failures"]
        payload["balance_account_best_interpretation"] = account_matrix_probe["balance_account_best_interpretation"]
    return payload


def run_overseas_probe(
    *,
    symbol: str,
    market: str,
    quote_only: bool = False,
    balance_only: bool = False,
    balance_matrix: bool = False,
    balance_account_matrix: bool = False,
    write_artifact: bool = True,
) -> OverseasProbeSummary:
    settings = get_settings()
    try:
        token = issue_access_token()
        quote_result = (
            None
            if balance_only or balance_matrix or balance_account_matrix
            else probe_overseas_quote(symbol=symbol, market=market, token=token)
        )
        balance_result = (
            None
            if quote_only or balance_matrix or balance_account_matrix
            else probe_overseas_balance(symbol=symbol, market=market, token=token)
        )
        balance_matrix_result = (
            None
            if quote_only or not balance_matrix
            else probe_overseas_balance_matrix(symbol=symbol, market=market, token=token)
        )
        balance_account_matrix_result = (
            None
            if quote_only or not balance_account_matrix
            else probe_overseas_balance_account_matrix(symbol=symbol, market=market, token=token)
        )
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        quote_result = None if balance_only or balance_matrix or balance_account_matrix else OverseasQuoteProbeResult(
            ok=False,
            supported=None,
            can_verify_mock_support=False,
            diagnostics=[f"quote probe 초기화 실패: {message}"],
        )
        balance_result = None if quote_only else OverseasBalanceProbeResult(
            ok=False,
            supported=None,
            can_verify_mock_support=False,
            diagnostics=[f"balance probe 초기화 실패: {message}"],
        )
        balance_matrix_result = None if quote_only or not balance_matrix else OverseasBalanceMatrixProbeResult(
            ok=False,
            supported=None,
            diagnostics=[f"balance matrix 초기화 실패: {message}"],
        )
        balance_account_matrix_result = None if quote_only or not balance_account_matrix else OverseasBalanceMatrixProbeResult(
            ok=False,
            supported=None,
            diagnostics=[f"balance account matrix 초기화 실패: {message}"],
        )
    diagnostics = [
        f"environment={_environment_label(settings.base_url)}",
        f"account={_mask_account(settings.cano)}-{settings.acnt_prdt_cd}",
    ]
    if balance_result is not None:
        diagnostics.extend(balance_result.diagnostics[:3])
    if balance_matrix_result is not None and balance_matrix_result.best_attempt is not None:
        diagnostics.extend((balance_matrix_result.best_attempt.diagnostics or [])[:3])
    if balance_account_matrix_result is not None and balance_account_matrix_result.best_attempt is not None:
        diagnostics.extend((balance_account_matrix_result.best_attempt.diagnostics or [])[:3])
    if quote_result is not None:
        diagnostics.extend(quote_result.diagnostics[:3])
    quote_verifiable = (
        bool(quote_result.can_verify_mock_support) if quote_result is not None else False
    )
    balance_verifiable = (
        bool(balance_result.can_verify_mock_support) if balance_result is not None else False
    )
    matrix_verifiable = (
        bool(balance_matrix_result.any_valid_account_context)
        if balance_matrix_result is not None
        else False
    )
    account_matrix_verifiable = (
        bool(balance_account_matrix_result.any_valid_account_context)
        if balance_account_matrix_result is not None
        else False
    )
    mock_support = _mock_support_label(
        environment=_environment_label(settings.base_url),
        quote_ok=bool(quote_result.ok) if quote_result is not None else False,
        balance_ok=(
            bool(balance_result.ok) if balance_result is not None else False
        ) or (
            bool(balance_matrix_result.any_valid_account_context)
            if balance_matrix_result is not None
            else False
        ) or (
            bool(balance_account_matrix_result.any_valid_account_context)
            if balance_account_matrix_result is not None
            else False
        ),
        quote_supported=(quote_result.supported if quote_result is not None else None),
        balance_supported=(
            balance_result.supported
            if balance_result is not None
            else balance_account_matrix_result.supported
            if balance_account_matrix_result is not None
            else balance_matrix_result.supported
            if balance_matrix_result is not None
            else None
        ),
        verifiable=quote_verifiable or balance_verifiable or matrix_verifiable or account_matrix_verifiable,
    )
    summary = OverseasProbeSummary(
        ok=(bool(quote_result.ok) if quote_result is not None else False)
        or (bool(balance_result.ok) if balance_result is not None else False)
        or (bool(balance_matrix_result.any_valid_account_context) if balance_matrix_result is not None else False)
        or (bool(balance_account_matrix_result.any_valid_account_context) if balance_account_matrix_result is not None else False),
        environment=_environment_label(settings.base_url),
        account_masked=f"{_mask_account(settings.cano)}-{settings.acnt_prdt_cd}",
        symbol=symbol,
        market=market,
        mock_support=mock_support,
        diagnostics=diagnostics,
        quote_probe=quote_result,
        balance_probe=balance_result,
        balance_probe_matrix=balance_matrix_result,
        balance_account_matrix=balance_account_matrix_result,
        recommended_next_step=None,
        artifact_path=None,
    )
    summary.recommended_next_step = _recommended_next_step(summary)
    if write_artifact:
        artifact = _artifact_payload(summary)
        artifact["generated_at"] = get_korean_now().isoformat()
        path = _artifact_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            summary.artifact_path = str(path)
        except OSError as exc:
            summary.diagnostics.append(f"artifact 저장 실패: {exc}")
    return summary
