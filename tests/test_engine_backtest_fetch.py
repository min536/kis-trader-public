from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - test env without optional dependency
    requests = None

if requests is not None:
    from backtester.engine_backtest.data_fetcher import (
        HistoricalDataFetchError,
        _fetch_batch,
        fetch_symbols_to_csv,
    )


@unittest.skipIf(requests is None, "requests is not installed in this Python environment")
class EngineBacktestFetchTests(unittest.TestCase):
    def test_fetch_batch_uses_requested_start_and_end_dates(self) -> None:
        captured: dict[str, object] = {}

        def fake_get(url, headers=None, params=None, timeout=None):  # type: ignore[no-untyped-def]
            captured["params"] = params
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"output2": []}
            return response

        settings = Mock(base_url="https://example.test", app_key="key", app_secret="secret")
        with patch("backtester.engine_backtest.data_fetcher.requests.get", side_effect=fake_get):
            rows = _fetch_batch(
                symbol="000660",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
                settings=settings,
                token="token",
                max_retries=0,
                retry_backoff_seconds=0.0,
            )

        self.assertEqual(rows, [])
        self.assertEqual(captured["params"]["FID_INPUT_DATE_1"], "20240101")
        self.assertEqual(captured["params"]["FID_INPUT_DATE_2"], "20240630")

    def test_best_effort_continues_after_symbol_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "prices.csv"
            settings = Mock()

            def fake_token(_settings):  # type: ignore[no-untyped-def]
                return "token"

            def fake_fetch_symbol(**kwargs):  # type: ignore[no-untyped-def]
                if kwargs["symbol"] == "000660":
                    raise HistoricalDataFetchError("HTTP 500 Internal Server Error")
                return [
                    {
                        "date": "2026-04-14",
                        "symbol": kwargs["symbol"],
                        "open": 100,
                        "high": 110,
                        "low": 90,
                        "close": 105,
                        "volume": 1000,
                        "prev_close": 99,
                    }
                ]

            with patch("backtester.engine_backtest.data_fetcher._get_access_token", side_effect=fake_token):
                with patch("backtester.engine_backtest.data_fetcher._fetch_symbol", side_effect=fake_fetch_symbol):
                    fetch_symbols_to_csv(
                        symbols=["005930", "000660"],
                        start_date=date(2026, 4, 1),
                        end_date=date(2026, 4, 14),
                        output_path=output_path,
                        settings=settings,
                        mode="best-effort",
                        max_retries=0,
                        retry_backoff_seconds=0.0,
                    )

            with output_path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "005930")

    def test_strict_raises_on_symbol_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "prices.csv"
            settings = Mock()

            with patch("backtester.engine_backtest.data_fetcher._get_access_token", return_value="token"):
                with patch(
                    "backtester.engine_backtest.data_fetcher._fetch_symbol",
                    side_effect=HistoricalDataFetchError("HTTP 500 Internal Server Error"),
                ):
                    with self.assertRaises(HistoricalDataFetchError):
                        fetch_symbols_to_csv(
                            symbols=["000660"],
                            start_date=date(2026, 4, 1),
                            end_date=date(2026, 4, 14),
                            output_path=output_path,
                            settings=settings,
                            mode="strict",
                            max_retries=0,
                            retry_backoff_seconds=0.0,
                        )

    def test_fetch_batch_retries_and_raises_helpful_error(self) -> None:
        response = requests.Response()
        response.status_code = 500
        response.reason = "Internal Server Error"
        response._content = b'{"msg":"server exploded"}'

        def fake_get(url, headers=None, params=None, timeout=None):  # type: ignore[no-untyped-def]
            raise requests.HTTPError("boom", response=response)

        settings = Mock(base_url="https://example.test", app_key="key", app_secret="secret")
        with patch("backtester.engine_backtest.data_fetcher.requests.get", side_effect=fake_get):
            with patch("backtester.engine_backtest.data_fetcher.time.sleep"):
                with self.assertRaises(HistoricalDataFetchError) as ctx:
                    _fetch_batch(
                        symbol="000660",
                        start_date=date(2024, 1, 1),
                        end_date=date(2024, 6, 30),
                        settings=settings,
                        token="token",
                        max_retries=2,
                        retry_backoff_seconds=0.1,
                    )

        message = str(ctx.exception)
        self.assertIn("symbol=000660", message)
        self.assertIn("start=20240101", message)
        self.assertIn("end=20240630", message)
        self.assertIn("attempts=3", message)
        self.assertIn("HTTP 500", message)


if __name__ == "__main__":
    unittest.main()
