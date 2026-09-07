"""Tests for M-R1 — morning US-regime classification CLI (plan §5).

Shadow classification+recommendation only. Synthetic fixtures under tmp_path:
US daily parquet (via ``import_us_daily_csv``) + a synthetic regime-library JSON.
No broker APIs, no runtime state.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from app.research.us_regime.ingest import import_us_daily_csv


def _write_us_asset(tmp_path, asset, rows):
    """Write one US-daily CSV -> parquet via the real ingest validator.

    ``rows`` is a list of (date, open, high, low, close, volume) tuples.
    Returns the us_root directory holding ``<asset>.parquet``.
    """
    csv_path = tmp_path / f"{asset}.csv"
    frame = pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    )
    frame.to_csv(csv_path, index=False)
    us_root = tmp_path / "us_daily"
    import_us_daily_csv(csv_path, asset=asset, out_root=us_root)
    return us_root


def _base_artifact_dict():
    """A minimal but schema-valid ScoreV2 artifact dict (baseline)."""
    from app.gate2.schema import default_artifact
    from dataclasses import asdict

    return asdict(default_artifact())


def _write_base_artifact(tmp_path):
    path = tmp_path / "base_artifact.json"
    path.write_text(json.dumps(_base_artifact_dict()))
    return path


class TestFallbackUsDataMissing:
    def test_missing_us_session_day_falls_back_to_baseline(self, tmp_path):
        from app.tools import morning_regime_pick

        # US parquet holds only 2016-10-03; "today" = 2016-10-02 is BEFORE every
        # parquet date, so there is NO prior US session day -> us_data_missing.
        rows = [
            (dt.date(2016, 10, 3), 100.0, 101.0, 99.0, 100.5, 0),
        ]
        us_root = _write_us_asset(tmp_path, "SPX", rows)
        base_path = _write_base_artifact(tmp_path)

        library = {
            "version": "regime_lib_v1",
            "assets": ["SPX"],
            "feature_columns": ["SPX_day_ret"],
            "scaler": {"mean": {"SPX_day_ret": 0.0}, "std": {"SPX_day_ret": 1.0}},
            "k": 1,
            "centroids": [[0.0]],
            "max_assign_distance": 1.0,
            "regimes": [
                {"regime_id": 0, "enrolled": True, "artifact": _base_artifact_dict()}
            ],
            "fallback": "baseline",
        }
        library_path = tmp_path / "regime_library.json"
        library_path.write_text(json.dumps(library))

        result = morning_regime_pick.run(
            date=dt.date(2016, 10, 2),
            us_root=us_root,
            library_path=library_path,
            base_artifact_path=base_path,
            out_dir=tmp_path / "out",
            assets=("SPX",),
        )

        out_path = tmp_path / "out" / "morning_regime_2016-10-02.json"
        payload = json.loads(out_path.read_text())
        assert payload["fallback_reason"] == "us_data_missing"
        assert payload["artifact_version"] == "baseline"
        assert payload["regime_id"] is None
        assert payload["artifact"] == _base_artifact_dict()
        assert result["output_path"] == str(out_path)


def _vix_library(*, centroids, max_assign_distance, regimes, mean=None, std=None):
    """A synthetic regime library over a single VIX asset (features level,
    day_ret) — the smallest frame that survives feature warmup (1 prior day)."""
    return {
        "version": "regime_lib_v1",
        "assets": ["VIX"],
        "feature_columns": ["VIX_level", "VIX_day_ret"],
        "scaler": {
            "mean": mean or {"VIX_level": 0.0, "VIX_day_ret": 0.0},
            "std": std or {"VIX_level": 1.0, "VIX_day_ret": 1.0},
        },
        "k": len(centroids),
        "centroids": centroids,
        "max_assign_distance": max_assign_distance,
        "regimes": regimes,
        "fallback": "baseline",
    }


class TestFallbackLowConfidence:
    def test_distance_beyond_max_assign_distance_falls_back(self, tmp_path):
        from app.tools import morning_regime_pick

        # Two VIX rows: target = 2016-10-04 (prior 10-03 present) survives warmup.
        # close 20 -> level 20, day_ret 20/10-1 = 1.0. Scaled by identity scaler,
        # the point (20, 1.0) sits far from the single centroid at the origin;
        # max_assign_distance=1.0 -> distance ~20 > 1.0 -> low_confidence.
        rows = [
            (dt.date(2016, 10, 3), 10.0, 10.0, 10.0, 10.0, 0),
            (dt.date(2016, 10, 4), 20.0, 20.0, 20.0, 20.0, 0),
        ]
        us_root = _write_us_asset(tmp_path, "VIX", rows)
        base_path = _write_base_artifact(tmp_path)

        library = _vix_library(
            centroids=[[0.0, 0.0]],
            max_assign_distance=1.0,
            regimes=[
                {"regime_id": 0, "enrolled": True, "artifact": _base_artifact_dict()}
            ],
        )
        library_path = tmp_path / "regime_library.json"
        library_path.write_text(json.dumps(library))

        morning_regime_pick.run(
            date=dt.date(2016, 10, 5),
            us_root=us_root,
            library_path=library_path,
            base_artifact_path=base_path,
            out_dir=tmp_path / "out",
            assets=("VIX",),
        )

        payload = json.loads(
            (tmp_path / "out" / "morning_regime_2016-10-05.json").read_text()
        )
        assert payload["fallback_reason"] == "low_confidence"
        assert payload["artifact_version"] == "baseline"
        assert payload["regime_id"] is None
        assert payload["us_session_day"] == "2016-10-04"
        assert payload["data_as_of"] == "2016-10-04"
        assert payload["assign_distance"] > 1.0
        assert payload["artifact"] == _base_artifact_dict()


class TestFallbackRegimeNotEnrolled:
    def test_within_distance_but_not_enrolled_falls_back(self, tmp_path):
        from app.tools import morning_regime_pick

        # target 2016-10-04: level 20, day_ret 1.0. Centroid at (20, 1.0) ->
        # distance 0 < max_assign_distance, but the assigned regime is NOT
        # enrolled -> regime_not_enrolled, baseline embedded.
        rows = [
            (dt.date(2016, 10, 3), 10.0, 10.0, 10.0, 10.0, 0),
            (dt.date(2016, 10, 4), 20.0, 20.0, 20.0, 20.0, 0),
        ]
        us_root = _write_us_asset(tmp_path, "VIX", rows)
        base_path = _write_base_artifact(tmp_path)

        library = _vix_library(
            centroids=[[20.0, 1.0]],
            max_assign_distance=5.0,
            regimes=[{"regime_id": 0, "enrolled": False, "artifact": None}],
        )
        library_path = tmp_path / "regime_library.json"
        library_path.write_text(json.dumps(library))

        morning_regime_pick.run(
            date=dt.date(2016, 10, 5),
            us_root=us_root,
            library_path=library_path,
            base_artifact_path=base_path,
            out_dir=tmp_path / "out",
            assets=("VIX",),
        )

        payload = json.loads(
            (tmp_path / "out" / "morning_regime_2016-10-05.json").read_text()
        )
        assert payload["fallback_reason"] == "regime_not_enrolled"
        assert payload["artifact_version"] == "baseline"
        assert payload["regime_id"] is None
        assert payload["assign_distance"] < 5.0
        assert payload["artifact"] == _base_artifact_dict()


def _regime_artifact_dict():
    """A regime-specific artifact dict, distinct from the baseline."""
    art = _base_artifact_dict()
    art["version"] = "regime_lib_w7"
    art["buy_threshold"] = 42.0
    return art


class TestNormalHit:
    def test_enrolled_regime_within_distance_picks_regime_artifact(self, tmp_path):
        from app.tools import morning_regime_pick

        # target 2016-10-04: (level 20, day_ret 1.0). Two centroids; the point is
        # nearest to regime 1 at (20, 1.0) (distance 0) and it IS enrolled ->
        # regime_1 pick with the regime's own artifact embedded.
        rows = [
            (dt.date(2016, 10, 3), 10.0, 10.0, 10.0, 10.0, 0),
            (dt.date(2016, 10, 4), 20.0, 20.0, 20.0, 20.0, 0),
        ]
        us_root = _write_us_asset(tmp_path, "VIX", rows)
        base_path = _write_base_artifact(tmp_path)

        regime_art = _regime_artifact_dict()
        library = _vix_library(
            centroids=[[0.0, 0.0], [20.0, 1.0]],
            max_assign_distance=5.0,
            regimes=[
                {"regime_id": 0, "enrolled": False, "artifact": None},
                {"regime_id": 1, "enrolled": True, "artifact": regime_art},
            ],
        )
        library_path = tmp_path / "regime_library.json"
        library_path.write_text(json.dumps(library))

        morning_regime_pick.run(
            date=dt.date(2016, 10, 5),
            us_root=us_root,
            library_path=library_path,
            base_artifact_path=base_path,
            out_dir=tmp_path / "out",
            assets=("VIX",),
        )

        payload = json.loads(
            (tmp_path / "out" / "morning_regime_2016-10-05.json").read_text()
        )
        assert payload["fallback_reason"] is None
        assert payload["regime_id"] == 1
        assert payload["artifact_version"] == "regime_1"
        assert payload["assign_distance"] < 5.0
        assert payload["artifact"] == regime_art
        assert payload["us_session_day"] == "2016-10-04"
        assert payload["data_as_of"] == "2016-10-04"


class TestZeroStdSafeScaler:
    def test_constant_column_std_zero_is_safe_and_deterministic(self, tmp_path):
        import math

        from app.tools import morning_regime_pick

        # VIX_level column has scaler std == 0.0 (a constant feature column, as
        # build_regime_library stores the RAW std). The morning job MUST use
        # safe_std = 1.0 for it — never divide by 0 -> no inf/nan.
        rows = [
            (dt.date(2016, 10, 3), 10.0, 10.0, 10.0, 10.0, 0),
            (dt.date(2016, 10, 4), 20.0, 20.0, 20.0, 20.0, 0),
        ]
        us_root = _write_us_asset(tmp_path, "VIX", rows)
        base_path = _write_base_artifact(tmp_path)

        regime_art = _regime_artifact_dict()
        # scaled VIX_level = (20 - 20)/safe_std(1.0) = 0.0 ; VIX_day_ret =
        # (1.0 - 0.0)/1.0 = 1.0 -> point (0.0, 1.0), centroid (0.0, 1.0) dist 0.
        library = _vix_library(
            centroids=[[0.0, 1.0]],
            max_assign_distance=5.0,
            regimes=[{"regime_id": 0, "enrolled": True, "artifact": regime_art}],
            mean={"VIX_level": 20.0, "VIX_day_ret": 0.0},
            std={"VIX_level": 0.0, "VIX_day_ret": 1.0},
        )
        library_path = tmp_path / "regime_library.json"
        library_path.write_text(json.dumps(library))

        result = morning_regime_pick.run(
            date=dt.date(2016, 10, 5),
            us_root=us_root,
            library_path=library_path,
            base_artifact_path=base_path,
            out_dir=tmp_path / "out",
            assets=("VIX",),
        )

        payload = result["payload"]
        assert math.isfinite(payload["assign_distance"])
        assert not math.isnan(payload["assign_distance"])
        assert payload["assign_distance"] < 1e-9  # deterministic distance-0 hit
        assert payload["fallback_reason"] is None
        assert payload["regime_id"] == 0
        assert payload["artifact_version"] == "regime_0"


def _hit_setup(tmp_path):
    """Common fixture for a normal-hit run — returns run(...) kwargs (minus
    ``notify``) that classify 2016-10-04 as enrolled regime 0."""
    rows = [
        (dt.date(2016, 10, 3), 10.0, 10.0, 10.0, 10.0, 0),
        (dt.date(2016, 10, 4), 20.0, 20.0, 20.0, 20.0, 0),
    ]
    us_root = _write_us_asset(tmp_path, "VIX", rows)
    base_path = _write_base_artifact(tmp_path)
    library = _vix_library(
        centroids=[[20.0, 1.0]],
        max_assign_distance=5.0,
        regimes=[{"regime_id": 0, "enrolled": True, "artifact": _regime_artifact_dict()}],
    )
    library_path = tmp_path / "regime_library.json"
    library_path.write_text(json.dumps(library))
    return {
        "date": dt.date(2016, 10, 5),
        "us_root": us_root,
        "library_path": library_path,
        "base_artifact_path": base_path,
        "out_dir": tmp_path / "out",
        "assets": ("VIX",),
    }


class TestSlackNotification:
    def test_notify_called_exactly_once(self, tmp_path):
        from app.tools import morning_regime_pick

        calls = []

        def spy(event_type, message, *, symbol=None, details=None):
            calls.append((event_type, message, symbol, details))

        morning_regime_pick.run(**_hit_setup(tmp_path), notify=spy)

        assert len(calls) == 1
        event_type, message, symbol, details = calls[0]
        assert event_type == "morning_regime_pick"
        assert symbol is None
        assert isinstance(message, str) and message
        assert details["regime_id"] == 0
        assert details["artifact_version"] == "regime_0"

    def test_notify_failure_swallowed_json_still_written(self, tmp_path):
        from app.tools import morning_regime_pick

        def boom(*args, **kwargs):
            raise RuntimeError("slack down")

        kwargs = _hit_setup(tmp_path)
        # Must not raise despite the notifier blowing up.
        result = morning_regime_pick.run(**kwargs, notify=boom)

        out_path = tmp_path / "out" / "morning_regime_2016-10-05.json"
        assert out_path.exists()
        payload = json.loads(out_path.read_text())
        assert payload["regime_id"] == 0
        assert result["payload"] == payload


class TestEventTypeRouting:
    def test_morning_regime_routes_to_operator_channel(self):
        from app.notifications import slack

        # 3-line registration: constant + by-type + options (operator-only,
        # NOT the BOTTLENECKS tuple style — plan review #11).
        assert slack.MORNING_REGIME_EVENT_TYPE == "morning_regime_pick"
        assert (
            slack.EVENT_CHANNEL_ENV_BY_TYPE[slack.MORNING_REGIME_EVENT_TYPE]
            == slack.OPERATOR_CHANNEL_ENV
        )
        assert slack.EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[
            slack.MORNING_REGIME_EVENT_TYPE
        ] == (slack.OPERATOR_CHANNEL_ENV,)
        # Resolver returns the operator channel env var for this event.
        assert slack.resolve_channel_env_vars(slack.MORNING_REGIME_EVENT_TYPE) == (
            slack.OPERATOR_CHANNEL_ENV,
        )
        assert (
            slack.resolve_channel_env_var(slack.MORNING_REGIME_EVENT_TYPE)
            == slack.OPERATOR_CHANNEL_ENV
        )
