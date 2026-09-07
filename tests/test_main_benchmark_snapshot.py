from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.market_data import benchmark as benchmark_module


class BenchmarkSnapshotResolutionTests(unittest.TestCase):
    def test_uses_fresh_cached_snapshot_before_api_lookup(self) -> None:
        settings = SimpleNamespace(
            performance_benchmark_symbol="005930",
            live_snapshot_ttl_seconds=420,
        )
        now = datetime.now(timezone.utc)

        with mock.patch.object(benchmark_module, "inquire_price") as mocked_inquire:
            snapshot = benchmark_module.resolve_benchmark_snapshot(
                settings=settings,
                token="tok",
                observed_market_snapshots={},
                cached_market_snapshots={
                    "005930": {
                        "current_price": 80100,
                        "observed_at": (now - timedelta(seconds=30)).isoformat(),
                    },
                },
            )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.symbol, "005930")
        self.assertEqual(snapshot.current_price, 80100)
        mocked_inquire.assert_not_called()

    def test_ignores_stale_cached_snapshot_and_falls_back_to_api(self) -> None:
        settings = SimpleNamespace(
            performance_benchmark_symbol="005930",
            live_snapshot_ttl_seconds=420,
        )
        now = datetime.now(timezone.utc)

        with mock.patch.object(
            benchmark_module,
            "inquire_price",
            return_value={
                "rt_cd": "0",
                "output": {
                    "stck_shrn_iscd": "005930",
                    "stck_prpr": "80200",
                    "stck_oprc": "80000",
                    "stck_lwpr": "79000",
                    "prdy_ctrt": "0.5",
                },
            },
        ) as mocked_inquire:
            snapshot = benchmark_module.resolve_benchmark_snapshot(
                settings=settings,
                token="tok",
                observed_market_snapshots={},
                cached_market_snapshots={
                    "005930": {
                        "current_price": 80100,
                        "observed_at": (now - timedelta(seconds=600)).isoformat(),
                    },
                },
            )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.current_price, 80200)
        mocked_inquire.assert_called_once_with("005930", token="tok")

    def test_skips_optional_api_lookup_during_adaptive_pacing(self) -> None:
        settings = SimpleNamespace(
            performance_benchmark_symbol="005930",
            live_snapshot_ttl_seconds=420,
        )

        with (
            mock.patch.object(
                benchmark_module,
                "get_adaptive_pacing_summary",
                return_value={
                    "active": True,
                    "extra_delay_ms": 250.0,
                    "remaining_ms": 5000.0,
                },
            ),
            mock.patch.object(benchmark_module, "inquire_price") as mocked_inquire,
        ):
            snapshot = benchmark_module.resolve_benchmark_snapshot(
                settings=settings,
                token="tok",
                observed_market_snapshots={},
                cached_market_snapshots={},
            )

        self.assertIsNone(snapshot)
        mocked_inquire.assert_not_called()


class MainAliasSeamTests(unittest.TestCase):
    """app.main re-exports the resolver under its legacy underscore name."""

    def test_main_exposes_benchmark_resolver(self) -> None:
        self.assertIs(
            main_module._resolve_benchmark_snapshot,
            benchmark_module.resolve_benchmark_snapshot,
        )


if __name__ == "__main__":
    unittest.main()
