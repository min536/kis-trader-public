from __future__ import annotations

from typing import Mapping

from app.auth.token import ApiHttpError, is_rate_limit_response


_TRANSIENT_API_ERROR_MARKERS = (
    "nodename nor servname",
    "temporary failure in name resolution",
    "name or service not known",
    "network is unreachable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote end closed connection",
    "read timed out",
    "timed out",
    "timeout",
    "urlopen error",
)


def looks_like_rate_limit_error(exc: Exception) -> bool:
    text = str(exc)
    if "EGW00201" in text or "초당 거래건수" in text:
        return True
    if isinstance(exc, ApiHttpError):
        return is_rate_limit_response(exc.data)
    return False


def looks_like_buy_untradable_response(response: object) -> bool:
    if isinstance(response, Mapping):
        code = str(
            response.get("msg_cd")
            or response.get("msg_code")
            or response.get("code")
            or ""
        ).strip()
        message = " ".join(
            str(response.get(key) or "")
            for key in ("msg1", "msg", "message", "rt_msg")
        )
        return code == "40070000" or "매매불가" in message
    text = str(response or "")
    return "40070000" in text or "매매불가" in text


def looks_like_no_position_sell_response(response: object) -> bool:
    if isinstance(response, Mapping):
        code = str(response.get("msg_cd") or "").strip()
        message = " ".join(
            str(response.get(key) or "")
            for key in ("msg1", "message")
        )
        return code == "40240000" or "잔고내역" in message
    text = str(response or "")
    return "40240000" in text or "잔고내역" in text


def looks_like_transient_api_error(exc: Exception) -> bool:
    if looks_like_rate_limit_error(exc):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_API_ERROR_MARKERS)


def transient_api_source_from_exception(exc: Exception) -> str | None:
    if not looks_like_transient_api_error(exc):
        return None
    text = str(exc)
    if "잔고" in text:
        return "balance"
    if "토큰" in text:
        return "token"
    if "Hashkey" in text or "hashkey" in text:
        return "hashkey"
    if "현재가" in text:
        return "quote"
    if "주문가능" in text or "매수가능" in text:
        return "orderable"
    if "시장가 매도" in text or "매도 주문" in text:
        return "sell_order"
    if "시장가 매수" in text or "주문 실패" in text:
        return "buy_order"
    return None


def rate_limit_source_from_response_body(
    response: Mapping[str, object],
    *,
    source: str,
) -> str | None:
    if isinstance(response, dict) and is_rate_limit_response(response):
        return source
    return None


def rate_limit_source_from_exception(exc: Exception) -> str | None:
    if not looks_like_rate_limit_error(exc):
        return None
    text = str(exc)
    if "시장가 매도" in text or "매도 주문" in text:
        return "sell_order"
    if "시장가 매수" in text or "주문 실패" in text:
        return "buy_order"
    if "주문가능" in text or "매수가능" in text:
        return "orderable"
    if "잔고" in text:
        return "balance"
    if "현재가" in text:
        return "quote"
    return None


def should_downgrade_empty_buy_scan_to_backoff(
    *,
    rate_limit_triggered: bool,
    rate_limit_source: str | None,
    buy_scan_evaluated_count: int,
    scan_results_count: int,
    buy_scan_skipped_reason: str | None,
    rate_limit_partial_stop: bool,
) -> bool:
    if not rate_limit_triggered or rate_limit_source != "buy_scan":
        return False
    if buy_scan_evaluated_count > 0 or scan_results_count > 0:
        return False
    if rate_limit_partial_stop:
        return True
    return (buy_scan_skipped_reason or "").strip() in {
        "rate_limit_detected",
        "rate_limit_partial_stop",
        "api_backoff",
    }


# Skip reasons that represent a deliberate, logged benign skip of the BUY scan
# (budget/cadence/prefetch-degraded). An empty BUY scan attributable to one of
# these is a safe no-buy HOLD, not a CYCLE_ERROR. Rate-limit reasons are
# deliberately EXCLUDED (owned by should_downgrade_empty_buy_scan_to_backoff),
# as are structural reasons (pre_gated_*/shallow_shortlist_empty) where an
# empty scan signals an upstream universe/gating collapse that must stay loud.
_BENIGN_EMPTY_BUY_SCAN_SKIP_REASONS = frozenset(
    {
        "cycle_budget_low",
        "quote_prefetch_deadline",
        "quote_prefetch_timeout",
        "quote_prefetch_failed",
        "quote_prefetch_missing",
        "previous_scan_running",
        "previous_scan_worker_stale",
        "api_transient_backoff",
        "api_backoff",
        "api_budget_limited",
        "api_request_budget_limited",
        "api_request_budget_wait",
        "cadence_not_reached",
    }
)


def is_benign_empty_buy_scan(
    *,
    buy_scan_evaluated_count: int,
    scan_results_count: int,
    buy_scan_skipped_reason: str | None,
    buy_scan_partial_budget: bool,
) -> bool:
    """Whether an empty BUY scan is a benign budget/prefetch skip (safe HOLD).

    Returns False — i.e. the empty scan must stay a CYCLE_ERROR — whenever
    anything was actually evaluated, or the empty scan is genuinely unexplained
    (no known benign reason and not a partial-budget cycle), or the reason is a
    structural universe/gating collapse. This is defense-in-depth for the
    buy_lane prefetch-race fix, never a substitute for it.
    """
    if buy_scan_evaluated_count > 0 or scan_results_count > 0:
        return False
    reason = (buy_scan_skipped_reason or "").strip()
    if reason in _BENIGN_EMPTY_BUY_SCAN_SKIP_REASONS:
        return True
    return bool(buy_scan_partial_budget)


def build_buy_scan_rate_limit_degraded_reason(
    *,
    backoff_applied_seconds: int,
    rate_limit_partial_stop_symbol: str | None,
    rate_limit_partial_completed_count: int,
    rate_limit_partial_remaining_count: int,
) -> str:
    detail_parts: list[str] = []
    if rate_limit_partial_stop_symbol:
        detail_parts.append(f"중단 종목={rate_limit_partial_stop_symbol}")
    if rate_limit_partial_completed_count or rate_limit_partial_remaining_count:
        detail_parts.append(
            f"완료={rate_limit_partial_completed_count}, 남음={rate_limit_partial_remaining_count}"
        )
    if backoff_applied_seconds > 0:
        detail_parts.append(f"backoff={backoff_applied_seconds}s")
    detail_text = ""
    if detail_parts:
        detail_text = " (" + " | ".join(detail_parts) + ")"
    return (
        "BUY scan이 KIS rate limit/backoff로 초기에 중단되어 "
        f"이번 cycle에서는 후보 분석을 생략합니다.{detail_text}"
    )
