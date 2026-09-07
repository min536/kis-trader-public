"""Render + load W3 portfolio analytics for the EOD/Slack surface (W7)."""
from __future__ import annotations

import json

from app.core.file_read_limits import LocalReadLimitError, iter_lines_bounded
from app.core.order_log import get_order_log_path
from app.portfolio.analytics import compute_portfolio_analytics
from app.portfolio.trade_records import build_closed_trade_records


def load_portfolio_analytics(settings):
    """Read the order log (bounded) → closed trades → portfolio analytics."""
    path = get_order_log_path(settings)
    records: list[dict] = []
    try:
        if path.exists():
            for _lineno, raw_line in iter_lines_bounded(path):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except (LocalReadLimitError, OSError, RuntimeError):
        # Oversize/over-long-line/unreadable log → degrade to empty, never crash
        # the EOD/Slack reporting surface.
        records = []
    trades = build_closed_trade_records(records, settings=settings)
    return compute_portfolio_analytics(trades)


def render_portfolio_analytics_lines(analytics, *, top_n=5):
    """Compact operator lines for a PortfolioAnalytics (empty when no trades).

    Holding-time metrics are intentionally omitted — the W7 reader does not yet
    derive hold_days (see app/portfolio/trade_records.py).
    """
    total = analytics.total
    if total.trade_count == 0:
        return []
    lines = [
        f"📊 Portfolio analytics (realized to date, {total.trade_count} trades)",
        (
            f"  total: net={total.net_pnl_krw:+,} gross={total.gross_pnl_krw:+,} "
            f"win_rate={total.win_rate_pct:.0f}% turnover={total.turnover_krw:,}"
        ),
    ]
    top_symbols = sorted(
        analytics.by_symbol.items(), key=lambda kv: kv[1].net_pnl_krw, reverse=True
    )[:top_n]
    for symbol, metrics in top_symbols:
        lines.append(
            f"  {symbol}: net={metrics.net_pnl_krw:+,} "
            f"(n={metrics.trade_count}, win={metrics.win_rate_pct:.0f}%)"
        )
    for trigger, metrics in sorted(
        analytics.by_trigger.items(), key=lambda kv: kv[1].net_pnl_krw, reverse=True
    ):
        lines.append(f"  trigger {trigger}: net={metrics.net_pnl_krw:+,} (n={metrics.trade_count})")
    return lines
