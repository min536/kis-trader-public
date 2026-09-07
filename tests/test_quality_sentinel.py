from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.market_data.quality_sentinel import (
    QUALITY_ARTIFACT_PATH,
    build_quality_alert_lines,
    evaluate_market_data_quality,
    write_market_data_quality_artifact,
)
from app.market_data.schema import MarketSnapshot


_NOW_EPOCH = 1_710_000_000.0


def _fresh_updated_at(age_seconds: float) -> str:
    return datetime.fromtimestamp(
        _NOW_EPOCH - age_seconds,
        tz=timezone.utc,
    ).isoformat()


def _clean_snapshot(symbol: str, current: int = 70_000) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        current_price=current,
        open_price=current - 1_000,
        low_price=current - 2_000,
        high_price=current + 1_000,
        prev_day_change_pct=1.5,
    )


class EvaluateMarketDataQualityTests(unittest.TestCase):
    def test_clean_snapshots_yield_ok_report(self) -> None:
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930"), _clean_snapshot("000660", current=12_345)],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.symbol_count, 2)
        self.assertEqual(report.ok_count, 2)
        self.assertEqual(report.issue_count, 0)
        self.assertEqual(report.issues, ())
        self.assertFalse(report.freshness.stale)
        self.assertEqual(report.freshness.reason, "fresh")

    def test_non_positive_current_price_is_flagged(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=0,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [_clean_snapshot("000660"), bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.symbol_count, 2)
        self.assertEqual(report.ok_count, 1)
        self.assertEqual(report.issue_count, 1)
        self.assertEqual(report.issues[0].symbol, "005930")
        self.assertEqual(report.issues[0].category, "non_positive_price")
        self.assertIn("current_price", report.issues[0].detail)

    def test_non_positive_open_and_low_prices_are_flagged(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=70_000,
            open_price=0,
            low_price=-1,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.ok_count, 0)
        details = {issue.detail for issue in report.issues}
        categories = {issue.category for issue in report.issues}
        self.assertEqual(categories, {"non_positive_price"})
        self.assertTrue(any("open_price" in detail for detail in details))
        self.assertTrue(any("low_price" in detail for detail in details))

    def test_current_price_above_intraday_high_is_out_of_range(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=100_000,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.issue_count, 1)
        self.assertEqual(report.issues[0].category, "price_out_of_range")
        self.assertIn("100000", report.issues[0].detail)

    def test_current_below_low_is_flagged_even_when_high_missing(self) -> None:
        # build_market_snapshot sets high_price=0 when stck_hgpr is absent, which
        # must not silently disable the lower-bound range check.
        bad = MarketSnapshot(
            symbol="005930",
            current_price=10,
            open_price=69_000,
            low_price=68_000,
            high_price=0,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        categories = {issue.category for issue in report.issues}
        self.assertIn("price_out_of_range", categories)

    def test_missing_high_price_is_flagged_so_upper_bound_gap_is_visible(self) -> None:
        # high_price=0 is the build_market_snapshot default when stck_hgpr is
        # absent; it disables the upper-bound range check, so it must be visible
        # (otherwise an over-high glitch with high=0 masquerades as "ok").
        glitch = MarketSnapshot(
            symbol="005930",
            current_price=700_000,
            open_price=69_000,
            low_price=68_000,
            high_price=0,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [glitch],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        missing = [i for i in report.issues if i.category == "missing_field"]
        self.assertTrue(any("high_price" in i.detail for i in missing))

    def test_low_above_high_is_out_of_range(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=70_000,
            open_price=69_000,
            low_price=72_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.issues[0].category, "price_out_of_range")
        self.assertIn("low", report.issues[0].detail)

    def test_daily_move_beyond_limit_is_implausible(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=70_000,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=-45.0,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.issue_count, 1)
        self.assertEqual(report.issues[0].category, "implausible_daily_move")

    def test_stale_snapshot_warns_without_symbol_issues(self) -> None:
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(400),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.issue_count, 0)
        self.assertTrue(report.freshness.stale)
        self.assertEqual(report.freshness.reason, "stale")
        self.assertEqual(report.freshness.stale_threshold_seconds, 360)

    def test_threshold_tracks_refresh_interval_not_fixed_constant(self) -> None:
        # Same 400s age that is stale at the default cadence stays fresh when the
        # refresh interval is longer (HT budget profile) — threshold = interval*2.
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=900,
            snapshot_updated_at=_fresh_updated_at(400),
        )
        self.assertEqual(report.freshness.stale_threshold_seconds, 1_800)
        self.assertFalse(report.freshness.stale)
        self.assertEqual(report.status, "ok")

    def test_threshold_floor_holds_at_ht_short_intervals(self) -> None:
        # HT stages use SHORTER refresh intervals (240→60s as r/s climbs 4→15);
        # the 360s floor must dominate so freshness can't tighten below it.
        for short_interval in (60, 120):
            report = evaluate_market_data_quality(
                [_clean_snapshot("005930")],
                now_epoch=_NOW_EPOCH,
                refresh_interval_seconds=short_interval,
                snapshot_updated_at=_fresh_updated_at(30),
            )
            with self.subTest(refresh_interval_seconds=short_interval):
                self.assertEqual(report.freshness.stale_threshold_seconds, 360)

    def test_missing_updated_at_is_stale(self) -> None:
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=None,
        )
        self.assertTrue(report.freshness.stale)
        self.assertEqual(report.freshness.reason, "updated_at_missing")
        self.assertIsNone(report.freshness.age_seconds)
        self.assertEqual(report.status, "warn")

    def test_non_finite_now_epoch_stays_json_safe_and_not_fresh(self) -> None:
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=float("inf"),
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertTrue(report.freshness.stale)
        self.assertIsNone(report.freshness.age_seconds)
        # allow_nan=False raises if any Infinity/NaN slipped into the payload.
        json.dumps(report.to_dict(), allow_nan=False)

    def test_hostile_now_epoch_degrades_to_age_unavailable(self) -> None:
        class _RaisesOnFloat:
            def __float__(self):
                raise RuntimeError("boom")

        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_RaisesOnFloat(),
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertTrue(report.freshness.stale)
        self.assertEqual(report.freshness.reason, "age_unavailable")
        self.assertIsNone(report.freshness.age_seconds)

    def test_malformed_records_are_skipped_without_crashing(self) -> None:
        report = evaluate_market_data_quality(
            [None, {}, "garbage", 42, _clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.symbol_count, 1)
        self.assertEqual(report.issue_count, 0)
        self.assertEqual(report.status, "ok")

    def test_repeated_bad_symbol_counts_each_row(self) -> None:
        # symbol_count counts rows; ok_count must stay row-consistent even when
        # the same symbol appears twice (both bad → zero clean rows).
        bad1 = MarketSnapshot(
            symbol="005930",
            current_price=0,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        bad2 = MarketSnapshot(
            symbol="005930",
            current_price=-5,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad1, bad2],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.symbol_count, 2)
        self.assertEqual(report.ok_count, 0)

    def test_non_finite_price_is_handled_as_missing_without_crashing(self) -> None:
        record = {
            "symbol": "005930",
            "current_price": float("inf"),
            "open_price": 69_000,
            "low_price": 68_000,
            "high_price": 71_000,
            "prev_day_change_pct": 1.5,
        }
        report = evaluate_market_data_quality(
            [record],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "warn")
        self.assertEqual(report.issues[0].category, "missing_field")
        self.assertIn("current_price", report.issues[0].detail)

    def test_hostile_records_never_crash_the_scan(self) -> None:
        class _RaisesOnFloat:
            def __float__(self):
                raise RuntimeError("float-boom")

        class _RaisesOnSymbol:
            @property
            def symbol(self):
                raise RuntimeError("symbol-boom")

        hostile_fields = {
            "symbol": "005930",
            "current_price": _RaisesOnFloat(),
            "open_price": 69_000,
            "low_price": 68_000,
            "high_price": 71_000,
            "prev_day_change_pct": _RaisesOnFloat(),
        }
        report = evaluate_market_data_quality(
            [hostile_fields, _RaisesOnSymbol(), _clean_snapshot("000660")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        # No crash; the hostile-symbol row is skipped, the hostile-field row
        # degrades to missing_field, the clean row is evaluated.
        self.assertEqual(report.status, "warn")
        self.assertIn("005930", {issue.symbol for issue in report.issues})

    def test_mapping_records_are_evaluated_like_dataclasses(self) -> None:
        report = evaluate_market_data_quality(
            [
                {
                    "symbol": "005930",
                    "current_price": 0,
                    "open_price": 69_000,
                    "low_price": 68_000,
                    "high_price": 71_000,
                    "prev_day_change_pct": 1.5,
                }
            ],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.symbol_count, 1)
        self.assertEqual(report.issue_count, 1)
        self.assertEqual(report.issues[0].category, "non_positive_price")

    def test_to_dict_is_json_serializable(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=0,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
            checked_at="2026-06-13T09:00:00+09:00",
        )
        payload = report.to_dict()
        restored = json.loads(json.dumps(payload))
        self.assertEqual(restored["status"], "warn")
        self.assertEqual(restored["checked_at"], "2026-06-13T09:00:00+09:00")
        self.assertEqual(restored["symbol_count"], 1)
        self.assertEqual(restored["issue_count"], 1)
        self.assertEqual(restored["issues"][0]["symbol"], "005930")
        self.assertEqual(restored["issues"][0]["category"], "non_positive_price")
        self.assertIn("stale", restored["freshness"])
        self.assertEqual(restored["freshness"]["stale_threshold_seconds"], 360)


class QualityAlertLinesTests(unittest.TestCase):
    def test_warn_report_produces_alert_lines(self) -> None:
        bad = MarketSnapshot(
            symbol="005930",
            current_price=0,
            open_price=69_000,
            low_price=68_000,
            high_price=71_000,
            prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        lines = build_quality_alert_lines(report)
        self.assertTrue(lines)
        self.assertIn("WARN", lines[0])
        joined = "\n".join(lines)
        self.assertIn("non_positive_price", joined)
        self.assertIn("005930", joined)

    def test_alert_lines_tolerate_none_issues(self) -> None:
        report = SimpleNamespace(
            status="warn",
            symbol_count=1,
            ok_count=0,
            issue_count=0,
            issues=None,
            freshness=SimpleNamespace(
                stale=True, reason="age_unavailable", age_seconds=None, stale_threshold_seconds=360
            ),
        )
        lines = build_quality_alert_lines(report)
        self.assertTrue(lines)
        self.assertIn("WARN", lines[0])

    def test_alert_category_line_dedups_repeated_symbols(self) -> None:
        bad1 = MarketSnapshot(
            symbol="005930", current_price=0, open_price=69_000,
            low_price=68_000, high_price=71_000, prev_day_change_pct=1.5,
        )
        bad2 = MarketSnapshot(
            symbol="005930", current_price=-5, open_price=69_000,
            low_price=68_000, high_price=71_000, prev_day_change_pct=1.5,
        )
        report = evaluate_market_data_quality(
            [bad1, bad2],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        lines = build_quality_alert_lines(report)
        category_line = next(line for line in lines if "non_positive_price" in line)
        self.assertEqual(category_line.count("005930"), 1)

    def test_ok_report_produces_no_alert_lines(self) -> None:
        report = evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
        )
        self.assertEqual(report.status, "ok")
        self.assertEqual(build_quality_alert_lines(report), ())


class QualityArtifactWriterTests(unittest.TestCase):
    def _report(self):
        return evaluate_market_data_quality(
            [_clean_snapshot("005930")],
            now_epoch=_NOW_EPOCH,
            refresh_interval_seconds=180,
            snapshot_updated_at=_fresh_updated_at(30),
            checked_at="2026-06-13T09:00:00+09:00",
        )

    def test_writes_report_payload_to_path(self) -> None:
        report = self._report()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "market_data_quality.json"
            ok = write_market_data_quality_artifact(report, path=path)
            self.assertTrue(ok)
            self.assertTrue(path.exists())
            restored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(restored, report.to_dict())

    def test_default_path_is_under_data_runtime(self) -> None:
        self.assertEqual(QUALITY_ARTIFACT_PATH.name, "market_data_quality.json")
        self.assertEqual(QUALITY_ARTIFACT_PATH.parent.name, "runtime")

    def test_unserializable_payload_returns_false_and_leaks_no_temp(self) -> None:
        class _Boom:
            def to_dict(self):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "market_data_quality.json"
            ok = write_market_data_quality_artifact(_Boom(), path=path)
            self.assertFalse(ok)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_write_failure_returns_false_without_raising(self) -> None:
        report = self._report()
        with tempfile.TemporaryDirectory() as temp_dir:
            # Path whose parent is an existing *file*, so mkdir/replace fail.
            blocker = Path(temp_dir) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            path = blocker / "market_data_quality.json"
            self.assertFalse(write_market_data_quality_artifact(report, path=path))


if __name__ == "__main__":
    unittest.main()
