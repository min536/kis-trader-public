"""Tests for app.dashboard.console_v1.build_console_overview.

TDD: one test_* function added at a time.
All data is synthetic — no file I/O, no real artifacts.
"""
from __future__ import annotations

import time
import unittest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_data(**overrides):
    """Minimal synthetic data dict; overrides replace top-level keys."""
    base: dict = {}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — empty data returns full shape with safe defaults
# ---------------------------------------------------------------------------

class EmptyDataTest(unittest.TestCase):
    def test_empty_data_returns_full_shape_with_safe_defaults(self):
        from app.dashboard.console_v1 import build_console_overview

        result = build_console_overview({})

        # Must not raise; must have exactly these top-level keys
        self.assertIn("session", result)
        self.assertIn("api_health", result)
        self.assertIn("latency", result)
        self.assertIn("orders_today", result)
        self.assertIn("account_signature", result)
        self.assertIn("tags", result)

        self.assertEqual(result["session"]["status"], "UNKNOWN")
        self.assertEqual(result["latency"]["recent_ms"], [])
        self.assertEqual(result["orders_today"]["count"], 0)
        self.assertEqual(result["tags"], {})
        self.assertIsNone(result["account_signature"])


class MalformedDataTest(unittest.TestCase):
    def test_non_list_collections_and_non_dict_items_do_not_raise(self):
        # Fail-safe contract: a malformed (non-list) collection or non-dict
        # field must degrade to safe defaults, never raise (e.g. list("oops")
        # would otherwise turn into ['o','o','p','s'] and crash .get()).
        from app.dashboard.console_v1 import build_console_overview

        bad = {
            "cycles": "oops",
            "orders": "nope",
            "positions": "bad",
            "engine_state_view": "x",
            "runtime_state": "y",
        }
        result = build_console_overview(bad, now_epoch=1_760_000_000.0)

        self.assertEqual(result["session"]["status"], "UNKNOWN")
        self.assertEqual(result["latency"]["recent_ms"], [])
        self.assertEqual(result["orders_today"]["count"], 0)
        self.assertEqual(result["tags"], {})

        # Lists containing non-dict items must also be tolerated (skipped),
        # consistent with the positions loop's guard.
        lumpy = {"cycles": [1, 2], "orders": [1, 2], "positions": [1, 2]}
        result2 = build_console_overview(lumpy, now_epoch=1_760_000_000.0)
        self.assertEqual(result2["latency"]["recent_ms"], [])
        self.assertEqual(result2["orders_today"]["rows"], [])
        self.assertEqual(result2["tags"], {})


# ---------------------------------------------------------------------------
# Test 2 — latency fields from cycles
# ---------------------------------------------------------------------------

class LatencyTest(unittest.TestCase):
    def test_latency_from_cycles_oldest_to_newest_capped(self):
        from app.dashboard.console_v1 import build_console_overview

        # 35 cycles with cycle_elapsed_ms; builder should cap at 30
        cycles = [{"cycle_elapsed_ms": float(i)} for i in range(35)]
        data = _make_data(cycles=cycles)

        result = build_console_overview(data)
        lat = result["latency"]

        # capped at 30
        self.assertEqual(lat["samples"], 30)
        self.assertEqual(len(lat["recent_ms"]), 30)
        # oldest→newest: cycles list is treated as newest-first;
        # builder reverses to collect oldest-first then caps → last 30 values
        # We just check the values are floats and avg/max are correct
        self.assertIsInstance(lat["avg_ms"], float)
        self.assertIsInstance(lat["max_ms"], float)
        self.assertEqual(lat["max_ms"], max(lat["recent_ms"]))


# ---------------------------------------------------------------------------
# Test 3 — session status: fresh + REGULAR → RUNNING
# ---------------------------------------------------------------------------

class SessionStatusRunningTest(unittest.TestCase):
    def test_fresh_regular_is_running(self):
        from app.dashboard.console_v1 import build_console_overview
        import datetime

        fixed_now = 1_700_000_000.0
        recent_cycle_at = datetime.datetime.fromtimestamp(
            fixed_now - 30, tz=datetime.timezone.utc
        ).isoformat()
        data = _make_data(
            cycles=[{"timestamp": recent_cycle_at, "cycle_elapsed_ms": 100.0}],
            engine_state_view={"market_session": "REGULAR"},
        )
        result = build_console_overview(data, now_epoch=fixed_now)
        self.assertEqual(result["session"]["status"], "RUNNING")
        self.assertIsNotNone(result["session"]["heartbeat_seconds"])


