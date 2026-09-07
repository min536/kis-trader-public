"""F1 — scheduler_state 스냅샷 다이어트 (E1).

설계: docs/daily_error_triage_design_20260707.md §2.

실측 근거: scheduler_state.lane_scheduler_runtime_context.buy_scan_outcome 가
사이클당 2.03MB(스냅샷 라인의 89%). 영속 스냅샷 소비처 없음. top-5 요약은 이미
top-level scanner_candidates_top에 별도 존재. → raw 스캔 페이로드를 크기 기준으로
드롭하되 소비 키(decision/*_due/*_check_at)는 보존.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.reporting import cycle_snapshots
from app.reporting.cycle_snapshots import (
    SNAPSHOT_LINE_SOFT_MAX_BYTES,
    compact_scheduler_state_for_snapshot,
)


def _sz(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))


class SchedulerStateCompactionTests(unittest.TestCase):
    def _bulky_state(self) -> dict:
        return {
            "decision": "BUY_SCAN",
            "last_sell_check_at": "2026-07-08T15:28:00",
            "last_buy_scan_at": "2026-07-08T15:28:00",
            "sell_check_due": True,
            "buy_scan_due": False,
            "cycle_id": "c1",
            "lane_scheduler_runtime_context": {
                "session_status": {"session": "REGULAR"},
                "timing_summary": {"scan_ms": 12},
                "buy_scan_outcome": {
                    "candidates": [{"symbol": f"{i:06d}", "blob": "q" * 400} for i in range(3000)],
                },
            },
        }

    def test_drops_bulky_runtime_context_payload(self):
        state = self._bulky_state()
        compacted = compact_scheduler_state_for_snapshot(state)
        # 벌크 payload 제거로 라인이 대폭 축소
        self.assertLess(_sz(compacted), 65_536)
        rc = compacted["lane_scheduler_runtime_context"]
        self.assertNotIn("buy_scan_outcome", rc.get("buy_scan_outcome", {}) and rc or {})
        # 작은 형제 키는 보존
        self.assertEqual(rc.get("session_status"), {"session": "REGULAR"})
        self.assertEqual(rc.get("timing_summary"), {"scan_ms": 12})
        # 드롭 기록
        self.assertIn("_compacted_dropped", compacted)
        self.assertTrue(any("buy_scan_outcome" in d["path"] for d in compacted["_compacted_dropped"]))

    def test_preserves_consumer_keys(self):
        compacted = compact_scheduler_state_for_snapshot(self._bulky_state())
        for key, expected in (
            ("decision", "BUY_SCAN"),
            ("sell_check_due", True),
            ("buy_scan_due", False),
            ("last_sell_check_at", "2026-07-08T15:28:00"),
            ("last_buy_scan_at", "2026-07-08T15:28:00"),
        ):
            self.assertEqual(compacted.get(key), expected)

    def test_small_state_value_equal_and_no_marker(self):
        small = {
            "decision": "IDLE",
            "sell_check_due": False,
            "lane_scheduler_runtime_context": {"session_status": {"session": "CLOSED"}},
        }
        compacted = compact_scheduler_state_for_snapshot(small)
        self.assertEqual(
            json.loads(json.dumps(compacted, default=str)),
            json.loads(json.dumps(small, default=str)),
        )
        self.assertNotIn("_compacted_dropped", compacted)

    def test_never_mutates_input(self):
        state = self._bulky_state()
        before = json.dumps(state, default=str)
        compact_scheduler_state_for_snapshot(state)
        self.assertEqual(json.dumps(state, default=str), before)

    def test_non_dict_returns_empty(self):
        self.assertEqual(compact_scheduler_state_for_snapshot(None), {})
        self.assertEqual(compact_scheduler_state_for_snapshot("nope"), {})


class ScanCyclePersistRegressionTests(unittest.TestCase):
    """회귀 핀: 실측형 스캔 사이클 스냅샷이 soft cap 아래로 영속된다."""

    def _scan_snapshot(self) -> dict:
        return {
            "cycle_id": "20260708T152857",
            "scanner_candidates_top": [{"symbol": "005930", "score": 4.0}],
            "scheduler_state": {
                "decision": "BUY_SCAN",
                "buy_scan_due": True,
                "lane_scheduler_runtime_context": {
                    "session_status": {"session": "REGULAR"},
                    "buy_scan_outcome": {
                        "candidates": [
                            {"symbol": f"{i:06d}", "blob": "z" * 700} for i in range(3000)
                        ],
                    },
                },
            },
        }

    def test_scan_cycle_persisted_under_soft_cap(self):
        snap = self._scan_snapshot()
        # 프리컨디션: 원본은 soft cap 초과 (2MB+)
        self.assertGreater(_sz(snap), SNAPSHOT_LINE_SOFT_MAX_BYTES)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                ok = cycle_snapshots.persist_cycle_snapshot(snap)
                self.assertTrue(ok)
                self.assertLessEqual(path.stat().st_size, SNAPSHOT_LINE_SOFT_MAX_BYTES)
                loaded = cycle_snapshots.load_recent_cycle_snapshots(limit=1)
        record = loaded[0]
        # 소비 키 + top-5 요약 보존
        self.assertEqual(record["scheduler_state"]["decision"], "BUY_SCAN")
        self.assertEqual(record["scanner_candidates_top"], [{"symbol": "005930", "score": 4.0}])

    def test_persist_does_not_mutate_caller_snapshot(self):
        snap = self._scan_snapshot()
        rc = snap["scheduler_state"]["lane_scheduler_runtime_context"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                cycle_snapshots.persist_cycle_snapshot(snap)
        # 호출자의 in-memory 스냅샷은 온전 (raw payload 그대로)
        self.assertIn("buy_scan_outcome", rc)


class GeneralizedShrinkTests(unittest.TestCase):
    """2차 방어: 벌크가 candidates/scheduler 밖 다른 top-level 키로 이동해도 캡 준수."""

    def test_drops_largest_nonessential_top_key(self):
        snap = {
            "cycle_id": "c9",
            "scanner_candidates_top": [{"symbol": "005930"}],
            "rogue_debug_blob": ["x" * 700 for _ in range(3000)],  # ~2MB, 비필수
        }
        self.assertGreater(_sz(snap), SNAPSHOT_LINE_SOFT_MAX_BYTES)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                ok = cycle_snapshots.persist_cycle_snapshot(snap)
                self.assertTrue(ok)
                self.assertLessEqual(path.stat().st_size, SNAPSHOT_LINE_SOFT_MAX_BYTES)
                loaded = cycle_snapshots.load_recent_cycle_snapshots(limit=1)
        record = loaded[0]
        self.assertNotIn("rogue_debug_blob", record)
        self.assertEqual(record.get("scanner_candidates_top"), [{"symbol": "005930"}])
        self.assertTrue(
            any(d["key"] == "rogue_debug_blob" for d in record.get("oversize_dropped_keys", []))
        )


if __name__ == "__main__":
    unittest.main()
