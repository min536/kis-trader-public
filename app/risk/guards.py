from dataclasses import dataclass

from app.core.formatters import format_krw
from app.core.order_log import (
    OrderLogReadError,
    count_today_buy_order_submissions,
    count_today_sell_order_submissions,
    sum_today_buy_order_submission_notional_krw,
    sum_today_sell_order_submission_notional_krw,
)
from app.execution.schema import ExecutionSnapshot
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioSnapshot
from app.risk.schema import (
    RiskEvaluationResult,
    RiskGuardResult,
    serialize_risk_evaluation_result,
)


@dataclass(frozen=True)
class RiskGuardInput:
    market_snapshot: MarketSnapshot
    portfolio_snapshot: PortfolioSnapshot
    execution_snapshot: ExecutionSnapshot
    side: str
    enabled: bool
    daily_max_order_submissions: int
    daily_max_notional_krw: int


def _is_buy_side(side: str) -> bool:
    return side.upper() == "BUY"


def _build_base_details(guard_input: RiskGuardInput) -> dict[str, object]:
    market_snapshot = guard_input.market_snapshot
    portfolio_snapshot = guard_input.portfolio_snapshot
    execution_snapshot = guard_input.execution_snapshot
    current_price_krw = execution_snapshot.current_price
    planned_notional_krw = execution_snapshot.expected_notional_krw
    side = guard_input.side.upper()
    order_log_integrity_ok = True
    order_log_error_code = None
    try:
        if _is_buy_side(side):
            today_order_submissions = count_today_buy_order_submissions(
                strict=guard_input.enabled
            )
            today_submitted_notional_krw = sum_today_buy_order_submission_notional_krw(
                strict=guard_input.enabled
            )
        else:
            today_order_submissions = count_today_sell_order_submissions(
                strict=guard_input.enabled
            )
            today_submitted_notional_krw = sum_today_sell_order_submission_notional_krw(
                strict=guard_input.enabled
            )
    except OrderLogReadError as exc:
        today_order_submissions = 0
        today_submitted_notional_krw = 0
        order_log_integrity_ok = False
        order_log_error_code = exc.reason_code
    projected_order_submissions = today_order_submissions + 1
    projected_notional_krw = today_submitted_notional_krw + planned_notional_krw
    daily_max_order_submissions = max(int(guard_input.daily_max_order_submissions or 0), 0)
    daily_max_notional_krw = max(int(guard_input.daily_max_notional_krw or 0), 0)
    remaining_order_submissions = max(daily_max_order_submissions - today_order_submissions, 0)
    remaining_notional_krw = max(daily_max_notional_krw - today_submitted_notional_krw, 0)
    projected_remaining_notional_krw = daily_max_notional_krw - projected_notional_krw
    notional_utilization_pct = (
        round(today_submitted_notional_krw / daily_max_notional_krw * 100.0, 2)
        if daily_max_notional_krw > 0
        else None
    )
    projected_notional_utilization_pct = (
        round(projected_notional_krw / daily_max_notional_krw * 100.0, 2)
        if daily_max_notional_krw > 0
        else None
    )

    return {
        "enabled": guard_input.enabled,
        "side": side,
        "symbol": market_snapshot.symbol,
        "current_price_krw": current_price_krw,
        "qty": (execution_snapshot.expected_notional_krw // current_price_krw) if current_price_krw > 0 else 0,
        "orderable_cash_krw": execution_snapshot.orderable_cash,
        "orderable_qty": execution_snapshot.orderable_qty,
        "planned_notional_krw": planned_notional_krw,
        "cash_total_krw": portfolio_snapshot.cash_total,
        "total_evaluation_amount_krw": portfolio_snapshot.total_evaluation_amount,
        "today_order_submissions": today_order_submissions,
        "daily_max_order_submissions": daily_max_order_submissions,
        "projected_order_submissions": projected_order_submissions,
        "remaining_order_submissions": remaining_order_submissions,
        "today_submitted_notional_krw": today_submitted_notional_krw,
        "daily_max_notional_krw": daily_max_notional_krw,
        "projected_notional_krw": projected_notional_krw,
        "remaining_notional_krw": remaining_notional_krw,
        "projected_remaining_notional_krw": projected_remaining_notional_krw,
        "notional_utilization_pct": notional_utilization_pct,
        "projected_notional_utilization_pct": projected_notional_utilization_pct,
        "order_log_integrity_ok": order_log_integrity_ok,
        "order_log_error_code": order_log_error_code,
    }


def build_risk_guard_console_lines(decision: RiskEvaluationResult) -> list[str]:
    details = decision.details
    enabled = bool(details.get("enabled", False))

    lines = [
        "=== 리스크 가드 ===",
        f"리스크 가드 사용 여부: {'ON' if enabled else 'OFF'}",
    ]

    if decision.evaluated:
        today_order_submissions = details.get("today_order_submissions", 0)
        daily_max_order_submissions = details.get("daily_max_order_submissions", 0)
        today_submitted_notional_krw = details.get("today_submitted_notional_krw", 0)
        daily_max_notional_krw = details.get("daily_max_notional_krw", 0)
        planned_notional_krw = details.get("planned_notional_krw", 0)
        projected_notional_krw = details.get("projected_notional_krw", 0)
        remaining_notional_krw = details.get("remaining_notional_krw", 0)
        projected_remaining_notional_krw = details.get("projected_remaining_notional_krw", 0)
        notional_utilization_pct = details.get("notional_utilization_pct")
        projected_notional_utilization_pct = details.get("projected_notional_utilization_pct")
        lines.extend(
            [
                f"일일 주문 제출 횟수: {today_order_submissions} / {daily_max_order_submissions}",
                f"일일 총 {_side_label(details)} 예정 금액: "
                f"{format_krw(today_submitted_notional_krw)} / {format_krw(daily_max_notional_krw)}",
                f"남은 일일 {_side_label(details)} 예산: {format_krw(remaining_notional_krw)}",
                f"이번 주문 예정 금액: {format_krw(planned_notional_krw)}",
                f"이번 주문 포함 예상 누적 금액: {format_krw(projected_notional_krw)}",
                f"이번 주문 포함 예상 잔여 예산: {format_krw(projected_remaining_notional_krw)}",
            ]
        )
        if notional_utilization_pct is not None:
            lines.append(f"현재 일일 예산 사용률: {float(notional_utilization_pct):.2f}%")
        if projected_notional_utilization_pct is not None:
            lines.append(
                "이번 주문 포함 예상 사용률: "
                f"{float(projected_notional_utilization_pct):.2f}%"
            )
            if float(projected_notional_utilization_pct) >= 95.0:
                lines.append(
                    f"경고: 일일 {_side_label(details)} 예산이 95% 이상 소진될 예정입니다."
                )

    lines.append(
        "최종 리스크 판단: "
        + (
            "미평가"
            if not decision.evaluated
            else ("통과" if decision.allowed else "차단")
        )
    )

    for guard_result in decision.guard_results:
        status = "PASS" if guard_result.passed else "FAIL"
        lines.append(f"{guard_result.guard_name}: {status}")

    if decision.reason:
        lines.append(f"이유: {decision.reason}")

    return lines


def build_risk_guard_skipped_console_lines(*, enabled: bool, skip_reason: str) -> list[str]:
    decision = build_skipped_risk_evaluation_result(
        enabled=enabled,
        skip_reason=skip_reason,
    )
    return build_risk_guard_console_lines(decision)


def build_skipped_risk_evaluation_result(
    *,
    enabled: bool,
    skip_reason: str,
) -> RiskEvaluationResult:
    return RiskEvaluationResult(
        evaluated=False,
        allowed=True,
        action=None,
        reason=skip_reason,
        details={"enabled": enabled},
        guard_results=(),
    )


def evaluate_order_log_integrity(
    *,
    base_details: dict[str, object],
) -> RiskGuardResult:
    side = str(base_details.get("side", "BUY")).upper()
    action = (
        "blocked_buy_order_log_untrusted"
        if _is_buy_side(side)
        else "blocked_sell_order_log_untrusted"
    )
    error_code = str(base_details.get("order_log_error_code") or "").strip()
    if not bool(base_details.get("order_log_integrity_ok", True)):
        return RiskGuardResult(
            guard_name="order_log_integrity",
            passed=False,
            action=action,
            reason="주문 로그를 신뢰할 수 없어 리스크 가드를 fail-closed로 차단합니다.",
            details={"order_log_error_code": error_code or "unknown"},
        )

    return RiskGuardResult(
        guard_name="order_log_integrity",
        passed=True,
        reason="주문 로그 무결성 검사를 통과했습니다.",
    )


def evaluate_daily_order_limit(
    *,
    base_details: dict[str, object],
) -> RiskGuardResult:
    today_order_submissions = int(base_details["today_order_submissions"])
    daily_max_order_submissions = int(base_details["daily_max_order_submissions"])
    side = str(base_details.get("side", "BUY")).upper()
    action = (
        "blocked_buy_daily_order_limit"
        if _is_buy_side(side)
        else "blocked_sell_daily_order_limit"
    )
    if today_order_submissions >= daily_max_order_submissions:
        return RiskGuardResult(
            guard_name="daily_order_limit",
            passed=False,
            action=action,
            reason=(
                f"오늘 주문 제출 횟수 {today_order_submissions}회가 "
                f"일일 제한 {daily_max_order_submissions}회에 도달했습니다."
            ),
            details={
                "today_order_submissions": today_order_submissions,
                "daily_max_order_submissions": daily_max_order_submissions,
            },
        )

    return RiskGuardResult(
        guard_name="daily_order_limit",
        passed=True,
        reason="일일 주문 제출 횟수 제한을 통과했습니다.",
        details={
            "today_order_submissions": today_order_submissions,
            "daily_max_order_submissions": daily_max_order_submissions,
        },
    )


def evaluate_daily_notional_limit(
    *,
    base_details: dict[str, object],
) -> RiskGuardResult:
    projected_notional_krw = int(base_details["projected_notional_krw"])
    daily_max_notional_krw = int(base_details["daily_max_notional_krw"])
    side = str(base_details.get("side", "BUY")).upper()
    action = (
        "blocked_buy_daily_notional_limit"
        if _is_buy_side(side)
        else "blocked_sell_daily_notional_limit"
    )
    side_label = _side_label(base_details)
    if projected_notional_krw > daily_max_notional_krw:
        return RiskGuardResult(
            guard_name="daily_notional_limit",
            passed=False,
            action=action,
            reason=(
                f"오늘 총 {side_label} 예정 금액 {projected_notional_krw:,}원이 "
                f"일일 제한 {daily_max_notional_krw:,}원을 초과합니다."
            ),
            details={
                "today_submitted_notional_krw": int(base_details.get("today_submitted_notional_krw", 0) or 0),
                "projected_notional_krw": projected_notional_krw,
                "daily_max_notional_krw": daily_max_notional_krw,
                "remaining_notional_krw": int(base_details.get("remaining_notional_krw", 0) or 0),
                "projected_remaining_notional_krw": int(
                    base_details.get("projected_remaining_notional_krw", 0) or 0
                ),
                "notional_utilization_pct": base_details.get("notional_utilization_pct"),
                "projected_notional_utilization_pct": base_details.get(
                    "projected_notional_utilization_pct"
                ),
            },
        )

    return RiskGuardResult(
        guard_name="daily_notional_limit",
        passed=True,
        reason=f"일일 총 {side_label} 예정 금액 제한을 통과했습니다.",
        details={
            "today_submitted_notional_krw": int(base_details.get("today_submitted_notional_krw", 0) or 0),
            "projected_notional_krw": projected_notional_krw,
            "daily_max_notional_krw": daily_max_notional_krw,
            "remaining_notional_krw": int(base_details.get("remaining_notional_krw", 0) or 0),
            "projected_remaining_notional_krw": int(
                base_details.get("projected_remaining_notional_krw", 0) or 0
            ),
            "notional_utilization_pct": base_details.get("notional_utilization_pct"),
            "projected_notional_utilization_pct": base_details.get(
                "projected_notional_utilization_pct"
            ),
        },
    )


def _side_label(details: dict[str, object]) -> str:
    return "매수" if _is_buy_side(str(details.get("side", "BUY"))) else "매도"


def _aggregate_risk_evaluation(
    *,
    enabled: bool,
    base_details: dict[str, object],
    guard_results: tuple[RiskGuardResult, ...],
) -> RiskEvaluationResult:
    if not enabled:
        return RiskEvaluationResult(
            evaluated=False,
            allowed=True,
            action=None,
            reason="리스크 가드가 비활성화되어 검사를 건너뜁니다.",
            details=base_details,
            guard_results=(),
        )

    for guard_result in guard_results:
        if not guard_result.passed:
            return RiskEvaluationResult(
                evaluated=True,
                allowed=False,
                action=guard_result.action,
                reason=guard_result.reason,
                details=base_details,
                guard_results=guard_results,
            )

    return RiskEvaluationResult(
        evaluated=True,
        allowed=True,
        action=None,
        reason="리스크 가드를 통과했습니다.",
        details=base_details,
        guard_results=guard_results,
    )

def evaluate_buy_risk_guards(
    *,
    guard_input: RiskGuardInput,
) -> RiskEvaluationResult:
    base_details = _build_base_details(guard_input)
    guard_results = (
        evaluate_order_log_integrity(base_details=base_details),
        evaluate_daily_order_limit(base_details=base_details),
        evaluate_daily_notional_limit(base_details=base_details),
    )
    return _aggregate_risk_evaluation(
        enabled=guard_input.enabled,
        base_details=base_details,
        guard_results=guard_results,
    )


def evaluate_sell_risk_guards(
    *,
    guard_input: RiskGuardInput,
) -> RiskEvaluationResult:
    base_details = _build_base_details(guard_input)
    guard_results = (
        evaluate_order_log_integrity(base_details=base_details),
        evaluate_daily_order_limit(base_details=base_details),
        evaluate_daily_notional_limit(base_details=base_details),
    )
    return _aggregate_risk_evaluation(
        enabled=guard_input.enabled,
        base_details=base_details,
        guard_results=guard_results,
    )


def serialize_risk_evaluation_for_log(result: RiskEvaluationResult) -> dict[str, object]:
    return serialize_risk_evaluation_result(result)
