import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.research.ingest import toss_minute_fetcher
from app.research.ingest.toss_minute_fetcher import (
    fetch_toss_minute_csvs,
    parse_toss_candles,
    write_rows_by_date,
)


class TossCandleParsingTest(unittest.TestCase):
    def test_parse_toss_candles_maps_ohlcv_fields(self):
        rows = parse_toss_candles(
            [
                {
                    "timestamp": "2026-06-25T09:01:00.000+09:00",
                    "openPrice": "72000",
                    "highPrice": "72100",
                    "lowPrice": "71900",
                    "closePrice": "72050",
                    "volume": "15200",
                    "currency": "KRW",
                },
                {"timestamp": ""},
            ],
            symbol="005930",
        )
        self.assertEqual(
            rows,
            [
                {
                    "datetime": "2026-06-25 09:01:00",
                    "date": "2026-06-25",
                    "time": "09:01:00",
                    "symbol": "005930",
                    "open": 72000,
                    "high": 72100,
                    "low": 71900,
                    "close": 72050,
                    "volume": 15200,
                }
            ],
        )


class TossRawWriteTest(unittest.TestCase):
    def test_write_rows_by_date_merges_and_deduplicates_existing_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            rows = [
                {
                    "datetime": "2026-06-25 09:00:00",
                    "date": "2026-06-25",
                    "time": "09:00:00",
                    "symbol": "005930",
                    "open": 1,
                    "high": 2,
                    "low": 1,
                    "close": 2,
                    "volume": 10,
                },
                {
                    "datetime": "2026-06-25 09:01:00",
                    "date": "2026-06-25",
                    "time": "09:01:00",
                    "symbol": "005930",
                    "open": 2,
                    "high": 3,
                    "low": 2,
                    "close": 3,
                    "volume": 20,
                },
            ]
            self.assertEqual(write_rows_by_date(raw, rows), 2)
            self.assertEqual(write_rows_by_date(raw, rows[:1]), 0)

            path = raw / "date=2026-06-25" / "symbol=005930_20260625.csv"
            with path.open(newline="", encoding="utf-8") as handle:
                stored = list(csv.DictReader(handle))
            self.assertEqual(
                [row["datetime"] for row in stored],
                ["2026-06-25 09:00:00", "2026-06-25 09:01:00"],
            )


class TossResumeTest(unittest.TestCase):
    def test_fetch_toss_minute_csvs_skips_unavailable_state_without_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            state_dir = raw / "_state"
            state_dir.mkdir(parents=True)
            (state_dir / "008560.json").write_text(
                json.dumps(
                    {
                        "symbol": "008560",
                        "status": "unavailable",
                        "next_before": None,
                        "pages": 0,
                        "rows": 0,
                        "api_calls": 0,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(toss_minute_fetcher, "_issue_bearer") as issue:
                summary = fetch_toss_minute_csvs(
                    symbols=["008560"],
                    raw_dir=raw,
                    before="2026-06-25T10:00:00+09:00",
                    rate_limit_per_second=100,
                )

            issue.assert_not_called()
            self.assertEqual(summary.skipped_symbols, 1)
            self.assertEqual(summary.api_calls, 0)

    def test_fetch_toss_minute_csvs_resumes_from_saved_cursor(self):
        first_page = {
            "candles": [
                {
                    "timestamp": "2026-06-25T09:00:00.000+09:00",
                    "openPrice": "1",
                    "highPrice": "2",
                    "lowPrice": "1",
                    "closePrice": "2",
                    "volume": "10",
                }
            ],
            "nextBefore": "2026-06-25T08:59:00.000+09:00",
        }
        final_page = {"candles": [], "nextBefore": None}

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            with mock.patch.object(
                toss_minute_fetcher, "_issue_bearer", return_value="bearer"
            ), mock.patch.object(
                toss_minute_fetcher, "_fetch_candle_page", return_value=first_page
            ):
                partial = fetch_toss_minute_csvs(
                    symbols=["005930"],
                    raw_dir=raw,
                    before="2026-06-25T10:00:00+09:00",
                    max_pages_per_symbol=1,
                    rate_limit_per_second=100,
                )
            self.assertEqual(partial.partial_symbols, 1)

            state_path = raw / "_state" / "005930.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "partial")
            self.assertEqual(state["next_before"], "2026-06-25T08:59:00.000+09:00")

            seen_before = []

            def fake_fetch(**kwargs):
                seen_before.append(kwargs["before"])
                return final_page

            with mock.patch.object(
                toss_minute_fetcher, "_issue_bearer", return_value="bearer"
            ), mock.patch.object(
                toss_minute_fetcher, "_fetch_candle_page", side_effect=fake_fetch
            ):
                completed = fetch_toss_minute_csvs(
                    symbols=["005930"],
                    raw_dir=raw,
                    before="should-not-be-used",
                    rate_limit_per_second=100,
                )
            self.assertEqual(completed.completed_symbols, 1)
            self.assertEqual(seen_before, ["2026-06-25T08:59:00.000+09:00"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "complete")
            self.assertIsNone(state["next_before"])


class TossRequestRetryTest(unittest.TestCase):
    @staticmethod
    def _resp(payload):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        return resp

    def test_request_json_retries_connection_reset(self):
        with mock.patch.object(
            toss_minute_fetcher.urllib.request,
            "urlopen",
            side_effect=[
                ConnectionResetError("reset by peer"),
                self._resp({"result": {"candles": [], "nextBefore": None}}),
            ],
        ) as urlopen:
            payload = toss_minute_fetcher._request_json(
                method="GET",
                url="https://example.invalid",
                headers={},
                max_retries=1,
                retry_backoff_seconds=0,
                context="test",
            )

        self.assertEqual(payload, {"result": {"candles": [], "nextBefore": None}})
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
