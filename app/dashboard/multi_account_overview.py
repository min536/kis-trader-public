"""Read-only multi-account console overview (C-3).

Aggregates the per-account C-2 console summary across every account signature
that already has state on disk. It is a thin composition of three existing,
already-tested pieces — no new data parsing, no network, no broker calls:

  1. ``account_discovery.discover_accounts``  — which signatures exist (filenames only)
  2. ``data_loader.load_dashboard_data(signature=…)`` — that account's payload
  3. ``console_v1.build_console_overview``    — the C-2 health summary per account

The v2 multi-account UI and live integration stay gated on a second real account
running in parallel (see ``docs/multi_account_parallelization_plan.md`` §11
Phase 2–4); this module is the offline, fixture-testable aggregation layer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.dashboard.account_discovery import discover_accounts
from app.dashboard.console_v1 import build_console_overview
from app.dashboard.data_loader import load_dashboard_data


def build_multi_account_overview(
    *,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Build a per-account console overview for every discovered signature.

    Returns ``{"count": N, "accounts": [{signature, environment, sources,
    overview}, …]}`` ordered by signature (discovery order). ``now_epoch`` is
    forwarded to ``build_console_overview`` so heartbeat/staleness is
    deterministic in tests.
    """
    accounts = discover_accounts(data_dir=data_dir, logs_dir=logs_dir)
    rows: list[dict[str, Any]] = []
    for account in accounts:
        data = load_dashboard_data(
            signature=account.signature,
            data_dir=data_dir,
            logs_dir=logs_dir,
        )
        overview = build_console_overview(
            data,
            account_signature=account.signature,
            now_epoch=now_epoch,
        )
        rows.append(
            {
                "signature": account.signature,
                "environment": account.environment,
                "sources": list(account.sources),
                "overview": overview,
            }
        )
    return {"count": len(rows), "accounts": rows}


def _format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    accounts = report.get("accounts") or []
    if not accounts:
        return "(no account state found)"
    lines.append(f"multi-account overview — {report.get('count', 0)} account(s)")
    for row in accounts:
        session = (row.get("overview") or {}).get("session") or {}
        status = session.get("status", "UNKNOWN")
        heartbeat = session.get("heartbeat_label", "")
        lines.append(
            f"  {row.get('signature')}  env={row.get('environment')}  "
            f"status={status}  heartbeat={heartbeat}"
        )
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.dashboard.multi_account_overview",
        description="계좌별 콘솔 overview 집계 (read-only, discovery 기반).",
    )
    parser.add_argument("--data-dir", default=None, help="data 디렉토리 (기본: repo data/)")
    parser.add_argument("--logs-dir", default=None, help="logs 디렉토리 (기본: repo logs/)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    report = build_multi_account_overview(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        logs_dir=Path(args.logs_dir) if args.logs_dir else None,
    )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
