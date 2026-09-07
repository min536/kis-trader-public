"""Risk-critical equity/account-state arithmetic (R6 / E1).

Neutral leaf relocated **verbatim** from ``app/reporting/performance.py`` (R6).
Lives outside ``app/reporting`` so the reporting facade's eager imports cannot
form a cycle (R6-11), and so the risk cluster is isolated from the 959-line
reporting module. ``performance.py`` re-imports these names for
``build_performance_report``; ``pnl_brake``/``console``/``main`` import the
risk functions directly from here. Pure calculation only — no alerts, no order
log, no broker calls (leaf-purity invariant).
"""

from typing import Any

from app.auth.account_scope import get_performance_snapshots_path
from app.core.jsonl import read_jsonl_objects
from app.core.time_utils import KOREA_TZ, _timestamp_to_datetime, get_korean_now

DEPLOYMENT_INVARIANT_EQUITY_BASIS = (
    "max_cash_total_or_next_day_or_orderable_plus_holdings_market_value"
)
# Legacy basis name retained so we can still treat older snapshots as compatible
# baselines on the day a session rolls over to the new basis. The values it
# encoded (max(cash_total, cash_next_day) + holdings) are a strict subset of
# the new max(...) so historical snapshots remain a valid lower-bound baseline.
LEGACY_DEPLOYMENT_INVARIANT_EQUITY_BASIS = (
    "max_cash_total_or_next_day_plus_holdings_market_value"
)


def _calculate_drawdowns(snapshots: list[dict[str, Any]]) -> tuple[float, float]:
    peak = 0
    max_drawdown = 0.0
    current_drawdown = 0.0
    ordered = sorted(
        snapshots,
        key=lambda item: str(item.get("timestamp", "")),
    )
    for snapshot in ordered:
        equity = int(snapshot.get("total_equity_krw", 0) or 0)
        if equity <= 0:
            continue
        peak = max(peak, equity)
        if peak <= 0:
            continue
        drawdown = ((equity / peak) - 1.0) * 100
        current_drawdown = drawdown
        max_drawdown = min(max_drawdown, drawdown)
    return current_drawdown, max_drawdown


def _load_performance_snapshots() -> list[dict[str, Any]]:
    performance_snapshots_file = get_performance_snapshots_path()
    if not performance_snapshots_file.exists():
        return []
    snapshots, _errors = read_jsonl_objects(performance_snapshots_file)
    return snapshots


def _uses_deployment_invariant_equity(snapshot: dict[str, Any]) -> bool:
    basis = str(snapshot.get("equity_basis", "")).strip()
    if basis:
        # Accept the legacy basis as well so a session that rolls over to the
        # new basis mid-day still has yesterday's snapshots available as a
        # baseline (the legacy formula is a strict lower bound of the new one).
        return basis in (
            DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            LEGACY_DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        )
    return bool(snapshot.get("cash_deployment_invariant"))


