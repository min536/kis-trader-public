"""Overseas risk guards — USD daily caps, fail-closed integrity."""
import json

from app.overseas_stock.session import to_eastern_date, us_session_date
from app.risk.schema import RiskEvaluationResult, RiskGuardResult


def evaluate_overseas_risk_guards(
    *, log_path, planned_notional_usd, max_orders, max_notional_usd, enabled=True, now=None
) -> RiskEvaluationResult:
    if not enabled:
        return RiskEvaluationResult(evaluated=False, allowed=True, action=None, reason="overseas risk guards disabled")
    integrity = evaluate_overseas_order_log_integrity(log_path=log_path)
    if not integrity.passed:
        return RiskEvaluationResult(evaluated=True, allowed=False, action=integrity.action, reason=integrity.reason, guard_results=(integrity,))
    orders_today = read_overseas_orders_today(log_path=log_path, now=now)
    order_limit = evaluate_overseas_daily_order_limit(orders_today=orders_today, max_orders=max_orders)
    notional_limit = evaluate_overseas_daily_notional_limit(
        orders_today=orders_today, planned_notional_usd=planned_notional_usd, max_notional_usd=max_notional_usd,
    )
    grs = (integrity, order_limit, notional_limit)
    for g in (order_limit, notional_limit):
        if not g.passed:
            return RiskEvaluationResult(evaluated=True, allowed=False, action=g.action, reason=g.reason, guard_results=grs)
    return RiskEvaluationResult(evaluated=True, allowed=True, action=None, reason="Overseas risk guards passed.", guard_results=grs)


def evaluate_overseas_order_log_integrity(*, log_path) -> RiskGuardResult:
    """Fail-closed integrity check on the overseas order log.

    Missing file → passed (nothing logged yet).
    Unreadable/corrupt → blocked (fail-closed).
    """
    from pathlib import Path
    p = Path(log_path)
    if not p.exists():
        return RiskGuardResult(
            guard_name="overseas_order_log_integrity",
            passed=True,
            reason="Overseas order log does not exist yet — nothing logged.",
        )
    try:
        with p.open("r") as f:
            for line in f:
                s = line.strip()
                if s:
                    json.loads(s)
    except (OSError, json.JSONDecodeError) as exc:
        return RiskGuardResult(
            guard_name="overseas_order_log_integrity",
            passed=False,
            reason=f"Overseas order log unreadable or corrupt: {exc}",
            action="blocked_overseas_order_log_unreadable",
        )
    return RiskGuardResult(
        guard_name="overseas_order_log_integrity",
        passed=True,
        reason="Overseas order log integrity check passed.",
    )


def evaluate_overseas_daily_notional_limit(
    *,
    orders_today: list[dict],
    planned_notional_usd: float,
    max_notional_usd: float,
) -> RiskGuardResult:
    """Block if today's notional + planned exceeds max_notional_usd."""
    today_notional = sum(
        int(o["qty"]) * float(o["unit_price"]) for o in orders_today
    )
    if max_notional_usd > 0 and today_notional + planned_notional_usd > max_notional_usd:
        return RiskGuardResult(
            guard_name="overseas_daily_notional_limit",
            passed=False,
            reason=(
                f"Today's overseas notional ${today_notional:.2f} + planned "
                f"${planned_notional_usd:.2f} exceeds daily limit "
                f"${max_notional_usd:.2f}."
            ),
            action="blocked_overseas_daily_notional_limit",
            details={
                "today_notional": today_notional,
                "planned": planned_notional_usd,
                "max": max_notional_usd,
            },
        )
    return RiskGuardResult(
        guard_name="overseas_daily_notional_limit",
        passed=True,
        reason="Overseas daily notional limit not exceeded.",
        details={
            "today_notional": today_notional,
            "planned": planned_notional_usd,
            "max": max_notional_usd,
        },
    )


def evaluate_overseas_daily_order_limit(
    *, orders_today: list[dict], max_orders: int
) -> RiskGuardResult:
    """Block if the number of overseas orders today >= max_orders."""
    count = len(orders_today)
    if max_orders > 0 and count >= max_orders:
        return RiskGuardResult(
            guard_name="overseas_daily_order_limit",
            passed=False,
            reason=f"Today's overseas order count {count} reached daily limit {max_orders}.",
            action="blocked_overseas_daily_order_limit",
            details={"count": count, "max": max_orders},
        )
    return RiskGuardResult(
        guard_name="overseas_daily_order_limit",
        passed=True,
        reason="Overseas daily order limit not reached.",
        details={"count": count, "max": max_orders},
    )


def read_overseas_orders_today(*, log_path, now=None) -> list[dict]:
    """Read today's overseas-submitted orders from a jsonl log file.

    Missing log → empty list. Malformed lines are silently skipped.
    Filters: action=="overseas_order_submitted" AND timestamp's ET date == today.
    """
    from pathlib import Path
    p = Path(log_path)
    if not p.exists():
        return []
    today = us_session_date(now)
    records = []
    with p.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("action") != "overseas_order_submitted":
                continue
            if to_eastern_date(record.get("timestamp", "")) != today:
                continue
            records.append(record)
    return records
