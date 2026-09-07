from __future__ import annotations

import time
import unittest
from unittest import mock

from app.core.time_utils import is_snapshot_fresh


class SnapshotFreshnessTests(unittest.TestCase):
    def _snapshot(self, age_seconds: float) -> dict:
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(time.time() - age_seconds, tz=timezone.utc).isoformat()
        return {"observed_at": ts, "current_price": 75000}

    def test_fresh_snapshot_within_ttl(self) -> None:
        self.assertTrue(is_snapshot_fresh(self._snapshot(30), max_age_seconds=420))

    def test_stale_snapshot_beyond_ttl(self) -> None:
        self.assertFalse(is_snapshot_fresh(self._snapshot(500), max_age_seconds=420))

    def test_missing_observed_at_is_not_fresh(self) -> None:
        self.assertFalse(is_snapshot_fresh({"current_price": 75000}, max_age_seconds=420))

    def test_none_max_age_is_always_fresh(self) -> None:
        self.assertTrue(is_snapshot_fresh(self._snapshot(9999), max_age_seconds=None))

    def test_zero_max_age_only_fresh_if_just_now(self) -> None:
        # A snapshot from 0 seconds ago should pass (age <= 0 is False but age ~= 0)
        # and one from 1 second ago should not
        self.assertFalse(is_snapshot_fresh(self._snapshot(1), max_age_seconds=0))

    def test_malformed_observed_at_is_not_fresh(self) -> None:
        self.assertFalse(
            is_snapshot_fresh({"observed_at": "not-a-date"}, max_age_seconds=420)
        )

    def test_scanner_shallow_scan_uses_shared_function(self) -> None:
        from app.scanner import service as svc
        # _is_recent_cached_snapshot should no longer exist; is_snapshot_fresh is used directly
        self.assertFalse(hasattr(svc, "_is_recent_cached_snapshot"))

    def test_main_no_longer_has_local_freshness_helper(self) -> None:
        from app import main as m
        self.assertFalse(hasattr(m, "_runtime_market_snapshot_is_fresh"))


if __name__ == "__main__":
    unittest.main()
