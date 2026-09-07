from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.core.time_utils import KOREA_TZ, get_korean_now
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.scanner.symbol_names import get_symbol_name


DIAGNOSTIC_FALLBACK_SYMBOLS = ("005930", "000660")
DIAGNOSTIC_CASH_TOTAL_KRW = 98_210_000
DIAGNOSTIC_CASH_ORDERABLE_KRW = 95_750_000

_DIAGNOSTIC_POSITION_TEMPLATES = (
    {
        "holding_qty": 12,
        "average_cost": 70_000,
        "current_price": 71_500,
        "prev_day_change_pct": 1.2,
        "open_multiplier": 1.01,
        "low_multiplier": 0.99,
    },
    {
        "holding_qty": 8,
        "average_cost": 118_500,
        "current_price": 116_500,
        "prev_day_change_pct": -0.6,
        "open_multiplier": 0.995,
        "low_multiplier": 0.98,
    },
)


def _diagnostic_symbols(settings: Any, symbols: tuple[str, ...]) -> tuple[str, str]:
    configured = tuple(symbols or getattr(settings, "target_symbols", ()) or ())
    unique_symbols: list[str] = []
    for raw_symbol in configured + DIAGNOSTIC_FALLBACK_SYMBOLS:
        symbol = str(raw_symbol).strip()
        if symbol and symbol not in unique_symbols:
            unique_symbols.append(symbol)
        if len(unique_symbols) >= 2:
            break
    return unique_symbols[0], unique_symbols[1]


def _position_from_template(symbol: str, template: dict[str, float | int]) -> PortfolioPosition:
    holding_qty = int(template["holding_qty"])
    average_cost = int(template["average_cost"])
    current_price = int(template["current_price"])
    market_value = holding_qty * current_price
    gross_pnl = (current_price - average_cost) * holding_qty
    gross_pnl_pct = (
        ((current_price - average_cost) / average_cost) * 100.0
        if average_cost > 0
        else 0.0
    )
    return PortfolioPosition(
        symbol=symbol,
        name=get_symbol_name(symbol),
        holding_qty=holding_qty,
        average_cost=average_cost,
        current_price=current_price,
        market_value=market_value,
        gross_pnl=gross_pnl,
        gross_pnl_pct=round(gross_pnl_pct, 2),
        has_position=True,
    )


def build_scan_only_diagnostic_portfolio_snapshot(
    *,
    settings: Any,
    symbols: tuple[str, ...],
) -> PortfolioSnapshot:
    primary_symbol, secondary_symbol = _diagnostic_symbols(settings, symbols)
    positions = (
        _position_from_template(primary_symbol, _DIAGNOSTIC_POSITION_TEMPLATES[0]),
        _position_from_template(secondary_symbol, _DIAGNOSTIC_POSITION_TEMPLATES[1]),
    )
    holdings_market_value = sum(int(position.market_value) for position in positions)
    return PortfolioSnapshot(
        positions=positions,
        cash_total=DIAGNOSTIC_CASH_TOTAL_KRW,
        cash_orderable=DIAGNOSTIC_CASH_ORDERABLE_KRW,
        cash_next_day=DIAGNOSTIC_CASH_TOTAL_KRW,
        total_evaluation_amount=DIAGNOSTIC_CASH_TOTAL_KRW + holdings_market_value,
    )