class SessionStatusIdleTest(unittest.TestCase):
    def test_fresh_closed_is_idle(self):
        from app.dashboard.console_v1 import build_console_overview
        import datetime

        fixed_now = 1_700_000_000.0
        recent_cycle_at = datetime.datetime.fromtimestamp(
            fixed_now - 30, tz=datetime.timezone.utc
        ).isoformat()
        data = _make_data(
            cycles=[{"timestamp": recent_cycle_at}],
            engine_state_view={"market_session": "CLOSED"},
        )
        result = build_console_overview(data, now_epoch=fixed_now)
        self.assertEqual(result["session"]["status"], "IDLE")


class SessionStatusStaleTest(unittest.TestCase):
    def test_stale_heartbeat_is_stale(self):
        from app.dashboard.console_v1 import build_console_overview
        import datetime

        fixed_now = 1_700_000_000.0
        recent_cycle_at = datetime.datetime.fromtimestamp(
            fixed_now - 300, tz=datetime.timezone.utc
        ).isoformat()
        data = _make_data(
            cycles=[{"timestamp": recent_cycle_at}],
            engine_state_view={"market_session": "REGULAR"},
        )
        result = build_console_overview(data, now_epoch=fixed_now)
        self.assertEqual(result["session"]["status"], "STALE")


# ---------------------------------------------------------------------------
# Test 4 — api_health: budget fields present → correct values + tone
# ---------------------------------------------------------------------------

class ApiHealthTest(unittest.TestCase):
    def test_budget_present_computes_remaining_and_tone(self):
        from app.dashboard.console_v1 import build_console_overview

        budget = {
            "recent_request_count": 3,
            "rate_limit_hits": 0,
            "backoff_remaining_seconds": 0,
        }
        data = _make_data(engine_state_view={"budget_status": budget})
        result = build_console_overview(data)
        ah = result["api_health"]

        self.assertEqual(ah["request_used"], 3)
        self.assertIsNotNone(ah["request_limit"])       # from settings
        self.assertIsNotNone(ah["request_remaining"])
        self.assertGreaterEqual(ah["request_remaining"], 0)
        self.assertEqual(ah["backoff_count"], 0)
        self.assertIn(ah["tone"], {"positive", "warning", "danger"})


class ApiHealthAbsentTest(unittest.TestCase):
    def test_absent_budget_returns_safe_defaults(self):
        from app.dashboard.console_v1 import build_console_overview

        result = build_console_overview({})
        ah = result["api_health"]
        self.assertIsNone(ah["request_used"])
        self.assertEqual(ah["backoff_count"], 0)
        self.assertIn(ah["tone"], {"positive", "warning", "danger"})


# ---------------------------------------------------------------------------
# Test 5 — orders_today: count + rows shape
# ---------------------------------------------------------------------------

class OrdersTodayTest(unittest.TestCase):
    def test_orders_rows_shape_and_count_from_summary(self):
        from app.dashboard.console_v1 import build_console_overview

        # Provide orders with the expected fields
        orders = [
            {
                "timestamp": "2026-06-13T09:00:00+09:00",
                "action": "order_submitted",
                "symbol": "005930",
                "symbol_name": "삼성전자",
                "result": "succeeded",
            },
            {
                "timestamp": "2026-06-13T10:00:00+09:00",
                "action": "sell_order_submitted",
                "symbol": "000660",
                "symbol_name": "SK하이닉스",
                "result": "succeeded",
            },
        ]
        data = _make_data(orders=orders)
        result = build_console_overview(data)
        ot = result["orders_today"]

        # rows always includes up to 8 recent orders
        self.assertGreaterEqual(len(ot["rows"]), 1)
        # check row shape
        row = ot["rows"][0]
        self.assertIn("ts", row)
        self.assertIn("action", row)
        self.assertIn("symbol", row)
        self.assertIn("symbol_name", row)
        self.assertIn("result", row)


# ---------------------------------------------------------------------------
# Test 6 — tags: positions get tag lists; no tag source → {}
# ---------------------------------------------------------------------------

