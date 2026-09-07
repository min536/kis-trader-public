"""Probe helper for the G2 real-path write watcher (see test_conftest_isolation_guard).

Under a normal suite run this is an inert no-op. Only when the parent
positive-detection test sets ``KIS_LEAK_PROBE_TOKEN`` does it deliberately write
to the repo's real ``logs/`` — bypassing the ``state_root()`` redirect — so the
watcher has something to catch. The parent test cleans the probe file up.
"""

import os
from pathlib import Path

from app.auth.settings import PROJECT_ROOT


def test_state_leak_probe():
    token = os.environ.get("KIS_LEAK_PROBE_TOKEN", "").strip()
    if not token:
        # Normal suite run — do not touch real paths.
        return
    target: Path = PROJECT_ROOT / "logs" / f"_leak_probe_{token}.jsonl"
    target.write_text("probe\n", encoding="utf-8")