def build_scan_only_diagnostic_sell_analyses(
    *,
    settings: Any,
    portfolio_snapshot: PortfolioSnapshot,
    build_sell_analysis: Callable[..., Any],
    get_live_snapshot_signal: Callable[[str], Any],
) -> tuple[Any, ...]:
    analyses: list[Any] = []
    for index, position in enumerate(portfolio_snapshot.held_positions):
        live_signal = get_live_snapshot_signal(position.symbol)
        template = _DIAGNOSTIC_POSITION_TEMPLATES[
            min(index, len(_DIAGNOSTIC_POSITION_TEMPLATES) - 1)
        ]
        market_snapshot = MarketSnapshot(
            symbol=position.symbol,
            current_price=int(position.current_price),
            open_price=int(position.average_cost * float(template["open_multiplier"])),
            low_price=int(position.average_cost * float(template["low_multiplier"])),
            prev_day_change_pct=float(template["prev_day_change_pct"]),
            live_snapshot_available=live_signal is not None,
            live_snapshot_updated_at=None if live_signal is None else live_signal.updated_at,
            live_snapshot_combined_rank=None if live_signal is None else live_signal.combined_rank,
            live_volume_rank=None if live_signal is None else live_signal.volume_rank,
            live_fluctuation_rank=None if live_signal is None else live_signal.fluctuation_rank,
            live_volume_power_rank=None if live_signal is None else live_signal.volume_power_rank,
            live_ranked_source_count=0 if live_signal is None else live_signal.ranked_source_count,
        )
        analyses.append(
            build_sell_analysis(
                symbol=position.symbol,
                holding_qty=position.holding_qty,
                average_cost=position.average_cost,
                market_snapshot=market_snapshot,
                portfolio_snapshot=portfolio_snapshot,
                settings=settings,
            )
        )
    return tuple(analyses)


def build_scan_only_runtime_mode_preview(
    *,
    settings: Any,
    build_runtime_rate_control: Callable[..., dict[str, object]],
    now_fn: Callable[[], datetime] = get_korean_now,
) -> tuple[dict[str, object], ...]:
    now = now_fn()
    tzinfo = now.tzinfo or KOREA_TZ
    normal_now = datetime(now.year, now.month, now.day, 10, 0, tzinfo=tzinfo)
    midday_now = datetime(now.year, now.month, now.day, 11, 30, tzinfo=tzinfo)
    degraded_now = datetime(now.year, now.month, now.day, 10, 15, tzinfo=tzinfo)
    preview_cases = (
        (
            "normal",
            normal_now,
            {
                "recent_rate_limit_hit_times": [],
                "consecutive_backoff_cycles": 0,
                "degraded_mode_until": None,
                "degraded_mode_reason": None,
                "backoff_until": None,
            },
        ),
        (
            "adaptive_midday",
            midday_now,
            {
                "recent_rate_limit_hit_times": [],
                "consecutive_backoff_cycles": 0,
                "degraded_mode_until": None,
                "degraded_mode_reason": None,
                "backoff_until": None,
            },
        ),
        (
            "degraded",
            degraded_now,
            {
                "recent_rate_limit_hit_times": [
                    degraded_now - timedelta(minutes=1),
                    degraded_now - timedelta(minutes=3),
                    degraded_now - timedelta(minutes=8),
                ],
                "consecutive_backoff_cycles": max(
                    int(settings.degraded_mode_consecutive_backoff_cycles),
                    4,
                ),
                "degraded_mode_until": None,
                "degraded_mode_reason": None,
                "backoff_until": degraded_now + timedelta(seconds=30),
            },
        ),
    )
    preview_rows: list[dict[str, object]] = []
    for label, preview_now, preview_state in preview_cases:
        runtime = build_runtime_rate_control(
            settings=settings,
            api_budget_state=preview_state,
            now=preview_now,
        )
        preview_rows.append(
            {
                "label": label,
                "mode": runtime.get("mode"),
                "reason": runtime.get("reason"),
                "buy_interval": runtime.get("effective_buy_scan_interval_seconds"),
                "sell_interval": runtime.get("effective_sell_check_interval_seconds"),
                "scan_max": runtime.get("effective_scan_symbols_max_per_cycle"),
                "deep_eval": runtime.get("effective_buy_scan_deep_eval_limit"),
                "sell_cap": runtime.get("effective_sell_watch_max_holdings_per_tick"),
            }
        )
    return tuple(preview_rows)


def print_scan_only_notice() -> None:
    print("scan_only 모드이므로 주문 가능 조회/주문 검토는 생략합니다.")
    print()


def print_scan_only_runtime_mode_preview(
    *,
    preview_rows: tuple[dict[str, object], ...],
) -> None:
    print("=== scan_only runtime mode preview ===")
    for row in preview_rows:
        print(
            f"{row.get('label')} | mode={row.get('mode')} | "
            f"buy_interval={row.get('buy_interval')}s | "
            f"sell_interval={row.get('sell_interval')}s | "
            f"scan_max={row.get('scan_max')} | "
            f"deep_eval={row.get('deep_eval')} | "
            f"sell_cap={row.get('sell_cap') or '-'} | "
            f"reason={row.get('reason') or '-'}"
        )
    print()


