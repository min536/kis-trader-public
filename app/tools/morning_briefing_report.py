"""CLI: morning briefing report (plan §3 S2).

Assembles the pre-open briefing from disk artifacts (file reads only — NO broker
or network calls), renders the Korean lines, optionally writes JSON
(``--json-out``) and sends one Slack notification to the OPERATOR channel
(``--slack``). Slack failure is swallowed and the CLI still exits 0 with the
briefing intact (``morning_regime_pick`` precedent).

Section sourcing (plan §2, all disk read-only):
- Accounts: glob ``cycle_snapshots_*.jsonl`` under ``--data-dir`` (default
  ``data``); each account = that file's last JSONL line. The primary account
  (first by signature) also drives the reused daily attribution.
- Yesterday attribution: reuse ``app.reporting.pnl_attribution`` builder over the
  primary account's orders/cycle-tail/runtime-state for its last-snapshot date;
  ``None`` when no account exists.
- Disclosures: tail of ``<state_dir>/disclosure_events.jsonl`` (``state_dir`` =
  env ``DISCLOSURE_SENTINEL_STATE_DIR`` else ``--data-dir``), windowed to the
  last 24h by ``detected_at``.
- Regime pick: ``morning_regime_<run-date>.json`` under env
  ``MORNING_REGIME_ARTIFACT_DIR`` when set, else the section is omitted.

Reads are tail-bounded via the core helpers (never whole-file). Pure builders
do the folding; this CLI only resolves paths and reads.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from app.core.jsonl import read_jsonl_tail_window
from app.core.time_utils import get_korean_now
from app.notifications.morning_briefing import (
    build_morning_briefing,
    render_briefing_lines,
)
from app.reporting.pnl_attribution import build_daily_attribution

TAIL_LINES_ENV = "MORNING_BRIEFING_TAIL_LINES"
DEFAULT_TAIL_LINES = 2000
DISCLOSURE_STATE_DIR_ENV = "DISCLOSURE_SENTINEL_STATE_DIR"
REGIME_ARTIFACT_DIR_ENV = "MORNING_REGIME_ARTIFACT_DIR"
_DISCLOSURE_WINDOW_MINUTES = 24 * 60


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
    """Decode the last ``max_lines`` JSONL objects (bounded EOF-window read).

    Live ``cycle_snapshots_*.jsonl`` files exceed the total-read limit (988MB
    observed), so this must stay on the tail-window reader — a full-scan reader
    raises ``LocalReadLimitError`` and silently drops the account."""
    return read_jsonl_tail_window(path, max_lines=max_lines)


def _read_json_dict(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _discover_signatures(data_dir: Path) -> list[str]:
    """Account signatures from ``cycle_snapshots_<sig>.jsonl`` under ``data_dir``
    (rotated archives excluded)."""
    prefix, suffix = "cycle_snapshots_", ".jsonl"
    signatures: set[str] = set()
    try:
        entries = list(data_dir.glob(f"{prefix}*{suffix}"))
    except OSError:
        return []
    for entry in entries:
        signature = entry.name[len(prefix) : -len(suffix)]
        if signature and "_rotated_" not in signature:
            signatures.add(signature)
    return sorted(signatures)


def _latest_snapshot(data_dir: Path, signature: str, tail_lines: int) -> dict | None:
    records = _read_jsonl_tail(
        data_dir / f"cycle_snapshots_{signature}.jsonl", max_lines=tail_lines
    )
    return records[-1] if records else None


def _build_attribution(data_dir: Path, signature: str, tail_lines: int) -> dict | None:
    """Reuse the B-track builder over the primary account's files. Target date =
    the account's last snapshot date (the last session). ``None`` on no basis."""
    cycle_tail = _read_jsonl_tail(
        data_dir / f"cycle_snapshots_{signature}.jsonl", max_lines=tail_lines
    )
    if not cycle_tail:
        return None
    target_date = str(cycle_tail[-1].get("timestamp") or "")[:10]
    if not target_date:
        return None
    orders = _read_jsonl_tail(
        data_dir / f"orders_{signature}.jsonl", max_lines=tail_lines
    )
    exit_state = _read_json_dict(data_dir / f"runtime_state_{signature}.json")
    return build_daily_attribution(
        orders=orders,
        cycle_tail=cycle_tail,
        exit_state=exit_state,
        target_date=target_date,
    )


def _minutes_between(detected_at: object, now: datetime) -> float | None:
    if not isinstance(detected_at, str) or not detected_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(detected_at.strip())
    except ValueError:
        return None
    try:
        reference = now
        if parsed.tzinfo is not None and reference.tzinfo is None:
            parsed = parsed.replace(tzinfo=None)
        elif parsed.tzinfo is None and reference.tzinfo is not None:
            reference = reference.replace(tzinfo=None)
        return (reference - parsed).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _recent_disclosures(data_dir: Path, now: datetime, tail_lines: int) -> list[dict]:
    """Disclosure events within the last 24h. Unparseable ``detected_at`` is kept
    (never silently drop a logged event)."""
    state_dir = Path(os.environ.get(DISCLOSURE_STATE_DIR_ENV) or str(data_dir))
    events = _read_jsonl_tail(state_dir / "disclosure_events.jsonl", max_lines=tail_lines)
    recent: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        age = _minutes_between(event.get("detected_at"), now)
        if age is None or (0 <= age <= _DISCLOSURE_WINDOW_MINUTES):
            recent.append(event)
    return recent


