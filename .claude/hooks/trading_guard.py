#!/usr/bin/env python3
"""Claude Code safety guard for kis-trader."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


PROTECTED_PATH_RE = re.compile(
    r"(^|[\s'\"=:/])(\.env|\.token_cache\.json|token\.dat|token\.json|access_token)([\s'\";:&|/]|$)"
)
# Repo-root data dirs only: nested dirs like docs/archive/ or .claude/tdd-guard/data/
# are legitimate reads, so the dir must appear at the start of a path token
# (bare, ./-prefixed, or an absolute path into this repo).
ARCHIVE_FULL_READ_RE = re.compile(
    r"(^|[;&|]\s*)(cat|grep|rg|find|ls|du)\b[^;&|]*"
    r"[\s'\"=]((\./)|(/\S*?/kis-trader/))?(data|logs|results|archive)(/|['\"\s]|$)"
)
LEGACY_CACHE_VARIANT_RE = re.compile(
    r"(^|[\s'\"=:/])(\." + r"token_cache_[A-Za-z0-9_-]+\.json)([\s'\";:&|/]|$)"
)
# Blocks starting the trading bot: ``-m app.main``, ``app/main.py``, and the
# repo-root ``main.py`` shim (``from app.main import main``). ``(\./)?main\.py``
# only matches a bare root-level main.py — ``foo_main.py`` / ``pkg/main.py`` /
# ``main_test.py`` are not root shims and stay unblocked.
# ``(?:-\S+\s+)*`` lets interpreter option flags (``-u``, ``-O``, ...) appear
# before the target without bypassing the guard; backtracking still lets
# ``-m app.main`` match as a unit, and ``-m pytest``/``-m app.tools.*`` stay
# unblocked because only ``app.main`` is a blocked module target.
APP_MAIN_RE = re.compile(
    r"(^|[;&|]\s*)(uv\s+run\s+)?(\S*/)?python[\d.]*\s+(?:-\S+\s+)*"
    r"(-m\s+app\.main|app/main\.py|(\./)?main\.py)\b"
)
RUN_SESSION_RE = re.compile(
    r"(^|[;&|]\s*|\b(?:bash|sh|zsh)\s+)(\./)?(scripts/)?run_session\.sh\b"
)


# main.py Boundary guard: app/main.py must stay a thin entrypoint, so new
# top-level helpers belong in a module + import, not in main.py itself.
MAIN_PY_PATH_RE = re.compile(r"(^|/)app/main\.py$")
MAIN_PY_TOPLEVEL_DEF_RE = re.compile(r"(?m)^(?:async[ \t]+)?def[ \t]+\w+|^class[ \t]+\w+")
MAIN_PY_ADDED_DEF_RE = re.compile(r"(?m)^\+(?:async[ \t]+)?def[ \t]+\w+|^\+class[ \t]+\w+")
MAIN_PY_REMOVED_DEF_RE = re.compile(r"(?m)^-(?:async[ \t]+)?def[ \t]+\w+|^-class[ \t]+\w+")
MAIN_PY_ALLOW_HELPER = "main.py-boundary: allow-helper"
MAIN_PY_BOUNDARY_REASON = (
    'app/main.py is a thin entrypoint (CLAUDE.md "main.py Boundary"): do not add a new '
    "top-level def/class helper here. Put the logic in an app/<package>/ module and import it "
    "(presentation->app/reporting|app/runtime, formatting->app/core/formatters.py, builders->"
    "owning package, API budget->app/core/runtime_budget.py). A deliberate, reviewed exception "
    "may include the marker 'main.py-boundary: allow-helper' in the edit."
)


def _count_main_py_toplevel_defs(text: str) -> int:
    return len(MAIN_PY_TOPLEVEL_DEF_RE.findall(text))


def _main_py_net_new_defs(tool_input: Any) -> int:
    """Net-new top-level def/class additions targeting app/main.py (0 otherwise)."""
    if not isinstance(tool_input, dict):
        return 0

    # apply_patch / unified-diff style edits carry the path inside the patch body.
    patch = tool_input.get("patch")
    if isinstance(patch, str) and "app/main.py" in patch:
        if MAIN_PY_ALLOW_HELPER in patch:
            return 0
        added = len(MAIN_PY_ADDED_DEF_RE.findall(patch))
        removed = len(MAIN_PY_REMOVED_DEF_RE.findall(patch))
        return max(0, added - removed)

    file_path = ""
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            file_path = value
            break
    if not MAIN_PY_PATH_RE.search(file_path):
        return 0

    # MultiEdit: sum net-new across edits.
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        net = 0
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            new = str(edit.get("new_string") or "")
            if MAIN_PY_ALLOW_HELPER in new:
                continue
            old = str(edit.get("old_string") or "")
            net += _count_main_py_toplevel_defs(new) - _count_main_py_toplevel_defs(old)
        return max(0, net)

    # Write: compare full new content against the file currently on disk.
    content = tool_input.get("content")
    if isinstance(content, str):
        if MAIN_PY_ALLOW_HELPER in content:
            return 0
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                existing = handle.read()
        except OSError:
            existing = ""
        return max(0, _count_main_py_toplevel_defs(content) - _count_main_py_toplevel_defs(existing))

    # Edit: net-new top-level defs introduced by new_string vs old_string.
    new = str(tool_input.get("new_string") or "")
    if MAIN_PY_ALLOW_HELPER in new:
        return 0
    old = str(tool_input.get("old_string") or "")
    return max(0, _count_main_py_toplevel_defs(new) - _count_main_py_toplevel_defs(old))


def _deny(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _load_payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _command_text(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd", "patch"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)


def _path_text(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    parts: list[str] = []
    for key in ("file_path", "path", "paths"):
        value = tool_input.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(parts)


def main() -> None:
    payload = _load_payload()
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input", {})
    text = f"{_command_text(tool_input)} {_path_text(tool_input)}"

    if PROTECTED_PATH_RE.search(text) or LEGACY_CACHE_VARIANT_RE.search(text):
        _deny("Protected credential/token files must not be read, printed, or edited by Claude Code.")

    if tool_name == "Bash":
        command = _command_text(tool_input)
        if APP_MAIN_RE.search(command):
            _deny("Direct app.main execution is blocked. Do not start the trading bot from Claude Code.")
        if RUN_SESSION_RE.search(command):
            _deny("Direct run_session.sh execution is blocked. Do not start trading sessions from Claude Code.")
        if ARCHIVE_FULL_READ_RE.search(command) and not re.search(r"(^|[;&|]\s*)(head|tail)\b", command):
            _deny("Full reads of archive/log data are blocked. Use a narrow head/tail sample only.")

    if _main_py_net_new_defs(tool_input) > 0:
        _deny(MAIN_PY_BOUNDARY_REASON)


if __name__ == "__main__":
    main()
