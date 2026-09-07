"""S2 — assemble a minute provider + score artifact from the gate2 manifest.

docs/slack_backtest_native_pipeline_design_20260707.md §4.1/§5. The shared
prerequisite for the native backtest CLI (S5a) and the autotuner native backend
(backtester_redesign §4.2 M2-S2): turn ``_workspace/gate2/paths.json`` into a
constructed ``ParquetMinuteProvider`` and a ``ScoreV2Artifact``, resolving the
data window against what the parquet store actually covers.

Path helpers are pure (no parquet reads); provider construction reads parquet
inside ``build_parquet_provider`` only.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


def load_paths_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Read the gate2 paths.json manifest into a dict."""
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def _resolve_relative(base_dir: Path | None, manifest_path: Path, value: str) -> Path:
    """Resolve a manifest path value. Absolute values pass through; relative
    values resolve against ``base_dir`` (defaults to the manifest's directory)."""
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    root = base_dir if base_dir is not None else manifest_path.parent
    return (root / candidate)


def resolve_parquet_root(
    manifest_path: str | Path, *, base_dir: Path | None = None
) -> Path:
    """Resolve the parquet store root from the manifest ``parquet_out_dir``."""
    manifest_path = Path(manifest_path)
    manifest = load_paths_manifest(manifest_path)
    value = str(manifest.get("parquet_out_dir") or "").strip()
    if not value:
        raise ValueError("manifest has no parquet_out_dir")
    return _resolve_relative(base_dir, manifest_path, value)


def available_window(parquet_root: str | Path) -> tuple[date, date] | None:
    """Return ``(min_date, max_date)`` over ``date=YYYY-MM-DD/part.parquet``
    partitions, or ``None`` when the store has no usable partitions."""
    root = Path(parquet_root)
    if not root.is_dir():
        return None
    days: list[date] = []
    for date_dir in root.glob("date=*"):
        if not date_dir.is_dir():
            continue
        if not (date_dir / "part.parquet").exists():
            continue
        try:
            days.append(date.fromisoformat(date_dir.name[len("date=") :]))
        except ValueError:
            continue
    if not days:
        return None
    return (min(days), max(days))


def available_dates(
    parquet_root: str | Path,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[date]:
    """Sorted trading dates with a ``part.parquet`` partition in ``[start, end]``
    (bounds optional). This is the ``dates`` list ``run_backtest`` iterates."""
    root = Path(parquet_root)
    if not root.is_dir():
        return []
    days: list[date] = []
    for date_dir in root.glob("date=*"):
        if not date_dir.is_dir() or not (date_dir / "part.parquet").exists():
            continue
        try:
            day = date.fromisoformat(date_dir.name[len("date=") :])
        except ValueError:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        days.append(day)
    return sorted(days)


def clamp_window(
    *, start: date, end: date, available: tuple[date, date]
) -> tuple[tuple[date, date], bool]:
    """Clamp ``[start, end]`` into the available window.

    Returns ``((clamped_start, clamped_end), adjusted)`` where ``adjusted`` is
    ``True`` when either bound was moved — the freshness gate reports this rather
    than silently failing open.
    """
    avail_start, avail_end = available
    clamped_start = max(start, avail_start)
    clamped_end = min(end, avail_end)
    adjusted = clamped_start != start or clamped_end != end
    return (clamped_start, clamped_end), adjusted


def resolve_artifact(
    manifest_path: str | Path, *, base_dir: Path | None = None
) -> Any:
    """Load the manifest ``artifact_out`` ScoreV2Artifact, or the default when
    the file is absent (the weight-search candidate may not exist yet)."""
    from app.gate2.schema import default_artifact, load_artifact

    manifest_path = Path(manifest_path)
    manifest = load_paths_manifest(manifest_path)
    value = str(manifest.get("artifact_out") or "").strip()
    if value:
        artifact_path = _resolve_relative(base_dir, manifest_path, value)
        if artifact_path.exists():
            return load_artifact(artifact_path)
    return default_artifact()


def build_parquet_provider(
    *,
    parquet_root: str | Path,
    symbols: tuple[str, ...] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    session_open: str = "09:00",
    session_close: str = "15:30",
) -> Any:
    """Construct a ``ParquetMinuteProvider`` bounded to ``[start_date, end_date]``
    and the requested symbols (reads parquet during rollup precompute)."""
    from app.research.replay.parquet_provider import ParquetMinuteProvider

    return ParquetMinuteProvider(
        Path(parquet_root),
        symbols=symbols,
        session_open=session_open,
        session_close=session_close,
        min_date=start_date,
        max_date=end_date,
    )