def _regime_pick(now: datetime) -> dict | None:
    """Read ``morning_regime_<run-date>.json`` from env-configured artifact dir;
    ``None`` when unset or absent (plan §2 — section omitted)."""
    artifact_dir = os.environ.get(REGIME_ARTIFACT_DIR_ENV)
    if not artifact_dir:
        return None
    path = Path(artifact_dir) / f"morning_regime_{now.date().isoformat()}.json"
    if not path.exists():
        return None
    payload = _read_json_dict(path)
    return payload or None


def _assemble_briefing(*, data_dir: str, now: datetime) -> dict:
    """Fold the on-disk artifacts into the briefing dict (no print, no Slack, no
    JSON write) — the single assembly path shared by the CLI ``run`` and the
    Slack bot's ``briefing`` reply."""
    root = Path(data_dir)
    tail_lines = _tail_lines_limit()

    signatures = _discover_signatures(root)
    accounts = [
        snapshot
        for snapshot in (
            _latest_snapshot(root, signature, tail_lines) for signature in signatures
        )
        if snapshot is not None
    ]
    attribution = (
        _build_attribution(root, signatures[0], tail_lines) if signatures else None
    )
    disclosures = _recent_disclosures(root, now, tail_lines)
    regime_pick = _regime_pick(now)

    return build_morning_briefing(
        accounts=accounts,
        attribution=attribution,
        disclosures=disclosures,
        regime_pick=regime_pick,
        now=now,
    )


def run(
    *,
    data_dir: str,
    now: datetime | None = None,
    json_out: str | None = None,
    notify=None,
) -> dict:
    """Assemble the briefing and return the briefing dict.

    ``notify`` is an injectable callable with the ``SlackNotifier.notify`` shape
    ``(event_type, message, *, symbol, details)``; ``None`` skips notification.
    Notify failure is swallowed — the briefing (and ``--json-out`` file) survive.
    """
    resolved_now = now or get_korean_now()
    briefing = _assemble_briefing(data_dir=data_dir, now=resolved_now)
    lines = render_briefing_lines(briefing)

    if json_out:
        out_path = Path(json_out)
        if str(out_path.parent):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write JSON FIRST — notify failure must not lose the file.
        out_path.write_text(
            json.dumps(briefing, ensure_ascii=False, indent=2, sort_keys=True)
        )

    print("\n".join(lines))
    if notify is not None:
        _notify_briefing(notify, briefing, lines)

    return briefing


def _notify_briefing(notify, briefing: dict, lines: list[str]) -> None:
    """Send one OPERATOR-channel Slack notification; swallow any failure."""
    try:
        from app.notifications.slack import MORNING_BRIEFING_EVENT_TYPE

        notify(
            MORNING_BRIEFING_EVENT_TYPE,
            "\n".join(lines),
            symbol=None,
            details={
                "generated_at": briefing.get("generated_at"),
                "accounts": len(briefing.get("accounts") or []),
                "disclosures_24h": len(briefing.get("disclosures_24h") or []),
            },
        )
    except Exception:
        return


def render_briefing_command_reply(
    *,
    env=None,
    data_dir: str | None = None,
    now: datetime | None = None,
) -> str:
    """Assemble the briefing and return the Slack-bot reply text (no send, no
    JSON write) — the read-only on-demand twin of the scheduled ``--slack`` run.
    Never raises: any assembly failure yields an error-text reply so the bot
    stays up (mirrors ``handle_app_mention_event``'s fail-safe contract)."""
    try:
        source = env if env is not None else os.environ
        resolved_dir = data_dir or source.get("MORNING_BRIEFING_DATA_DIR") or "data"
        resolved_now = now or get_korean_now()
        briefing = _assemble_briefing(data_dir=resolved_dir, now=resolved_now)
        return "\n".join(render_briefing_lines(briefing))
    except Exception as exc:
        return f"⚠️ 모닝 브리핑 조립 중 오류가 발생했습니다: {exc.__class__.__name__}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Morning briefing report (file reads only — offline)."
    )
    parser.add_argument(
        "--data-dir", default="data", help="directory holding cycle_snapshots_*.jsonl"
    )
    parser.add_argument(
        "--slack", action="store_true", help="send the briefing to the OPERATOR channel"
    )
    parser.add_argument(
        "--json-out", default=None, help="write the briefing JSON to this path"
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

    return run(data_dir=args.data_dir, json_out=args.json_out, notify=notify)


if __name__ == "__main__":  # pragma: no cover
    main()
