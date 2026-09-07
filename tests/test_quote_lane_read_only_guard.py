import unittest
from unittest import mock

from types import SimpleNamespace

import app.domestic_stock.quote as quote_module
import app.scanner.quote_account as quote_account
from app.domestic_stock.quote import inquire_price_for_credentials
from app.scanner.quote_account import (
    BuyScanQuoteContext,
    assert_read_only_quote_target,
    prefetch_buy_scan_prices,
)


class AssertReadOnlyQuoteTargetTests(unittest.TestCase):
    def test_allows_domestic_quote_url_and_tr(self) -> None:
        self.assertIsNone(
            assert_read_only_quote_target(
                "https://openapi.koreainvestment.com:9443"
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
            )
        )

    def test_blocks_order_url_and_tr(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_read_only_quote_target(
                "https://openapi.koreainvestment.com:9443"
                "/uapi/domestic-stock/v1/trading/order-cash",
                "TTTC0802U",
            )

    def test_blocks_quote_url_with_order_tr(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_read_only_quote_target(
                "https://openapi.koreainvestment.com:9443"
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "TTTC0802U",
            )

    def test_blocks_order_url_with_quote_tr(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_read_only_quote_target(
                "https://openapi.koreainvestment.com:9443"
                "/uapi/domestic-stock/v1/trading/order-cash",
                "FHKST01010100",
            )


class EgressRequestGuardTests(unittest.TestCase):
    def test_guard_blocks_before_request_is_sent(self) -> None:
        sent: list[tuple] = []

        def fake_request_json(method, url, **kwargs):
            sent.append((method, url, kwargs))
            return {"rt_cd": "0"}

        def always_raises(url, tr_id):
            raise RuntimeError("blocked")

        with mock.patch.object(quote_module, "request_json", fake_request_json):
            with self.assertRaises(RuntimeError):
                inquire_price_for_credentials(
                    "005930",
                    base_url="https://live",
                    token="t",
                    app_key="k",
                    app_secret="s",
                    request_guard=always_raises,
                )

        self.assertEqual(sent, [])

    def test_guard_allows_real_quote_target_and_sends_once(self) -> None:
        sent: list[tuple] = []

        def fake_request_json(method, url, **kwargs):
            sent.append((method, url))
            return {"rt_cd": "0", "output": {"stck_shrn_iscd": "005930"}}

        with mock.patch.object(quote_module, "request_json", fake_request_json):
            result = inquire_price_for_credentials(
                "005930",
                base_url="https://live",
                token="t",
                app_key="k",
                app_secret="s",
                request_guard=assert_read_only_quote_target,
            )

        self.assertEqual(result, {"rt_cd": "0", "output": {"stck_shrn_iscd": "005930"}})
        self.assertEqual(len(sent), 1)


class PrefetchArmsGuardTests(unittest.TestCase):
    def test_read_only_prefetch_arms_the_guard(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        context = BuyScanQuoteContext(
            mode="read_only_quote_account",
            env="live",
            token="t",
            base_url="https://live",
            app_key="k",
            app_secret="s",
        )
        received_guards: list = []

        def fake_fetch(symbol, **kwargs):
            received_guards.append(kwargs.get("request_guard"))
            return {"rt_cd": "0", "output": {"stck_shrn_iscd": symbol}}

        with mock.patch.object(
            quote_account,
            "build_buy_scan_quote_context",
            return_value=context,
        ):
            prefetch_buy_scan_prices(
                symbols=("005930",),
                settings=settings,
                execution_token="mock-token",
                fetch_price_for_credentials=fake_fetch,
                throttle_quote_lane=lambda _env: 0.0,
            )

        self.assertEqual(received_guards, [assert_read_only_quote_target])


if __name__ == "__main__":
    unittest.main()


class FetchBuyScanPriceArmsGuardTests(unittest.TestCase):
    def test_read_only_fetch_arms_the_guard(self) -> None:
        context = quote_account.BuyScanQuoteContext(
            mode="read_only_quote_account",
            env="live",
            token="live-token",
            base_url="https://live",
            app_key="k",
            app_secret="s",
        )
        with mock.patch.object(
            quote_account,
            "inquire_price_for_credentials",
            return_value={"rt_cd": "0"},
        ) as fetch:
            quote_account.fetch_buy_scan_price("005930", context=context)
        self.assertIs(
            fetch.call_args.kwargs.get("request_guard"),
            quote_account.assert_read_only_quote_target,
        )
