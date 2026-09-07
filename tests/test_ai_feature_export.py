"""Unit tests for backtester.ai_integration.feature_export.

Step 1 deliverables:
* CandidateFeatureExporter writes well-formed JSONL rows with the exact
  feature dict handed in by the caller (no recomputation).
* FeatureAuditMixin raises LookAheadBiasError on leaked timestamps.
* The exporter module itself is pure; no runner / env dependencies.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime

from backtester.ai_integration import (
    CandidateFeatureExporter,
    FeatureAuditMixin,
    LookAheadBiasError,
)


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip().splitlines()


class CandidateFeatureExporterTests(unittest.TestCase):
    def _read_lines(self, path: str) -> list[str]:
        return _read_lines(path)

    def test_writes_expected_jsonl_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "features.jsonl")
            components = {
                "passed_count_base": 3.0,
                "pullback_pct": 1.42,
                "rebound_pct": 2.11,
                "overheat_penalty": 0.0,
            }
            with CandidateFeatureExporter(out, audit=False) as exp:
                exp.record(
                    trading_date=date(2024, 3, 15),
                    ticker="005930",
                    base_score=4.73,
                    score_components=components,
                    decision="approved",
                    decision_reason="executed",
                    candidate_rank=1,
                    executed=True,
                )
                exp.record(
                    trading_date=date(2024, 3, 15),
                    ticker="000660",
                    base_score=1.10,
                    score_components={"passed_count_base": 1.0},
                    decision="rejected",
                    decision_reason="score_below_min",
                    score_gate_passed=False,
                )

            lines = self._read_lines(out)
            self.assertEqual(len(lines), 2)
            row0 = json.loads(lines[0])
            self.assertEqual(row0["date"], "2024-03-15")
            self.assertEqual(row0["ticker"], "005930")
            self.assertAlmostEqual(row0["base_score"], 4.73)
            self.assertEqual(row0["features"], components)
            self.assertEqual(row0["decision"], "approved")
            self.assertTrue(row0["executed"])
            self.assertEqual(row0["candidate_rank"], 1)

            row1 = json.loads(lines[1])
            self.assertEqual(row1["decision"], "rejected")
            self.assertFalse(row1["score_gate_passed"])
            self.assertFalse(row1["executed"])
            self.assertIsNone(row1["candidate_rank"])

    def test_invalid_decision_value_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = CandidateFeatureExporter(os.path.join(tmp, "f.jsonl"))
            with self.assertRaises(ValueError):
                exp.record(
                    trading_date=date(2024, 1, 2),
                    ticker="X",
                    base_score=0.0,
                    score_components={},
                    decision="maybe",  # type: ignore[arg-type]
                )
            exp.close()

    def test_features_dict_is_copied_not_aliased(self) -> None:
        """Caller should not be able to poison the written record by
        mutating the source dict after `record()` returns."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "f.jsonl")
            components = {"x": 1.0}
            with CandidateFeatureExporter(out, audit=False) as exp:
                exp.record(
                    trading_date=date(2024, 1, 2),
                    ticker="A",
                    base_score=1.0,
                    score_components=components,
                    decision="approved",
                )
                components["x"] = 999.0  # mutate after record
            row = json.loads(_read_lines(out)[0])
            self.assertEqual(row["features"], {"x": 1.0})


class FeatureAuditMixinTests(unittest.TestCase):
    def test_same_day_allowed_by_default(self) -> None:
        auditor = FeatureAuditMixin()
        auditor.audit_features(
            decision_timestamp=date(2024, 3, 15),
            feature_timestamps={"pullback_pct": date(2024, 3, 15)},
        )  # no raise

    def test_future_feature_raises(self) -> None:
        auditor = FeatureAuditMixin()
        with self.assertRaises(LookAheadBiasError) as ctx:
            auditor.audit_features(
                decision_timestamp=date(2024, 3, 15),
                feature_timestamps={"leaked": date(2024, 3, 16)},
            )
        self.assertIn("leaked", str(ctx.exception))

    def test_strict_less_than_rejects_same_day(self) -> None:
        auditor = FeatureAuditMixin()
        with self.assertRaises(LookAheadBiasError):
            auditor.audit_features(
                decision_timestamp=date(2024, 3, 15),
                feature_timestamps={"pullback_pct": date(2024, 3, 15)},
                strict_less_than=True,
            )

    def test_iso_string_timestamps_accepted(self) -> None:
        auditor = FeatureAuditMixin()
        auditor.audit_features(
            decision_timestamp="2024-03-15",
            feature_timestamps={"f": "2024-03-14"},
            strict_less_than=True,
        )

    def test_exporter_audits_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = CandidateFeatureExporter(
                os.path.join(tmp, "f.jsonl"),
                audit=True,
                strict_less_than=True,
            )
            with self.assertRaises(LookAheadBiasError):
                exp.record(
                    trading_date=date(2024, 3, 15),
                    ticker="A",
                    base_score=1.0,
                    score_components={"x": 0.0},
                    decision="approved",
                    feature_timestamps={"x": datetime(2024, 3, 15, 9, 30)},
                )
            exp.close()

    def test_exporter_skips_audit_when_no_timestamps_supplied(self) -> None:
        """Current engine derives features from same-day snapshot and
        does not attach per-feature timestamps. The exporter must not
        block that usage — audit only kicks in when timestamps are
        explicitly provided."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "f.jsonl")
            with CandidateFeatureExporter(out, audit=True) as exp:
                exp.record(
                    trading_date=date(2024, 3, 15),
                    ticker="A",
                    base_score=1.0,
                    score_components={"x": 0.0},
                    decision="approved",
                )
            self.assertEqual(len(_read_lines(out)), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
