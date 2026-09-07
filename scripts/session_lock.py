#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.session_lock import hold_lock, inspect_lock, summarize_lock_status, update_lock_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="kis-trader session lock helper")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    hold_parser = subparsers.add_parser("hold")
    hold_parser.add_argument("--lock-path", required=True)
    hold_parser.add_argument("--owner-pid", required=True, type=int)
    hold_parser.add_argument("--command", required=True)
    hold_parser.add_argument("--ready-file")
    hold_parser.add_argument("--heartbeat-interval", type=float, default=5.0)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--lock-path", required=True)
    status_parser.add_argument("--expected-command", default="")
    status_parser.add_argument("--summary", action="store_true")
    status_parser.add_argument("--field", default="")

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--lock-path", required=True)
    update_parser.add_argument("--pid", type=int)
    update_parser.add_argument("--app-pid", type=int)
    update_parser.add_argument("--holder-pid", type=int)
    update_parser.add_argument("--status")
    update_parser.add_argument("--command")

    args = parser.parse_args()

    if args.command_name == "hold":
        return hold_lock(
            lock_path=args.lock_path,
            owner_pid=args.owner_pid,
            command=args.command,
            ready_file=args.ready_file,
            heartbeat_interval_seconds=args.heartbeat_interval,
        )

    if args.command_name == "update":
        metadata = update_lock_metadata(
            args.lock_path,
            pid=args.pid,
            app_pid=args.app_pid,
            holder_pid=args.holder_pid,
            status=args.status,
            command=args.command,
        )
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        return 0

    status = inspect_lock(args.lock_path, expected_command=args.expected_command)
    if args.summary:
        print(summarize_lock_status(args.lock_path, expected_command=args.expected_command))
        return 0
    if args.field:
        value = status.get(args.field)
        print("" if value is None else value)
        return 0
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
