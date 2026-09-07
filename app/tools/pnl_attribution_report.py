"""CLI: daily PnL attribution report (plan §3 S2).

Reads the account's order log / cycle snapshots / runtime state (file reads
only — NO broker or network calls), builds the daily attribution via
``app.reporting.pnl_attribution.build_daily_attribution``, prints the Korean
lines, optionally writes JSON (``--json-out``) and sends one Slack summary
notification (``--slack``, SUMMARY channel). Slack failure is swallowed and the
CLI still exits 0 with the report intact (``morning_regime_pick`` precedent).

Path resolution (plan §2 naming): per-account files under
``state_root()/data`` (``cycle_snapshots_<sig>.jsonl``,
``runtime_state_<sig>.json``); the order log anchors on ``get_order_log_path()``
when it matches the requested signature, else the sibling
``orders_<sig>.jsonl``. Reads are tail-bounded: last N JSONL lines
(default 2000, env ``PNL_ATTRIBUTION_TAIL_LINES``).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.auth.account_scope import get_order_log_path, state_root
from app.core.jsonl import read_jsonl_tail_window
from app.reporting.pnl_attribution import (
    build_daily_attribution,
    render_attribution_lines,
)

TAIL_LINES_ENV = "PNL_ATTRIBUTION_TAIL_LINES"
DEFAULT_TAIL_LINES = 2000


def _tail_lines_limit() -> int:
    raw = os.environ.get(TAIL_LINES_ENV, "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_TAIL_LINES


def _read_jsonl_tail(path: Path, *, max_lines: int) -> list[dict]:
    """Decode the last ``max_lines`` JSONL objects of ``path`` (bounded
    EOF-window read). Live ``cycle_snapshots_*.jsonl`` files exceed the
    total-read limit (988MB observed), so this must stay on the tail-window
    reader — a full-scan reader raises ``LocalReadLimitError`` and the account
    silently drops out of the report."""
    return read_jsonl_tail_window(path, max_lines=max_lines)


def _read_json_dict(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _orders_path_for_signature(signature: str) -> Path:
    """Order-log path for a signature. Uses ``get_order_log_path()`` when the
    active settings resolve to this signature (plan §2); otherwise the sibling
    ``orders_<sig>.jsonl`` under the same naming rule."""
    expected_name = f"orders_{signature}.jsonl"
    try:
        active = get_order_log_path()
        if active.name == expected_name:
            return active
    except Exception:
        pass
    return state_root() / "logs" / expected_name


def _discover_signatures() -> list[str]:
    """Account signatures discovered from ``state_root()/data`` per the §2
    naming rule (rotated archives excluded)."""
    data_dir = state_root() / "data"
    signatures: set[str] = set()
    for prefix, suffix in (("cycle_snapshots_", ".jsonl"), ("runtime_state_", ".json")):
        try:
            entries = list(data_dir.glob(f"{prefix}*{suffix}"))
        except OSError:
            continue
        for entry in entries:
            signature = entry.name[len(prefix) : -len(suffix)]
            if signature and "_rotated_" not in signature:
                signatures.add(signature)
    return sorted(signatures)


def _build_report_for_signature(signature: str, target_date: str, tail_lines: int) -> dict:
    data_dir = state_root() / "data"
    orders = _read_jsonl_tail(
        _orders_path_for_signature(signature), max_lines=tail_lines
    )
    cycle_tail = _read_jsonl_tail(
        data_dir / f"cycle_snapshots_{signature}.jsonl", max_lines=tail_lines
    )
    exit_state = _read_json_dict(data_dir / f"runtime_state_{signature}.json")
    report = build_daily_attribution(
        orders=orders,
        cycle_tail=cycle_tail,
        exit_state=exit_state,
        target_date=target_date,
    )
    if not report.get("account_signature"):
        report["account_signature"] = signature
    return report


def run(
    *,
    account: str,
    target_date: str,
    json_out: str | None = None,
    notify=None,
) -> dict:
    """Build report(s) and return ``{"date", "reports"}``.

    ``notify`` is an injectable callable with the ``SlackNotifier.notify`` shape
    ``(event_type, message, *, symbol, details)``; ``None`` skips notification.
    Notify failure is swallowed — the report (and ``--json-out`` file) survive.
    """
    tail_lines = _tail_lines_limit()
    if account == "all":
        signatures = _discover_signatures()
    else:
        signatures = [account]

    reports = [
        _build_report_for_signature(signature, target_date, tail_lines)
        for signature in signatures
    ]
    result = {"date": target_date, "reports": reports}

    if json_out:
        out_path = Path(json_out)
        if out_path.parent != Path(""):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write JSON FIRST — notify failure must not lose the file.
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        )

    for report in reports:
        lines = render_attribution_lines(report)
        print("\n".join(lines))
        if notify is not None:
            _notify_report(notify, report, lines)

    return result


def _notify_report(notify, report: dict, lines: list[str]) -> None:
    """Send one SUMMARY-channel Slack notification; swallow any failure."""
    try:
        from app.notifications.slack import PNL_ATTRIBUTION_EVENT_TYPE

        notify(
            PNL_ATTRIBUTION_EVENT_TYPE,
            "\n".join(lines),
            symbol=None,
            details={
                "date": report.get("date"),
                "account_signature": report.get("account_signature"),
                "realized_krw": (report.get("totals") or {}).get("realized_krw"),
            },
        )
    except Exception:
        return


def _today_kst() -> str:
    from datetime import datetime, timedelta, timezone

    return ((datetime.now(timezone.utc) + timedelta(hours=9)).date()).isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily PnL attribution report (file reads only — offline)."
    )
    parser.add_argument(
        "--account",
        required=True,
        help="account signature, or 'all' to report every discovered account",
    )
    parser.add_argument(
        "--date", default=None, help="target date YYYY-MM-DD (default today KST)"
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
    target_date = args.date or _today_kst()

    notify = None
    if args.slack:
        try:
            from app.notifications.runtime_alerts import get_slack_notifier

            notify = get_slack_notifier().notify
        except Exception:
            notify = None

    return run(
        account=args.account,
        target_date=target_date,
        json_out=args.json_out,
        notify=notify,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
