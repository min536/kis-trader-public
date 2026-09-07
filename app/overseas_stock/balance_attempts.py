from typing import Any

from app.auth.settings import get_settings
from app.overseas_stock.models import (
    OverseasBalanceAttemptResult,
    OverseasBalanceMatrixProbeResult,
    OverseasBalanceProbeResult,
    OverseasHolding,
    ProbeAttempt,
)

_ACCOUNT_MATRIX_FIXED_EXCHANGE = "NASD"
_ACCOUNT_MATRIX_FIXED_CURRENCY = "USD"


def _market_candidates(market: str) -> tuple[str, ...]:
    normalized = market.strip().upper()
    if normalized == "US":
        return ("NASD", "NAS", "NYSE", "AMEX")
    return (normalized,)


def _currency_candidates(market: str) -> tuple[str, ...]:
    normalized = market.strip().upper()
    if normalized == "US":
        return ("USD", "")
    return ("USD", "")


def _tr_id_candidates(*, mock: bool) -> tuple[str, ...]:
    if mock:
        return ("VTTT3012R", "VTTS3012R")
    return ("TTTS3012R", "JTTT3012R")


def _build_balance_attempts(symbol: str, market: str, *, mock: bool, matrix: bool) -> list[ProbeAttempt]:
    settings = get_settings()
    exchanges = _market_candidates(market)
    currencies = _currency_candidates(market) if matrix else ("USD",)
    attempts: list[ProbeAttempt] = []
    for exchange in exchanges:
        for tr_id in _tr_id_candidates(mock=mock):
            for currency in currencies:
                params = {
                    "CANO": settings.cano,
                    "ACNT_PRDT_CD": settings.acnt_prdt_cd,
                    "OVRS_EXCG_CD": exchange,
                    "TR_CRCY_CD": currency,
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                }
                attempts.append(
                    ProbeAttempt(
                        name=f"balance:{exchange}:{tr_id}:{currency or 'EMPTY'}",
                        endpoint="/uapi/overseas-stock/v1/trading/inquire-balance",
                        tr_id=tr_id,
                        params=params,
                        note=f"match_symbol={symbol}",
                    )
                )
    return attempts


def _build_account_context_candidates() -> list[dict[str, Any]]:
    settings = get_settings()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, cano: str, acnt_prdt_cd: str) -> None:
        key = (str(cano).strip(), str(acnt_prdt_cd).strip())
        if key in seen or not key[0]:
            return
        seen.add(key)
        candidates.append(
            {
                "source": source,
                "cano": key[0],
                "acnt_prdt_cd": key[1],
            }
        )

    cano = settings.cano.strip()
    acnt = settings.acnt_prdt_cd.strip()
    add("settings_direct", cano, acnt)

    cano_digits = "".join(char for char in cano if char.isdigit())
    acnt_digits = "".join(char for char in acnt if char.isdigit())
    if cano_digits or acnt_digits:
        add(
            "digits_only_normalized",
            cano_digits,
            acnt_digits.zfill(2) if acnt_digits else acnt_digits,
        )
    if acnt_digits and acnt_digits != acnt_digits.zfill(2):
        add("product_code_zero_padded", cano_digits or cano, acnt_digits.zfill(2))
    if acnt_digits and acnt_digits != acnt_digits.lstrip("0"):
        stripped = acnt_digits.lstrip("0") or "0"
        add("product_code_no_leading_zero", cano_digits or cano, stripped)
    if len(cano_digits) >= 10:
        add("split_from_cano_suffix", cano_digits[:8], cano_digits[8:10])

    return candidates


