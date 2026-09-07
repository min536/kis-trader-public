import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.auth.account_scope import get_account_scope_context
from app.core.costs import calc_net_pnl
from app.core.order_log import get_order_log_path
from app.core.time_utils import KOREA_TZ, get_korean_now
from app.reporting.performance_report import (
    _build_integrity_warnings,
    _build_trade_quality_summary,
    _save_performance_report,
    _save_performance_snapshot,
    build_performance_console_lines,
    persist_performance_report,
)
from app.reporting.performance_metrics import (
    _choose_period_start,
    _downside_std,
    _format_optional_pct,
    _format_optional_ratio,
    _format_pct,
    _format_signed_krw,
    _format_signed_pct,
    _is_operating_record,
    _iter_operating_order_records,
    _mean,
    _safe_ratio,
    _same_day,
    _same_month,
    _same_week,
    _std,
    _timestamp_to_datetime,
)
from app.scanner.symbol_names import get_symbol_name
# Risk-cluster symbols that build_performance_report (below) still consumes,
# imported from the relocated leaf (R6). The rest of the cluster moved with it
# and is imported directly from app.portfolio.equity_state by its consumers.
from app.portfolio.equity_state import (
    DEPLOYMENT_INVARIANT_EQUITY_BASIS,
    _build_account_state,
    _calculate_drawdowns,
    _load_performance_snapshots,
    _select_equity_basis_compatible_history,
    select_risk_managed_current_equity_krw,
)



@dataclass(frozen=True)
class BenchmarkSnapshot:
    symbol: str
    name: str | None
    current_price: int


# Pure math/format/order-record/period helpers: canonical implementations
# live in app.reporting.performance_metrics and are bound by the module-top
# import (R3-S6).


