from __future__ import annotations

import unittest

from app.market_data import live_snapshot_collect


class ExtractSymbolsTests(unittest.TestCase):
    def test_filters_six_digit_dedupes_and_respects_field_priority_and_top_n(self) -> None:
        rows = [
            {"mksc_shrn_iscd": "005930"},  # priority field
            {"stck_shrn_iscd": "000660"},  # second priority
            {"pdno": "035720"},  # third priority
            {"pdno": "005930"},  # duplicate -> dropped
            {"pdno": "ABCDEF"},  # non 6-digit -> dropped
            {"pdno": "12345"},  # 5-digit -> dropped
            {"pdno": "207940"},
        ]
        result = live_snapshot_collect._extract_symbols(rows, top_n=3)
        self.assertEqual(result, ["005930", "000660", "035720"])


class MergeRankedListsTests(unittest.TestCase):
    def test_round_robin_interleave_dedupe_and_early_return_at_top_n(self) -> None:
        a = ["005930", "000660", "035720"]
        b = ["000660", "207940"]  # 000660 duplicate of a[1]
        c = ["068270", "005930"]  # 005930 duplicate of a[0]
        merged = live_snapshot_collect._merge_ranked_lists(a, b, c, top_n=4)
        # round-robin: a[0]=005930, b[0]=000660, c[0]=068270, then a[1]=000660(dup),
        # b[1]=207940 -> reaches top_n=4
        self.assertEqual(merged, ["005930", "000660", "068270", "207940"])


class ParseResponseBodyTests(unittest.TestCase):
    def test_dict_json_passthrough(self) -> None:
        self.assertEqual(
            live_snapshot_collect._parse_response_body('{"rt_cd": "0"}'),
            {"rt_cd": "0"},
        )

    def test_non_dict_and_broken_json_fall_back_to_raw_text(self) -> None:
        self.assertEqual(
            live_snapshot_collect._parse_response_body("[1, 2, 3]"),
            {"raw_text": "[1, 2, 3]"},
        )
        self.assertEqual(
            live_snapshot_collect._parse_response_body("not json"),
            {"raw_text": "not json"},
        )


class ParseRuntimeTimestampTests(unittest.TestCase):
    def test_valid_iso_else_none(self) -> None:
        from datetime import datetime

        parsed = live_snapshot_collect._parse_runtime_timestamp("2026-06-07T09:00:00")
        self.assertIsInstance(parsed, datetime)
        self.assertIsNone(live_snapshot_collect._parse_runtime_timestamp(""))
        self.assertIsNone(live_snapshot_collect._parse_runtime_timestamp(None))
        self.assertIsNone(live_snapshot_collect._parse_runtime_timestamp("garbage"))


class PositiveIntTests(unittest.TestCase):
    def test_positive_passes_zero_and_negative_raise_with_name(self) -> None:
        self.assertEqual(live_snapshot_collect._positive_int(5, name="top_n"), 5)
        with self.assertRaises(ValueError) as ctx_zero:
            live_snapshot_collect._positive_int(0, name="top_n")
        self.assertIn("top_n", str(ctx_zero.exception))
        with self.assertRaises(ValueError) as ctx_neg:
            live_snapshot_collect._positive_int(-3, name="ttl_seconds")
        self.assertIn("ttl_seconds", str(ctx_neg.exception))


class FirstEnvTests(unittest.TestCase):
    def test_returns_first_nonblank_skips_blank_and_falls_back_to_default(self) -> None:
        from unittest import mock

        with mock.patch.dict(
            "os.environ",
            {"A": "  ", "B": "value-b", "C": "value-c"},
            clear=True,
        ):
            self.assertEqual(
                live_snapshot_collect._first_env("A", "B", "C"), "value-b"
            )
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                live_snapshot_collect._first_env("X", "Y", default="fallback"),
                "fallback",
            )


class BuildHeadersTests(unittest.TestCase):
    def test_exact_key_set_and_bearer_authorization(self) -> None:
        headers = live_snapshot_collect._build_headers(
            token="tok",
            tr_id="FHPST01710000",
            app_key="key",
            app_secret="secret",
        )
        self.assertEqual(
            set(headers.keys()),
            {"content-type", "authorization", "appkey", "appsecret", "tr_id", "custtype"},
        )
        self.assertEqual(headers["authorization"], "Bearer tok")
        self.assertEqual(headers["tr_id"], "FHPST01710000")
        self.assertEqual(headers["custtype"], "P")


class RuntimePressureDeferReasonTests(unittest.TestCase):
    def test_defers_on_fresh_snapshot_with_recent_rate_limit_backoff(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 6, 7, 9, 0, 0, tzinfo=timezone.utc)
        reason = live_snapshot_collect._build_runtime_pressure_defer_reason(
            existing_snapshot={
                "updated_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
                "top_symbols": ["005930"],
            },
            runtime_state={
                "last_cycle_started_at": (now - timedelta(seconds=20)).isoformat(),
                "rate_limit_source": "sell_watch",
                "backoff_applied_seconds": 180,
            },
            now=now,
            ttl_seconds=420,
            refresh_interval_seconds=180,
        )
        self.assertIsNotNone(reason)
        self.assertIn("sell_watch", str(reason))

    def test_none_when_snapshot_older_than_ttl(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 6, 7, 9, 0, 0, tzinfo=timezone.utc)
        reason = live_snapshot_collect._build_runtime_pressure_defer_reason(
            existing_snapshot={
                "updated_at": (now - timedelta(seconds=600)).isoformat(timespec="seconds"),
                "top_symbols": ["005930"],
            },
            runtime_state={
                "last_cycle_started_at": (now - timedelta(seconds=15)).isoformat(),
                "rate_limit_source": "sell_watch",
                "backoff_applied_seconds": 180,
            },
            now=now,
            ttl_seconds=420,
            refresh_interval_seconds=180,
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
