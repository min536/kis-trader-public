"""Tests for the Phase A screening CLI (backtest evals -> screening.json).

Globs a directory of run_proposal_backtest eval JSONs and emits a screening.json
that feeds the human-gated ``autotuner_suggest`` tick. Read-only/offline: it never
runs the backtester and never applies anything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.tools.autotuner_screen import main


def _write_eval(path: Path, *, parameter: str, to_value, verdict: str) -> None:
    # Real run_proposal_backtest on-disk shape: changes/verdict live inside the
    # top-level `evaluations` array.
    path.write_text(
        json.dumps(
            {
                "proposal_id": "atp_x",
                "evaluations": [
                    {
                        "changes": [
                            {"parameter": parameter, "from_value": 10, "to_value": to_value}
                        ],
                        "verdict": verdict,
                    }
                ],
                "overall_verdict": verdict,
            }
        ),
        encoding="utf-8",
    )


class ScreenCliTests(unittest.TestCase):
    def test_writes_screening_from_an_evals_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evals = Path(tmp) / "evaluations"
            evals.mkdir()
            _write_eval(evals / "a.json", parameter="buy_scan_shallow_top_k", to_value=12, verdict="pass")
            _write_eval(evals / "b.json", parameter="buy_scan_shallow_top_k", to_value=18, verdict="fail")
            out = Path(tmp) / "screening.json"

            code = main(["--evals-dir", str(evals), "--out", str(out)])

            self.assertEqual(code, 0)
            screening = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(screening), 2)
            by_value = {e["to_value"]: e["verdict"] for e in screening}
            self.assertEqual(by_value, {12: "pass", 18: "fail"})
            self.assertTrue(all("backtest_eval" in e for e in screening))


    def test_missing_evals_dir_errors_without_scanning_cwd(self) -> None:
        # Omitting --evals-dir must NOT silently scan the current directory.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "screening.json"
            code = main(["--out", str(out)])
            self.assertNotEqual(code, 0)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