def _ensure_prior_snapshot(
    current_timestamp: str,
    start_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not start_snapshot:
        return None
    if str(start_snapshot.get("timestamp", "")) == current_timestamp:
        return None
    return start_snapshot


def _extract_snapshot_return(
    current_equity: int,
    start_snapshot: dict[str, Any] | None,
) -> float | None:
    if not start_snapshot:
        return None
    start_equity = int(start_snapshot.get("total_equity_krw", 0) or 0)
    if start_equity <= 0:
        return None
    return ((current_equity / start_equity) - 1.0) * 100


def _extract_benchmark_return(
    current_price: int,
    start_snapshot: dict[str, Any] | None,
) -> float | None:
    if not start_snapshot:
        return None
    start_price = int(start_snapshot.get("benchmark_price_krw", 0) or 0)
    if start_price <= 0 or current_price <= 0:
        return None
    return ((current_price / start_price) - 1.0) * 100


def _build_return_series(snapshots: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    portfolio_returns: list[float] = []
    benchmark_returns: list[float] = []
    ordered = sorted(
        snapshots,
        key=lambda item: str(item.get("timestamp", "")),
    )
    for previous, current in zip(ordered, ordered[1:]):
        prev_equity = int(previous.get("total_equity_krw", 0) or 0)
        current_equity = int(current.get("total_equity_krw", 0) or 0)
        if prev_equity > 0 and current_equity > 0:
            portfolio_returns.append((current_equity / prev_equity) - 1.0)

        prev_benchmark = int(previous.get("benchmark_price_krw", 0) or 0)
        current_benchmark = int(current.get("benchmark_price_krw", 0) or 0)
        if prev_benchmark > 0 and current_benchmark > 0:
            benchmark_returns.append((current_benchmark / prev_benchmark) - 1.0)
    return portfolio_returns, benchmark_returns


def _estimate_selection_contribution_pct(
    positions: list[dict[str, Any]],
    *,
    benchmark_return_pct: float | None,
    operating_equity_krw: int,
) -> float | None:
    if benchmark_return_pct is None or operating_equity_krw <= 0 or not positions:
        return None

    contribution_pct = 0.0
    has_valid_position = False
    for position in positions:
        evaluation_amount = int(position.get("evaluation_amount_krw", 0) or 0)
        gross_pnl_pct = position.get("gross_pnl_pct")
        if evaluation_amount <= 0 or gross_pnl_pct is None:
            continue
        weight = evaluation_amount / operating_equity_krw
        contribution_pct += weight * (float(gross_pnl_pct) - float(benchmark_return_pct))
        has_valid_position = True

    if not has_valid_position:
        return None
    return contribution_pct


def _estimate_cost_drag_pct(
    *,
    cumulative_fee_krw: int,
    cumulative_tax_krw: int,
    cumulative_slippage_krw: int,
    base_equity_krw: int,
) -> float | None:
    if base_equity_krw <= 0:
        return None
    total_cost_krw = cumulative_fee_krw + cumulative_tax_krw + cumulative_slippage_krw
    return (total_cost_krw / base_equity_krw) * 100




# Report persistence + console rendering: canonical implementations live in
# app.reporting.performance_report and are bound by the module-top import
# (R3-S7). build_performance_report stays below: it calls the risk-critical
# account-state cluster (R6) and moving it would create a circular import.






def build_performance_report(
    *,
    portfolio_snapshot,
    sell_analysis_results: tuple[Any, ...],
    benchmark_snapshot: BenchmarkSnapshot | None,
) -> dict[str, Any]:
    now = get_korean_now()
    account_scope = get_account_scope_context()
    operating_records = _iter_operating_order_records()
    account_state = _build_account_state(
        portfolio_snapshot=portfolio_snapshot,
        sell_analysis_results=sell_analysis_results,
    )
    holdings_market_value = int(account_state["holdings_market_value_krw"])
    total_cost_basis = int(account_state["total_cost_basis_krw"])
    unrealized_gross = int(account_state["total_unrealized_gross_pnl_krw"])
    unrealized_net = int(account_state["total_unrealized_net_pnl_krw"])
    operating_equity = int(account_state["operating_equity_krw"])
    deployment_invariant_equity = int(account_state["deployment_invariant_equity_krw"])
    risk_managed_equity = select_risk_managed_current_equity_krw(
        account_state=account_state,
        raw_balance_total_evaluation_amount_krw=int(
            portfolio_snapshot.total_evaluation_amount
        ),
    )
    positions_count = len(portfolio_snapshot.held_positions)
    analysis_by_symbol = {
        result.symbol: result for result in sell_analysis_results
    }
    position_summaries = []
    for position in portfolio_snapshot.held_positions:
        analysis = analysis_by_symbol.get(position.symbol)
        evaluation_amount = int(position.market_value)
        current_price = int(position.current_price)
        gross_pnl_krw = int(position.gross_pnl)
        gross_pnl_pct = float(position.gross_pnl_pct)
        net_pnl_krw = gross_pnl_krw
        net_pnl_pct = gross_pnl_pct
        holding_qty = int(position.holding_qty)
        average_cost = int(position.average_cost)
        name = position.name or get_symbol_name(position.symbol)
        if analysis is not None:
            evaluation_amount = analysis.market_snapshot.current_price * analysis.holding_qty
            current_price = int(analysis.market_snapshot.current_price)
            gross_pnl_krw = int(analysis.sell_decision.details.get("gross_pnl_krw", 0))
            gross_pnl_pct = float(analysis.sell_decision.details.get("gross_pnl_pct", 0.0))
            net_pnl_krw = int(analysis.sell_decision.details.get("net_pnl_krw", 0))
            net_pnl_pct = float(analysis.sell_decision.details.get("net_pnl_pct", 0.0))
            holding_qty = int(analysis.holding_qty)
            average_cost = int(analysis.average_cost)
            name = analysis.name or name
        weight_pct = round(
            _safe_ratio(
                evaluation_amount,
                max(operating_equity, 1),
            )
            * 100,
            2,
        )
        position_summaries.append(
            {
                "symbol": position.symbol,
                "name": name,
                "symbol_name": name,
                "holding_qty": holding_qty,
                "quantity": holding_qty,
                "average_cost_krw": average_cost,
                "average_price": average_cost,
                "current_price_krw": current_price,
                "current_price": current_price,
                "evaluation_amount_krw": evaluation_amount,
                "market_value": evaluation_amount,
                "gross_pnl_krw": gross_pnl_krw,
                "gross_pnl": gross_pnl_krw,
                "gross_pnl_pct": gross_pnl_pct,
                "net_pnl_krw": net_pnl_krw,
                "net_pnl": net_pnl_krw,
                "net_pnl_pct": net_pnl_pct,
                "weight_pct": weight_pct,
            }
        )
    top_holdings = sorted(
        position_summaries,
        key=lambda item: float(item["weight_pct"]),
        reverse=True,
    )

    integrity_warnings = _build_integrity_warnings(
        positions_count=positions_count,
        holdings_market_value=holdings_market_value,
        cash_orderable=int(account_state["cash_orderable_krw"]),
        operating_equity=operating_equity,
        total_cost_basis=int(total_cost_basis),
    )

    snapshot = {
        "timestamp": now.isoformat(),
        "trading_date": now.date().isoformat(),
        "account_signature": account_scope["account_signature"],
        "account_environment": account_scope["account_environment"],
        "masked_account_display": account_scope["masked_account_display"],
        "total_equity_krw": risk_managed_equity,
        "operating_equity_krw": operating_equity,
        "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        "cash_deployment_invariant": True,
        "cash_total_krw": int(account_state["cash_total_krw"]),
        "cash_orderable_krw": int(account_state["cash_orderable_krw"]),
        "cash_next_day_krw": int(account_state["cash_next_day_krw"]),
        "holdings_market_value_krw": int(holdings_market_value),
        "total_cost_basis_krw": int(total_cost_basis),
        "total_unrealized_pnl_krw": int(unrealized_net),
        "unrealized_gross_pnl_krw": int(unrealized_gross),
        "unrealized_net_pnl_krw": int(unrealized_net),
        "raw_balance_total_evaluation_amount_krw": int(portfolio_snapshot.total_evaluation_amount),
        "benchmark_symbol": benchmark_snapshot.symbol if benchmark_snapshot else None,
        "benchmark_name": benchmark_snapshot.name if benchmark_snapshot else None,
        "benchmark_price_krw": benchmark_snapshot.current_price if benchmark_snapshot else 0,
        "positions_count": positions_count,
        "cash_weight_pct": round(
            _safe_ratio(
                int(account_state["cash_orderable_krw"]),
                max(operating_equity, 1),
            )
            * 100,
            2,
        ),
    }

    history = _load_performance_snapshots()
    history.append(snapshot)
    history = _select_equity_basis_compatible_history(history)
    history = sorted(history, key=lambda item: str(item.get("timestamp", "")))
    portfolio_returns, benchmark_returns = _build_return_series(history)
    target_dt = now.astimezone(KOREA_TZ)

    current_timestamp = str(snapshot["timestamp"])
    current_return_pct = _extract_snapshot_return(
        snapshot["total_equity_krw"],
        history[0] if history else None,
    ) or 0.0
    daily_return_pct = _extract_snapshot_return(
        snapshot["total_equity_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_day),
        ),
    )
    weekly_return_pct = _extract_snapshot_return(
        snapshot["total_equity_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_week),
        ),
    )
    monthly_return_pct = _extract_snapshot_return(
        snapshot["total_equity_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_month),
        ),
    )

    benchmark_current_return_pct = _extract_benchmark_return(
        snapshot["benchmark_price_krw"],
        history[0] if history else None,
    )
    benchmark_daily_return_pct = _extract_benchmark_return(
        snapshot["benchmark_price_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_day),
        ),
    )
    benchmark_weekly_return_pct = _extract_benchmark_return(
        snapshot["benchmark_price_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_week),
        ),
    )
    benchmark_monthly_return_pct = _extract_benchmark_return(
        snapshot["benchmark_price_krw"],
        _ensure_prior_snapshot(
            current_timestamp,
            _choose_period_start(history, target_dt=target_dt, predicate=_same_month),
        ),
    )
    excess_return_pct = None
    if benchmark_current_return_pct is not None:
        excess_return_pct = current_return_pct - benchmark_current_return_pct
    excess_daily_return_pct = None
    if daily_return_pct is not None and benchmark_daily_return_pct is not None:
        excess_daily_return_pct = daily_return_pct - benchmark_daily_return_pct
    excess_weekly_return_pct = None
    if weekly_return_pct is not None and benchmark_weekly_return_pct is not None:
        excess_weekly_return_pct = weekly_return_pct - benchmark_weekly_return_pct
    excess_monthly_return_pct = None
    if monthly_return_pct is not None and benchmark_monthly_return_pct is not None:
        excess_monthly_return_pct = monthly_return_pct - benchmark_monthly_return_pct

    aligned_excess_returns = [
        portfolio - benchmark
        for portfolio, benchmark in zip(portfolio_returns, benchmark_returns)
    ]
    sharpe_ratio = None
    sortino_ratio = None
    information_ratio = None
    if len(portfolio_returns) >= 2:
        sharpe_ratio = _safe_ratio(_mean(portfolio_returns), _std(portfolio_returns))
    if len([value for value in portfolio_returns if value < 0]) >= 2:
        sortino_ratio = _safe_ratio(
            _mean(portfolio_returns),
            _downside_std(portfolio_returns),
        )
    if len(aligned_excess_returns) >= 2:
        information_ratio = _safe_ratio(
            _mean(aligned_excess_returns),
            _std(aligned_excess_returns),
        )

    current_drawdown_pct, max_drawdown_pct = _calculate_drawdowns(history)
    calmar_ratio = None
    if max_drawdown_pct < 0:
        calmar_ratio = _safe_ratio(
            current_return_pct / 100,
            abs(max_drawdown_pct / 100),
        )

    trade_quality = _build_trade_quality_summary(operating_records)
    base_equity = int(history[0].get("total_equity_krw", snapshot["total_equity_krw"]) or 0)
    turnover = 0.0
    if base_equity > 0:
        turnover = (
            (trade_quality["total_buy_notional_krw"] + trade_quality["total_sell_notional_krw"])
            / base_equity
        ) * 100

    selection_contribution_pct = _estimate_selection_contribution_pct(
        position_summaries,
        benchmark_return_pct=benchmark_current_return_pct,
        operating_equity_krw=operating_equity,
    )
    cost_drag_pct = _estimate_cost_drag_pct(
        cumulative_fee_krw=int(trade_quality["cumulative_fee_krw"]),
        cumulative_tax_krw=int(trade_quality["cumulative_tax_krw"]),
        cumulative_slippage_krw=int(trade_quality["cumulative_slippage_krw"]),
        base_equity_krw=base_equity,
    )
    timing_contribution_pct = None
    if excess_return_pct is not None and selection_contribution_pct is not None and cost_drag_pct is not None:
        timing_contribution_pct = excess_return_pct - selection_contribution_pct + cost_drag_pct
    gross_alpha_before_cost_pct = None
    if excess_return_pct is not None and cost_drag_pct is not None:
        gross_alpha_before_cost_pct = excess_return_pct + cost_drag_pct

    total_return_pct = round(current_return_pct, 2)

    report = {
        "generated_at": now.isoformat(),
        "account_signature": account_scope["account_signature"],
        "account_environment": account_scope["account_environment"],
        "masked_account_display": account_scope["masked_account_display"],
        "integrity_warnings": integrity_warnings,
        "equity": snapshot,
        "account_summary": {
            "operating_equity_krw": operating_equity,
            "current_equity_krw": risk_managed_equity,
            "deployment_invariant_cash_leg_krw": int(
                account_state["deployment_invariant_cash_leg_krw"]
            ),
            "equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "cash_total_krw": int(account_state["cash_total_krw"]),
            "orderable_cash_krw": int(account_state["cash_orderable_krw"]),
            "cash_next_day_krw": int(account_state["cash_next_day_krw"]),
            "holdings_market_value_krw": holdings_market_value,
            "raw_balance_total_evaluation_amount_krw": int(portfolio_snapshot.total_evaluation_amount),
            "total_cost_basis_krw": int(total_cost_basis),
            "realized_gross_pnl_krw": int(trade_quality["gross_pnl_krw"]),
            "realized_net_pnl_krw": int(trade_quality["net_pnl_krw"]),
            "realized_pnl_basis": "sell_order_succeeded 로그 기준",
            "total_unrealized_gross_pnl_krw": int(unrealized_gross),
            "total_unrealized_net_pnl_krw": int(unrealized_net),
            "total_return_pct": total_return_pct,
            "cash_weight_pct": snapshot["cash_weight_pct"],
            "positions_count": positions_count,
        },
        "positions": position_summaries,
        "absolute_performance": {
            "cumulative_return_pct": round(current_return_pct, 2),
            "daily_return_pct": None if daily_return_pct is None else round(daily_return_pct, 2),
            "weekly_return_pct": None if weekly_return_pct is None else round(weekly_return_pct, 2),
            "monthly_return_pct": None if monthly_return_pct is None else round(monthly_return_pct, 2),
        },
        "benchmark_performance": {
            "benchmark_symbol": snapshot["benchmark_symbol"],
            "benchmark_name": snapshot["benchmark_name"],
            "benchmark_cumulative_return_pct": None if benchmark_current_return_pct is None else round(benchmark_current_return_pct, 2),
            "benchmark_daily_return_pct": None if benchmark_daily_return_pct is None else round(benchmark_daily_return_pct, 2),
            "benchmark_weekly_return_pct": None if benchmark_weekly_return_pct is None else round(benchmark_weekly_return_pct, 2),
            "benchmark_monthly_return_pct": None if benchmark_monthly_return_pct is None else round(benchmark_monthly_return_pct, 2),
            "excess_return_pct": None if excess_return_pct is None else round(excess_return_pct, 2),
            "excess_daily_return_pct": None if excess_daily_return_pct is None else round(excess_daily_return_pct, 2),
            "excess_weekly_return_pct": None if excess_weekly_return_pct is None else round(excess_weekly_return_pct, 2),
            "excess_monthly_return_pct": None if excess_monthly_return_pct is None else round(excess_monthly_return_pct, 2),
            "information_ratio": None if information_ratio is None else round(information_ratio, 4),
        },
        "risk_adjusted_performance": {
            "sharpe_ratio": None if sharpe_ratio is None else round(sharpe_ratio, 4),
            "sortino_ratio": None if sortino_ratio is None else round(sortino_ratio, 4),
        },
        "attribution": {
            "method": "운영용 기초 근사치",
            "selection_contribution_pct": None if selection_contribution_pct is None else round(selection_contribution_pct, 2),
            "timing_contribution_pct": None if timing_contribution_pct is None else round(timing_contribution_pct, 2),
            "cost_drag_pct": None if cost_drag_pct is None else round(cost_drag_pct, 2),
            "cost_drag_krw": int(
                int(trade_quality["cumulative_fee_krw"])
                + int(trade_quality["cumulative_tax_krw"])
                + int(trade_quality["cumulative_slippage_krw"])
            ),
            "gross_alpha_before_cost_pct": None if gross_alpha_before_cost_pct is None else round(gross_alpha_before_cost_pct, 2),
            "net_excess_return_pct": None if excess_return_pct is None else round(excess_return_pct, 2),
            "timing_interpretation": (
                "selection + cost로 설명되지 않는 잔여 초과수익을 단순 timing 근사치로 봅니다."
            ),
        },
        "drawdown": {
            "current_drawdown_pct": round(current_drawdown_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "calmar_ratio": None if calmar_ratio is None else round(calmar_ratio, 4),
        },
        "cost_and_execution": {
            **trade_quality,
            "turnover_pct": round(turnover, 2),
        },
        "portfolio_concentration": {
            "cash_weight_pct": snapshot["cash_weight_pct"],
            "positions_count": positions_count,
            "top3_concentration_pct": round(
                sum(float(item["weight_pct"]) for item in top_holdings[:3]),
                2,
            ),
            "holdings": top_holdings,
        },
    }
    return report




def is_same_korean_date(timestamp: str, target_date) -> bool:
    try:
        record_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if record_time.tzinfo is None:
        return record_time.date() == target_date
    return record_time.astimezone(KOREA_TZ).date() == target_date


def build_today_realized_summary(settings) -> dict[str, object]:
    target_date = get_korean_now().date()
    summary = {
        "realized_gross_pnl_krw": 0,
        "realized_net_pnl_krw": 0,
    }
    order_log_file = get_order_log_path(settings)
    if not order_log_file.exists():
        return summary

    for raw_line in order_log_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if str(record.get("environment", "mock")).strip() != "mock":
            continue
        if str(record.get("action", "")).strip() != "sell_order_succeeded":
            continue
        timestamp = str(record.get("timestamp", "")).strip()
        if not timestamp or not is_same_korean_date(timestamp, target_date):
            continue

        raw_response = record.get("raw_response")
        if not isinstance(raw_response, dict):
            continue
        sell_plan = raw_response.get("sell_plan")
        strategy_details = raw_response.get("sell_strategy_details")
        if not isinstance(sell_plan, dict) or not isinstance(strategy_details, dict):
            continue
        details = strategy_details.get("details")
        if not isinstance(details, dict):
            continue

        qty = int(sell_plan.get("qty", 0) or 0)
        current_price = int(sell_plan.get("current_price_krw", 0) or 0)
        average_cost = int(details.get("average_cost", 0) or 0)
        if qty <= 0 or current_price <= 0 or average_cost <= 0:
            continue

        pnl_metrics = calc_net_pnl(
            avg_cost_krw=average_cost,
            current_price_krw=current_price,
            qty=qty,
            settings=settings,
        )
        summary["realized_gross_pnl_krw"] += int(pnl_metrics["gross_pnl_krw"])
        summary["realized_net_pnl_krw"] += int(pnl_metrics["net_pnl_krw"])

    return summary
