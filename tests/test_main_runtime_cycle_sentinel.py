from types import SimpleNamespace

from app.notifications.main_runtime_hooks import run_cycle_market_data_quality_sentinel


def _good_snap():
    return SimpleNamespace(
        symbol="005930", current_price=70000, open_price=69000, low_price=68000,
        high_price=71000, prev_day_change_pct=1.0,
        live_snapshot_updated_at="2026-06-10T10:00:00+09:00",
    )


def test_wrapper_runs_sentinel_and_writes_artifact(tmp_path):
    art = tmp_path / "q.json"
    report = run_cycle_market_data_quality_sentinel(
        [_good_snap()],
        settings=SimpleNamespace(live_snapshot_refresh_interval_seconds=30),
        artifact_path=art,
        now_epoch=1_760_000_000.0,
    )
    assert report is not None
    assert art.exists()


def test_wrapper_derives_updated_at_from_snapshots(tmp_path):
    report = run_cycle_market_data_quality_sentinel(
        [_good_snap()],
        settings=SimpleNamespace(live_snapshot_refresh_interval_seconds=30),
        artifact_path=tmp_path / "q.json",
        now_epoch=1_760_000_000.0,
    )
    assert report.freshness.updated_at == "2026-06-10T10:00:00+09:00"


def test_wrapper_fires_alert_when_flag_enabled(tmp_path):
    sent = []
    bad = SimpleNamespace(
        symbol="005930", current_price=0, open_price=0, low_price=0,
        high_price=0, prev_day_change_pct=0.0, live_snapshot_updated_at=None,
    )
    run_cycle_market_data_quality_sentinel(
        [bad],
        settings=SimpleNamespace(live_snapshot_refresh_interval_seconds=30),
        artifact_path=tmp_path / "q.json",
        env={"MARKET_DATA_QUALITY_ALERTS_ENABLED": "1"},
        alert_sender=lambda text: sent.append(text),
        now_epoch=1_760_000_000.0,
    )
    assert sent and "quality" in sent[0].lower()


def test_wrapper_is_fail_safe_on_bad_settings(tmp_path):
    # settings missing the attribute must not raise.
    report = run_cycle_market_data_quality_sentinel(
        [_good_snap()],
        settings=SimpleNamespace(),
        artifact_path=tmp_path / "q.json",
        now_epoch=1_760_000_000.0,
    )
    assert report is not None
