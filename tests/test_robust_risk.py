"""Offline risk diagnostics adapted from the GS Quant reference contracts."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest

from backtester.analytics.robust_risk import (
    build_risk_diagnostics,
    drawdown_path,
    rolling_annualized_volatility,
    winsorize_returns,
)
from backtester.engine_backtest.report import write_report
from backtester.engine_backtest.runner import BacktestResult


def _curve(values: list[float]) -> list[tuple[date, float]]:
    start = date(2026, 1, 2)
    return [(start + timedelta(days=index), value) for index, value in enumerate(values)]


def _result(values: list[int]) -> BacktestResult:
    curve = _curve([float(value) for value in values])
    return BacktestResult(
        initial_cash=values[0] if values else 0,
        final_value=values[-1] if values else 0,
        trade_log=[],
        daily_records=[],
        equity_curve=curve,
    )


def test_drawdown_path_tracks_new_peaks_with_negative_percentages() -> None:
    path = drawdown_path(_curve([100.0, 120.0, 90.0, 108.0, 130.0, 104.0]))

    assert [value for _, value in path] == pytest.approx(
        [0.0, 0.0, -25.0, -10.0, 0.0, -20.0]
    )


def test_drawdown_path_marks_nonfinite_value_missing_without_resetting_peak() -> None:
    path = drawdown_path(_curve([100.0, 120.0, float("nan"), 90.0]))

    assert path[2][1] is None
    assert path[3][1] == pytest.approx(-25.0)


def test_rolling_volatility_uses_sample_std_and_annualizes() -> None:
    curve = _curve([100.0, 110.0, 99.0, 108.9])
    path = rolling_annualized_volatility(
        curve,
        window=2,
        periods_per_year=1,
    )

    assert [day for day, _ in path] == [day for day, _ in curve[1:]]
    assert path[0][1] is None
    assert path[1][1] == pytest.approx(math.sqrt(0.02) * 100)
    assert path[2][1] == pytest.approx(math.sqrt(0.02) * 100)


def test_rolling_volatility_returns_none_until_full_window() -> None:
    path = rolling_annualized_volatility(_curve([100.0, 101.0, 102.0]), window=3)

    assert [value for _, value in path] == [None, None]


def test_rolling_volatility_gap_invalidates_only_affected_windows() -> None:
    path = rolling_annualized_volatility(
        _curve([100.0, float("nan"), 110.0, 121.0, 133.1]),
        window=2,
        periods_per_year=1,
    )

    assert [value for _, value in path[:3]] == [None, None, None]
    assert path[3][1] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("window", [0, 1, -1])
def test_rolling_volatility_rejects_window_smaller_than_two(window: int) -> None:
    with pytest.raises(ValueError, match="window must be at least 2"):
        rolling_annualized_volatility(_curve([100.0, 101.0]), window=window)


def test_winsorize_returns_clips_both_extremes_without_mutating_input() -> None:
    raw = [0.01, 0.02, 1.0, -0.5, 0.03]
    before = list(raw)

    result = winsorize_returns(raw, z_limit=1.0)

    assert raw == before
    assert result.clipped_count == 2
    assert result.lower_bound is not None
    assert result.upper_bound is not None
    assert min(value for value in result.values if value is not None) >= result.lower_bound
    assert max(value for value in result.values if value is not None) <= result.upper_bound


def test_winsorize_returns_preserves_missing_values_and_handles_small_samples() -> None:
    empty = winsorize_returns([None, float("nan")])
    single = winsorize_returns([None, 0.25])

    assert empty.values == (None, None)
    assert empty.lower_bound is None
    assert empty.upper_bound is None
    assert empty.clipped_count == 0
    assert single.values == (None, pytest.approx(0.25))
    assert single.lower_bound == pytest.approx(0.25)
    assert single.upper_bound == pytest.approx(0.25)


def test_winsorize_returns_rejects_nonpositive_limit() -> None:
    with pytest.raises(ValueError, match="z_limit must be positive"):
        winsorize_returns([0.1, 0.2], z_limit=0)


def test_build_risk_diagnostics_is_json_safe_and_keeps_full_paths() -> None:
    diagnostics = build_risk_diagnostics(
        _curve([100.0, 120.0, 90.0, 108.0]),
        volatility_window=2,
        volatility_periods_per_year=1,
        winsor_z_limit=1.0,
    )

    json.dumps(diagnostics, allow_nan=False)
    assert diagnostics["current_drawdown_pct"] == pytest.approx(-10.0)
    assert diagnostics["drawdown_path"][2] == {
        "date": "2026-01-04",
        "drawdown_pct": -25.0,
    }
    assert diagnostics["rolling_volatility"]["window"] == 2
    assert len(diagnostics["rolling_volatility"]["path"]) == 3
    assert diagnostics["winsorized_returns"]["z_limit"] == 1.0
    assert len(diagnostics["winsorized_returns"]["path"]) == 3


def test_build_risk_diagnostics_does_not_hide_missing_latest_observation() -> None:
    diagnostics = build_risk_diagnostics(
        _curve([100.0, 110.0, 121.0, float("nan")]),
        volatility_window=2,
    )

    assert diagnostics["current_drawdown_pct"] is None
    assert diagnostics["rolling_volatility"]["latest_annualized_pct"] is None


def test_write_report_connects_full_risk_diagnostics(tmp_path) -> None:
    report_path = write_report(
        _result([100, 120, 90, 108]),
        tmp_path,
        run_label="robust-risk",
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["risk_diagnostics"]["drawdown_path"][2]["drawdown_pct"] == -25.0
    assert payload["risk_diagnostics"]["winsorized_returns"]["clipped_count"] >= 0
    assert payload["metrics"]["current_drawdown_pct"] == -10.0

    summary_path = next(tmp_path.glob("*_summary.txt"))
    summary = summary_path.read_text(encoding="utf-8")
    assert "Current drawdown" in summary
    assert "Rolling vol (20d)" in summary
    assert "Winsor clipped" in summary
