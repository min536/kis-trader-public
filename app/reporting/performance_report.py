"""Performance report persistence + console rendering layer (R3-S7).

Relocated verbatim from app.reporting.performance. performance keeps legacy
bindings so existing import sites and patch targets remain valid.
build_performance_report itself stays in performance: it calls the
risk-critical account-state cluster (R6) and moving it would create a
circular import.
"""

from __future__ import annotations

import json
import math
from typing import Any

from app.auth.account_scope import (
    get_performance_snapshots_path,
    get_performance_summary_path,
)
from app.core.formatters import format_krw
from app.reporting.performance_metrics import (
    _format_optional_pct,
    _format_optional_ratio,
    _format_pct,
    _format_signed_krw,
    _format_signed_pct,
    _mean,
    _safe_ratio,
)


def _save_performance_snapshot(snapshot: dict[str, Any]) -> bool:
    try:
        performance_snapshots_file = get_performance_snapshots_path()
        performance_snapshots_file.parent.mkdir(parents=True, exist_ok=True)
        with performance_snapshots_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False))
            handle.write("\n")
        return True
    except OSError:
        return False


def _save_performance_report(report: dict[str, Any]) -> bool:
    try:
        performance_summary_file = get_performance_summary_path()
        performance_summary_file.parent.mkdir(parents=True, exist_ok=True)
        with performance_summary_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False))
            handle.write("\n")
        return True
    except OSError:
        return False


