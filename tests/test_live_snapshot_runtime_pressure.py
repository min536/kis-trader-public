from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock
from urllib import error

from app.core.time_utils import get_korean_now
import scripts.live_snapshot as live_snapshot_script


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class LiveSnapshotRuntimePressureTests(unittest.TestCase):
    def test_build_runtime_pressure_defer_reason_defers_on_recent_rate_limit_backoff(self) -> None:
        now = get_korean_now()
        reason = live_snapshot_script._build_runtime_pressure_defer_reason(
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

    def test_build_runtime_pressure_defer_reason_skips_when_snapshot_is_stale(self) -> None:
        now = get_korean_now()
        reason = live_snapshot_script._build_runtime_pressure_defer_reason(
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

    def test_build_runtime_pressure_defer_reason_defers_on_recent_sell_watch_partial(self) -> None:
        now = get_korean_now()
        reason = live_snapshot_script._build_runtime_pressure_defer_reason(
            existing_snapshot={
                "updated_at": (now - timedelta(seconds=150)).isoformat(timespec="seconds"),
                "top_symbols": ["005930"],
            },
            runtime_state={
                "last_cycle_started_at": (now - timedelta(seconds=45)).isoformat(),
                "last_sell_watch_partial": True,
            },
            now=now,
            ttl_seconds=420,
            refresh_interval_seconds=180,
        )

        self.assertIsNotNone(reason)
        self.assertIn("sell_watch partial", str(reason))

    def test_collect_reuses_fresh_snapshot_without_network_calls_under_runtime_pressure(self) -> None:
        now = get_korean_now()
        snapshot_payload = {
            "updated_at": (now - timedelta(seconds=120)).isoformat(timespec="seconds"),
            "ttl_seconds": 420,
            "refresh_interval_seconds": 180,
            "top_n": 30,
            "top_symbols": ["005930", "000660"],
            "source_counts": {},
            "source_symbols": {},
            "errors": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            snapshot_path.write_text(
                json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_script, "SNAPSHOT_PATH", snapshot_path),
                mock.patch.object(
                    live_snapshot_script,
                    "_load_latest_runtime_state",
                    return_value={
                        "last_cycle_started_at": (now - timedelta(seconds=20)).isoformat(),
                        "rate_limit_source": "sell_watch",
                        "backoff_applied_seconds": 180,
                        "last_sell_watch_partial": True,
                    },
                ),
                mock.patch.object(live_snapshot_script, "get_korean_now", return_value=now),
                mock.patch.object(live_snapshot_script, "issue_access_token_for") as issue_token,
                mock.patch.object(live_snapshot_script, "_fetch_volume_rank") as fetch_volume_rank,
                mock.patch.dict(
                    "os.environ",
                    {
                        "KIS_LIVE_APP_KEY": "app-key",
                        "KIS_LIVE_APP_SECRET": "app-secret",
                        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "180",
                    },
                ),
            ):
                result = live_snapshot_script.collect(top_n=30, ttl_seconds=420)

            self.assertTrue(bool(result.get("refresh_deferred")))
            self.assertEqual(result.get("top_symbols"), ["005930", "000660"])
            issue_token.assert_not_called()
            fetch_volume_rank.assert_not_called()

    def test_get_json_retries_transient_network_error_for_safe_get(self) -> None:
        call_count = {"n": 0}

        def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise error.URLError(
                    "[Errno 8] nodename nor servname provided, or not known"
                )
            return _FakeResponse({"rt_cd": "0", "output": []})

        with (
            mock.patch.object(
                live_snapshot_script.urllib_request,
                "urlopen",
                side_effect=fake_urlopen,
            ),
            mock.patch.object(live_snapshot_script.time, "sleep", return_value=None),
        ):
            payload = live_snapshot_script._get_json(
                "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/fluctuation",
                headers={"authorization": "Bearer tok"},
                params={"FID_INPUT_ISCD": "0000"},
            )

        self.assertEqual(payload, {"rt_cd": "0", "output": []})
        self.assertEqual(call_count["n"], 2)

    def test_get_json_raises_rate_limit_error_on_200_ok_body(self) -> None:
        def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
            return _FakeResponse(
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00201",
                    "msg1": "초당 거래건수를 초과하였습니다.",
                }
            )

        with mock.patch.object(
            live_snapshot_script.urllib_request,
            "urlopen",
            side_effect=fake_urlopen,
        ):
            with self.assertRaises(live_snapshot_script.LiveSnapshotRateLimitError):
                live_snapshot_script._get_json(
                    "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/fluctuation",
                    headers={"authorization": "Bearer tok"},
                    params={"FID_INPUT_ISCD": "0000"},
                )

    def test_collect_stops_remaining_rank_fetches_after_rate_limit(self) -> None:
        now = get_korean_now()
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"

            with (
                mock.patch.object(live_snapshot_script, "SNAPSHOT_PATH", snapshot_path),
                mock.patch.object(
                    live_snapshot_script,
                    "_load_latest_runtime_state",
                    return_value=None,
                ),
                mock.patch.object(live_snapshot_script, "get_korean_now", return_value=now),
                mock.patch.object(
                    live_snapshot_script,
                    "issue_access_token_for",
                    return_value="tok",
                ),
                mock.patch.object(
                    live_snapshot_script,
                    "_fetch_volume_rank",
                    side_effect=live_snapshot_script.LiveSnapshotRateLimitError(
                        "KIS rate limit response"
                    ),
                ) as fetch_volume_rank,
                mock.patch.object(
                    live_snapshot_script,
                    "_fetch_fluctuation_rank",
                ) as fetch_fluctuation_rank,
                mock.patch.object(
                    live_snapshot_script,
                    "_fetch_volume_power_rank",
                ) as fetch_volume_power_rank,
                mock.patch.object(live_snapshot_script.time, "sleep", return_value=None),
                mock.patch.dict(
                    "os.environ",
                    {
                        "KIS_LIVE_APP_KEY": "app-key",
                        "KIS_LIVE_APP_SECRET": "app-secret",
                        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "180",
                    },
                ),
            ):
                result = live_snapshot_script.collect(top_n=30, ttl_seconds=420)

            fetch_volume_rank.assert_called_once()
            fetch_fluctuation_rank.assert_not_called()
            fetch_volume_power_rank.assert_not_called()
            self.assertEqual(result.get("top_symbols"), [])
            self.assertTrue(
                any("rate limit" in error for error in result.get("errors", []))
            )


if __name__ == "__main__":
    unittest.main()