def _select_equity_basis_compatible_history(
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compatible = [
        snapshot for snapshot in snapshots if _uses_deployment_invariant_equity(snapshot)
    ]
    if not compatible:
        return snapshots
    reliable_compatible = [
        snapshot for snapshot in compatible if _is_snapshot_reliable_for_risk(snapshot)
    ]
    return reliable_compatible or compatible


def _is_snapshot_reliable_for_risk(snapshot: dict[str, Any]) -> bool:
    """Check whether a deployment-invariant equity snapshot is trustworthy.

    KIS API's ``tot_evlu_amt`` equals ``cash_orderable + holdings_at_market``,
    which excludes T+2-pending settlement cash sitting in ``dnca_tot_amt``.
    When our deployment-invariant formula picks up that unsettled cash via
    ``max(cash_total, cash_next_day, cash_orderable)``, the resulting equity
    can far exceed the raw balance, manufacturing a phantom loss later when
    the cash settles and orderable rises to match.

    A snapshot is *unreliable* when deployment-invariant equity exceeds
    ``raw_balance + allowed_settlement_excess + tolerance``.
    """
    total_equity_krw = int(snapshot.get("total_equity_krw", 0) or 0)
    if total_equity_krw <= 0:
        return False

    raw_balance_total_evaluation_amount_krw = int(
        snapshot.get("raw_balance_total_evaluation_amount_krw", 0) or 0
    )
    # Older snapshots that lack raw_balance are assumed reliable —
    # we cannot retroactively verify them.
    if raw_balance_total_evaluation_amount_krw <= 0:
        return True

    cash_orderable_krw = int(snapshot.get("cash_orderable_krw", 0) or 0)
    cash_next_day_krw = int(snapshot.get("cash_next_day_krw", 0) or 0)
    allowed_settlement_excess_krw = max(
        cash_next_day_krw - cash_orderable_krw,
        0,
    )
    reliability_tolerance_krw = 5_000
    max_reliable_equity_krw = (
        raw_balance_total_evaluation_amount_krw
        + allowed_settlement_excess_krw
        + reliability_tolerance_krw
    )
    return total_equity_krw <= max_reliable_equity_krw


def _build_deployment_invariant_equity(
    *,
    cash_total_krw: int,
    cash_next_day_krw: int,
    cash_orderable_krw: int,
    holdings_market_value_krw: int,
) -> tuple[int, int]:
    # KIS의 dnca_tot_amt(총예수금) / nxdy_excc_amt(익일정산) 두 필드는
    # 매도 직후 수 분 동안 갱신이 지연되거나 0에 가깝게 떨어질 수 있어,
    # 그 두 값만 max로 잡으면 prvs_rcdl_excc_amt(주문가능현금)에 이미 반영된
    # 매도 회수금이 brake equity에서 통째로 사라져 false drawdown이 발생한다.
    # cash_orderable도 함께 max에 포함시켜 settled cash, settlement-pending cash,
    # 주문가능 cash 중 가장 큰 값을 cash leg로 채택한다. 매수 직후
    # cash_orderable이 줄어들어도 다른 두 값이 보완해 주므로 양방향으로 안전하다.
    settlement_aware_cash_leg = max(
        int(cash_total_krw or 0),
        int(cash_next_day_krw or 0),
        int(cash_orderable_krw or 0),
    )
    return (
        settlement_aware_cash_leg + int(holdings_market_value_krw or 0),
        settlement_aware_cash_leg,
    )


def _build_account_state(
    *,
    portfolio_snapshot,
    sell_analysis_results: tuple[Any, ...],
) -> dict[str, int]:
    analysis_by_symbol = {
        result.symbol: result for result in sell_analysis_results
    }
    holdings_market_value = 0
    total_cost_basis = 0
    unrealized_gross = 0
    unrealized_net = 0

    for position in portfolio_snapshot.held_positions:
        analysis = analysis_by_symbol.get(position.symbol)
        total_cost_basis += int(position.average_cost) * int(position.holding_qty)
        if analysis is not None:
            holdings_market_value += (
                int(analysis.market_snapshot.current_price) * int(analysis.holding_qty)
            )
            unrealized_gross += int(
                analysis.sell_decision.details.get("gross_pnl_krw", 0)
            )
            unrealized_net += int(
                analysis.sell_decision.details.get("net_pnl_krw", 0)
            )
            continue

        holdings_market_value += int(position.market_value)
        unrealized_gross += int(position.gross_pnl)
        unrealized_net += int(position.gross_pnl)

    operating_equity = int(portfolio_snapshot.cash_orderable) + int(holdings_market_value)
    # `cash_orderable` shrinks immediately after intraday buys, so
    # `orderable_cash + holdings_value` can look like a loss even when the
    # account simply rotated cash into stock without losing mark-to-market value.
    # We also prefer the larger of same-day cash and next-day settlement cash so
    # that intraday sells/partial fills do not look like a loss before cash settles.
    deployment_invariant_equity, deployment_invariant_cash_leg = (
        _build_deployment_invariant_equity(
            cash_total_krw=int(portfolio_snapshot.cash_total),
            cash_next_day_krw=int(portfolio_snapshot.cash_next_day),
            cash_orderable_krw=int(portfolio_snapshot.cash_orderable),
            holdings_market_value_krw=int(holdings_market_value),
        )
    )
    return {
        "cash_total_krw": int(portfolio_snapshot.cash_total),
        "cash_orderable_krw": int(portfolio_snapshot.cash_orderable),
        "cash_next_day_krw": int(portfolio_snapshot.cash_next_day),
        "holdings_market_value_krw": int(holdings_market_value),
        "total_cost_basis_krw": int(total_cost_basis),
        "total_unrealized_gross_pnl_krw": int(unrealized_gross),
        "total_unrealized_net_pnl_krw": int(unrealized_net),
        "operating_equity_krw": operating_equity,
        "deployment_invariant_cash_leg_krw": deployment_invariant_cash_leg,
        "deployment_invariant_equity_krw": deployment_invariant_equity,
    }


def build_account_state_payload(
    *,
    portfolio_snapshot,
    sell_analysis_results: tuple[Any, ...],
) -> dict[str, int]:
    return _build_account_state(
        portfolio_snapshot=portfolio_snapshot,
        sell_analysis_results=sell_analysis_results,
    )


def select_risk_managed_current_equity_krw(
    *,
    account_state: dict[str, int],
    raw_balance_total_evaluation_amount_krw: int,
) -> int:
    """Choose the most reliable equity figure for risk calculations.

    When the deployment-invariant equity is inflated by T+2 settlement cash
    that exceeds the KIS raw balance, fall back to the raw balance or
    operating equity — whichever is higher — to avoid manufacturing phantom
    drawdown or PnL loss.
    """
    deployment_invariant_equity_krw = int(
        account_state.get("deployment_invariant_equity_krw", 0) or 0
    )
    operating_equity_krw = int(account_state.get("operating_equity_krw", 0) or 0)
    raw_balance_total_evaluation_amount_krw = int(
        raw_balance_total_evaluation_amount_krw or 0
    )
    probe_snapshot = {
        "total_equity_krw": deployment_invariant_equity_krw,
        "raw_balance_total_evaluation_amount_krw": raw_balance_total_evaluation_amount_krw,
        "cash_orderable_krw": int(account_state.get("cash_orderable_krw", 0) or 0),
        "cash_next_day_krw": int(account_state.get("cash_next_day_krw", 0) or 0),
        "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        "cash_deployment_invariant": True,
    }
    if _is_snapshot_reliable_for_risk(probe_snapshot):
        return deployment_invariant_equity_krw
    fallback_equity_krw = max(
        raw_balance_total_evaluation_amount_krw,
        operating_equity_krw,
    )
    if fallback_equity_krw > 0:
        return fallback_equity_krw
    return deployment_invariant_equity_krw


def build_daily_pnl_state(
    *,
    current_equity_krw: int,
    warning_pct: float,
    buy_pause_pct: float,
    hard_stop_pct: float,
) -> dict[str, Any]:
    history = _load_performance_snapshots()
    now = get_korean_now()
    today_snapshots = []
    for snapshot in history:
        timestamp = _timestamp_to_datetime(str(snapshot.get("timestamp", "")))
        if timestamp is None or timestamp.date() != now.date():
            continue
        # Exclude snapshots taken before 09:30 KST. KIS T+2 settlement processes
        # during the first ~20 minutes after market open (09:00–09:20), causing
        # dnca_tot_amt to drop as prior-day buy cash finalises. A pre-settlement
        # baseline would manufacture a phantom loss for the rest of the session.
        if (timestamp.hour, timestamp.minute) < (9, 30):
            continue
        today_snapshots.append(snapshot)

    compatible_today_snapshots = [
        snapshot for snapshot in today_snapshots if _uses_deployment_invariant_equity(snapshot)
    ]
    reliable_today_snapshots = [
        snapshot
        for snapshot in compatible_today_snapshots
        if _is_snapshot_reliable_for_risk(snapshot)
    ]
    if reliable_today_snapshots:
        today_snapshots = reliable_today_snapshots
    elif compatible_today_snapshots:
        return {
            "evaluated": False,
            "status": "DATA_INSUFFICIENT",
            "daily_pnl_pct": None,
            "baseline_equity_krw": None,
            "current_equity_krw": int(current_equity_krw),
            "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "당일 deployment-invariant snapshot이 있으나 risk baseline 신뢰도가 낮아 일중 손실률 계산을 보류합니다.",
        }

    if not today_snapshots:
        return {
            "evaluated": False,
            "status": "DATA_INSUFFICIENT",
            "daily_pnl_pct": None,
            "baseline_equity_krw": None,
            "current_equity_krw": int(current_equity_krw),
            "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "당일 첫 equity snapshot 데이터가 없어 일중 손실률을 계산하지 않았습니다.",
        }
    if not compatible_today_snapshots:
        return {
            "evaluated": False,
            "status": "DATA_INSUFFICIENT",
            "daily_pnl_pct": None,
            "baseline_equity_krw": None,
            "current_equity_krw": int(current_equity_krw),
            "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "당일 equity snapshot이 legacy basis라 새 basis snapshot 누적 전까지 일중 손실률 계산을 보류합니다.",
        }

    baseline_snapshot = sorted(
        today_snapshots,
        key=lambda item: str(item.get("timestamp", "")),
    )[0]
    baseline_equity_krw = int(baseline_snapshot.get("total_equity_krw", 0) or 0)
    if baseline_equity_krw <= 0 or current_equity_krw <= 0:
        return {
            "evaluated": False,
            "status": "DATA_INSUFFICIENT",
            "daily_pnl_pct": None,
            "baseline_equity_krw": baseline_equity_krw,
            "current_equity_krw": int(current_equity_krw),
            "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률 계산에 필요한 equity 데이터가 부족합니다.",
        }

    daily_pnl_pct = ((current_equity_krw / baseline_equity_krw) - 1.0) * 100
    status = "NORMAL"
    reason = "일중 손실 브레이크 기준을 통과했습니다."
    if daily_pnl_pct <= hard_stop_pct:
        status = "HARD_STOP"
        reason = "일중 손실률이 hard stop 기준을 하회해 신규 BUY를 중단합니다."
    elif daily_pnl_pct <= buy_pause_pct:
        status = "BUY_PAUSE"
        reason = "일중 손실률이 buy pause 기준을 하회해 신규 BUY를 잠시 중단합니다."
    elif daily_pnl_pct <= warning_pct:
        status = "WARNING"
        reason = "일중 손실률이 warning 구간입니다."

    return {
        "evaluated": True,
        "status": status,
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "baseline_equity_krw": baseline_equity_krw,
        "current_equity_krw": int(current_equity_krw),
        "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        "cash_deployment_invariant": True,
        "reason": reason,
    }


def build_current_drawdown_state(*, current_equity_krw: int) -> dict[str, Any]:
    if current_equity_krw <= 0:
        return {
            "evaluated": False,
            "current_drawdown_pct": None,
            "max_drawdown_pct": None,
            "reason": "현재 equity가 유효하지 않아 drawdown을 계산하지 않았습니다.",
        }

    history = [
        snapshot
        for snapshot in _load_performance_snapshots()
        if _uses_deployment_invariant_equity(snapshot)
        and _is_snapshot_reliable_for_risk(snapshot)
    ]
    probe_snapshot = {
        "timestamp": get_korean_now().isoformat(),
        "total_equity_krw": int(current_equity_krw),
        "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        "cash_deployment_invariant": True,
    }
    drawdown_source = [*history, probe_snapshot]
    current_drawdown_pct, max_drawdown_pct = _calculate_drawdowns(drawdown_source)
    if not history:
        return {
            "evaluated": False,
            "current_drawdown_pct": round(current_drawdown_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "reason": "기존 performance snapshot 표본이 부족해 보수적으로 현재 drawdown만 계산했습니다.",
        }
    return {
        "evaluated": True,
        "current_drawdown_pct": round(current_drawdown_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "reason": "performance snapshot 기준 drawdown 상태입니다.",
    }