def _build_trade_quality_summary(records: list[dict[str, Any]]) -> dict[str, object]:
    gross_pnl = 0
    net_pnl = 0
    total_fee = 0
    total_tax = 0
    total_slippage = 0
    total_buy_notional = 0
    total_sell_notional = 0
    net_trade_results: list[int] = []

    for record in records:
        action = str(record.get("action", "")).strip()
        raw_response = record.get("raw_response")
        if not isinstance(raw_response, dict):
            continue

        if action == "order_succeeded":
            sizing = raw_response.get("position_sizing")
            if isinstance(sizing, dict):
                total_fee += int(sizing.get("estimated_buy_fee_krw", 0) or 0)
                total_slippage += int(
                    sizing.get("estimated_buy_slippage_krw", 0) or 0
                )
                total_buy_notional += int(
                    sizing.get("recommended_notional_krw", 0)
                    or raw_response.get("order_plan", {}).get("notional_krw", 0)
                    or 0
                )
        elif action == "sell_order_succeeded":
            sell_plan = raw_response.get("sell_plan")
            strategy_details = raw_response.get("sell_strategy_details")
            if isinstance(sell_plan, dict):
                total_fee += int(sell_plan.get("estimated_sell_fee_krw", 0) or 0)
                total_tax += int(sell_plan.get("estimated_sell_tax_krw", 0) or 0)
                total_slippage += int(
                    sell_plan.get("estimated_sell_slippage_krw", 0) or 0
                )
                total_sell_notional += int(sell_plan.get("notional_krw", 0) or 0)

            if isinstance(strategy_details, dict):
                details = strategy_details.get("details")
                if isinstance(details, dict):
                    gross_pnl += int(details.get("gross_pnl_krw", 0) or 0)
                    trade_net_pnl = int(details.get("net_pnl_krw", 0) or 0)
                    net_pnl += trade_net_pnl
                    net_trade_results.append(trade_net_pnl)

    wins = [value for value in net_trade_results if value > 0]
    losses = [value for value in net_trade_results if value < 0]
    win_rate = _safe_ratio(len(wins), len(net_trade_results)) * 100
    # No losses: pure-win streak → profit factor is mathematically undefined (∞).
    # Serialised to None by the caller (line below), consistent with "표본 부족" display.
    profit_factor = (
        _safe_ratio(sum(wins), abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    )
    average_win = int(round(_mean([float(value) for value in wins]))) if wins else 0
    average_loss = int(round(_mean([float(value) for value in losses]))) if losses else 0

    return {
        "gross_pnl_krw": gross_pnl,
        "net_pnl_krw": net_pnl,
        "cumulative_fee_krw": total_fee,
        "cumulative_tax_krw": total_tax,
        "cumulative_slippage_krw": total_slippage,
        "total_buy_notional_krw": total_buy_notional,
        "total_sell_notional_krw": total_sell_notional,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if not math.isinf(profit_factor) else None,
        "average_win_krw": average_win,
        "average_loss_krw": average_loss,
    }


def _build_integrity_warnings(
    *,
    positions_count: int,
    holdings_market_value: int,
    cash_orderable: int,
    operating_equity: int,
    total_cost_basis: int,
) -> list[str]:
    warnings: list[str] = []
    if positions_count > 0 and holdings_market_value == 0:
        warnings.append(
            "보유 종목 수는 존재하지만 보유 평가금액이 0원입니다."
        )
    if holdings_market_value > 0 and operating_equity <= cash_orderable:
        warnings.append(
            "보유 평가금액이 있는데 운영 equity가 주문가능현금 중심으로만 계산된 흔적이 있습니다."
        )
    if positions_count > 0 and total_cost_basis == 0:
        warnings.append("보유 종목이 있는데 총 매입원금이 0원입니다.")
    return warnings


def persist_performance_report(report: dict[str, Any]) -> dict[str, bool]:
    snapshot_saved = _save_performance_snapshot(report["equity"])
    report_saved = _save_performance_report(report)
    return {
        "snapshot_saved": snapshot_saved,
        "report_saved": report_saved,
    }


def build_performance_console_lines(report: dict[str, Any]) -> list[str]:
    account = report["account_summary"]
    positions = report["positions"]
    absolute = report["absolute_performance"]
    benchmark = report["benchmark_performance"]
    risk = report["risk_adjusted_performance"]
    attribution = report.get("attribution") or {}
    drawdown = report["drawdown"]
    cost = report["cost_and_execution"]
    concentration = report["portfolio_concentration"]

    lines = ["=== 성과 요약 ==="]
    lines.append(
        "계정 스코프 | "
        f"{report.get('account_signature', '-')} | "
        f"{report.get('masked_account_display', '-')}"
    )
    lines.append(
        "계좌 자산 | "
        f"운영 equity {format_krw(account['operating_equity_krw'])} | "
        f"브레이크 equity {format_krw(account.get('current_equity_krw', account['operating_equity_krw']))} | "
        f"브레이크 cash leg {format_krw(account.get('deployment_invariant_cash_leg_krw', account['cash_total_krw']))} | "
        f"총예수금 {format_krw(account['cash_total_krw'])} | "
        f"주문가능현금 {format_krw(account['orderable_cash_krw'])} | "
        f"익일정산예수금 {format_krw(account['cash_next_day_krw'])} | "
        f"보유 평가금액 {format_krw(account['holdings_market_value_krw'])} | "
        f"총 매입원금 {format_krw(account['total_cost_basis_krw'])}"
    )
    lines.append(
        "손익 | "
        f"실현 gross {_format_signed_krw(account['realized_gross_pnl_krw'])} | "
        f"실현 net {_format_signed_krw(account['realized_net_pnl_krw'])} | "
        f"미실현 gross {_format_signed_krw(account['total_unrealized_gross_pnl_krw'])} | "
        f"미실현 net {_format_signed_krw(account['total_unrealized_net_pnl_krw'])}"
    )
    lines.append(
        "계좌 상태 | "
        f"누적수익률 {_format_signed_pct(account['total_return_pct'])} | "
        f"현금 비중 {_format_pct(account['cash_weight_pct'])} | "
        f"보유 종목 수 {account['positions_count']}"
    )
    lines.append(
        "계산 기준 | "
        "운영 equity = 주문가능현금 + 보유 평가금액 | "
        f"실현손익 기준: {account['realized_pnl_basis']}"
    )

    lines.append("=== 종목별 손익 ===")
    if positions:
        for item in positions:
            name = item.get("name")
            display_name = f"{item['symbol']} {name}" if name else item["symbol"]
            lines.append(
                f"{display_name} | {int(item['holding_qty']):,}주 | "
                f"평균 {format_krw(item['average_cost_krw'])} | "
                f"현재 {format_krw(item['current_price_krw'])} | "
                f"평가 {format_krw(item['evaluation_amount_krw'])} | "
                f"손익 {_format_signed_krw(item['net_pnl_krw'])} | "
                f"수익률 {_format_signed_pct(item['net_pnl_pct'])} | "
                f"비중 {_format_pct(item['weight_pct'])}"
            )
    else:
        lines.append("보유 종목 없음")

    lines.append("=== 비용 요약 ===")
    lines.append(
        "비용/거래 품질 | "
        f"Gross {_format_signed_krw(cost['gross_pnl_krw'])} | "
        f"Net {_format_signed_krw(cost['net_pnl_krw'])} | "
        f"수수료 {format_krw(cost['cumulative_fee_krw'])} | "
        f"세금 {format_krw(cost['cumulative_tax_krw'])} | "
        f"슬리피지 {format_krw(cost['cumulative_slippage_krw'])}"
    )
    lines.append(
        "거래 품질 | "
        f"Turnover {_format_pct(cost['turnover_pct'])} | "
        f"Win rate {_format_pct(cost['win_rate_pct'])} | "
        f"Profit factor {cost['profit_factor'] if cost['profit_factor'] is not None else '표본 부족'} | "
        f"Avg win {_format_signed_krw(cost['average_win_krw'])} | "
        f"Avg loss {_format_signed_krw(cost['average_loss_krw'])}"
    )

    lines.append("=== 기간 수익률 ===")
    lines.append(
        "절대 성과 | "
        f"누적 {_format_signed_pct(absolute['cumulative_return_pct'])} | "
        f"일별 {_format_optional_pct(absolute['daily_return_pct'])} | "
        f"주별 {_format_optional_pct(absolute['weekly_return_pct'])} | "
        f"월별 {_format_optional_pct(absolute['monthly_return_pct'])}"
    )
    if benchmark.get("benchmark_symbol"):
        lines.append(
            "벤치마크 대비 | "
            f"{benchmark['benchmark_symbol']} {benchmark.get('benchmark_name') or ''}".strip()
            + " | "
            f"일별 초과 {_format_optional_pct(benchmark['excess_daily_return_pct'])} | "
            f"주별 초과 {_format_optional_pct(benchmark['excess_weekly_return_pct'])} | "
            f"월별 초과 {_format_optional_pct(benchmark['excess_monthly_return_pct'])} | "
            f"초과수익률 {_format_optional_pct(benchmark['excess_return_pct'])} | "
            f"IR {_format_optional_ratio(benchmark['information_ratio'])}"
        )
    else:
        lines.append("벤치마크 대비 | benchmark 미설정")

    lines.append(
        "기초 attribution | "
        f"selection {_format_optional_pct(attribution.get('selection_contribution_pct'))} | "
        f"timing {_format_optional_pct(attribution.get('timing_contribution_pct'))} | "
        f"cost drag {_format_optional_pct(attribution.get('cost_drag_pct'))} | "
        f"gross alpha {_format_optional_pct(attribution.get('gross_alpha_before_cost_pct'))}"
    )

    lines.append("=== 위험 지표 ===")
    lines.append(
        "위험 조정 | "
        f"Sharpe {_format_optional_ratio(risk['sharpe_ratio'])} | "
        f"Sortino {_format_optional_ratio(risk['sortino_ratio'])}"
    )

    lines.append(
        "낙폭 | "
        f"Current DD {_format_signed_pct(drawdown['current_drawdown_pct'])} | "
        f"Max DD {_format_signed_pct(drawdown['max_drawdown_pct'])} | "
        f"Calmar {_format_optional_ratio(drawdown['calmar_ratio'])}"
    )

    lines.append("=== 포지션/집중도 ===")
    lines.append(
        f"현금 비중 {_format_pct(concentration['cash_weight_pct'])} | "
        f"상위3 집중도 {_format_pct(concentration['top3_concentration_pct'])} | "
        f"총 보유 종목 수 {concentration['positions_count']}"
    )
    return lines
