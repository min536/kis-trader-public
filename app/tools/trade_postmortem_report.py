"""CLI: weekly trade postmortem report (plan §3 S4).

Reads the account's order log + runtime state (file reads only — NO broker or
network calls), builds the weekly closed-trade review via
``app.reporting.trade_postmortem.build_weekly_postmortem``, prints the Korean
lines, optionally writes JSON (``--json-out``) and sends one Slack summary
notification (``--slack``, SUMMARY channel; failure swallowed, exit 0 —
``morning_regime_pick`` precedent).

Path resolution and tail-bounded reads are shared with the sibling attribution
CLI (``app/tools/pnl_attribution_report.py``): per-account files under
``state_root()``, last-N-lines JSONL tail (default 2000, env
``PNL_ATTRIBUTION_TAIL_LINES`` — raise it when a week of orders exceeds it).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.auth.account_scope import state_root
from app.reporting.trade_postmortem import (
    build_weekly_postmortem,
    render_postmortem_lines,
)
from app.tools.pnl_attribution_report import (
    _discover_signatures,
    _orders_path_for_signature,
    _read_json_dict,
    _read_jsonl_tail,
    _tail_lines_limit,
)

_KST = timezone(timedelta(hours=9))


def _build_report_for_signature(signature: str, now: datetime, days: int, tail_lines: int) -> dict:
    orders = _read_jsonl_tail(
        _orders_path_for_signature(signature), max_lines=tail_lines
    )
    exit_state = _read_json_dict(
        state_root() / "data" / f"runtime_state_{signature}.json"
    )
    report = build_weekly_postmortem(
        orders=orders,
        exit_state=exit_state,
        now=now,
        window_days=days,
    )
    report["account_signature"] = signature
    return report


def run(
    *,
    account: str,
    days: int = 7,
    json_out: str | None = None,
    notify=None,
    now: datetime | None = None,
) -> dict:
    """Build postmortem report(s) and return ``{"reports": [...]}``.

    ``notify`` is an injectable callable with the ``SlackNotifier.notify`` shape
    (``None`` skips notification; failure is swallowed). ``now`` is injectable
    for hermetic tests and defaults to the current KST time.
    """
    resolved_now = now if now is not None else datetime.now(_KST)
    tail_lines = _tail_lines_limit()
    signatures = _discover_signatures() if account == "all" else [account]

    reports = [
        _build_report_for_signature(signature, resolved_now, days, tail_lines)
        for signature in signatures
    ]
    result = {"reports": reports}

    if json_out:
        out_path = Path(json_out)
        if out_path.parent != Path(""):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write JSON FIRST — notify failure must not lose the file.
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        )

    for report in reports:
        lines = render_postmortem_lines(report)
        print("\n".join(lines))
        if notify is not None:
            _notify_report(notify, report, lines)

    return result


def _notify_report(notify, report: dict, lines: list[str]) -> None:
    """Send one SUMMARY-channel Slack notification; swallow any failure."""
    try:
        from app.notifications.slack import TRADE_POSTMORTEM_EVENT_TYPE

        window = report.get("window") if isinstance(report.get("window"), dict) else {}
        notify(
            TRADE_POSTMORTEM_EVENT_TYPE,
            "\n".join(lines),
            symbol=None,
            details={
                "account_signature": report.get("account_signature"),
                "window_start": window.get("start"),
                "window_end": window.get("end"),
                "closed_trades": len(report.get("closed_trades") or []),
            },
        )
    except Exception:
        return


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Weekly trade postmortem report (file reads only — offline)."
    )
    parser.add_argument(
        "--account",
        required=True,
        help="account signature, or 'all' to report every discovered account",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="review window in days (default 7)"
    )
    parser.add_argument(
        "--slack", action="store_true", help="send the report to the SUMMARY channel"
    )
    parser.add_argument(
        "--json-out", default=None, help="write the report JSON to this path"
    )
    return parser


def main(argv=None) -> dict:
    args = _build_parser().parse_args(argv)

    notify = None
    if args.slack:
        try:
            from app.notifications.runtime_alerts import get_slack_notifier

            notify = get_slack_notifier().notify
        except Exception:
            notify = None

    return run(
        account=args.account,
        days=args.days,
        json_out=args.json_out,
        notify=notify,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
