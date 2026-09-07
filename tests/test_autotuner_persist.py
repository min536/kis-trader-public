"""Tests for the Phase 2 autotuner persist layer.

The persist layer resolves the D3 ``proposal_id`` sequence and lays out the
``_workspace/autotuner/{proposals,baselines}/`` directories. It performs file
I/O into _workspace only — no runtime state, no trading-critical surface.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.autotuner.persist import (
    next_proposal_id,
    save_baseline,
    workspace_paths,
)


class NextProposalIdTests(unittest.TestCase):
    def test_first_id_for_empty_dir_is_0001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid = next_proposal_id(tmp, "20260604", "mock")
            self.assertEqual(pid, "atp_20260604_mock_0001")

    def test_sequence_increments_past_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "atp_20260604_mock_0001.json").write_text("{}", encoding="utf-8")
            Path(tmp, "atp_20260604_mock_0002.json").write_text("{}", encoding="utf-8")
            pid = next_proposal_id(tmp, "20260604", "mock")
            self.assertEqual(pid, "atp_20260604_mock_0003")

    def test_sequence_handles_five_digit_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "atp_20260604_mock_10000.json").write_text("{}", encoding="utf-8")
            pid = next_proposal_id(tmp, "20260604", "mock")
            self.assertEqual(pid, "atp_20260604_mock_10001")

    def test_sequence_is_scoped_by_date_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # different mode (shadow) and different date must not affect mock/today
            Path(tmp, "atp_20260604_shadow_0009.json").write_text("{}", encoding="utf-8")
            Path(tmp, "atp_20260603_mock_0009.json").write_text("{}", encoding="utf-8")
            pid = next_proposal_id(tmp, "20260604", "mock")
            self.assertEqual(pid, "atp_20260604_mock_0001")


class WorkspacePathsTests(unittest.TestCase):
    def test_workspace_paths_layout_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = workspace_paths(tmp)
            self.assertEqual(
                Path(paths["proposals"]),
                Path(tmp) / "_workspace" / "autotuner" / "proposals",
            )
            self.assertEqual(
                Path(paths["baselines"]),
                Path(tmp) / "_workspace" / "autotuner" / "baselines",
            )


class SaveBaselineTests(unittest.TestCase):
    def test_save_baseline_rejects_path_traversal_id(self) -> None:
        baseline = {
            "baseline_id": "../escape",
            "values": {"buy_scan_shallow_top_k": 10},
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "baselines"
            with self.assertRaises(ValueError):
                save_baseline(baseline, target)

    def test_save_baseline_writes_named_snapshot(self) -> None:
        baseline = {
            "baseline_id": "settings_default_20260604",
            "values": {"buy_scan_shallow_top_k": 10},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = save_baseline(baseline, tmp)
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).name, "settings_default_20260604.json")
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(loaded, baseline)


if __name__ == "__main__":
    unittest.main()
