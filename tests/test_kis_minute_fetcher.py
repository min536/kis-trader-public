import unittest
from datetime import date
from unittest import mock

from app.research.ingest.kis_minute_fetcher import (
    KRXMasterETF,
    build_current_plus_etf_symbols,
    fetch_minute_csvs,
    minute_csv_path,
    parse_kis_date,
    parse_kis_time,
    parse_kospi_master_etfs,
    parse_minute_output,
    previous_minute_cursor,
    write_minute_csv,
)
from app.research.ingest import kis_minute_fetcher


class MinuteOutputParsingTest(unittest.TestCase):
    def test_parse_minute_output_maps_kis_fields(self):
        rows = parse_minute_output(
            [
                {
                    "stck_bsop_date": "20260624",
                    "stck_cntg_hour": "93000",
                    "stck_prpr": "70500",
                    "stck_oprc": "70000",
                    "stck_hgpr": "70600",
                    "stck_lwpr": "69900",
                    "cntg_vol": "1234",
                },
                {"stck_bsop_date": "bad"},
            ],
            symbol="005930",
        )
        self.assertEqual(
            rows,
            [
                {
                    "datetime": "2026-06-24 09:30:00",
                    "date": "2026-06-24",
                    "time": "09:30:00",
                    "symbol": "005930",
                    "open": 70000,
                    "high": 70600,
                    "low": 69900,
                    "close": 70500,
                    "volume": 1234,
                }
            ],
        )

    def test_parse_date_time_and_previous_cursor(self):
        self.assertEqual(parse_kis_date("20260624"), "2026-06-24")
        self.assertEqual(parse_kis_time("93000"), "09:30:00")
        self.assertEqual(previous_minute_cursor("09:30:00"), "092900")


class MinuteCsvWriteTest(unittest.TestCase):
    def test_write_minute_csv_uses_expected_raw_layout(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            path = minute_csv_path(raw_dir, date(2026, 6, 24), "005930")
            write_minute_csv(
                path,
                [
                    {
                        "datetime": "2026-06-24 09:00:00",
                        "open": 1,
                        "high": 2,
                        "low": 1,
                        "close": 2,
                        "volume": 10,
                    }
                ],
            )
            self.assertEqual(
                path,
                raw_dir / "date=2026-06-24" / "symbol=005930_20260624.csv",
            )
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "datetime,open,high,low,close,volume\n"
                "2026-06-24 09:00:00,1,2,1,2,10\n",
            )


class CurrentPlusETFUniverseTest(unittest.TestCase):
    def test_build_current_plus_etf_symbols_preserves_current_then_adds_etfs(self):
        symbols = build_current_plus_etf_symbols(
            current_symbols=["005930", "000660", "005930"],
            tagged_etfs=["069500", "000660"],
            master_etfs=[
                KRXMasterETF("102110", "TIGER 200", 20),
                KRXMasterETF("133690", "TIGER 미국나스닥100", 200),
            ],
            target_count=5,
        )
        self.assertEqual(
            symbols,
            ["005930", "000660", "069500", "133690", "102110"],
        )

    def test_build_current_plus_etf_symbols_raises_when_master_is_short(self):
        with self.assertRaises(ValueError):
            build_current_plus_etf_symbols(
                current_symbols=["005930"],
                master_etfs=[],
                target_count=2,
            )


class KospiMasterETFParsingTest(unittest.TestCase):
    def test_parse_kospi_master_etfs_filters_etp_etf_like_rows(self):
        content = b"\n".join(
            [
                _master_line("069500", "KODEX 200", etp="Y", prev_volume=3000),
                _master_line("580001", "TRUE KOSPI ETN", etp="Y", prev_volume=9999),
                _master_line("005930", "삼성전자", etp="N", prev_volume=5000),
                _master_line("102110", "TIGER 200", etp="2", prev_volume=7000),
            ]
        )
        etfs = parse_kospi_master_etfs(content)
        self.assertEqual(
            etfs,
            [
                KRXMasterETF("069500", "KODEX 200", 3000),
                KRXMasterETF("102110", "TIGER 200", 7000),
            ],
        )


