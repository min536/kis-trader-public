from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterator

DEFAULT_LOCAL_READ_MAX_BYTES = 250_000_000
DEFAULT_LOCAL_LINE_MAX_BYTES = 1_000_000
DEFAULT_TAIL_CHUNK_BYTES = 64 * 1024
LOCAL_READ_MAX_BYTES_ENV = "KIS_LOCAL_READ_MAX_BYTES"
LOCAL_LINE_MAX_BYTES_ENV = "KIS_LOCAL_LINE_MAX_BYTES"


class LocalReadLimitError(RuntimeError):
    """Raised when a local data/log file exceeds configured read limits."""


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def local_read_max_bytes() -> int:
    return _positive_int_env(LOCAL_READ_MAX_BYTES_ENV, DEFAULT_LOCAL_READ_MAX_BYTES)


def local_line_max_bytes() -> int:
    return _positive_int_env(LOCAL_LINE_MAX_BYTES_ENV, DEFAULT_LOCAL_LINE_MAX_BYTES)


def check_file_size(path: Path, *, max_bytes: int | None = None) -> int:
    limit = local_read_max_bytes() if max_bytes is None else int(max_bytes)
    size = path.stat().st_size
    if limit > 0 and size > limit:
        raise LocalReadLimitError(
            f"{path} is {size} bytes, above local read limit {limit} bytes"
        )
    return size


def read_text_bounded(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str | None = None,
    max_bytes: int | None = None,
) -> str:
    check_file_size(path, max_bytes=max_bytes)
    return path.read_text(encoding=encoding, errors=errors)


def iter_lines_bounded(
    path: Path,
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    max_line_bytes: int | None = None,
) -> Iterator[tuple[int, str]]:
    check_file_size(path, max_bytes=max_bytes)
    line_limit = local_line_max_bytes() if max_line_bytes is None else int(max_line_bytes)
    with path.open(encoding=encoding) as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            if line_limit > 0 and len(raw_line.encode(encoding)) > line_limit:
                raise LocalReadLimitError(
                    f"{path} line {lineno} exceeds local line read limit {line_limit} bytes"
                )
            yield lineno, raw_line


def iter_tail_lines_window(
    path: Path,
    *,
    max_lines: int,
    window_bytes: int = 64 * 1024 * 1024,
    encoding: str = "utf-8",
    max_line_bytes: int | None = None,
    on_skip=None,
) -> Iterator[tuple[int, str]]:
    """Tolerant tail reader: read at most ``window_bytes`` from the END of the
    file in a single bounded backward read, drop the first (possibly partial)
    line when the window did not reach file start, and yield up to the last
    ``max_lines`` complete lines as ``(lineno, text)``.

    Unlike ``iter_tail_lines_bounded`` this never raises ``LocalReadLimitError``:
    a line exceeding the per-line limit is SKIPPED (and ``on_skip(lineno,
    byte_len)`` is invoked when supplied) rather than raised. Deterministic; no
    whole-file reads.
    """
    requested_lines = int(max_lines)
    if requested_lines <= 0:
        return

    window_limit = max(1, int(window_bytes))
    line_limit = local_line_max_bytes() if max_line_bytes is None else int(max_line_bytes)

    size = path.stat().st_size
    if size <= 0:
        return

    read_size = min(window_limit, size)
    start = size - read_size
    with path.open("rb") as handle:
        handle.seek(start)
        window = handle.read(read_size)

    reached_file_start = start <= 0
    lines = window.split(b"\n")
    # split on b"\n" yields a trailing empty element when the window ends with a
    # newline (the common jsonl case) — drop it so we only keep real lines.
    if lines and lines[-1] == b"":
        lines.pop()
    if not reached_file_start and lines:
        # The window began mid-file, so the first fragment is a partial line.
        lines.pop(0)

    tail_lines = lines[-requested_lines:]
    for lineno, raw_line in enumerate(tail_lines, start=1):
        if line_limit > 0 and len(raw_line) > line_limit:
            if on_skip is not None:
                on_skip(lineno, len(raw_line))
            continue
        yield lineno, raw_line.decode(encoding)


def read_csv_dicts_bounded(
    path: Path,
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    max_line_bytes: int | None = None,
) -> list[dict[str, str]]:
    """Read a CSV as dictionaries after applying whole-file and line limits."""

    rows, _fieldnames = read_csv_dicts_with_fieldnames_bounded(
        path,
        encoding=encoding,
        max_bytes=max_bytes,
        max_line_bytes=max_line_bytes,
    )
    return rows


def read_csv_dicts_with_fieldnames_bounded(
    path: Path,
    *,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    max_line_bytes: int | None = None,
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    """Read CSV dictionaries and return the header after applying read limits."""

    reader = csv.DictReader(
        raw_line
        for _lineno, raw_line in iter_lines_bounded(
            path,
            encoding=encoding,
            max_bytes=max_bytes,
            max_line_bytes=max_line_bytes,
        )
    )
    fieldnames = tuple(reader.fieldnames or ())
    return list(reader), fieldnames


def iter_tail_lines_bounded(
    path: Path,
    *,
    max_lines: int,
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    max_line_bytes: int | None = None,
    chunk_size: int = DEFAULT_TAIL_CHUNK_BYTES,
) -> Iterator[tuple[int, str]]:
    """Yield up to the last max_lines without applying whole-file size checks."""

    requested_lines = int(max_lines)
    if requested_lines <= 0:
        return

    read_limit = local_read_max_bytes() if max_bytes is None else int(max_bytes)
    chunk_limit = max(1, int(chunk_size))
    tail_read_limit = max(read_limit, chunk_limit) if read_limit > 0 else chunk_limit
    line_limit = local_line_max_bytes() if max_line_bytes is None else int(max_line_bytes)

    size = path.stat().st_size
    if size <= 0:
        return

    chunks: list[bytes] = []
    bytes_read = 0
    position = size
    newline_count = 0

    with path.open("rb") as handle:
        while position > 0:
            read_size = min(chunk_limit, position)
            if bytes_read + read_size > tail_read_limit:
                raise LocalReadLimitError(
                    f"{path} tail window exceeds local read limit "
                    f"{tail_read_limit} bytes for last {requested_lines} lines"
                )
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.insert(0, chunk)
            bytes_read += len(chunk)
            newline_count += chunk.count(b"\n")
            if newline_count > requested_lines:
                break

    lines = b"".join(chunks).splitlines()
    tail_lines = lines[-requested_lines:]
    for lineno, raw_line in enumerate(tail_lines, start=1):
        if line_limit > 0 and len(raw_line) > line_limit:
            raise LocalReadLimitError(
                f"{path} tail line {lineno} exceeds local line read limit "
                f"{line_limit} bytes"
            )
        yield lineno, raw_line.decode(encoding)
