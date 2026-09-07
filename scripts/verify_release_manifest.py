"""Verify the exact reviewed snapshot without reading unlisted files.

This is an integrity check, not a replacement for secret or provenance review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify(root: Path) -> list[str]:
    manifest_path = root / "PUBLIC_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest["files"]
    failures: list[str] = []
    expected = {"PUBLIC_MANIFEST.json"}
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            failures.append("unsafe manifest path")
            continue
        expected.add(relative.as_posix())
        path = root / relative
        if row["kind"] == "symlink":
            if not path.is_symlink() or str(path.readlink()) != row["target"]:
                failures.append(f"changed symlink: {relative}")
            elif not path.resolve().is_relative_to(root):
                failures.append(f"external symlink: {relative}")
            continue
        if path.is_symlink() or not path.is_file():
            failures.append(f"missing or changed type: {relative}")
            continue
        if not path.resolve().is_relative_to(root):
            failures.append(f"external file: {relative}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            failures.append(f"changed content: {relative}")
    if (root / ".git").exists():
        tracked = set(subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"], text=True
        ).split("\0")) - {""}
        for extra in sorted(tracked - expected):
            failures.append(f"unreviewed tracked file: {extra}")
        for absent in sorted(expected - tracked):
            failures.append(f"manifest file not tracked: {absent}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures = verify(root)
    for failure in failures:
        print(failure)
    if failures:
        print(f"FAIL: {len(failures)} integrity findings")
        return 1
    print("PASS: reviewed source manifest matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
