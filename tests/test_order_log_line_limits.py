"""주문로그/스냅샷 라인 비대 → 매수 fail-closed 차단 해결.

docs/order_log_line_limit_design_20260707.md 의 S2(R1 판독 헤드룸)·S3(W1 주문로그
인베리언트)·S4(W2 스냅샷 인베리언트) 핀 테스트.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.core.jsonl import read_jsonl_objects
from app.core.order_log import OrderLogReadError, _read_log_lines_cached


def _write_line(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def _order_record(pad_bytes: int) -> dict:
    return {
        "timestamp": "2026-07-07T10:00:00+09:00",
        "action": "order_submitted",
        "symbol": "005930",
        "raw_response": {"filler": "x" * pad_bytes},
    }


class OrderLogReadHeadroomTests(unittest.TestCase):
    """S2: 기존에 쌓인 1.3MB 오염 라인이 재시작 즉시 판독되어 매수 차단이 풀려야 한다."""

    def test_line_between_1mb_and_headroom_is_read_strict(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            _write_line(path, _order_record(1_300_000))
            self.assertGreater(path.stat().st_size, 1_000_000)
            records = _read_log_lines_cached(path, strict=True)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["action"], "order_submitted")

    def test_line_above_headroom_still_fails_closed(self) -> None:
        # fail-closed 시맨틱 유지: 헤드룸(8MB)을 넘는 라인은 여전히 차단.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            _write_line(path, _order_record(9_000_000))
            with self.assertRaises(OrderLogReadError) as ctx:
                _read_log_lines_cached(path, strict=True)
            self.assertEqual(ctx.exception.reason_code, "order_log_too_large")


class JsonlReadHeadroomTests(unittest.TestCase):
    """S2: cycle_snapshots 판독은 헤드룸 옵트인, 기본값은 하위호환(1MB) 유지."""

    def test_default_still_drops_oversize_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snap.jsonl"
            _write_line(path, {"cycle_id": "x", "filler": "y" * 3_000_000})
            records, errors = read_jsonl_objects(path)
            self.assertEqual(records, [])
            self.assertTrue(errors)

    def test_max_line_bytes_param_allows_large_snapshot_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snap.jsonl"
            _write_line(path, {"cycle_id": "x", "filler": "y" * 3_000_000})
            records, errors = read_jsonl_objects(path, max_line_bytes=8_000_000)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["cycle_id"], "x")
            self.assertEqual(errors, [])


class SnapshotReaderHeadroomTests(unittest.TestCase):
    """S2: cycle_snapshots 로더가 헤드룸으로 3MB 오염 스냅샷 라인을 복원해야 한다."""

    def test_load_recent_cycle_snapshots_reads_oversize_line(self) -> None:
        from unittest import mock

        from app.reporting import cycle_snapshots

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            _write_line(path, {"cycle_id": "c1", "filler": "z" * 3_000_000})
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                snapshots = cycle_snapshots.load_recent_cycle_snapshots(limit=5)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["cycle_id"], "c1")


class OrderLogWriteInvariantTests(unittest.TestCase):
    """S3(W1): writer가 판독기 계약을 강제 — soft cap 초과 라인은 애초에 안 쓴다."""

    def _oversize_raw_response(self) -> dict:
        return {
            # 소비자(성과 리포터)가 읽는 작은 필드 — 보존되어야 함
            "position_sizing": {"recommended_notional_krw": 5_000_000},
            "reference_price_krw": 70_000,
            "fill_price_krw": 70_100,
            # 벌크 — 잘려야 함
            "selection_details": {
                "candidates": [
                    {"feature_vector": {f"f{i}": i for i in range(200)}}
                    for _ in range(200)
                ]
            },
        }

    def test_oversize_order_log_line_is_truncated_and_readable(self) -> None:
        from unittest import mock

        from app.core import order_log
        from app.core.order_log import ORDER_LOG_LINE_SOFT_MAX_BYTES

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            with mock.patch.object(order_log, "get_order_log_path", return_value=path):
                ok = order_log.log_order_event(
                    symbol="005930",
                    qty=0,
                    order_type="market_buy",
                    confirm_buy="NO",
                    market_open=True,
                    action="blocked_buy_order_log_untrusted",
                    result="skipped",
                    reason="test",
                    raw_response=self._oversize_raw_response(),
                    environment="mock",
                )
            self.assertTrue(ok)
            # 기록된 라인은 soft cap 이하 (판독 8MB 캡보다 훨씬 작아 항상 판독 가능)
            self.assertLessEqual(path.stat().st_size, ORDER_LOG_LINE_SOFT_MAX_BYTES)
            # strict 재판독 성공 (fail-closed 미발동)
            records = order_log._read_log_lines_cached(path, strict=True)
            self.assertEqual(len(records), 1)
            raw = records[0]["raw_response"]
            self.assertTrue(raw.get("truncated"))
            self.assertIn("original_bytes", raw)
            # 소비자 필드 보존
            self.assertEqual(raw.get("position_sizing"), {"recommended_notional_krw": 5_000_000})
            self.assertEqual(raw.get("reference_price_krw"), 70_000)
            self.assertEqual(raw.get("fill_price_krw"), 70_100)
            # 벌크는 드롭 + 어떤 키가 얼마나 잘렸는지 마커
            self.assertIn("selection_details", raw.get("dropped_keys", {}))

    def test_normal_order_log_line_is_untouched(self) -> None:
        from unittest import mock

        from app.core import order_log

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            payload = {"position_sizing": {"recommended_notional_krw": 1_000_000}}
            with mock.patch.object(order_log, "get_order_log_path", return_value=path):
                order_log.log_order_event(
                    symbol="005930",
                    qty=1,
                    order_type="market_buy",
                    confirm_buy="YES",
                    market_open=True,
                    action="order_submitted",
                    result="skipped",
                    reason="ok",
                    raw_response=payload,
                    environment="mock",
                )
            records = order_log._read_log_lines_cached(path, strict=True)
            self.assertEqual(records[0]["raw_response"], payload)
            self.assertNotIn("truncated", records[0]["raw_response"])


class SnapshotWriteInvariantTests(unittest.TestCase):
    """S4(W2): persist_cycle_snapshot도 판독 계약을 강제한다."""

    def _oversize_snapshot(self) -> dict:
        return {
            "cycle_id": "c1",
            "selection_details": {
                "selected_symbol": "005930",
                "candidates_schema": "compact_v1",
                "candidates": [
                    {"symbol": f"{i:06d}", "blob": "q" * 400} for i in range(3000)
                ],
            },
            "scanner_candidates_top": [{"symbol": "005930", "score": 4.0}],
        }

    def test_oversize_snapshot_drops_candidates_but_keeps_top(self) -> None:
        from unittest import mock

        from app.reporting import cycle_snapshots
        from app.reporting.cycle_snapshots import SNAPSHOT_LINE_SOFT_MAX_BYTES

        snap = self._oversize_snapshot()
        # 프리컨디션: 원본은 soft cap을 넘는다
        self.assertGreater(
            len(json.dumps(snap, ensure_ascii=False, default=str).encode("utf-8")),
            SNAPSHOT_LINE_SOFT_MAX_BYTES,
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                ok = cycle_snapshots.persist_cycle_snapshot(snap)
                self.assertTrue(ok)
                self.assertLessEqual(path.stat().st_size, SNAPSHOT_LINE_SOFT_MAX_BYTES)
                loaded = cycle_snapshots.load_recent_cycle_snapshots(limit=5)
        self.assertEqual(len(loaded), 1)
        record = loaded[0]
        self.assertTrue(record.get("selection_details_truncated"))
        self.assertNotIn("candidates", record.get("selection_details", {}))
        # top-5 요약은 항상 보존
        self.assertEqual(record.get("scanner_candidates_top"), [{"symbol": "005930", "score": 4.0}])

    def test_normal_snapshot_is_untouched(self) -> None:
        from unittest import mock

        from app.reporting import cycle_snapshots

        snap = {
            "cycle_id": "c2",
            "selection_details": {"candidates": [{"symbol": "005930"}]},
            "scanner_candidates_top": [{"symbol": "005930"}],
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cycle_snapshots.jsonl"
            with mock.patch.object(
                cycle_snapshots, "get_cycle_snapshots_path", return_value=path
            ):
                cycle_snapshots.persist_cycle_snapshot(snap)
                loaded = cycle_snapshots.load_recent_cycle_snapshots(limit=5)
        self.assertEqual(loaded[0]["selection_details"], {"candidates": [{"symbol": "005930"}]})
        self.assertNotIn("selection_details_truncated", loaded[0])


if __name__ == "__main__":
    unittest.main()
