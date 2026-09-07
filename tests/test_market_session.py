import unittest
from datetime import datetime

from app.core.market_session import get_korean_market_session
from app.core.time_utils import KOREA_TZ


class MarketSessionTests(unittest.TestCase):
    def test_weekday_before_open_is_premarket(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 8, 59, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "PREMARKET")
        self.assertFalse(status.order_allowed)
        self.assertEqual(status.buy_block_action, "blocked_premarket")

    def test_regular_open_boundary_is_allowed(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 9, 0, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "REGULAR")
        self.assertTrue(status.order_allowed)

    def test_regular_before_closing_buffer_is_allowed(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 15, 19, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "REGULAR")
        self.assertTrue(status.order_allowed)

    def test_closing_buffer_blocks_orders_but_keeps_regular_session_label(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 15, 20, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "REGULAR")
        self.assertFalse(status.order_allowed)
        self.assertEqual(status.buy_block_action, "blocked_closing_buffer")
        self.assertEqual(status.sell_block_action, "blocked_sell_closing_buffer")

    def test_regular_close_boundary_is_blocked_by_closing_buffer(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 15, 30, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "REGULAR")
        self.assertFalse(status.order_allowed)

    def test_after_market_starts_after_1530(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 13, 15, 31, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "AFTER_MARKET")
        self.assertFalse(status.order_allowed)
        self.assertEqual(status.sell_block_action, "blocked_sell_after_market")

    def test_weekend_is_closed(self) -> None:
        status = get_korean_market_session(
            datetime(2026, 4, 11, 10, 0, tzinfo=KOREA_TZ)
        )

        self.assertEqual(status.session, "CLOSED")
        self.assertFalse(status.order_allowed)


if __name__ == "__main__":
    unittest.main()
