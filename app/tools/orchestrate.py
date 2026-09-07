"""CLI for the runtime orchestrator.

Runs a deterministic, read-only orchestrator workflow and prints its Markdown
report to stdout. Read-only is the default; ``--allow-write`` additionally
persists the report under ``_workspace/`` (gated by ``assert_write_allowed``).

Example::

    python -m app.tools.orchestrate postrun_audit --date 20260602 --account mock_12345678_01
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from app.orchestrator.report import write_report
from app.orchestrator.runners import run_workflow
from app.orchestrator.types import OrchestratorContext

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.tools.orchestrate",
        description="Run a deterministic read-only orchestrator workflow.",
    )
    parser.add_argument(
        "workflow",
        choices=["postrun_audit"],
        help="Workflow to run.",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Trading date YYYYMMDD.",
    )
    parser.add_argument(
        "--account",
        required=True,
        help="Account identifier, e.g. mock_12345678_01.",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Persist the report under _workspace/ (default: read-only, stdout only).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    ctx = OrchestratorContext(
        project_root=_PROJECT_ROOT,
        trading_date=args.date,
        workflow=args.workflow,
        account=args.account,
        read_only=not args.allow_write,
    )
    report = run_workflow(ctx)
    print(report)
    if not ctx.read_only:
        path = write_report(ctx, report)
        print(f"\n_written: {path}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