def print_scan_only_diagnostic_buy_scan(
    *,
    settings: Any,
    snapshot_info: dict[str, object],
    requested_symbols: tuple[str, ...],
) -> dict[str, object]:
    shortlist_symbols = tuple(requested_symbols[: settings.buy_scan_shallow_top_k])
    deep_eval_symbols = tuple(shortlist_symbols[: settings.buy_scan_deep_eval_limit])
    selected_symbol = deep_eval_symbols[0] if deep_eval_symbols else None

    print("=== BUY scan diagnostic ===")
    print(
        f"source={snapshot_info.get('source') or 'settings'} | "
        f"freshness={snapshot_info.get('detail') or '-'} | "
        f"requested={len(requested_symbols)} | "
        f"shortlist={len(shortlist_symbols)} | "
        f"deep_eval={len(deep_eval_symbols)}"
    )
    if requested_symbols:
        print("requested preview: " + ", ".join(requested_symbols[:5]))
    if shortlist_symbols:
        print("shortlist preview: " + ", ".join(shortlist_symbols[:5]))
    if deep_eval_symbols:
        print("deep eval preview: " + ", ".join(deep_eval_symbols[:5]))
    if selected_symbol:
        print(f"diagnostic top candidate: {selected_symbol}")
    print()

    return {
        "requested_symbols": requested_symbols,
        "shortlist_symbols": shortlist_symbols,
        "deep_eval_symbols": deep_eval_symbols,
        "selected_symbol": selected_symbol,
    }


def build_scan_only_diagnostic_summary(
    *,
    runtime_rate_control: dict[str, object] | None,
    snapshot_info: dict[str, object],
    daily_pnl_brake_state: dict[str, object] | None,
    buy_scan_summary: dict[str, object],
    sell_analysis_results: tuple[Any, ...],
    fallback_reason: str,
    daily_pnl_brake_display_status: Callable[[dict[str, object] | None], str],
) -> dict[str, object]:
    return {
        "rate_control_engaged": str(runtime_rate_control.get("mode") or "normal") != "normal"
        if isinstance(runtime_rate_control, dict)
        else False,
        "runtime_mode": (
            str(runtime_rate_control.get("mode") or "normal")
            if isinstance(runtime_rate_control, dict)
            else "normal"
        ),
        "snapshot_used": bool(snapshot_info.get("used")),
        "fallback_occurred": not bool(snapshot_info.get("used")),
        "snapshot_status": str(snapshot_info.get("detail") or snapshot_info.get("reason") or "-"),
        "daily_pnl_brake_state": daily_pnl_brake_display_status(daily_pnl_brake_state),
        "sell_watch_simulated_count": len(sell_analysis_results),
        "buy_scan_requested_count": len(tuple(buy_scan_summary.get("requested_symbols") or ())),
        "buy_scan_deep_eval_count": len(tuple(buy_scan_summary.get("deep_eval_symbols") or ())),
        "fallback_reason": fallback_reason,
    }


def print_scan_only_diagnostic_summary(summary: dict[str, object]) -> None:
    print("=== scan_only diagnostic summary ===")
    print(
        f"runtime_mode={summary.get('runtime_mode')} | "
        f"rate_control_engaged={'YES' if summary.get('rate_control_engaged') else 'NO'}"
    )
    print(
        f"snapshot_used={'YES' if summary.get('snapshot_used') else 'NO'} | "
        f"fallback_occurred={'YES' if summary.get('fallback_occurred') else 'NO'} | "
        f"snapshot_status={summary.get('snapshot_status') or '-'}"
    )
    print(
        f"sell_watch_simulated={summary.get('sell_watch_simulated_count', 0)} | "
        f"buy_scan_requested={summary.get('buy_scan_requested_count', 0)} | "
        f"buy_scan_deep_eval={summary.get('buy_scan_deep_eval_count', 0)}"
    )
    print(
        f"daily_pnl_brake={summary.get('daily_pnl_brake_state') or '-'} | "
        f"fallback_reason={summary.get('fallback_reason') or '-'}"
    )
    print()
