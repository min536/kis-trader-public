"""Tests for app/overseas_stock/session.py (US/Eastern clock + session)."""
import unittest
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")


class TestGetUsEasternNow(unittest.TestCase):
    """Test get_us_eastern_now returns ET-aware datetime."""

    def test_get_us_eastern_now_returns_et_aware_datetime(self) -> None:
        from zoneinfo import ZoneInfo

        from app.overseas_stock.session import US_EASTERN, get_us_eastern_now

        result = get_us_eastern_now()
        self.assertIsNotNone(result.tzinfo)
        # Should be ET (or an equivalent offset)
        self.assertEqual(result.astimezone(US_EASTERN).tzinfo.key, "America/New_York")


class TestUsSessionDate(unittest.TestCase):
    """Test 1: us_session_date returns the ET date of the injected datetime."""

    def test_us_session_date_for_injected_et_datetime(self) -> None:
        from datetime import datetime

        from app.overseas_stock.session import us_session_date

        # Wed 2026-06-10 10:00:00 ET
        now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)
        result = us_session_date(now=now)
        self.assertEqual(result, "2026-06-10")


class TestToEasternDate(unittest.TestCase):
    """Test 2: to_eastern_date converts tz-aware timestamps."""

    def test_to_eastern_date_kst_timestamp_converts_to_et_date(self) -> None:
        from app.overseas_stock.session import to_eastern_date

        # 2026-06-15T05:00:00+09:00  →  2026-06-14T16:00:00 ET (previous ET day)
        result = to_eastern_date("2026-06-15T05:00:00+09:00")
        self.assertEqual(result, "2026-06-14")


    def test_to_eastern_date_returns_none_for_garbage(self) -> None:
        from app.overseas_stock.session import to_eastern_date

        result = to_eastern_date("garbage")
        self.assertIsNone(result)


class TestIsUsRegularSession(unittest.TestCase):
    """Test 3: is_us_regular_session gate checks."""

    def test_is_regular_session_true_at_10am_et_wednesday(self) -> None:
        from datetime import datetime

        from app.overseas_stock.session import is_us_regular_session

        # Wed 2026-06-10 10:00 ET — normal trading day
        now = datetime(2026, 6, 10, 10, 0, 0, tzinfo=US_EASTERN)
        self.assertTrue(is_us_regular_session(now=now))


    def test_is_regular_session_false_pre_open_08am_et(self) -> None:
        from datetime import datetime

        from app.overseas_stock.session import is_us_regular_session

        # Wed 2026-06-10 08:00 ET — pre-open
        now = datetime(2026, 6, 10, 8, 0, 0, tzinfo=US_EASTERN)
        self.assertFalse(is_us_regular_session(now=now))

    def test_is_regular_session_false_on_saturday(self) -> None:
        from datetime import datetime

        from app.overseas_stock.session import is_us_regular_session

        # Sat 2026-06-13 11:00 ET — weekend
        now = datetime(2026, 6, 13, 11, 0, 0, tzinfo=US_EASTERN)
        self.assertFalse(is_us_regular_session(now=now))

    def test_is_regular_session_false_on_holiday(self) -> None:
        from datetime import datetime

        from app.overseas_stock.session import (
            DEFAULT_US_HOLIDAYS,
            is_us_regular_session,
        )

        # 2026-07-03 is a provisional US holiday in DEFAULT_US_HOLIDAYS
        now = datetime(2026, 7, 3, 11, 0, 0, tzinfo=US_EASTERN)
        self.assertIn("2026-07-03", DEFAULT_US_HOLIDAYS)
        self.assertFalse(is_us_regular_session(now=now))


if __name__ == "__main__":
    unittest.main()
