"""CLI: autotuner_propose -- one periodic propose-cycle tick.

Designed to be invoked every N minutes during market hours by an external
scheduler. Reads a curated candidate plan (JSON list), runs one
``run_propose_cycle`` (read-only by default), and prints the human notification
report. Only the explicit ``--allow-write`` flag persists DRAFT proposals under
``_workspace/autotuner/proposals/``. It never approves and never applies anything
to the runtime; no broker calls.

Usage:
    python -m app.tools.autotuner_propose --plan plan.json            # dry-run
    python -m app.tools.autotuner_propose --plan plan.json --allow-write
"""

from __future__ import annotations


def main(argv=None) -> int:
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from app.autotuner.propose_cycle import run_propose_cycle

    argv = list(argv or [])

    def _opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default

    plan = json.loads(Path(_opt("--plan", "")).read_text(encoding="utf-8"))
    result = run_propose_cycle(
        plan,
        project_root=Path(_opt("--project-root", ".")),
        now=datetime.now(timezone.utc),
        read_only="--allow-write" not in argv,
        out_dir=Path(_opt("--out-dir", "_workspace/autotuner/proposals")),
    )
    print(result["report"])
    if "--notify-slack" in argv:
        from app.autotuner import notify as _notify
        from app.notifications.slack import SlackNotifier

        _notify.notify_propose_cycle(result, notifier=SlackNotifier())
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
