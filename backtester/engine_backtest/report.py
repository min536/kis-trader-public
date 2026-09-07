"""Backtest report writer: JSON + text summary."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from backtester.analytics.robust_risk import build_risk_diagnostics
from backtester.engine_backtest.metrics import compute_metrics
from backtester.engine_backtest.runner import BacktestResult


def write_report(
    result: BacktestResult,
    output_dir: str | Path,
    run_label: str = "",
) -> Path:
    """Write JSON report + human-readable summary to output_dir.

    Returns the path to the JSON report file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{ts}_{run_label}" if run_label else ts

    metrics = compute_metrics(result)

    report = {
        "generated_at": datetime.now().isoformat(),
        "run_label": run_label,
        "metrics": metrics,
        "risk_diagnostics": build_risk_diagnostics(result.equity_curve),
        "equity_curve": [
            {"date": d.isoformat(), "value": v}
            for d, v in result.equity_curve
        ],
        "trade_log": [t.to_dict() for t in result.trade_log],
        "daily_records": [r.to_dict() for r in result.daily_records],
    }

    json_path = output_dir / f"{prefix}_backtest.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    txt_path = output_dir / f"{prefix}_summary.txt"
    txt_path.write_text(_build_summary(metrics, run_label), encoding="utf-8")

    return json_path


def print_summary(result: BacktestResult, run_label: str = "") -> None:
    """Print a human-readable summary to stdout."""
    metrics = compute_metrics(result)
    print(_build_summary(metrics, run_label))


def _build_summary(metrics: dict, run_label: str) -> str:
    current_drawdown = metrics.get("current_drawdown_pct")
    rolling_volatility = metrics.get("rolling_volatility_20d_pct")
    current_drawdown_text = (
        "n/a" if current_drawdown is None else f"{current_drawdown:+.2f} %"
    )
    rolling_volatility_text = (
        "n/a" if rolling_volatility is None else f"{rolling_volatility:.2f} %"
    )
    lines = [
        "=" * 58,
        f"  BACKTEST RESULT  {run_label}",
        "=" * 58,
        f"  Initial cash     : {metrics['initial_cash']:>15,} KRW",
        f"  Final value      : {metrics['final_value']:>15,} KRW",
        f"  Total return     : {metrics['total_return_pct']:>+14.2f} %",
        f"  CAGR             : {metrics['cagr_pct']:>+14.2f} %",
        f"  Sharpe ratio     : {metrics['sharpe_ratio']:>14.3f}",
        f"  Max drawdown     : {metrics['max_drawdown_pct']:>14.2f} %",
        f"  Current drawdown : {current_drawdown_text:>16}",
        f"  Rolling vol (20d): {rolling_volatility_text:>16}",
        f"  Winsor clipped   : {metrics.get('winsorized_return_clipped_count', 0):>14}",
        "  " + "-" * 54,
        f"  Trades           : {metrics['n_trades']:>14}",
        f"  Win rate         : {metrics['win_rate_pct']:>14.1f} %",
        f"  Profit factor    : {'∞ (pure-win)':>14}" if metrics['profit_factor'] is None else f"  Profit factor    : {metrics['profit_factor']:>14.3f}",
        f"  Avg P&L          : {metrics['avg_pnl_pct']:>+14.2f} %",
        f"  Avg hold days    : {metrics['avg_hold_days']:>14.1f}",
        f"  Net P&L (KRW)    : {metrics['total_net_pnl_krw']:>+15,}",
        "  " + "-" * 54,
        "  Sell triggers:",
    ]
    for trigger, cnt in sorted(metrics["sell_trigger_breakdown"].items()):
        lines.append(f"    {trigger:<30} {cnt:>5}")
    lines.append("=" * 58)
    return "\n".join(lines)
