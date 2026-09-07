"""Tests for app/overseas_risk/guards.py (USD daily caps, fail-closed integrity)."""
import json
import unittest


class TestReadOverseasOrdersToday(unittest.TestCase):
    """Test 11: read_overseas_orders_today filters by ET date."""

    def test_returns_only_today_et_records_skipping_malformed_and_yesterday(
        self,
    ) -> None:
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from zoneinfo import ZoneInfo

        from app.overseas_risk.guards import read_overseas_orders_today

        US_EASTERN = ZoneInfo("America/New_York")
        # Fixed "now": Wed 2026-06-10 10:00 ET  →  date "2026-06-10"
        fixed_now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)

        # Valid today record: 2026-06-10T09:45:00-04:00 (ET) → "2026-06-10"
        today_record = {
            "timestamp": "2026-06-10T09:45:00-04:00",
            "market": "overseas",
            "env": "mock",
            "action": "overseas_order_submitted",
            "side": "BUY",
            "symbol": "AAPL",
            "exchange": "NASD",
            "qty": 2,
            "unit_price": "150.00",
            "tr_id": "TTTT1234U",
            "result": {},
        }
        # Yesterday record: 2026-06-09T15:00:00-04:00 (ET) → "2026-06-09"
        yesterday_record = {
            "timestamp": "2026-06-09T15:00:00-04:00",
            "market": "overseas",
            "env": "mock",
            "action": "overseas_order_submitted",
            "side": "BUY",
            "symbol": "MSFT",
            "exchange": "NASD",
            "qty": 1,
            "unit_price": "400.00",
            "tr_id": "TTTT1234U",
            "result": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "overseas_orders.jsonl"
            with log_path.open("w") as f:
                f.write(json.dumps(today_record) + "\n")
                f.write("MALFORMED LINE\n")
                f.write(json.dumps(yesterday_record) + "\n")

            result = read_overseas_orders_today(
                log_path=log_path, now=fixed_now
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["symbol"], "AAPL")


class TestEvaluateOverseasDailyOrderLimit(unittest.TestCase):
    """Test 12: order-limit blocks when count >= max_orders."""

    def test_order_limit_blocks_when_at_max(self) -> None:
        from app.overseas_risk.guards import evaluate_overseas_daily_order_limit

        orders_today = [{"qty": 1, "unit_price": "100.0"}] * 3
        result = evaluate_overseas_daily_order_limit(
            orders_today=orders_today, max_orders=3
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.action, "blocked_overseas_daily_order_limit")


class TestEvaluateOverseasDailyNotionalLimit(unittest.TestCase):
    """Test 13: notional-limit blocks when today+planned > max; passes within."""

    def test_notional_limit_blocks_when_exceeded(self) -> None:
        from app.overseas_risk.guards import evaluate_overseas_daily_notional_limit

        # 2 orders of 10 * $150 = $3000 today + $1500 planned = $4500 > $4000 max
        orders_today = [{"qty": 10, "unit_price": "150.00"}] * 2
        result = evaluate_overseas_daily_notional_limit(
            orders_today=orders_today,
            planned_notional_usd=1500.0,
            max_notional_usd=4000.0,
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.action, "blocked_overseas_daily_notional_limit")

    def test_notional_limit_passes_when_within_max(self) -> None:
        from app.overseas_risk.guards import evaluate_overseas_daily_notional_limit

        # 1 order of 5 * $100 = $500 today + $400 planned = $900 <= $1000 max
        orders_today = [{"qty": 5, "unit_price": "100.00"}]
        result = evaluate_overseas_daily_notional_limit(
            orders_today=orders_today,
            planned_notional_usd=400.0,
            max_notional_usd=1000.0,
        )
        self.assertTrue(result.passed)


class TestOverseasOrderLogIntegrity(unittest.TestCase):
    """Test 14: corrupt log → evaluate_overseas_risk_guards returns allowed=False."""

    def test_corrupt_log_fails_closed(self) -> None:
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from zoneinfo import ZoneInfo

        from app.overseas_risk.guards import evaluate_overseas_risk_guards

        US_EASTERN = ZoneInfo("America/New_York")
        fixed_now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            # Write a file that exists but is completely corrupt (not valid JSON)
            log_path.write_text("NOT JSON AT ALL\nALSO NOT JSON\n")

            result = evaluate_overseas_risk_guards(
                log_path=log_path,
                planned_notional_usd=500.0,
                max_orders=10,
                max_notional_usd=10000.0,
                enabled=True,
                now=fixed_now,
            )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_overseas_order_log_unreadable")


class TestOverseasRiskGuardsDisabled(unittest.TestCase):
    """Test 15: enabled=False → evaluated=False, allowed=True."""

    def test_disabled_guards_return_not_evaluated_and_allowed(self) -> None:
        from pathlib import Path

        from app.overseas_risk.guards import evaluate_overseas_risk_guards

        result = evaluate_overseas_risk_guards(
            log_path=Path("/nonexistent/path"),
            planned_notional_usd=100.0,
            max_orders=5,
            max_notional_usd=1000.0,
            enabled=False,
        )
        self.assertFalse(result.evaluated)
        self.assertTrue(result.allowed)


class TestEvaluateOverseasOrderLogIntegrity(unittest.TestCase):
    """Test that evaluate_overseas_order_log_integrity is a public function."""

    def test_integrity_passes_for_missing_log(self) -> None:
        from pathlib import Path

        from app.overseas_risk.guards import evaluate_overseas_order_log_integrity

        result = evaluate_overseas_order_log_integrity(
            log_path=Path("/nonexistent/path/orders.jsonl")
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.guard_name, "overseas_order_log_integrity")


class TestOverseasRiskGuardsOrderLimitIntegration(unittest.TestCase):
    """Verify order-limit cap is applied inside evaluate_overseas_risk_guards."""

    def test_evaluate_overseas_risk_guards_blocks_on_order_limit(self) -> None:
        import json
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from zoneinfo import ZoneInfo

        from app.overseas_risk.guards import evaluate_overseas_risk_guards

        US_EASTERN = ZoneInfo("America/New_York")
        fixed_now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)

        record = {
            "timestamp": "2026-06-10T09:45:00-04:00",
            "action": "overseas_order_submitted",
            "side": "BUY", "symbol": "AAPL", "exchange": "NASD",
            "qty": 1, "unit_price": "100.00",
            "market": "overseas", "env": "mock", "tr_id": "X", "result": {},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            # Write 3 valid orders today — at max_orders=3 this should block
            with log_path.open("w") as f:
                for _ in range(3):
                    f.write(json.dumps(record) + "\n")

            result = evaluate_overseas_risk_guards(
                log_path=log_path,
                planned_notional_usd=100.0,
                max_orders=3,
                max_notional_usd=100000.0,
                enabled=True,
                now=fixed_now,
            )

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_overseas_daily_order_limit")


class TestOverseasRiskGuardsHappyPath(unittest.TestCase):
    """Test 16: empty/missing log + within caps → allowed=True, 3 guard_results."""

    def test_missing_log_within_caps_returns_allowed(self) -> None:
        from pathlib import Path

        from app.overseas_risk.guards import evaluate_overseas_risk_guards

        result = evaluate_overseas_risk_guards(
            log_path=Path("/nonexistent/path/orders.jsonl"),
            planned_notional_usd=100.0,
            max_orders=5,
            max_notional_usd=1000.0,
            enabled=True,
        )
        self.assertTrue(result.evaluated)
        self.assertTrue(result.allowed)
        self.assertEqual(len(result.guard_results), 3)


if __name__ == "__main__":
    unittest.main()
