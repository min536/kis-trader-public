"""Read-only status check for the sibling open-trading-api integration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from typing import Callable, Mapping

from app.integrations.open_trading_api import (
    OPEN_TRADING_API_ROOT_ENV,
    append_audit_record,
    build_command_audit_record,
    resolve_open_trading_api_root,
    run_allowed_open_trading_api_command,
)


def build_status_payload(
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    audit_log=None,
) -> dict[str, object]:
    """Build a bounded, read-only integration status payload."""
    source_env = os.environ if env is None else env
    root_resolution = resolve_open_trading_api_root(env=source_env)
    payload: dict[str, object] = {
        "integration": "open-trading-api",
        "mode": "read-only",
        "env_var": OPEN_TRADING_API_ROOT_ENV,
        "available": False,
        "root": str(root_resolution.root) if root_resolution.root else None,
        "repo_head": None,
        "reason": root_resolution.reason,
    }
    if not root_resolution.available or root_resolution.root is None:
        return payload

    command = run_allowed_open_trading_api_command(
        "repo_head",
        root=root_resolution.root,
        env=source_env,
        runner=runner,
    )
    payload["available"] = command.ok
    payload["repo_head"] = command.stdout.strip() if command.ok else None
    payload["reason"] = "" if command.ok else command.error

    if audit_log is not None:
        from datetime import datetime, timezone

        record = build_command_audit_record(
            command,
            repo_head=payload["repo_head"],
            at=datetime.now(timezone.utc).isoformat(),
        )
        append_audit_record(audit_log, record)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the read-only open-trading-api sibling integration."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Append an observability record (command/commit/path) to this JSONL path.",
    )
    args = parser.parse_args(argv)

    payload = build_status_payload(audit_log=args.audit_log)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("open-trading-api integration")
        print(f"  mode      : {payload['mode']}")
        print(f"  env var   : {payload['env_var']}")
        print(f"  available : {'yes' if payload['available'] else 'no'}")
        print(f"  root      : {payload['root'] or 'N/A'}")
        print(f"  repo head : {payload['repo_head'] or 'N/A'}")
        if payload["reason"]:
            print(f"  reason    : {payload['reason']}")
    return 0 if payload["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
