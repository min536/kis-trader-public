"""Morning briefing assembler + renderer (plan §3 S1).

The securities-firm RA's pre-open desk in code: one Slack message that folds the
whole system's state together just before the open. This module is a *pure,
offline assembler* — it takes already-parsed disk artifacts (per-account cycle
snapshots, the previous day's attribution report, recent disclosure events, the
morning regime pick) and folds them into one briefing dict + Korean render
lines. **No network, no broker, no DART, no file I/O** — path resolution and
tail-bounded reads are the CLI layer's job (``app/tools/morning_briefing_report``).

Section sources (all disk read-only, resolved upstream):
- Account state: the last line of ``cycle_snapshots_<account>.jsonl`` — real
  keys ``equity_krw / cash_krw / orderable_cash_krw / intraday_pnl_pct /
  current_regime / daily_pnl_brake_state / timestamp / masked_account_display /
  environment``. **T+2 pending = ``cash_krw − orderable_cash_krw``** (settlement
  still locked; ``prvs_rcdl_excc_amt`` < ``dnca_tot_amt`` — see
  ``app/portfolio/equity_state.py`` T+2 semantics).
- Brake display: ``app.risk.pnl_brake.daily_pnl_brake_display_status`` is
  *imported and called* (never reimplemented; risk module read-only).
- Yesterday attribution / disclosures / regime pick: embedded as provided.

Contract: ``build_morning_briefing`` and ``render_briefing_lines`` never raise —
a missing or hostile section degrades to ``None``/empty.
"""

from __future__ import annotations

from datetime import datetime

from app.reporting.pnl_attribution import render_attribution_lines
from app.risk.pnl_brake import daily_pnl_brake_display_status

