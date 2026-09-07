"""Guard test: every ``note(...)`` call in app/main.py must pass exactly 2 args.

The ``note`` closure defined in ``run_cycle`` has the signature
``note(level: str, message: str)``. A trailing comma inside an f-string
concatenation can silently turn a single message into two arguments
(``note("INFO", "...", "...")``) which then fails at runtime with
``takes 2 positional arguments but 3 were given``, aborting the cycle.

This AST-level guard catches that regression at test time so a broken
``note(...)`` call never makes it to a live trading cycle again.
"""

from __future__ import annotations

import ast
import pathlib
import unittest


MAIN_PY = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


class NoteCallArityTests(unittest.TestCase):
    def test_every_note_call_passes_exactly_two_arguments(self) -> None:
        tree = ast.parse(MAIN_PY.read_text())
        violations: list[tuple[int, int, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "note"):
                continue
            # Allow **kwargs / *args only if total surface matches 2.
            total = len(node.args) + len(node.keywords)
            if total != 2:
                violations.append((node.lineno, len(node.args), len(node.keywords)))
        self.assertEqual(
            violations,
            [],
            "note() must be called with exactly (level, message). "
            "Offenders (line, positional, keyword): " + repr(violations),
        )


if __name__ == "__main__":
    unittest.main()
