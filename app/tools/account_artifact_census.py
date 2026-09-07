"""D0 — deterministic account × artifact census (filenames + stat only).

docs/dashboard_account_routing_design_20260707.md §D0. Answers "which account
has which artifacts, how big, how fresh" WITHOUT reading any data/ file content
(respects the large-file / data-read policy — ``Path.stat`` only). Used both as
an operator CLI and as the ``RAW_DIAGNOSTICS.census`` source so equity-gap and
stale-account questions are confirmed from evidence, not guessed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dashboard.account_discovery import discover_accounts
from app.auth.account_scope import dashboard_paths_for_signature

# The five dashboard-facing artifact families keyed exactly as
# ``dashboard_paths_for_signature`` returns them.
_ARTIFACT_KEYS: tuple[str, ...] = (
    "runtime_state",
    "cycle_snapshots",
    "performance_snapshots",
    "orders",
    "performance_summary",
)

# Equity on the dashboard is derived primarily from the performance artifacts;
# when both are absent the account cannot show a trustworthy equity figure.
_EQUITY_SOURCE_KEYS: tuple[str, ...] = (
    "performance_summary",
    "performance_snapshots",
)


def _stat_artifact(path: Path) -> dict[str, Any]:
    """Presence + size + mtime from ``stat`` only — never opens the file."""
    try:
        st = path.stat()
    except (OSError, ValueError):
        return {"exists": False, "size_bytes": 0, "mtime_iso": None, "path": str(path)}
    return {
        "exists": True,
        "size_bytes": int(st.st_size),
        "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "path": str(path),
    }


def build_account_census(
    *,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Census each discovered signature against the five artifact families."""

    rows: list[dict[str, Any]] = []
    for presence in discover_accounts(data_dir=data_dir, logs_dir=logs_dir):
        paths = dashboard_paths_for_signature(
            presence.signature, data_dir=data_dir, logs_dir=logs_dir
        )
        artifacts = {key: _stat_artifact(paths[key]) for key in _ARTIFACT_KEYS}
        equity_available = any(
            artifacts[key]["exists"] for key in _EQUITY_SOURCE_KEYS
        )
        equity_gap_reason: str | None = None
        if not equity_available:
            equity_gap_reason = (
                "performance_summary/performance_snapshots absent — no equity source"
            )
        rows.append(
            {
                "signature": presence.signature,
                "environment": presence.environment,
                "artifacts": artifacts,
                "equity_available": equity_available,
                "equity_gap_reason": equity_gap_reason,
            }
        )
    return rows


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f}MB"
    if size_bytes >= 1_000:
        return f"{size_bytes / 1_000:.1f}KB"
    return f"{size_bytes}B"


def render_census_lines(census: list[dict[str, Any]]) -> list[str]:
    """Render a plaintext table (one artifact column group per account)."""

    lines: list[str] = []
    for row in census:
        head = f"{row['signature']} [{row['environment']}]"
        if not row["equity_available"]:
            head += f"  ⚠ equity_gap: {row['equity_gap_reason']}"
        lines.append(head)
        for key in _ARTIFACT_KEYS:
            art = row["artifacts"][key]
            if art["exists"]:
                lines.append(
                    f"    {key:<22} {_fmt_size(art['size_bytes']):>8}  {art['mtime_iso']}"
                )
            else:
                lines.append(f"    {key:<22} {'-':>8}  (missing)")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Account × artifact census (stat only).")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--logs-dir", default=None)
    args = parser.parse_args(argv)
    census = build_account_census(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        logs_dir=Path(args.logs_dir) if args.logs_dir else None,
    )
    for line in render_census_lines(census):
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
