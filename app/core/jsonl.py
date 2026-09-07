from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_lines_bounded,
    iter_tail_lines_window,
)

# cycle_snapshots lines can legitimately reach a few MB (one record per cycle
# carrying scan context). Snapshot readers opt into this per-line headroom so a
# single oversize line (e.g. pre-W0 bloated candidates) doesn't make
# read_jsonl_objects drop the WHOLE file. The generic default stays at 1MB; only
# snapshot call sites pass this. See docs/order_log_line_limit_design_20260707.md §R1.
SNAPSHOT_READ_LINE_MAX_BYTES = 8_000_000


def _decode_jsonl_line(
    line: str,
    *,
    lineno: int,
    decoder: json.JSONDecoder,
    records: list[dict[str, Any]],
    errors: list[str],
) -> None:
    cursor = 0
    decoded_any = False
    while cursor < len(line):
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1
        if cursor >= len(line):
            break
        try:
            payload, next_cursor = decoder.raw_decode(line, cursor)
        except json.JSONDecodeError as exc:
            errors.append(f"Line {lineno}: JSON parse error - {exc}")
            break
        if isinstance(payload, dict):
            records.append(payload)
        else:
            errors.append(f"Line {lineno}: not a JSON object")
        decoded_any = True
        cursor = next_cursor
    if not decoded_any:
        errors.append(f"Line {lineno}: no JSON object decoded")


def decode_jsonl_objects(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    decoder = json.JSONDecoder()

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        _decode_jsonl_line(
            line,
            lineno=lineno,
            decoder=decoder,
            records=records,
            errors=errors,
        )

    return records, errors


def read_jsonl_objects(
    path: Path, *, max_line_bytes: int | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"File not found: {path}"]
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    decoder = json.JSONDecoder()
    try:
        for lineno, raw_line in iter_lines_bounded(path, max_line_bytes=max_line_bytes):
            line = raw_line.strip()
            if not line:
                continue
            _decode_jsonl_line(
                line,
                lineno=lineno,
                decoder=decoder,
                records=records,
                errors=errors,
            )
        return records, errors
    except LocalReadLimitError as exc:
        return [], [f"Read limit exceeded: {path}: {exc}"]
    except UnicodeDecodeError as exc:
        return [], [f"Decode failed: {path}: {exc}"]
    except OSError as exc:
        return [], [f"Read failed: {path}: {exc}"]


def read_jsonl_tail_window(path: Path, *, max_lines: int) -> list[dict[str, Any]]:
    """Decode the last ``max_lines`` JSONL objects via a bounded EOF window.

    Reporting/briefing CLIs must use this — NOT the full-scan readers above —
    because live ``cycle_snapshots_*.jsonl`` files exceed the total-read limit
    (988MB observed 2026-07-19; ``iter_lines_bounded`` raises there and the
    account silently vanished from the briefing). Missing/broken files degrade
    to ``[]`` (never raises).
    """
    if not path.exists():
        return []
    try:
        lines = [
            raw_line
            for _lineno, raw_line in iter_tail_lines_window(
                path,
                max_lines=max_lines,
                max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES,
            )
        ]
        records, _errors = decode_jsonl_objects("\n".join(lines))
        return records
    except Exception:
        return []