def _build_balance_account_attempts(*, market: str, mock: bool) -> tuple[list[ProbeAttempt], list[dict[str, Any]]]:
    attempts: list[ProbeAttempt] = []
    account_candidates = _build_account_context_candidates()
    exchange = _ACCOUNT_MATRIX_FIXED_EXCHANGE if market.strip().upper() == "US" else market.strip().upper()
    currency = _ACCOUNT_MATRIX_FIXED_CURRENCY if market.strip().upper() == "US" else "USD"
    for candidate in account_candidates:
        for tr_id in _tr_id_candidates(mock=mock):
            params = {
                "CANO": candidate["cano"],
                "ACNT_PRDT_CD": candidate["acnt_prdt_cd"],
                "OVRS_EXCG_CD": exchange,
                "TR_CRCY_CD": currency,
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            }
            attempts.append(
                ProbeAttempt(
                    name=f"balance-account:{candidate['source']}:{tr_id}",
                    endpoint="/uapi/overseas-stock/v1/trading/inquire-balance",
                    tr_id=tr_id,
                    params=params,
                    note=f"account_context_source={candidate['source']}",
                )
            )
    return attempts, account_candidates


def _recommended_matrix_next_step(result: OverseasBalanceMatrixProbeResult) -> str:
    if result.any_symbol_match:
        return "NVDA 매칭이 확인됐습니다. 다음 단계로 watch-only reconciliation 토대를 붙이세요."
    if result.any_valid_account_context and result.any_raw_rows:
        return "유효한 계좌 컨텍스트와 raw rows는 확인됐지만 NVDA 매칭이 없습니다. symbol/code mapping을 먼저 대조하세요."
    if result.any_valid_account_context and not result.any_raw_rows:
        return "유효한 계좌 컨텍스트는 있었지만 rows가 0건입니다. broker 화면의 실제 시장/계좌와 probe 파라미터를 대조하세요."
    return "아직 유효한 account context가 확인되지 않았습니다. 계좌번호, 상품코드, mock 해외 계좌 지원 여부를 먼저 재확인하세요."


def _recommended_account_matrix_next_step(result: OverseasBalanceMatrixProbeResult) -> str:
    if result.any_valid_account_context:
        return "account-valid response가 확인됐습니다. 다음 단계는 holdings normalization과 symbol matching 검증입니다."
    if result.account_validation_failures:
        return "quote works but balance account validation fails; do not trust holdings absence. broker UI에서 해외 mock 계좌번호/상품코드 매핑을 먼저 확인하세요."
    return "mock overseas balance support still unclear. broker UI에서 해외 mock balance 조회 가능 여부를 먼저 확인하세요."


def _attempt_to_probe_result(
    attempt: OverseasBalanceAttemptResult,
    *,
    supported: bool | None,
    can_verify_mock_support: bool,
) -> OverseasBalanceProbeResult:
    holdings = []
    for item in attempt.normalized_holdings_preview:
        if not isinstance(item, dict):
            continue
        holdings.append(
            OverseasHolding(
                symbol=str(item.get("symbol") or "").strip(),
                market=item.get("market"),
                exchange_code=item.get("exchange_code"),
                quantity=item.get("quantity"),
                avg_price=item.get("avg_price"),
                current_price=item.get("current_price"),
                market_value=item.get("market_value"),
                unrealized_pnl=item.get("unrealized_pnl"),
                currency=item.get("currency"),
                raw_identity_fields=dict(item.get("raw_identity_fields") or {}),
            )
        )
    return OverseasBalanceProbeResult(
        ok=attempt.ok,
        supported=supported,
        can_verify_mock_support=can_verify_mock_support,
        account_valid=attempt.account_valid,
        rate_limited=attempt.rate_limited,
        diagnostics=list(attempt.diagnostics),
        holdings=holdings,
        matched_holding=attempt.matched_holding,
        attempts=[],
        endpoint_used=attempt.endpoint_used,
        tr_id_used=attempt.tr_id_used,
        request_context=dict(attempt.request_context),
        response_status=dict(attempt.response_status),
        raw_row_count=attempt.raw_row_count,
        normalized_row_count=attempt.normalized_row_count,
        filtered_out_row_count=attempt.filtered_out_row_count,
        filter_reason_counts=dict(attempt.filter_reason_counts),
        raw_rows_preview=list(attempt.raw_rows_preview),
        normalized_holdings_preview=list(attempt.normalized_holdings_preview),
        interpretation=attempt.interpretation,
        raw=None,
    )
