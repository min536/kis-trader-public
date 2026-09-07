"""CLI: lane pipeline observation digest (BP-2 S3).

Read-only and bounded — reads a limited window of recent cycle snapshots and
prints a lane-telemetry digest. Never writes, never touches order paths. Not
imported by any runtime module (reporting leaf).

Usage:
    .venv/bin/python -m app.tools.lane_digest [--limit N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

from app.reporting.cycle_snapshots import load_recent_cycle_snapshots
from app.reporting.lane_observation import (
    aggregate_lane_observations,
    format_lane_digest_console,
    format_lane_digest_json,
)


def render_lane_digest(records: Iterable[object], *, as_json: bool = False) -> str:
    digest = aggregate_lane_observations(records)
    if as_json:
        return json.dumps(
            format_lane_digest_json(digest),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    return "\n".join(format_lane_digest_console(digest))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lane pipeline observation digest (read-only, bounded)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="how many recent cycle snapshots to read (bounded; default 200)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of console text"
    )
    args = parser.parse_args(argv)

    try:
        records = load_recent_cycle_snapshots(max(1, args.limit))
    except Exception as exc:  # never crash the CLI on a read problem
        print(
            f"lane_digest: could not read snapshots: {type(exc).__name__}",
            file=sys.stderr,
        )
        records = []

    print(render_lane_digest(records, as_json=args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
