"""Slice F1 — KIS quote payload synthesizer round-trip contract tests.

The point of this slice: a payload synthesized from a MinuteObservation must
survive a round-trip through the REAL app.market_data.schema.build_market_snapshot
parser. If the parser's key contract ever changes, the round-trip test must break.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.market_data.schema import build_market_snapshot
from app.research.replay.minute_provider import MinuteObservation
from app.research.replay.payload_synth import synth_quote_payload


def _observation(
    *,
    symbol: str = "005930",
    close: float = 71234.0,
    day_open: float = 70500.0,
    day_high: float = 71900.0,
    day_low: float = 70100.0,
) -> MinuteObservation:
    ts = datetime(2024, 6, 3, 10, 30)
    return MinuteObservation(
        symbol=symbol,
        ts=ts,
        bar_ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
        day_open=day_open,
        day_high=day_high,
        day_low=day_low,
        day_cum_volume=50_000,
    )


def _isolate_live_snapshot(monkeypatch, tmp_path: Path) -> None:
    """Guarantee build_market_snapshot finds no live snapshot signal.

    Point the documented C-4 P1 override env at an empty tmp dir AND redirect
    the import-cached SNAPSHOT_PATH to that empty dir, so load_live_snapshot()
    reads no real file regardless of what exists under repo data/.
    """
    import app.market_data.live_snapshot as live_snapshot

    empty_dir = tmp_path / "live_snapshot_dir"
    empty_dir.mkdir()
    monkeypatch.setenv(live_snapshot.LIVE_SNAPSHOT_DIR_ENV, str(empty_dir))
    monkeypatch.setattr(
        live_snapshot, "SNAPSHOT_PATH", empty_dir / "live_snapshot.json"
    )


def test_synth_payload_round_trips_through_real_parser(monkeypatch, tmp_path):
    _isolate_live_snapshot(monkeypatch, tmp_path)
    obs = _observation(
        close=71234.0, day_open=70500.0, day_high=71900.0, day_low=70100.0
    )
    prev_close = 70000.0

    payload = synth_quote_payload(obs, prev_close=prev_close)
    assert payload is not None
    assert payload["rt_cd"] == "0"

    snapshot = build_market_snapshot(payload["output"])

    assert snapshot.symbol == "005930"
    assert snapshot.current_price == 71234  # int-truncated close
    assert snapshot.open_price == 70500  # int-truncated day_open
    assert snapshot.high_price == 71900  # int-truncated day_high
    assert snapshot.low_price == 70100  # int-truncated day_low
    # (71234 / 70000 - 1) * 100 = 1.762857... -> 1.76 to 2 decimals
    assert snapshot.prev_day_change_pct == 1.76
    # No live snapshot file exists in this environment.
    assert snapshot.live_snapshot_available is False


def test_synth_payload_returns_none_when_prev_close_missing():
    obs = _observation()

    assert synth_quote_payload(obs, prev_close=None) is None
    assert synth_quote_payload(obs, prev_close=0) is None
    assert synth_quote_payload(obs, prev_close=0.0) is None


def test_synth_payload_prdy_ctrt_is_negative_on_down_day():
    # close below prev_close -> down day -> negative change ratio.
    obs = _observation(close=68600.0)
    prev_close = 70000.0

    payload = synth_quote_payload(obs, prev_close=prev_close)
    assert payload is not None

    prdy_ctrt = payload["output"]["prdy_ctrt"]
    # (68600 / 70000 - 1) * 100 = -2.00 exactly.
    assert prdy_ctrt == "-2.00"
    assert float(prdy_ctrt) < 0
