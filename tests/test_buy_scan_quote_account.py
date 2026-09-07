from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from app.scanner import quote_account as quote_account_module
from app.scanner import service as scanner_service
from app.scanner.quote_account import (
    BUY_SCAN_QUOTE_KIS_ENV,
    BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND,
    BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS,
    BuyScanQuoteContext,
    build_buy_scan_quote_context,
    buy_scan_uses_separate_quote_account,
    fetch_buy_scan_price,
    prefetch_buy_scan_prices,
)


class BuyScanQuoteAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_keys = (
            BUY_SCAN_QUOTE_KIS_ENV,
            BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND,
            BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS,
            "KIS_APP_LIVE_KEY",
            "KIS_APP_live_KEY",
            "KIS_LIVE_APP_KEY",
            "KIS_APP_LIVE_SECRET",
            "KIS_APP_live_SECRET",
            "KIS_LIVE_APP_SECRET",
            "KIS_BASE_LIVE_URL",
            "KIS_BASE_live_URL",
            "KIS_LIVE_BASE_URL",
        )
        self._original_env = {key: os.environ.get(key) for key in self._env_keys}

    def tearDown(self) -> None:
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _clear_env(self) -> None:
        for key in self._env_keys:
            os.environ.pop(key, None)

    def test_default_uses_execution_account_quote_token(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        self._clear_env()

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            side_effect=AssertionError("alternate token must not be issued"),
        ):
            context = build_buy_scan_quote_context(
                settings=settings,
                execution_token="mock-token",
            )

        self.assertEqual(context.mode, "execution_account")
        self.assertEqual(context.env, "mock")
        self.assertEqual(context.token, "mock-token")

        with mock.patch(
            "app.scanner.quote_account.inquire_price",
            return_value={"rt_cd": "0"},
        ) as inquire_price:
            self.assertEqual(
                fetch_buy_scan_price("005930", context=context),
                {"rt_cd": "0"},
            )

        inquire_price.assert_called_once_with("005930", token="mock-token")

    def test_live_quote_env_uses_live_read_only_credentials(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            return_value="live-token",
        ) as issue_access_token_for:
            context = build_buy_scan_quote_context(
                settings=settings,
                execution_token="mock-token",
            )

        self.assertEqual(context.mode, "read_only_quote_account")
        self.assertEqual(context.env, "live")
        self.assertEqual(context.token, "live-token")
        issue_access_token_for.assert_called_once_with(
            base_url="https://openapi.koreainvestment.com:9443",
            app_key="live-key",
            app_secret="live-secret",
            env="live",
            request_timeout_seconds=2.0,
            max_attempts=1,
        )

        with mock.patch(
            "app.scanner.quote_account.inquire_price_for_credentials",
            return_value={"rt_cd": "0"},
        ) as inquire_price_for_credentials:
            self.assertEqual(
                fetch_buy_scan_price("005930", context=context),
                {"rt_cd": "0"},
            )

        inquire_price_for_credentials.assert_called_once_with(
            "005930",
            base_url="https://openapi.koreainvestment.com:9443",
            token="live-token",
            app_key="live-key",
            app_secret="live-secret",
            use_global_throttle=False,
            record_metrics=True,
            request_timeout_seconds=None,
            max_attempts=None,
            request_guard=quote_account_module.assert_read_only_quote_target,
        )

    def test_live_quote_env_is_detected_as_separate_lane(self) -> None:
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"

        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )

        self.assertTrue(buy_scan_uses_separate_quote_account(settings))

    def test_live_quote_lane_clamps_config_to_current_official_limit(self) -> None:
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND] = "20"
        os.environ[BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS] = "0"

        max_requests, min_interval = quote_account_module._quote_lane_limits("live")

        self.assertEqual(max_requests, 18)
        self.assertGreaterEqual(min_interval, 0.1)

    def test_same_quote_env_is_not_detected_as_separate_lane(self) -> None:
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "mock"

        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )

        self.assertFalse(buy_scan_uses_separate_quote_account(settings))

    def test_live_quote_env_requires_live_scoped_credentials(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        with self.assertRaisesRegex(ValueError, "credential profile is incomplete"):
            build_buy_scan_quote_context(
                settings=settings,
                execution_token="mock-token",
            )

    def test_live_quote_env_defaults_base_url_when_unset(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            return_value="live-token",
        ) as issue_access_token_for:
            context = build_buy_scan_quote_context(
                settings=settings,
                execution_token="mock-token",
            )

        self.assertEqual(context.mode, "read_only_quote_account")
        self.assertEqual(context.env, "live")
        self.assertEqual(context.token, "live-token")
        self.assertEqual(
            context.base_url,
            "https://openapi.koreainvestment.com:9443",
        )
        issue_access_token_for.assert_called_once_with(
            base_url="https://openapi.koreainvestment.com:9443",
            app_key="live-key",
            app_secret="live-secret",
            env="live",
            request_timeout_seconds=2.0,
            max_attempts=1,
        )

    def test_scan_target_symbols_records_quote_account_context(self) -> None:
        settings = SimpleNamespace(
            target_symbols=("005930",),
            scan_symbols_max_per_cycle=1,
        )
        quote_context = BuyScanQuoteContext(
            mode="read_only_quote_account",
            env="live",
            token="live-token",
        )
        received_contexts: list[BuyScanQuoteContext] = []

        def fake_analyze(
            *,
            symbol,
            token,
            settings,
            portfolio_snapshot,
            selection_layer,
            quote_context,
            price_data,
            scan_cache=None,
        ):
            received_contexts.append(quote_context)
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, {
                "api_ms": 0.0,
                "parse_ms": 0.0,
                "score_calc_ms": 0.0,
                "candidate_build_ms": 0.0,
            }

        with (
            mock.patch.object(
                scanner_service,
                "build_buy_scan_quote_context",
                return_value=quote_context,
            ),
            mock.patch.object(
                scanner_service,
                "_analyze_symbol_with_metrics",
                side_effect=fake_analyze,
            ),
            mock.patch.object(scanner_service, "get_throttle_metrics_summary", return_value={}),
        ):
            scanner_service.scan_target_symbols(settings=settings, token="mock-token")

        self.assertEqual(received_contexts, [quote_context])
        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertEqual(diagnostics["quote_account_mode"], "read_only_quote_account")
        self.assertEqual(diagnostics["quote_account_env"], "live")

    def test_scan_target_symbols_uses_prefetched_price_data(self) -> None:
        settings = SimpleNamespace(
            target_symbols=("005930",),
            scan_symbols_max_per_cycle=1,
        )
        quote_context = BuyScanQuoteContext(
            mode="read_only_quote_account",
            env="live",
            token="live-token",
        )
        received_price_data: list[dict[str, object] | None] = []

        def fake_analyze(
            *,
            symbol,
            token,
            settings,
            portfolio_snapshot,
            selection_layer,
            quote_context,
            price_data,
            scan_cache=None,
        ):
            received_price_data.append(price_data)
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, {
                "api_ms": 0.0,
                "parse_ms": 0.0,
                "score_calc_ms": 0.0,
                "candidate_build_ms": 0.0,
            }

        prefetched = {"005930": {"rt_cd": "0", "output": {"symbol": "005930"}}}
        with (
            mock.patch.object(
                scanner_service,
                "build_buy_scan_quote_context",
                return_value=quote_context,
            ),
            mock.patch.object(
                scanner_service,
                "_analyze_symbol_with_metrics",
                side_effect=fake_analyze,
            ),
            mock.patch.object(scanner_service, "get_throttle_metrics_summary", return_value={}),
        ):
            scanner_service.scan_target_symbols(
                settings=settings,
                token="mock-token",
                price_data_by_symbol=prefetched,
            )

        self.assertEqual(received_price_data, [prefetched["005930"]])

    def test_scan_target_symbols_skips_missing_prefetch_without_inline_refetch(self) -> None:
        settings = SimpleNamespace(
            target_symbols=("005930", "000660"),
            scan_symbols_max_per_cycle=2,
        )
        quote_context = BuyScanQuoteContext(
            mode="read_only_quote_account",
            env="live",
            token="live-token",
        )

        def fake_analyze(**kwargs):
            fake_result = SimpleNamespace(
                symbol=kwargs["symbol"],
                sort_key=(0, 0.0, kwargs["symbol"]),
            )
            return fake_result, {
                "api_ms": 0.0,
                "parse_ms": 0.0,
                "score_calc_ms": 0.0,
                "candidate_build_ms": 0.0,
            }

        prefetched = {"005930": {"rt_cd": "0", "output": {"symbol": "005930"}}}
        with (
            mock.patch.object(
                scanner_service,
                "build_buy_scan_quote_context",
                return_value=quote_context,
            ),
            mock.patch.object(
                scanner_service,
                "_analyze_symbol_with_metrics",
                side_effect=fake_analyze,
            ) as analyze,
            mock.patch.object(scanner_service, "get_throttle_metrics_summary", return_value={}),
        ):
            scanner_service.scan_target_symbols(
                settings=settings,
                token="mock-token",
                price_data_by_symbol=prefetched,
                allow_inline_quote_fetch=False,
            )

        self.assertEqual(
            [call.kwargs["symbol"] for call in analyze.call_args_list],
            ["005930"],
        )
        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertEqual(diagnostics["quote_prefetch_missing_count"], 1)
        self.assertEqual(diagnostics["quote_prefetch_missing_symbols"], ["000660"])
        self.assertEqual(diagnostics["interrupted_reason"], "quote_prefetch_missing")

    def test_prefetch_uses_quote_credentials_without_global_metrics(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            buy_scan_prefetch_deadline_enabled=True,
            buy_scan_quote_prefetch_deadline_seconds=18.0,
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ[BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND] = "20"
        os.environ[BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS] = "0"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        with (
            mock.patch(
                "app.scanner.quote_account.issue_access_token_for",
                return_value="live-token",
            ),
            mock.patch(
                "app.scanner.quote_account.inquire_price_for_credentials",
                return_value={"rt_cd": "0", "output": {"stck_shrn_iscd": "005930"}},
            ) as inquire_price_for_credentials,
        ):
            result = prefetch_buy_scan_prices(
                symbols=("005930", "000660", "005930"),
                settings=settings,
                execution_token="mock-token",
            )

        self.assertEqual(result.requested_symbols, ("005930", "000660"))
        self.assertEqual(result.completed_symbols, ("005930", "000660"))
        self.assertEqual(result.failed_symbols, ())
        self.assertEqual(set(result.price_data_by_symbol), {"005930", "000660"})
        self.assertEqual(inquire_price_for_credentials.call_count, 2)
        for call in inquire_price_for_credentials.call_args_list:
            self.assertEqual(call.kwargs["use_global_throttle"], False)
            self.assertEqual(call.kwargs["record_metrics"], False)

    def test_prefetch_deadline_returns_partial_results_without_fetching_remaining_symbols(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            buy_scan_prefetch_deadline_enabled=True,
            buy_scan_quote_prefetch_deadline_seconds=1.5,
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        current_time = 0.0
        fetched: list[str] = []

        def fake_clock() -> float:
            return current_time

        def fake_fetch(symbol: str, **_kwargs):
            nonlocal current_time
            fetched.append(symbol)
            current_time += 1.0
            return {"rt_cd": "0", "output": {"stck_shrn_iscd": symbol}}

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            return_value="live-token",
        ):
            result = prefetch_buy_scan_prices(
                symbols=("005930", "000660", "035420"),
                settings=settings,
                execution_token="mock-token",
                clock=fake_clock,
                fetch_price_for_credentials=fake_fetch,
                throttle_quote_lane=lambda _env: 0.0,
            )

        self.assertEqual(fetched, ["005930", "000660"])
        self.assertEqual(result.completed_symbols, ("005930", "000660"))
        self.assertEqual(result.skipped_deadline_symbols, ("035420",))
        self.assertTrue(result.deadline_hit)
        self.assertEqual(result.deadline_seconds, 1.5)
        self.assertEqual(result.success_ratio, round(2 / 3, 4))

    def test_prefetch_request_timeout_is_budget_aware_and_does_not_retry_entire_cycle(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            buy_scan_prefetch_deadline_enabled=True,
            buy_scan_quote_prefetch_deadline_seconds=0.75,
            buy_scan_quote_request_timeout_seconds=2.0,
            buy_scan_quote_max_attempts=1,
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        current_time = 0.0
        calls: list[dict[str, object]] = []

        def fake_clock() -> float:
            return current_time

        def fake_fetch(symbol: str, **kwargs):
            nonlocal current_time
            calls.append({"symbol": symbol, **kwargs})
            current_time += float(kwargs["request_timeout_seconds"])
            raise TimeoutError("timed out")

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            return_value="live-token",
        ):
            result = prefetch_buy_scan_prices(
                symbols=("005930", "000660", "035420"),
                settings=settings,
                execution_token="mock-token",
                clock=fake_clock,
                fetch_price_for_credentials=fake_fetch,
                throttle_quote_lane=lambda _env: 0.0,
            )

        self.assertEqual([call["symbol"] for call in calls], ["005930"])
        self.assertEqual(calls[0]["request_timeout_seconds"], 0.75)
        self.assertEqual(calls[0]["max_attempts"], 1)
        self.assertEqual(result.failed_symbols, ("005930",))
        self.assertEqual(result.skipped_deadline_symbols, ("000660", "035420"))
        self.assertEqual(result.budget_skipped_symbols, ("000660", "035420"))
        self.assertEqual(result.timeout_count, 1)
        self.assertTrue(result.deadline_hit)
        self.assertLess(current_time, 60.0)


if __name__ == "__main__":
    unittest.main()


class PrefetchCollectedTimestampTests(BuyScanQuoteAccountTests):
    def test_prefetch_records_per_symbol_collection_clock(self) -> None:
        settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            buy_scan_prefetch_deadline_enabled=False,
        )
        self._clear_env()
        os.environ[BUY_SCAN_QUOTE_KIS_ENV] = "live"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"

        current_time = 0.0

        def fake_clock() -> float:
            return current_time

        def fake_fetch(symbol: str, **_kwargs):
            nonlocal current_time
            current_time += 1.0
            return {"rt_cd": "0", "output": {"stck_shrn_iscd": symbol}}

        with mock.patch(
            "app.scanner.quote_account.issue_access_token_for",
            return_value="live-token",
        ):
            result = prefetch_buy_scan_prices(
                symbols=("005930", "000660"),
                settings=settings,
                execution_token="mock-token",
                clock=fake_clock,
                fetch_price_for_credentials=fake_fetch,
                throttle_quote_lane=lambda _env: 0.0,
            )

        self.assertEqual(
            result.collected_monotonic_by_symbol,
            {"005930": 1.0, "000660": 2.0},
        )