_STALE_AFTER_MINUTES = 12 * 60


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _snapshot_age_minutes(timestamp: object, now: datetime) -> int | None:
    """Whole minutes between ``timestamp`` (ISO string) and ``now``. ``None`` when
    the timestamp is absent or unparseable."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.strip())
    except ValueError:
        return None
    try:
        reference = now
        if parsed.tzinfo is not None and reference.tzinfo is None:
            parsed = parsed.replace(tzinfo=None)
        elif parsed.tzinfo is None and reference.tzinfo is not None:
            reference = reference.replace(tzinfo=None)
        delta = reference - parsed
    except (TypeError, ValueError):
        return None
    return int(delta.total_seconds() // 60)


def _brake_display(snapshot: dict) -> str:
    brake_state = snapshot.get("daily_pnl_brake_state")
    if not isinstance(brake_state, dict):
        brake_state = None
    return daily_pnl_brake_display_status(brake_state)


def _account_entry(snapshot: dict, now: datetime) -> dict:
    """Fold one cycle snapshot into the briefing account row. Never raises — any
    missing/hostile field degrades to ``None``/``0``."""
    if not isinstance(snapshot, dict):
        snapshot = {}
    cash_krw = _coerce_int(snapshot.get("cash_krw"))
    orderable_cash_krw = _coerce_int(snapshot.get("orderable_cash_krw"))
    t2_pending_krw = max(0, cash_krw - orderable_cash_krw)
    t2_pending_pct_of_cash = (
        round(t2_pending_krw / cash_krw * 100, 2) if cash_krw > 0 else None
    )
    age_minutes = _snapshot_age_minutes(snapshot.get("timestamp"), now)
    # Unknown freshness is treated as stale (a snapshot we cannot date is not
    # trustworthy for a pre-open readout).
    stale = age_minutes is None or age_minutes > _STALE_AFTER_MINUTES
    return {
        "display": snapshot.get("masked_account_display"),
        "environment": snapshot.get("environment"),
        "equity_krw": snapshot.get("equity_krw"),
        "cash_krw": snapshot.get("cash_krw"),
        "orderable_cash_krw": snapshot.get("orderable_cash_krw"),
        "t2_pending_krw": t2_pending_krw,
        "t2_pending_pct_of_cash": t2_pending_pct_of_cash,
        "intraday_pnl_pct": snapshot.get("intraday_pnl_pct"),
        "regime": snapshot.get("current_regime"),
        "brake_display": _brake_display(snapshot),
        "snapshot_age_minutes": age_minutes,
        "stale": stale,
    }


def build_morning_briefing(
    *,
    accounts: list[dict],
    attribution: dict | None,
    disclosures: list[dict],
    regime_pick: dict | None,
    now: datetime,
) -> dict:
    """Assemble the pre-open briefing from already-parsed disk artifacts.

    ``accounts`` are per-account latest cycle-snapshot dicts (raw keys). Every
    other section is embedded as provided (the CLI windows disclosures to the
    last 24h and resolves the regime-pick artifact). Never raises."""
    account_rows = [
        _account_entry(snapshot, now)
        for snapshot in (accounts if isinstance(accounts, list) else [])
    ]
    return {
        "generated_at": now.isoformat(),
        "accounts": account_rows,
        "yesterday_attribution": attribution if isinstance(attribution, dict) else None,
        "disclosures_24h": list(disclosures) if isinstance(disclosures, list) else [],
        "regime_pick": regime_pick if isinstance(regime_pick, dict) else None,
    }


def _format_krw(value: object) -> str:
    # None must stay visibly unknown ("n/a"), not coerce to 0원 — after-close
    # cycles persist snapshot money fields as None (known upstream gap).
    if value is None:
        return "n/a"
    number = _coerce_int(value)
    return f"{number:,}원"


def render_briefing_lines(briefing: dict) -> list[str]:
    """Korean console/Slack lines for the briefing. Never raises — a hostile or
    degraded briefing renders with ``— 없음`` placeholders."""
    if not isinstance(briefing, dict):
        return ["🌅 모닝 브리핑: 데이터 없음"]

    lines = [f"🌅 모닝 브리핑 — {briefing.get('generated_at') or 'unknown'}"]

    accounts = briefing.get("accounts")
    lines.append("[계좌]")
    if isinstance(accounts, list) and accounts:
        for acct in accounts:
            if not isinstance(acct, dict):
                continue
            stale_tag = " ⚠️stale" if acct.get("stale") else ""
            pnl = acct.get("intraday_pnl_pct")
            pnl_text = f"{float(pnl):+.2f}%" if isinstance(pnl, (int, float)) else "n/a"
            lines.append(
                f"  · {acct.get('display') or 'unknown'} ({acct.get('environment') or '-'})"
                f" | 평가 {_format_krw(acct.get('equity_krw'))}"
                f" | 장중 {pnl_text} | 레짐 {acct.get('regime') or '-'}{stale_tag}"
            )
            pct = acct.get("t2_pending_pct_of_cash")
            pct_text = f"{float(pct):.1f}%" if isinstance(pct, (int, float)) else "n/a"
            lines.append(
                f"    T+2 미결제 {_format_krw(acct.get('t2_pending_krw'))} (현금의 {pct_text})"
            )
    else:
        lines.append("  — 없음")

    lines.append("[브레이크]")
    if isinstance(accounts, list) and accounts:
        for acct in accounts:
            if not isinstance(acct, dict):
                continue
            lines.append(
                f"  · {acct.get('display') or 'unknown'}: {acct.get('brake_display') or '-'}"
            )
    else:
        lines.append("  — 없음")

    lines.append("[전일 귀속]")
    attribution = briefing.get("yesterday_attribution")
    if isinstance(attribution, dict):
        lines.extend(f"  {line}" for line in render_attribution_lines(attribution))
    else:
        lines.append("  — 없음")

    lines.append("[공시 24h]")
    disclosures = briefing.get("disclosures_24h")
    if isinstance(disclosures, list) and disclosures:
        for event in disclosures:
            if not isinstance(event, dict):
                continue
            lines.append(
                f"  · {event.get('corp_name') or '-'}({event.get('stock_code') or '-'})"
                f" {event.get('category') or '-'} — {event.get('report_nm') or '-'}"
            )
    else:
        lines.append("  — 없음")

    lines.append("[레짐 픽]")
    regime_pick = briefing.get("regime_pick")
    if isinstance(regime_pick, dict):
        fallback = regime_pick.get("fallback_reason")
        version = regime_pick.get("artifact_version") or "-"
        us_day = regime_pick.get("us_session_day") or "-"
        if fallback:
            lines.append(f"  fallback={fallback} → {version} (US {us_day})")
        else:
            lines.append(
                f"  regime {regime_pick.get('regime_id')} → {version} (US {us_day})"
            )
    else:
        lines.append("  — 없음")

    return lines
