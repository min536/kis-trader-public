"""Unit tests for app.notifications.runtime_status_snapshot.

Tests the core build/write/read/render functions without modifying production code.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.notifications.runtime_status_snapshot import (
    _BOTTLENECK_ACTION_KEYS,
    _ORDER_COUNTER_KEYS,
    RuntimeSnapshotReadResult,
    build_slack_status_snapshot,
    build_smoke_test_snapshot,
    read_slack_status_snapshot,
    render_bottlenecks_snapshot_reply,
    render_health_snapshot_reply,
    render_positions_snapshot_reply,
    render_status_snapshot_reply,
    write_slack_status_snapshot,
    LIMITED_STATUS_MISSING_TEXT,
    LIMITED_STATUS_STALE_TEXT,
    LIMITED_BOTTLENECKS_MISSING_TEXT,
    LIMITED_HEALTH_MISSING_TEXT,
    LIMITED_POSITIONS_MISSING_TEXT,
)


def _make_timestamp(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


def _minimal_runtime_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "last_error_count": 0,
        "rate_limit_hits": 0,
    }
    state.update(overrides)
    return state


def _daily_summary_with_counts(**counts: int) -> SimpleNamespace:
    return SimpleNamespace(action_counts=counts)


def _write_snapshot_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# build_slack_status_snapshot
# ---------------------------------------------------------------------------


class BuildSlackStatusSnapshotTests(unittest.TestCase):
    def test_contains_all_top_level_keys(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        expected_keys = {
            "timestamp",
            "pid",
            "account_signature",
            "masked_account_display",
            "session_status",
            "last_heartbeat",
            "order_counters_today",
            "bottleneck_counters_today",
            "last_order_event",
            "exception_count",
            "daily_summary_sent",
            "positions_snapshot",
            "positions_snapshot_source",
            "positions_synced_at",
            "pending_sell_intents",
            "health",
        }
        self.assertEqual(set(snapshot.keys()), expected_keys)

    def test_order_counters_map_action_names(self) -> None:
        summary = _daily_summary_with_counts(
            order_submitted=3, order_succeeded=2, order_failed=1,
            sell_order_submitted=5, sell_order_succeeded=4, sell_order_failed=0,
        )
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            daily_summary=summary,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        counters = snapshot["order_counters_today"]
        self.assertEqual(counters["buy_submitted"], 3)
        self.assertEqual(counters["buy_succeeded"], 2)
        self.assertEqual(counters["buy_failed"], 1)
        self.assertEqual(counters["sell_submitted"], 5)
        self.assertEqual(counters["sell_succeeded"], 4)
        self.assertEqual(counters["sell_failed"], 0)

    def test_order_counters_all_keys_present(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        for key in _ORDER_COUNTER_KEYS:
            self.assertIn(key, snapshot["order_counters_today"])

    def test_bottleneck_counters_all_keys_present(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        for key in _BOTTLENECK_ACTION_KEYS:
            self.assertIn(key, snapshot["bottleneck_counters_today"])

    def test_session_status_from_explicit_argument(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            session_status="REGULAR",
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["session_status"], "REGULAR")

    def test_session_status_falls_back_to_runtime_state(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(last_market_session="PRE_MARKET"),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["session_status"], "PRE_MARKET")

    def test_session_status_defaults_to_unknown(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["session_status"], "unknown")

    def test_exception_count_uses_max_of_two_sources(self) -> None:
        summary = _daily_summary_with_counts(cycle_error=5)
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(last_error_count=3),
            daily_summary=summary,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["exception_count"], 5)

    def test_exception_count_prefers_runtime_when_higher(self) -> None:
        summary = _daily_summary_with_counts(cycle_error=1)
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(last_error_count=7),
            daily_summary=summary,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["exception_count"], 7)

    def test_positions_snapshot_from_broker_state(self) -> None:
        state = _minimal_runtime_state(
            broker_last_synced_positions_by_symbol={"005930": 10, "000660": 5},
            recent_market_snapshots_by_symbol={
                "005930": {
                    "current_price": 70000,
                    "observed_at": "2026-05-10T09:01:00+09:00",
                }
            },
        )
        snapshot = build_slack_status_snapshot(
            runtime_state=state,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        positions = snapshot["positions_snapshot"]
        self.assertEqual(len(positions), 2)
        positions_by_symbol = {p["symbol"]: p for p in positions}
        self.assertEqual(set(positions_by_symbol), {"005930", "000660"})
        samsung = positions_by_symbol["005930"]
        self.assertEqual(samsung["symbol_name"], "삼성전자")
        self.assertEqual(samsung["current_price"], 70000)
        self.assertEqual(samsung["market_value"], 700000)
        self.assertEqual(samsung["price_observed_at"], "2026-05-10T09:01:00+09:00")
        self.assertIsNone(samsung["avg_price"])
        self.assertIsNone(positions_by_symbol["000660"]["current_price"])

    def test_positions_snapshot_includes_account_metadata_and_sync_timestamp(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(
                account_signature="mock_1234_01",
                masked_account_display="1234***34-01",
                broker_last_synced_at="2026-05-10T09:01:00+09:00",
                broker_last_synced_positions_by_symbol={"005930": 10},
            ),
            timestamp="2026-05-10T09:00:00+09:00",
        )

        self.assertEqual(snapshot["account_signature"], "mock_1234_01")
        self.assertEqual(snapshot["masked_account_display"], "1234***34-01")
        self.assertEqual(snapshot["positions_synced_at"], "2026-05-10T09:01:00+09:00")

    def test_positions_snapshot_empty_when_no_broker_data(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["positions_snapshot"], [])

    def test_health_data_fields(self) -> None:
        state = _minimal_runtime_state(
            rate_limit_hits=3,
            last_rate_limit_source="EGW00201",
            daily_pnl_pause_state="PAUSED",
            daily_pnl_pause_reason="loss_limit",
            last_cycle_elapsed_ms=1500,
            consecutive_backoff_cycles=2,
        )
        snapshot = build_slack_status_snapshot(
            runtime_state=state,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        health = snapshot["health"]
        self.assertEqual(health["rate_limit_hits"], 3)
        self.assertEqual(health["last_rate_limit_source"], "EGW00201")
        self.assertEqual(health["daily_pnl_pause_state"], "PAUSED")
        self.assertEqual(health["daily_pnl_pause_reason"], "loss_limit")
        self.assertEqual(health["last_cycle_elapsed_ms"], 1500)
        self.assertEqual(health["consecutive_backoff_cycles"], 2)

    def test_last_order_event_from_recent_orders(self) -> None:
        state = _minimal_runtime_state(recent_orders=[
            {"timestamp": "2026-05-10T09:30:00+09:00", "side": "BUY",
             "symbol": "005930", "qty": 5, "action": "order_succeeded"},
        ])
        snapshot = build_slack_status_snapshot(
            runtime_state=state,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        last = snapshot["last_order_event"]
        self.assertIsNotNone(last)
        self.assertEqual(last["symbol"], "005930")
        self.assertEqual(last["quantity"], 5)
        self.assertEqual(last["side"], "BUY")

    def test_last_order_event_none_when_no_recent_orders(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertIsNone(snapshot["last_order_event"])

    def test_daily_summary_sent_passed_through(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            daily_summary_sent=True,
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertTrue(snapshot["daily_summary_sent"])

    def test_daily_summary_none_when_omitted(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertIsNone(snapshot["daily_summary_sent"])

    def test_no_daily_summary_object_yields_zero_counters(self) -> None:
        snapshot = build_slack_status_snapshot(
            runtime_state=_minimal_runtime_state(),
            timestamp="2026-05-10T09:00:00+09:00",
        )
        for val in snapshot["order_counters_today"].values():
            self.assertEqual(val, 0)


# ---------------------------------------------------------------------------
# write + read round-trip
# ---------------------------------------------------------------------------


class WriteReadSnapshotTests(unittest.TestCase):
    def test_write_then_read_returns_ok(self, ) -> None:
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            self.assertTrue(write_slack_status_snapshot(data, path=path))
            result = read_slack_status_snapshot(path=path, now=now)
            self.assertEqual(result.status, "ok")
            self.assertIsNotNone(result.snapshot)
            self.assertAlmostEqual(result.age_seconds, 0.0, delta=2.0)

    def test_missing_file_returns_missing(self) -> None:
        result = read_slack_status_snapshot(
            path=Path("/tmp/_nonexistent_test_path_/snapshot.json"),
        )
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.snapshot)

    def test_malformed_json_returns_unreadable(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            path.write_text("NOT JSON", encoding="utf-8")
            result = read_slack_status_snapshot(path=path)
            self.assertEqual(result.status, "unreadable")

    def test_json_without_timestamp_returns_unreadable(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            path.write_text('{"session_status": "REGULAR"}', encoding="utf-8")
            result = read_slack_status_snapshot(path=path)
            self.assertEqual(result.status, "unreadable")

    def test_stale_snapshot_returns_stale(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            old_time = datetime.now(timezone.utc) - timedelta(hours=1)
            data = build_smoke_test_snapshot(timestamp=old_time.isoformat())
            write_slack_status_snapshot(data, path=path)
            result = read_slack_status_snapshot(
                path=path,
                now=datetime.now(timezone.utc),
                stale_after_sec=60,
            )
            self.assertEqual(result.status, "stale")
            self.assertIsNotNone(result.snapshot)
            self.assertGreater(result.age_seconds, 3500)

    def test_json_array_returns_unreadable(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            path.write_text("[1,2,3]", encoding="utf-8")
            result = read_slack_status_snapshot(path=path)
            self.assertEqual(result.status, "unreadable")


# ---------------------------------------------------------------------------
# render_status_snapshot_reply
# ---------------------------------------------------------------------------


class RenderStatusSnapshotReplyTests(unittest.TestCase):
    def _write_ok_snapshot(self, tmpdir: str) -> Path:
        path = Path(tmpdir) / "snapshot.json"
        now = datetime.now(timezone.utc)
        data = build_smoke_test_snapshot(timestamp=now.isoformat())
        data["session_status"] = "REGULAR"
        data["order_counters_today"]["buy_succeeded"] = 3
        write_slack_status_snapshot(data, path=path)
        return path

    def test_ok_snapshot_renders_session_and_counters(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_ok_snapshot(tmpdir)
            reply = render_status_snapshot_reply(path=path)
            self.assertIsInstance(reply, str)
            self.assertIn("REGULAR", reply)
            self.assertIn("PROJECT-SIGNALOR", reply)
            self.assertIn("매수", reply)
            self.assertIn("매도", reply)

    def test_missing_snapshot_returns_limited_text(self) -> None:
        reply = render_status_snapshot_reply(
            path=Path("/tmp/_nonexistent_/snapshot.json"),
        )
        self.assertEqual(reply, LIMITED_STATUS_MISSING_TEXT)


# ---------------------------------------------------------------------------
# render_bottlenecks_snapshot_reply
# ---------------------------------------------------------------------------


class RenderBottlenecksSnapshotReplyTests(unittest.TestCase):
    def test_no_bottlenecks_shows_clean_message(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["bottleneck_counters_today"] = {
                k: 0 for k in _BOTTLENECK_ACTION_KEYS
            }
            write_slack_status_snapshot(data, path=path)
            reply = render_bottlenecks_snapshot_reply(path=path)
            self.assertIn("병목이 없습니다", reply)

    def test_nonzero_bottleneck_shows_label_and_count(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["bottleneck_counters_today"] = {
                k: 0 for k in _BOTTLENECK_ACTION_KEYS
            }
            data["bottleneck_counters_today"]["kis_rate_limit"] = 5
            write_slack_status_snapshot(data, path=path)
            reply = render_bottlenecks_snapshot_reply(path=path)
            self.assertIn("KIS rate-limit", reply)
            self.assertIn("5", reply)
            self.assertNotIn("그 외 주요 병목", reply)

    def test_bottleneck_reply_includes_rate_limit_source_detail(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["bottleneck_counters_today"] = {
                k: 0 for k in _BOTTLENECK_ACTION_KEYS
            }
            data["bottleneck_counters_today"]["kis_rate_limit"] = 4
            data["health"] = {
                "rate_limit_hits": 4,
                "last_rate_limit_source": "balance",
                "consecutive_backoff_cycles": 3,
            }
            write_slack_status_snapshot(data, path=path)
            reply = render_bottlenecks_snapshot_reply(path=path)
            self.assertIn("KIS rate-limit", reply)
            self.assertIn("source `balance` 잔고 조회", reply)
            self.assertIn("backoff `3` cycles", reply)

    def test_missing_snapshot_returns_limited_text(self) -> None:
        reply = render_bottlenecks_snapshot_reply(
            path=Path("/tmp/_nonexistent_/snapshot.json"),
        )
        self.assertEqual(reply, LIMITED_BOTTLENECKS_MISSING_TEXT)


# ---------------------------------------------------------------------------
# render_health_snapshot_reply
# ---------------------------------------------------------------------------


class RenderHealthSnapshotReplyTests(unittest.TestCase):
    def test_healthy_snapshot_shows_ok_header(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["health"] = {
                "rate_limit_hits": 0,
                "last_rate_limit_source": None,
                "daily_pnl_pause_state": None,
                "daily_pnl_pause_reason": None,
                "last_cycle_elapsed_ms": 800,
                "consecutive_backoff_cycles": 0,
            }
            write_slack_status_snapshot(data, path=path)
            reply = render_health_snapshot_reply(path=path, now=now)
            self.assertIn("✅", reply)
            self.assertIn("시스템 건강 상태", reply)

    def test_rate_limit_warning_in_health(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["health"] = {
                "rate_limit_hits": 3,
                "last_rate_limit_source": "EGW00201",
                "daily_pnl_pause_state": None,
                "daily_pnl_pause_reason": None,
                "last_cycle_elapsed_ms": None,
                "consecutive_backoff_cycles": 0,
            }
            write_slack_status_snapshot(data, path=path)
            reply = render_health_snapshot_reply(path=path, now=now)
            self.assertIn("⚠️", reply)
            self.assertIn("3", reply)
            self.assertIn("EGW00201", reply)

    def test_rate_limit_health_uses_source_specific_label(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["health"] = {
                "rate_limit_hits": 7,
                "last_rate_limit_source": "balance",
                "daily_pnl_pause_state": None,
                "daily_pnl_pause_reason": None,
                "last_cycle_elapsed_ms": None,
                "consecutive_backoff_cycles": 2,
            }
            write_slack_status_snapshot(data, path=path)
            reply = render_health_snapshot_reply(path=path, now=now)
            self.assertIn("source `balance` 잔고 조회", reply)
            self.assertIn("action 이번 사이클 스킵", reply)

    def test_missing_snapshot_returns_limited_text(self) -> None:
        reply = render_health_snapshot_reply(
            path=Path("/tmp/_nonexistent_/snapshot.json"),
        )
        self.assertEqual(reply, LIMITED_HEALTH_MISSING_TEXT)


# ---------------------------------------------------------------------------
# render_positions_snapshot_reply
# ---------------------------------------------------------------------------


class RenderPositionsSnapshotReplyTests(unittest.TestCase):
    def test_positions_rendered_with_symbol_name_qty_and_prices(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = [
                {
                    "symbol": "005930",
                    "symbol_name": "삼성전자",
                    "qty": 10,
                    "current_price": 70000,
                    "avg_price": 65000,
                    "market_value": 700000,
                },
                {"symbol": "000660", "qty": 5},
            ]
            data["positions_synced_at"] = now.isoformat()
            data["masked_account_display"] = "1234***78-01"
            write_slack_status_snapshot(data, path=path)
            reply = render_positions_snapshot_reply(path=path, now=now)
            self.assertIn("005930", reply)
            self.assertIn("삼성전자", reply)
            self.assertIn("000660", reply)
            self.assertIn("`10`주", reply)
            self.assertIn("현재 `70,000원`", reply)
            self.assertIn("평균 `65,000원`", reply)
            self.assertIn("평가 `700,000원`", reply)
            self.assertIn("2", reply)  # 총 2종목
            self.assertIn("freshness=`fresh`", reply)
            self.assertIn("account=`1234***78-01`", reply)

    def test_positions_prefers_fresh_performance_summary_details(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            performance_path = Path(tmpdir) / "performance_summary.jsonl"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = [
                {"symbol": "001450", "qty": 4},
            ]
            data["positions_synced_at"] = now.isoformat()
            write_slack_status_snapshot(data, path=path)
            performance_path.write_text(
                json.dumps(
                    {
                        "generated_at": now.isoformat(),
                        "positions": [
                            {
                                "symbol": "001450",
                                "symbol_name": "현대해상",
                                "holding_qty": 4,
                                "average_price": 30175,
                                "current_price": 39100,
                                "market_value": 156400,
                                "net_pnl": 35700,
                                "net_pnl_pct": 29.58,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            reply = render_positions_snapshot_reply(
                path=path,
                performance_summary_path=performance_path,
                now=now,
            )

            self.assertIn("001450", reply)
            self.assertIn("현대해상", reply)
            self.assertIn("현재 `39,100원`", reply)
            self.assertIn("평균 `30,175원`", reply)
            self.assertIn("평가 `156,400원`", reply)
            self.assertIn("손익 `+35,700원` (+29.58%)", reply)

    def test_positions_falls_back_when_performance_summary_is_stale(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            performance_path = Path(tmpdir) / "performance_summary.jsonl"
            snapshot_time = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
            now = snapshot_time + timedelta(minutes=5)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = [
                {
                    "symbol": "005930",
                    "symbol_name": "삼성전자",
                    "qty": 10,
                    "current_price": 70000,
                },
            ]
            write_slack_status_snapshot(data, path=path)
            performance_path.write_text(
                json.dumps(
                    {
                        "generated_at": snapshot_time.isoformat(),
                        "positions": [
                            {
                                "symbol": "005930",
                                "symbol_name": "삼성전자",
                                "holding_qty": 10,
                                "average_price": 65000,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            reply = render_positions_snapshot_reply(
                path=path,
                performance_summary_path=performance_path,
                now=now,
                stale_after_sec=60,
            )

            self.assertIn("현재 `70,000원`", reply)
            self.assertNotIn("평균 `65,000원`", reply)

    def test_empty_positions_shows_no_data_message(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = []
            write_slack_status_snapshot(data, path=path)
            reply = render_positions_snapshot_reply(path=path, now=now)
            self.assertIn("No positions in latest local snapshot", reply)
            self.assertIn("freshness=`fresh`", reply)

    def test_stale_positions_still_show_last_known_holdings(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            snapshot_time = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
            now = snapshot_time + timedelta(minutes=30)
            data = build_smoke_test_snapshot(timestamp=snapshot_time.isoformat())
            data["positions_snapshot"] = [
                {"symbol": "005930", "symbol_name": "삼성전자", "qty": 10},
            ]
            write_slack_status_snapshot(data, path=path)
            reply = render_positions_snapshot_reply(
                path=path,
                now=now,
                stale_after_sec=60,
            )

            self.assertIn("Last known positions are stale", reply)
            self.assertIn("005930", reply)
            self.assertIn("삼성전자", reply)
            self.assertIn("freshness=`stale`", reply)
            self.assertIn("snapshot older than freshness window", reply)

    def test_missing_optional_position_fields_render_cleanly(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = [
                {"symbol": "999999", "qty": 2},
            ]
            write_slack_status_snapshot(data, path=path)
            reply = render_positions_snapshot_reply(path=path, now=now)

            self.assertIn("`999999`", reply)
            self.assertIn("`2`주", reply)
            self.assertNotIn("현재 `", reply)
            self.assertNotIn("평균 `", reply)
            self.assertNotIn("평가 `", reply)

    def test_missing_snapshot_returns_limited_text(self) -> None:
        reply = render_positions_snapshot_reply(
            path=Path("/tmp/_nonexistent_/snapshot.json"),
        )
        self.assertEqual(reply, LIMITED_POSITIONS_MISSING_TEXT)

    def test_positions_rendered_as_two_line_blocks(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            data = build_smoke_test_snapshot(timestamp=now.isoformat())
            data["positions_snapshot"] = [
                {
                    "symbol": "005930",
                    "symbol_name": "삼성전자",
                    "qty": 10,
                    "current_price": 70000,
                },
            ]
            data["positions_synced_at"] = now.isoformat()
            write_slack_status_snapshot(data, path=path)
            reply = render_positions_snapshot_reply(path=path, now=now)

        lines = reply.split("\n")
        header_idx = next(i for i, l in enumerate(lines) if "005930" in l)
        self.assertIn("삼성전자", lines[header_idx])
        detail_line = lines[header_idx + 1]
        self.assertIn("`10`주", detail_line)
        self.assertIn("현재 `70,000원`", detail_line)


# ---------------------------------------------------------------------------
# build_smoke_test_snapshot
# ---------------------------------------------------------------------------


class BuildSmokeTestSnapshotTests(unittest.TestCase):
    def test_smoke_snapshot_has_required_keys(self) -> None:
        snapshot = build_smoke_test_snapshot(
            timestamp="2026-05-10T09:00:00+09:00",
        )
        self.assertEqual(snapshot["session_status"], "SMOKE_TEST")
        self.assertIn("order_counters_today", snapshot)
        self.assertIn("bottleneck_counters_today", snapshot)
        self.assertIn("last_order_event", snapshot)
        self.assertEqual(snapshot["exception_count"], 0)

    def test_smoke_snapshot_write_and_read_roundtrip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshot.json"
            now = datetime.now(timezone.utc)
            snapshot = build_smoke_test_snapshot(timestamp=now.isoformat())
            self.assertTrue(write_slack_status_snapshot(snapshot, path=path))
            result = read_slack_status_snapshot(path=path, now=now)
            self.assertEqual(result.status, "ok")


# ---------------------------------------------------------------------------
# RuntimeSnapshotReadResult dataclass
# ---------------------------------------------------------------------------


class RuntimeSnapshotReadResultTests(unittest.TestCase):
    def test_defaults(self) -> None:
        result = RuntimeSnapshotReadResult(status="missing")
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.snapshot)
        self.assertIsNone(result.age_seconds)

    def test_with_all_fields(self) -> None:
        result = RuntimeSnapshotReadResult(
            status="ok", snapshot={"a": 1}, age_seconds=42.0,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.snapshot, {"a": 1})
        self.assertEqual(result.age_seconds, 42.0)

    def test_frozen(self) -> None:
        result = RuntimeSnapshotReadResult(status="ok")
        with self.assertRaises(AttributeError):
            result.status = "stale"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
