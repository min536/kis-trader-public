from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.reporting import candidate_outcome_logger


class CandidateOutcomeLoggerTests(unittest.TestCase):
    def test_append_candidate_outcomes_writes_stage_and_outcome_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "candidate_outcomes_test.jsonl"

            with mock.patch.object(
                candidate_outcome_logger,
                "get_partitioned_log_path",
                return_value=path,
            ):
                ok = candidate_outcome_logger.append_candidate_outcomes(
                    [
                        {
                            "symbol": "005930",
                            "stage_reached": "deep_eval",
                            "selection_outcome": "deep_eval_rejected",
                        }
                    ],
                    ts="2026-05-07T12:00:00+09:00",
                )

            self.assertTrue(ok)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["stage_reached"], "deep_eval")
            self.assertEqual(rows[0]["stage"], "deep_eval")
            self.assertEqual(rows[0]["selection_outcome"], "deep_eval_rejected")
            self.assertEqual(rows[0]["outcome"], "deep_eval_rejected")

    def test_append_candidate_outcomes_accepts_legacy_alias_only_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "candidate_outcomes_test.jsonl"

            with mock.patch.object(
                candidate_outcome_logger,
                "get_partitioned_log_path",
                return_value=path,
            ):
                ok = candidate_outcome_logger.append_candidate_outcomes(
                    [
                        {
                            "symbol": "005930",
                            "stage": "executed",
                            "outcome": "executed",
                        }
                    ],
                    ts="2026-05-07T12:00:00+09:00",
                )

            self.assertTrue(ok)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["stage"], "executed")
            self.assertEqual(rows[0]["stage_reached"], "executed")
            self.assertEqual(rows[0]["outcome"], "executed")
            self.assertEqual(rows[0]["selection_outcome"], "executed")


if __name__ == "__main__":
    unittest.main()
