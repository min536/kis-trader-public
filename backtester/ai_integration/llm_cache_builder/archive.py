"""Raw LLM response archiving.

``ResponseArchive`` writes one JSONL file per trading date under
``<archive_dir>/YYYY-MM-DD.jsonl``. Each line is an attempt record:

    {
      "date_key":     "2024-01-15",
      "attempt":      1,
      "timestamp":    "2024-06-01T12:00:00+00:00",
      "success":      true,
      "raw_response": "...",
      "parsed":       { ... },   // present only on success
      "error":        null
    }

Appending is always safe: re-running the batch job simply adds new
attempt records without destroying the history. The ``last_success``
helper makes it easy to replay a day without re-calling the LLM.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


__all__ = ["ResponseArchive"]


class ResponseArchive:
    """Thread-safe append-only archive of raw LLM responses.

    Parameters
    ----------
    archive_dir:
        Directory to write ``YYYY-MM-DD.jsonl`` files.  Created on
        first use if it does not exist.
    """

    def __init__(self, archive_dir: str | Path) -> None:
        self._dir = Path(archive_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── write ──────────────────────────────────────────────────────────────

    def save(
        self,
        date_key: str,
        attempt: int,
        raw_response: str,
        parsed: dict[str, Any] | None,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Append one attempt record for ``date_key``.

        Parameters
        ----------
        date_key:
            ISO date string, e.g. ``"2024-01-15"``.
        attempt:
            1-based attempt counter for this date.
        raw_response:
            The verbatim text returned by the LLM (empty string for API
            errors that produced no text).
        parsed:
            The validated payload dict on success; ``None`` on failure.
        success:
            Whether the attempt produced a usable payload.
        error:
            Human-readable error description on failure; ``None`` on success.
        """
        record: dict[str, Any] = {
            "date_key": date_key,
            "attempt": attempt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "raw_response": raw_response,
            "parsed": parsed,
            "error": error,
        }
        path = self._dir / f"{date_key}.jsonl"
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ── read ───────────────────────────────────────────────────────────────

    def load(self, date_key: str) -> list[dict[str, Any]]:
        """Return all archived records for ``date_key``, oldest first.

        Returns an empty list if no archive file exists.
        """
        path = self._dir / f"{date_key}.jsonl"
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # corrupted line — skip silently
        return records

    def last_success(self, date_key: str) -> dict[str, Any] | None:
        """Return the most recently archived successful payload, or ``None``.

        Useful for replaying a day without re-calling the LLM when a cache
        file was deleted but the archive still exists.
        """
        for rec in reversed(self.load(date_key)):
            if rec.get("success") and rec.get("parsed"):
                return rec["parsed"]  # type: ignore[return-value]
        return None

    def dates_with_success(self) -> list[str]:
        """Return sorted list of date keys that have at least one success."""
        results: list[str] = []
        for p in sorted(self._dir.glob("????-??-??.jsonl")):
            date_key = p.stem
            if self.last_success(date_key) is not None:
                results.append(date_key)
        return results
