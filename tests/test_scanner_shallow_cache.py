from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.scanner.service import build_shallow_scan_candidates


class ScannerShallowCacheTests(unittest.TestCase):
    def test_stale_cached_snapshot_is_not_used_for_shallow_ranking(self) -> None:
        now = datetime.now(timezone.utc)
        candidates = build_shallow_scan_candidates(
            symbols=("005930",),
            profile="momentum",
            layer_by_symbol={"005930": "core"},
            cached_snapshots={
                "005930": {
                    "current_price": 80000,
                    "open_price": 78000,
                    "low_price": 77000,
                    "prev_day_change_pct": 1.2,
                    "observed_at": (now - timedelta(seconds=600)).isoformat(),
                },
            },
            max_cache_age_seconds=420,
        )

        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].snapshot_available)
        self.assertEqual(candidates[0].summary, "시장 데이터 부족")

    def test_fresh_cached_snapshot_is_used_for_shallow_ranking(self) -> None:
        now = datetime.now(timezone.utc)
        candidates = build_shallow_scan_candidates(
            symbols=("005930",),
            profile="momentum",
            layer_by_symbol={"005930": "core"},
            cached_snapshots={
                "005930": {
                    "current_price": 80000,
                    "open_price": 78000,
                    "low_price": 77000,
                    "prev_day_change_pct": 1.2,
                    "observed_at": (now - timedelta(seconds=60)).isoformat(),
                },
            },
            max_cache_age_seconds=420,
        )

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].snapshot_available)
        self.assertEqual(candidates[0].summary, "상승 추세/장중 강세 우선")


if __name__ == "__main__":
    unittest.main()
