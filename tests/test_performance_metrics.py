"""Tests for app.reporting.performance_metrics (R3-S6).

performance.py의 순수 수학/포맷/주문레코드 반복/기간 헬퍼 클러스터를 verbatim
이동. performance는 facade로 동일 객체를 재수출해야 한다 (patch/import seam
보존 — tests/test_daily_pnl_brake.py가 performance._iter_operating_order_records를
patch.object로 가로챈다).
"""

from __future__ import annotations

import unittest

from app.reporting import performance, performance_metrics

_METRIC_NAMES = (
    "_safe_ratio",
    "_mean",
    "_std",
    "_downside_std",
    "_format_signed_krw",
    "_format_signed_pct",
    "_format_pct",
    "_format_optional_pct",
    "_format_optional_ratio",
    "_is_operating_record",
    "_iter_operating_order_records",
    "_timestamp_to_datetime",
    "_same_day",
    "_same_week",
    "_same_month",
    "_choose_period_start",
)


class MathHelperTests(unittest.TestCase):
    def test_mean_of_values_and_empty_list(self) -> None:
        self.assertEqual(performance_metrics._mean([1.0, 2.0, 3.0]), 2.0)
        self.assertEqual(performance_metrics._mean([]), 0.0)

    def test_std_uses_sample_variance_and_small_samples_are_zero(self) -> None:
        self.assertAlmostEqual(performance_metrics._std([1.0, 2.0, 3.0]), 1.0)
        self.assertEqual(performance_metrics._std([5.0]), 0.0)

    def test_downside_std_only_uses_negative_values(self) -> None:
        self.assertAlmostEqual(
            performance_metrics._downside_std([-1.0, -3.0, 2.0, 4.0]),
            performance_metrics._std([-1.0, -3.0]),
        )
        self.assertEqual(performance_metrics._downside_std([-1.0, 2.0]), 0.0)


class FormatHelperTests(unittest.TestCase):
    def test_signed_krw_covers_positive_negative_zero(self) -> None:
        self.assertEqual(performance_metrics._format_signed_krw(1500), "+1,500원")
        self.assertEqual(performance_metrics._format_signed_krw(-1500), "-1,500원")
        self.assertEqual(performance_metrics._format_signed_krw(0), "0원")

    def test_signed_pct_covers_positive_negative_zero(self) -> None:
        self.assertEqual(performance_metrics._format_signed_pct(1.234), "+1.23%")
        self.assertEqual(performance_metrics._format_signed_pct(-1.236), "-1.24%")
        self.assertEqual(performance_metrics._format_signed_pct(0.0), "0.00%")

    def test_plain_pct(self) -> None:
        self.assertEqual(performance_metrics._format_pct(12.345), "12.35%")

    def test_optional_pct_falls_back_on_none(self) -> None:
        self.assertEqual(performance_metrics._format_optional_pct(None), "데이터 부족")
        self.assertEqual(performance_metrics._format_optional_pct(1.0), "+1.00%")

    def test_optional_ratio_falls_back_on_none(self) -> None:
        self.assertEqual(performance_metrics._format_optional_ratio(None), "표본 부족")
        self.assertEqual(performance_metrics._format_optional_ratio(1.23456), "1.2346")


class OperatingRecordTests(unittest.TestCase):
    def test_non_mock_environment_is_excluded(self) -> None:
        self.assertFalse(
            performance_metrics._is_operating_record({"environment": "live"})
        )
        self.assertTrue(performance_metrics._is_operating_record({}))

    def test_iter_operating_order_records_reads_logs_and_filters(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "orders.jsonl"
            log_path.write_text(
                json.dumps({"symbol": "005930", "environment": "mock"})
                + "\n"
                + json.dumps({"symbol": "000100", "environment": "live"})
                + "\n",
                encoding="utf-8",
            )
            missing_path = Path(tmp_dir) / "absent.jsonl"
            with mock.patch.object(
                performance_metrics,
                "get_order_log_read_paths",
                return_value=[log_path, missing_path],
            ):
                records = performance_metrics._iter_operating_order_records()

        self.assertEqual([r["symbol"] for r in records], ["005930"])

    def test_sell_test_mode_records_are_excluded(self) -> None:
        record = {"raw_response": {"sell_test_mode": "forced"}}
        self.assertFalse(performance_metrics._is_operating_record(record))
        self.assertTrue(
            performance_metrics._is_operating_record(
                {"raw_response": {"sell_test_mode": "off"}}
            )
        )


class PeriodHelperTests(unittest.TestCase):
    def test_timestamp_to_datetime_handles_naive_invalid_and_aware(self) -> None:
        from app.core.time_utils import KOREA_TZ

        self.assertIsNone(performance_metrics._timestamp_to_datetime(""))
        self.assertIsNone(performance_metrics._timestamp_to_datetime("not-a-date"))
        naive = performance_metrics._timestamp_to_datetime("2026-06-11T09:00:00")
        self.assertEqual(naive.tzinfo, KOREA_TZ)

    def test_same_day_week_month_predicates(self) -> None:
        from datetime import datetime

        base = datetime(2026, 6, 11, 9, 0)
        self.assertTrue(
            performance_metrics._same_day(datetime(2026, 6, 11, 23, 0), base)
        )
        self.assertFalse(
            performance_metrics._same_day(datetime(2026, 6, 12, 0, 0), base)
        )
        self.assertTrue(
            performance_metrics._same_week(datetime(2026, 6, 8, 0, 0), base)
        )
        self.assertTrue(
            performance_metrics._same_month(datetime(2026, 6, 1, 0, 0), base)
        )

    def test_choose_period_start_picks_earliest_matching_snapshot(self) -> None:
        from datetime import datetime

        target = datetime(2026, 6, 11, 15, 0)
        snapshots = [
            {"timestamp": "2026-06-11T14:00:00"},
            {"timestamp": "2026-06-11T09:00:00"},
            {"timestamp": "2026-06-10T09:00:00"},
            {"timestamp": ""},
        ]
        chosen = performance_metrics._choose_period_start(
            snapshots,
            target_dt=target,
            predicate=performance_metrics._same_day,
        )
        self.assertEqual(chosen, {"timestamp": "2026-06-11T09:00:00"})


class PerformanceMetricsFacadeTests(unittest.TestCase):
    def test_performance_binds_canonical_objects(self) -> None:
        for name in _METRIC_NAMES:
            with self.subTest(name):
                self.assertIs(
                    getattr(performance, name),
                    getattr(performance_metrics, name),
                )


if __name__ == "__main__":
    unittest.main()
