from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.scanner import service as scanner_service


class ScannerParseErrorSkipTests(unittest.TestCase):
    def test_duplicate_symbols_are_scanned_once(self) -> None:
        symbols = ("005930", "000660", "005930", "035420", "000660")
        settings = SimpleNamespace(
            target_symbols=symbols,
            scan_symbols_max_per_cycle=len(symbols),
        )
        analyzed_symbols: list[str] = []

        fake_symbol_metrics = {
            "api_ms": 0.0,
            "parse_ms": 0.0,
            "score_calc_ms": 0.0,
            "candidate_build_ms": 0.0,
        }

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
            analyzed_symbols.append(symbol)
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, dict(fake_symbol_metrics)

        with (
            mock.patch.object(scanner_service, "_analyze_symbol_with_metrics", side_effect=fake_analyze),
            mock.patch.object(
                scanner_service, "get_throttle_metrics_summary", return_value={}
            ),
        ):
            results = scanner_service.scan_target_symbols(
                settings=settings,
                token="tok",
                symbols=symbols,
            )

        self.assertEqual(analyzed_symbols, ["005930", "000660", "035420"])
        self.assertEqual([r.symbol for r in results], ["005930", "000660", "035420"])

        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertEqual(diagnostics["requested_count"], 3)
        self.assertEqual(diagnostics["quote_request_count"], 3)

    def test_value_error_skips_symbol_instead_of_aborting(self) -> None:
        symbols = ("005930", "000660", "035420")
        settings = SimpleNamespace(
            target_symbols=symbols,
            scan_symbols_max_per_cycle=len(symbols),
        )

        fake_symbol_metrics = {
            "api_ms": 0.0,
            "parse_ms": 0.0,
            "score_calc_ms": 0.0,
            "candidate_build_ms": 0.0,
        }

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
            if symbol == "000660":
                raise ValueError("현재가 응답에 필수 필드 stck_shrn_iscd 가 없습니다.")
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, dict(fake_symbol_metrics)

        with (
            mock.patch.object(scanner_service, "_analyze_symbol_with_metrics", side_effect=fake_analyze),
            mock.patch.object(
                scanner_service, "get_throttle_metrics_summary", return_value={}
            ),
        ):
            results = scanner_service.scan_target_symbols(
                settings=settings,
                token="tok",
            )

        scanned = {r.symbol for r in results}
        self.assertIn("005930", scanned)
        self.assertIn("035420", scanned)
        self.assertNotIn("000660", scanned)

        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertEqual(diagnostics["parse_error_skipped_count"], 1)
        self.assertEqual(diagnostics["parse_error_skipped_symbols"], ["000660"])

    def test_value_error_rate_limit_stops_scan_instead_of_parse_skip(self) -> None:
        symbols = ("005930", "000660", "035420")
        settings = SimpleNamespace(
            target_symbols=symbols,
            scan_symbols_max_per_cycle=len(symbols),
        )

        fake_symbol_metrics = {
            "api_ms": 0.0,
            "parse_ms": 0.0,
            "score_calc_ms": 0.0,
            "candidate_build_ms": 0.0,
        }

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
            if symbol == "000660":
                raise ValueError("EGW00201 초당 거래건수 초과")
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, dict(fake_symbol_metrics)

        with (
            mock.patch.object(scanner_service, "_analyze_symbol_with_metrics", side_effect=fake_analyze),
            mock.patch.object(
                scanner_service, "get_throttle_metrics_summary", return_value={}
            ),
        ):
            results = scanner_service.scan_target_symbols(
                settings=settings,
                token="tok",
            )

        self.assertEqual([r.symbol for r in results], ["005930"])

        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertTrue(diagnostics["rate_limit_triggered"])
        self.assertEqual(diagnostics["interrupted_reason"], "rate_limit_parse_skip")
        self.assertEqual(diagnostics["rate_limit_partial_stop_symbol"], "000660")
        self.assertEqual(diagnostics["rate_limit_partial_completed_count"], 1)
        self.assertEqual(diagnostics["rate_limit_partial_remaining_count"], 2)
        self.assertEqual(diagnostics["parse_error_skipped_count"], 0)
        self.assertEqual(diagnostics["parse_error_skipped_symbols"], [])

    def test_runtime_error_rate_limit_stops_scan_instead_of_aborting(self) -> None:
        symbols = ("005930", "000660", "035420")
        settings = SimpleNamespace(
            target_symbols=symbols,
            scan_symbols_max_per_cycle=len(symbols),
        )

        fake_symbol_metrics = {
            "api_ms": 0.0,
            "parse_ms": 0.0,
            "score_calc_ms": 0.0,
            "candidate_build_ms": 0.0,
        }

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
            if symbol == "000660":
                raise RuntimeError(
                    "현재가 조회 실패(000660): {'msg_cd': 'EGW00201', 'msg1': '초당 거래건수 초과'}"
                )
            fake_result = SimpleNamespace(symbol=symbol, sort_key=(0, 0.0, symbol))
            return fake_result, dict(fake_symbol_metrics)

        with (
            mock.patch.object(scanner_service, "_analyze_symbol_with_metrics", side_effect=fake_analyze),
            mock.patch.object(
                scanner_service, "get_throttle_metrics_summary", return_value={}
            ),
        ):
            results = scanner_service.scan_target_symbols(
                settings=settings,
                token="tok",
            )

        self.assertEqual([r.symbol for r in results], ["005930"])

        diagnostics = scanner_service.get_last_scan_diagnostics()
        self.assertTrue(diagnostics["rate_limit_triggered"])
        self.assertEqual(diagnostics["interrupted_reason"], "rate_limit_parse_skip")
        self.assertEqual(diagnostics["rate_limit_partial_stop_symbol"], "000660")
        self.assertEqual(diagnostics["parse_error_skipped_count"], 0)

    def test_rate_limit_detection_uses_common_kis_markers(self) -> None:
        exc = scanner_service.ApiHttpError(
            "현재가 조회 실패",
            status_code=200,
            data={"msg1": "초당 거래건수 초과"},
        )

        self.assertTrue(scanner_service._looks_like_rate_limit_error(exc))


if __name__ == "__main__":
    unittest.main()
