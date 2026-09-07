"""Payload-simulation tests for the trading_guard app.main execution block.

Both hook copies (.claude and .codex) must block starting the trading bot,
including via the repo-root ``main.py`` shim (``from app.main import main``),
without false-positives on unrelated ``*main.py`` filenames. See
docs/master_blueprint_20260704.md §16 F-07 / BP-14 S1.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATHS = [
    REPO_ROOT / ".claude" / "hooks" / "trading_guard.py",
    REPO_ROOT / ".codex" / "hooks" / "trading_guard.py",
]


def _run_hook(hook_path: Path, command: str) -> str:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _is_blocked(stdout: str) -> bool:
    if not stdout:
        return False
    try:
        decision = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    # .claude hook: {"decision": "block"}; .codex hook:
    # {"hookSpecificOutput": {"permissionDecision": "deny"}}.
    if decision.get("decision") == "block":
        return True
    hook_out = decision.get("hookSpecificOutput")
    return isinstance(hook_out, dict) and hook_out.get("permissionDecision") == "deny"


# Commands that MUST be blocked (starting the trading bot).
BLOCK_COMMANDS = [
    "python main.py",              # repo-root shim (the F-07 gap)
    ".venv/bin/python main.py",    # venv interpreter + shim
    "python3 main.py",             # versioned interpreter + shim
    "python ./main.py",            # ./-prefixed shim
    "cd /path/to/kis-trader && python main.py",  # chained
    "python -m app.main",          # regression: existing block
    "python app/main.py",          # regression: existing block
    "scripts/run_session.sh",      # regression: session start
    # Option-flag insertion must not bypass the guard (found by adversarial
    # probe 2026-07-05; the -u form is exactly how long-running jobs invoke it).
    "python -u main.py",
    "python -u -m app.main",
    "python -O app/main.py",
    "python -u -O main.py",
]

# Commands that MUST NOT be blocked (false-positive guard / legit dev loop).
ALLOW_COMMANDS = [
    "python main_test.py",         # different file, not the shim
    "python foo_main.py",          # main.py as suffix of another name
    "python pkg/main.py",          # a main.py in a subdir, not the root shim
    "python -m pytest tests/",     # normal test run
    "cat main.py",                 # reading the shim is fine
    "python -m app.tools.orchestrate",  # a tools module, not app.main
    "python -u -m app.tools.run_gate2_weight_search --records r.parquet",  # flagged tools run
    "python -X importtime -m pytest",   # option with separate value token
]


@pytest.mark.parametrize("hook_path", HOOK_PATHS, ids=lambda p: p.parent.parent.name)
@pytest.mark.parametrize("command", BLOCK_COMMANDS)
def test_bot_start_is_blocked(hook_path: Path, command: str) -> None:
    assert _is_blocked(_run_hook(hook_path, command)), (
        f"{hook_path.parent.parent.name} hook failed to block: {command!r}"
    )


@pytest.mark.parametrize("hook_path", HOOK_PATHS, ids=lambda p: p.parent.parent.name)
@pytest.mark.parametrize("command", ALLOW_COMMANDS)
def test_legit_command_is_not_blocked(hook_path: Path, command: str) -> None:
    assert not _is_blocked(_run_hook(hook_path, command)), (
        f"{hook_path.parent.parent.name} hook false-positive blocked: {command!r}"
    )