class ParallelFetchTest(unittest.TestCase):
    def test_fetch_minute_csvs_parallel_writes_manifest_and_csvs(self):
        import json
        import tempfile
        from pathlib import Path

        def fake_fetch(**kwargs):
            symbol = kwargs["symbol"]
            target_date = kwargs["target_date"]
            return (
                [
                    {
                        "datetime": f"{target_date.isoformat()} 09:00:00",
                        "open": 1,
                        "high": 2,
                        "low": 1,
                        "close": 2,
                        "volume": 10,
                    }
                ],
                1,
            )

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            with mock.patch.object(kis_minute_fetcher, "_get_bearer", return_value="b"):
                with mock.patch.object(
                    kis_minute_fetcher,
                    "fetch_symbol_day_minute_rows",
                    side_effect=fake_fetch,
                ):
                    summary = fetch_minute_csvs(
                        symbols=["005930", "000660"],
                        start_date=date(2026, 6, 24),
                        end_date=date(2026, 6, 24),
                        raw_dir=raw,
                        settings=object(),
                        workers=2,
                        rate_limit_per_second=20,
                        delay_seconds=0,
                    )
            self.assertEqual(summary.saved_files, 2)
            self.assertEqual(summary.rows, 2)
            manifest_rows = [
                json.loads(line)
                for line in (raw / "fetch_manifest.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                sorted((row["status"], row["symbol"]) for row in manifest_rows),
                [("saved", "000660"), ("saved", "005930")],
            )
            self.assertTrue(
                (raw / "date=2026-06-24" / "symbol=005930_20260624.csv").exists()
            )


class BearerTokenTest(unittest.TestCase):
    def test_get_bearer_uses_env_specific_auth_cache(self):
        class Settings:
            base_url = "https://openapi.koreainvestment.com:9443"
            app_key = "k"
            app_secret = "s"

        with mock.patch(
            "app.auth.token.issue_access_token_for", return_value="cached-live"
        ) as issue_access_token_for:
            bearer = kis_minute_fetcher._get_bearer(Settings())

        self.assertEqual(bearer, "cached-live")
        issue_access_token_for.assert_called_once_with(
            base_url="https://openapi.koreainvestment.com:9443",
            app_key="k",
            app_secret="s",
            env="live",
        )


class RequestJsonRetryTest(unittest.TestCase):
    @staticmethod
    def _resp(payload):
        import json

        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        return resp

    def _call(self, **overrides):
        kwargs = dict(
            url="http://127.0.0.1:1/api",
            headers={"authorization": "Bearer x"},
            params={"FID_INPUT_ISCD": "005930"},
            symbol="005930",
            target_date=date(2026, 6, 24),
            input_hour="153000",
            max_retries=3,
            retry_backoff_seconds=0.0,
            rate_limiter=None,
        )
        kwargs.update(overrides)
        return kis_minute_fetcher._request_json(**kwargs)

    def test_request_json_retries_on_egw00201_then_succeeds(self):
        throttled = self._resp(
            {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
        )
        ok = self._resp({"rt_cd": "0", "output2": []})
        with mock.patch.object(
            kis_minute_fetcher.urllib.request,
            "urlopen",
            side_effect=[throttled, ok],
        ) as urlopen:
            data = self._call()
        self.assertEqual(data["rt_cd"], "0")
        self.assertEqual(urlopen.call_count, 2)

    def test_request_json_raises_on_non_retryable_kis_error(self):
        bad = self._resp(
            {"rt_cd": "1", "msg_cd": "40310000", "msg1": "조회할 수 없는 종목"}
        )
        with mock.patch.object(
            kis_minute_fetcher.urllib.request, "urlopen", side_effect=[bad]
        ) as urlopen:
            with self.assertRaises(kis_minute_fetcher.KISMinuteFetchError):
                self._call()
        self.assertEqual(urlopen.call_count, 1)

    def test_request_json_exhausts_retries_on_http_error(self):
        import io
        import urllib.error

        http_error = urllib.error.HTTPError(
            "http://x", 500, "Server Error", {}, io.BytesIO(b"boom")
        )
        with mock.patch.object(
            kis_minute_fetcher.urllib.request, "urlopen", side_effect=http_error
        ) as urlopen:
            with self.assertRaises(kis_minute_fetcher.KISMinuteFetchError) as ctx:
                self._call(max_retries=2)
        self.assertIn("attempts=3", str(ctx.exception))
        self.assertEqual(urlopen.call_count, 3)


def _master_line(code: str, name: str, *, etp: str, prev_volume: int) -> bytes:
    part1 = (
        code.encode("euc-kr").ljust(9)
        + b"KR0000000000"
        + name.encode("euc-kr")
    )
    fields = [""] * len(kis_minute_fetcher._KOSPI_MASTER_PART2_WIDTHS)
    fields[kis_minute_fetcher._KOSPI_MASTER_ETP_INDEX] = etp
    fields[kis_minute_fetcher._KOSPI_MASTER_PREV_VOLUME_INDEX] = str(prev_volume)
    part2 = b"".join(
        field.encode("euc-kr").ljust(width)[:width]
        for field, width in zip(fields, kis_minute_fetcher._KOSPI_MASTER_PART2_WIDTHS)
    )
    return part1 + part2


if __name__ == "__main__":
    unittest.main()