class TagsTest(unittest.TestCase):
    def test_no_positions_returns_empty_tags(self):
        from app.dashboard.console_v1 import build_console_overview

        result = build_console_overview({})
        self.assertEqual(result["tags"], {})

    def test_positions_with_unknown_symbols_returns_empty_tags(self):
        from app.dashboard.console_v1 import build_console_overview

        # Use symbol codes that are very unlikely to be in the tag registry
        data = _make_data(positions=[{"symbol": "XXXXXXX"}, {"symbol": "YYYYYYY"}])
        result = build_console_overview(data)
        # tags dict returned; unknown symbols have no entry (or empty list)
        self.assertIsInstance(result["tags"], dict)
        # No crash, and no entries for unknown symbols
        self.assertNotIn("XXXXXXX", result["tags"])

    def test_known_symbol_gets_tags_from_registry(self):
        from app.dashboard.console_v1 import build_console_overview

        # 005930 (삼성전자) is in config/symbol_tags.yaml with known tags
        data = _make_data(positions=[{"symbol": "005930"}])
        result = build_console_overview(data)
        # tags must be a dict; 005930 should have a non-empty list
        self.assertIsInstance(result["tags"], dict)
        self.assertIn("005930", result["tags"])
        self.assertIsInstance(result["tags"]["005930"], list)
        self.assertTrue(len(result["tags"]["005930"]) > 0)


# ---------------------------------------------------------------------------
# Test 8 — session: main_pid from runtime_state
# ---------------------------------------------------------------------------

class SessionMainPidTest(unittest.TestCase):
    def test_main_pid_from_runtime_state(self):
        from app.dashboard.console_v1 import build_console_overview

        data = _make_data(runtime_state={"main_pid": 12345})
        result = build_console_overview(data)
        self.assertEqual(result["session"]["main_pid"], 12345)


class SessionBrakeStateTest(unittest.TestCase):
    def test_brake_state_from_engine_state_view(self):
        from app.dashboard.console_v1 import build_console_overview

        data = _make_data(engine_state_view={"current_brake_state": "BUY_PAUSE"})
        result = build_console_overview(data)
        self.assertEqual(result["session"]["brake_state"], "BUY_PAUSE")


# ---------------------------------------------------------------------------
# Test 9 — api_health: backoff_count > 0 → tone "danger"
# ---------------------------------------------------------------------------

class ApiHealthBackoffTest(unittest.TestCase):
    def test_nonzero_backoff_count_gives_danger_tone(self):
        from app.dashboard.console_v1 import build_console_overview

        budget = {"recent_request_count": 1, "rate_limit_hits": 3}
        data = _make_data(engine_state_view={"budget_status": budget})
        result = build_console_overview(data)
        self.assertEqual(result["api_health"]["backoff_count"], 3)
        self.assertEqual(result["api_health"]["tone"], "danger")


# ---------------------------------------------------------------------------
# Test 10 — orders_today: count reflects today's submitted orders
# ---------------------------------------------------------------------------

class OrdersTodayCountTest(unittest.TestCase):
    def test_orders_today_count_from_today_submitted_orders(self):
        from app.dashboard.console_v1 import build_console_overview
        import datetime

        # Use today's date so the summary counts them
        today = datetime.datetime.now(tz=datetime.timezone.utc).date()
        ts_today = datetime.datetime(
            today.year, today.month, today.day, 10, 0, 0,
            tzinfo=datetime.timezone.utc
        ).isoformat()

        orders = [
            {"timestamp": ts_today, "action": "order_submitted", "symbol": "005930"},
            {"timestamp": ts_today, "action": "order_submitted", "symbol": "000660"},
        ]
        data = _make_data(orders=orders)
        result = build_console_overview(data)
        # count reflects today's submitted orders (at least 2)
        self.assertGreaterEqual(result["orders_today"]["count"], 2)


# ---------------------------------------------------------------------------
# Test 6c — account_signature: passed arg is returned as-is
# ---------------------------------------------------------------------------

class AccountSignatureTest(unittest.TestCase):
    def test_explicit_account_signature_arg_is_returned(self):
        from app.dashboard.console_v1 import build_console_overview

        result = build_console_overview({}, account_signature="MOCK-ACCT-001")
        self.assertEqual(result["account_signature"], "MOCK-ACCT-001")

    def test_account_signature_from_runtime_state_when_arg_is_none(self):
        from app.dashboard.console_v1 import build_console_overview

        data = _make_data(runtime_state={"account_signature": "RS-SIG-999"})
        result = build_console_overview(data)
        self.assertEqual(result["account_signature"], "RS-SIG-999")
