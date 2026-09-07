"""Tests for app.notifications.snapshot_render + snapshot_shared additions (R3-S4).

포맷 헬퍼 + positions 읽기/렌더 클러스터를 runtime_status_snapshot에서 verbatim
이동; 공유 프리미티브(_parse_timestamp, RuntimeSnapshotReadResult)는
snapshot_shared로 하향. 기존 모듈은 facade로 동일 객체를 재수출해야 한다
(patch/import seam 보존).
"""

from __future__ import annotations

import unittest

from app.notifications import runtime_status_snapshot, snapshot_render, snapshot_shared

_SHARED_NAMES = (
    "_parse_timestamp",
    "RuntimeSnapshotReadResult",
)

_RENDER_NAMES = (
    "DEFAULT_PERFORMANCE_SUMMARY_MAX_LINES",
    "LIMITED_STATUS_MISSING_TEXT",
    "LIMITED_STATUS_STALE_TEXT",
    "LIMITED_STATUS_UNREADABLE_TEXT",
    "LIMITED_BOTTLENECKS_MISSING_TEXT",
    "LIMITED_BOTTLENECKS_STALE_TEXT",
    "LIMITED_BOTTLENECKS_UNREADABLE_TEXT",
    "LIMITED_HEALTH_MISSING_TEXT",
    "LIMITED_HEALTH_STALE_TEXT",
    "LIMITED_HEALTH_UNREADABLE_TEXT",
    "LIMITED_POSITIONS_MISSING_TEXT",
    "LIMITED_POSITIONS_STALE_TEXT",
    "LIMITED_POSITIONS_UNREADABLE_TEXT",
    "_mapping_from_snapshot",
    "_format_counter_map",
    "_limited_text_for_result",
    "_format_snapshot_time",
    "_format_krw_optional",
    "_format_signed_krw_optional",
    "_first_int_value",
    "_normalize_performance_position",
    "_read_latest_performance_positions",
    "_positions_freshness_line",
    "_render_position_line",
)


class FormatCounterMapTests(unittest.TestCase):
    def test_formats_requested_keys_with_safe_int_coercion(self) -> None:
        counters = {"buy_submitted": 3, "buy_failed": "2", "noise": 9}
        line = snapshot_render._format_counter_map(
            counters, ("buy_submitted", "buy_failed", "missing")
        )
        self.assertEqual(line, "buy_submitted=3, buy_failed=2, missing=0")


class FormatSignedKrwOptionalTests(unittest.TestCase):
    def test_non_numeric_value_returns_none(self) -> None:
        self.assertIsNone(snapshot_render._format_signed_krw_optional("n/a"))
        self.assertIsNone(snapshot_render._format_signed_krw_optional(object()))


class ReadLatestPerformancePositionsTests(unittest.TestCase):
    def test_skips_invalid_and_blank_tail_lines_and_sorts_by_symbol(self) -> None:
        import json
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path

        now = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "performance_summary.jsonl"
            fresh_line = json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "positions": [
                        {"symbol": "005930", "holding_qty": 1},
                        {"symbol": "000100", "holding_qty": 2},
                    ],
                }
            )
            path.write_text(
                fresh_line + "\n\n{not json\n",
                encoding="utf-8",
            )
            positions = snapshot_render._read_latest_performance_positions(
                path=path, now=now, stale_after_sec=900
            )
            self.assertEqual(
                [pos["symbol"] for pos in positions],
                ["000100", "005930"],
            )

    def test_only_scans_bounded_tail_of_summary_log(self) -> None:
        import json
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path

        now = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "performance_summary.jsonl"
            fresh_line = json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "positions": [{"symbol": "005930", "holding_qty": 1}],
                }
            )
            path.write_text(
                fresh_line + "\n{not json\n{not json either\n",
                encoding="utf-8",
            )
            positions = snapshot_render._read_latest_performance_positions(
                path=path, now=now, stale_after_sec=900, max_lines=2
            )
            self.assertEqual(positions, [])

    def test_none_path_resolves_account_scoped_summary_path(self) -> None:
        import json
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from unittest import mock

        from app.auth import account_scope

        now = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "performance_summary.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "positions": [{"symbol": "005930", "holding_qty": 1}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                account_scope, "get_performance_summary_path", return_value=path
            ):
                positions = snapshot_render._read_latest_performance_positions(
                    path=None, now=now, stale_after_sec=900
                )
            self.assertEqual(
                [pos["symbol"] for pos in positions],
                ["005930"],
            )

    def test_unreadable_path_returns_empty_instead_of_raising(self) -> None:
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path

        now = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir)
            positions = snapshot_render._read_latest_performance_positions(
                path=directory_path, now=now, stale_after_sec=900
            )
            self.assertEqual(positions, [])


class SnapshotRenderFacadeTests(unittest.TestCase):
    def test_runtime_status_snapshot_binds_canonical_objects(self) -> None:
        for name in _SHARED_NAMES:
            with self.subTest(f"shared:{name}"):
                self.assertIs(
                    getattr(runtime_status_snapshot, name),
                    getattr(snapshot_shared, name),
                )
        for name in _RENDER_NAMES:
            with self.subTest(f"render:{name}"):
                self.assertIs(
                    getattr(runtime_status_snapshot, name),
                    getattr(snapshot_render, name),
                )


if __name__ == "__main__":
    unittest.main()
