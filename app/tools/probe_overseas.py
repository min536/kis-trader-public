import argparse

from app.auth.settings import get_settings
from app.overseas_stock.probe import run_overseas_probe


def _status_label(*, ok: bool, supported: bool | None) -> str:
    if ok:
        return "success"
    if supported is False:
        return "unsupported"
    return "failed"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="KIS 해외주식 mock/live read probe",
    )
    parser.add_argument("--symbol", default="NVDA", help="해외 종목 심볼")
    parser.add_argument("--market", default="US", help="시장 힌트 (예: US)")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--quote-only", action="store_true", help="시세 probe만 실행")
    mode_group.add_argument("--balance-only", action="store_true", help="잔고 probe만 실행")
    mode_group.add_argument("--balance-matrix", action="store_true", help="잔고 probe 파라미터 matrix 실행")
    mode_group.add_argument("--balance-account-matrix", action="store_true", help="계좌/상품코드 중심 잔고 matrix 실행")
    args = parser.parse_args()

    settings = get_settings()
    environment = "mock" if "openapivts" in settings.base_url.lower() else "live_or_unknown"
    summary = run_overseas_probe(
        symbol=args.symbol.strip().upper(),
        market=args.market.strip().upper(),
        quote_only=bool(args.quote_only),
        balance_only=bool(args.balance_only),
        balance_matrix=bool(args.balance_matrix),
        balance_account_matrix=bool(args.balance_account_matrix),
    )

    print("=== overseas probe ===")
    print(f"environment: {environment}")
    print(f"account: {summary.account_masked}")
    print(f"symbol: {summary.symbol} | market: {summary.market}")
    print()
    if summary.balance_probe is not None:
        print(
            f"balance probe: {_status_label(ok=summary.balance_probe.ok, supported=summary.balance_probe.supported)}"
        )
        if summary.balance_probe.endpoint_used or summary.balance_probe.tr_id_used:
            print(
                "  endpoint used: "
                f"{summary.balance_probe.endpoint_used or '-'} | tr_id={summary.balance_probe.tr_id_used or '-'}"
            )
        if summary.balance_probe.matched_holding is not None:
            holding = summary.balance_probe.matched_holding
            print(
                "  matched holding: "
                f"{holding.symbol} | qty={holding.quantity if holding.quantity is not None else '-'} | "
                f"avg={holding.avg_price if holding.avg_price is not None else '-'} | "
                f"px={holding.current_price if holding.current_price is not None else '-'} | "
                f"mv={holding.market_value if holding.market_value is not None else '-'} | "
                f"pnl={holding.unrealized_pnl if holding.unrealized_pnl is not None else '-'} | "
                f"ccy={holding.currency or '-'}"
            )
        else:
            print(f"  holdings seen: {len(summary.balance_probe.holdings)}")
        print(
            "  normalization: "
            f"raw_rows={summary.balance_probe.raw_row_count} | "
            f"normalized={summary.balance_probe.normalized_row_count} | "
            f"filtered={summary.balance_probe.filtered_out_row_count}"
        )
        print(
            f"  balance interpretation: {summary.balance_probe.interpretation or '불명확'}"
        )
        if summary.balance_probe.filter_reason_counts:
            parts = ", ".join(
                f"{key}={value}"
                for key, value in summary.balance_probe.filter_reason_counts.items()
            )
            print(f"  filter reasons: {parts}")
        if summary.balance_probe.raw_rows_preview:
            print(f"  raw rows preview: {summary.balance_probe.raw_rows_preview[:2]}")
        if summary.balance_probe.normalized_holdings_preview:
            print(
                f"  normalized preview: {summary.balance_probe.normalized_holdings_preview[:2]}"
            )
        balance_diagnostics = list(summary.balance_probe.diagnostics[:5])
        for line in balance_diagnostics:
            print(f"  - {line}")
        if not balance_diagnostics:
            print("  - diagnostics 없음")
        print()
    if summary.balance_probe_matrix is not None:
        matrix = summary.balance_probe_matrix
        print("balance matrix:")
        print(
            f"  any_valid_account_context={'yes' if matrix.any_valid_account_context else 'no'} | "
            f"any_raw_rows={'yes' if matrix.any_raw_rows else 'no'} | "
            f"any_symbol_match={'yes' if matrix.any_symbol_match else 'no'}"
        )
        for idx, item in enumerate(matrix.attempts[:8], start=1):
            status = item.response_status or {}
            request = item.request_context or {}
            print(
                f"  {idx}. "
                f"exch={request.get('OVRS_EXCG_CD', '-')} | "
                f"ccy={request.get('TR_CRCY_CD', '-') or 'EMPTY'} | "
                f"tr_id={item.tr_id_used or '-'} | "
                f"rt_cd={status.get('rt_cd') or '-'} | "
                f"msg_cd={status.get('msg_cd') or '-'} | "
                f"raw={item.raw_row_count} | norm={item.normalized_row_count} | "
                f"match={'yes' if item.matched_symbol else 'no'}"
            )
        if matrix.best_attempt is not None:
            best = matrix.best_attempt
            print("  best evidence:")
            print(
                f"    exch={best.request_context.get('OVRS_EXCG_CD', '-')} | "
                f"ccy={best.request_context.get('TR_CRCY_CD', '-') or 'EMPTY'} | "
                f"tr_id={best.tr_id_used or '-'} | "
                f"rt_cd={(best.response_status or {}).get('rt_cd') or '-'} | "
                f"interpretation={best.interpretation or '-'}"
            )
        print()
    if summary.balance_account_matrix is not None:
        matrix = summary.balance_account_matrix
        print("balance account matrix:")
        print(
            f"  any_valid_account_context={'yes' if matrix.any_valid_account_context else 'no'} | "
            f"validation_failures={len(matrix.account_validation_failures)} | "
            f"any_raw_rows={'yes' if matrix.any_raw_rows else 'no'} | "
            f"any_symbol_match={'yes' if matrix.any_symbol_match else 'no'}"
        )
        for idx, item in enumerate(matrix.attempts[:8], start=1):
            status = item.response_status or {}
            account_used = item.account_context_used or {}
            print(
                f"  {idx}. "
                f"source={item.account_context_source or '-'} | "
                f"CANO={account_used.get('CANO', '-')} | "
                f"ACNT={account_used.get('ACNT_PRDT_CD', '-')} | "
                f"tr_id={item.tr_id_used or '-'} | "
                f"validation_failed={'yes' if item.account_validation_error_detected else 'no'} | "
                f"account_valid={item.account_valid if item.account_valid is not None else '-'} | "
                f"rt_cd={status.get('rt_cd') or '-'} | "
                f"msg_cd={status.get('msg_cd') or '-'}"
            )
        if matrix.best_attempt is not None:
            best = matrix.best_attempt
            print("  best evidence:")
            print(
                f"    source={best.account_context_source or '-'} | "
                f"CANO={(best.account_context_used or {}).get('CANO', '-')} | "
                f"ACNT={(best.account_context_used or {}).get('ACNT_PRDT_CD', '-')} | "
                f"tr_id={best.tr_id_used or '-'} | "
                f"rt_cd={(best.response_status or {}).get('rt_cd') or '-'} | "
                f"validation_failed={'yes' if best.account_validation_error_detected else 'no'} | "
                f"interpretation={best.interpretation or '-'}"
            )
        print()
    if summary.quote_probe is not None:
        print(
            f"quote probe: {_status_label(ok=summary.quote_probe.ok, supported=summary.quote_probe.supported)}"
        )
        if summary.quote_probe.endpoint_used or summary.quote_probe.tr_id_used:
            print(
                "  endpoint used: "
                f"{summary.quote_probe.endpoint_used or '-'} | tr_id={summary.quote_probe.tr_id_used or '-'}"
            )
        if summary.quote_probe.quote is not None:
            quote = summary.quote_probe.quote
            print(
                "  quote: "
                f"{quote.symbol} | market={quote.market} | exchange={quote.exchange_code or '-'} | "
                f"px={quote.last_price if quote.last_price is not None else '-'} | "
                f"chg={quote.change if quote.change is not None else '-'} | "
                f"chg%={quote.change_pct if quote.change_pct is not None else '-'} | "
                f"ccy={quote.currency or '-'}"
            )
        for line in summary.quote_probe.diagnostics[:5]:
            print(f"  - {line}")
        print()
    print(f"mock support: {summary.mock_support}")
    print(f"recommended next step: {summary.recommended_next_step}")
    if summary.artifact_path:
        print(f"artifact: {summary.artifact_path}")


if __name__ == "__main__":
    main()
