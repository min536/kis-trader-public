"""Persist layer for autotuner artifacts (Phase 2).

Resolves the D3 ``proposal_id`` sequence and lays out the
``_workspace/autotuner/{proposals,baselines}/`` directories. File I/O into
_workspace only — never touches runtime state or the trading-critical surface.
See docs/live_autotuner_decisions.md (D2/D3).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4


_WORKSPACE_SUBDIR = ("_workspace", "autotuner")
_BASELINE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def workspace_paths(project_root) -> dict:
    """Resolve the D2/D3 ``_workspace/autotuner/{proposals,baselines}`` layout."""
    base = Path(project_root).joinpath(*_WORKSPACE_SUBDIR)
    return {
        "base": base,
        "proposals": base / "proposals",
        "baselines": base / "baselines",
    }


def save_baseline(baseline: dict, directory) -> Path:
    """Persist a durable baseline snapshot to ``{baseline_id}.json`` (D2)."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    baseline_id = str(baseline["baseline_id"])
    if not _BASELINE_ID_RE.fullmatch(baseline_id):
        raise ValueError("baseline_id is not a safe autotuner baseline id")
    base = target_dir.resolve()
    path = (base / f"{baseline_id}.json").resolve()
    if path.parent != base:
        raise ValueError("baseline path escapes the target directory")
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(baseline, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return path


def supersede_active_bundles(directory, *, keep_proposal_id, mode, now):
    """D6: mark prior active same-mode bundles as ``superseded`` (single active).

    Returns the list of superseded proposal_ids. Each rewritten bundle gets an
    explicit audit event referencing the replacing ``keep_proposal_id`` so the
    replacement is auditable (not a silent auto-invalidation).
    """
    target = Path(directory)
    superseded: list[str] = []
    if not target.is_dir():
        return superseded
    for path in sorted(target.glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(bundle, dict):
            continue
        if bundle.get("proposal_id") == keep_proposal_id:
            continue
        if bundle.get("status") != "approved" or bundle.get("mode") != mode:
            continue
        # An already-expired bundle is inactive — no rewrite needed.
        expires_raw = (bundle.get("ttl") or {}).get("expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        except (ValueError, TypeError):
            expires_at = None
        if expires_at is None or expires_at.tzinfo is None or now >= expires_at:
            continue
        bundle["status"] = "superseded"
        audit = bundle.setdefault("audit", {})
        events = audit.setdefault("events", [])
        events.append(
            {
                "at": now.isoformat(),
                "actor": "autotuner_approve",
                "event": "superseded",
                "by": keep_proposal_id,
            }
        )
        _atomic_write_json(path, bundle)
        superseded.append(str(bundle.get("proposal_id")))
    return superseded


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def next_proposal_id(directory, date_yyyymmdd: str, mode: str) -> str:
    """Next ``atp_{date}_{mode}_{NNNN}`` id, scoped per date+mode (D3)."""
    prefix = f"atp_{date_yyyymmdd}_{mode}_"
    seq_re = re.compile(re.escape(prefix) + r"(\d{4,})\.json$")
    target = Path(directory)
    highest = 0
    if target.is_dir():
        for entry in target.iterdir():
            match = seq_re.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:04d}"
