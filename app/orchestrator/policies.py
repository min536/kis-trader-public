"""Deterministic policy guards for orchestrator workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

# Files that must never be read by a collector, matched by basename.
_BLOCKED_NAMES = frozenset({".env", ".token_cache.json"})
# Any path with a component named exactly this is an archive subtree (full reads
# of archived logs/results are out of scope for read-only collectors).
_BLOCKED_PART = "archive"
_BLOCKED_NAMES = _BLOCKED_NAMES | frozenset({"." + "token_cache_live.json"})


def assert_write_allowed(path: PathLike, read_only: bool = True) -> None:
    """Block writes while in read-only mode.

    Raises ``PermissionError`` when ``read_only`` is True so callers cannot
    accidentally write under the project tree during a read-only run.
    """

    if read_only:
        raise PermissionError(f"write blocked in read-only mode: {path}")


def assert_read_allowed(path: PathLike) -> None:
    """Block reads of protected files and archived subtrees.

    Raises ``PermissionError`` for ``.env``, ``.token_cache.json``, and any path
    that descends through an ``archive`` directory. Everything else — normal
    current-day ``logs/``/``data/``/``results/``/``docs/`` files — is allowed, so
    collectors can read what they need without re-deriving the policy each time.
    """

    p = Path(path)
    if p.name in _BLOCKED_NAMES:
        raise PermissionError(f"read blocked (protected file): {path}")
    if _BLOCKED_PART in p.parts:
        raise PermissionError(f"read blocked (archive subtree): {path}")
    return None
