"""Slice S4-1 — portfolio_risk pair 재계산 제거 + 스캔-로컬 캐시
(docs/slack_backtest_speed_timeout_delegation_20260712.md §7 S4-1).

Parity-safe optimization: same inputs must produce bit-identical outputs.
These tests pin (1) the new dict-input ``_aligned_return_pairs`` signature
against the legacy raw-history computation, and (2) that threading a shared
``scan_cache`` through ``build_portfolio_risk_features`` reduces
``get_recent_symbol_price_history`` lookups without changing results.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.math_models import portfolio_risk

_TIMESTAMPS = [f"202607{day:02d}0900" for day in range(1, 12)]

_PRICE_PATTERNS: dict[str, list[float]] = {
    "AAA": [100.0, 102.0, 101.0, 104.0, 103.0, 106.0, 105.0, 108.0, 107.0, 110.0, 109.0],
    "BBB": [50.0, 49.0, 51.0, 50.0, 52.0, 51.0, 53.0, 52.0, 54.0, 53.0, 55.0],
}


def _synthetic_history(symbol: str) -> list[dict[str, object]]:
    prices = _PRICE_PATTERNS[symbol]
    return [
        {"timestamp": timestamp, "price": price}
        for timestamp, price in zip(_TIMESTAMPS, prices)
    ]


def _make_counting_history_fn(call_log: list[tuple[str, int]]):
    def _fake_get_recent_symbol_price_history(symbol: str, *, limit: int = 60):
        call_log.append((symbol, limit))
        return _synthetic_history(symbol)

    return _fake_get_recent_symbol_price_history


def test_aligned_return_pairs_matches_legacy_raw_history_computation() -> None:
    left_history = _synthetic_history("AAA")
    right_history = _synthetic_history("BBB")

    # Legacy behavior: pairs computed by running _returns_by_timestamp over the
    # raw histories, then sorting/intersecting the two timestamp sets.
    legacy_left_returns = portfolio_risk._returns_by_timestamp(left_history)
    legacy_right_returns = portfolio_risk._returns_by_timestamp(right_history)
    common_timestamps = sorted(set(legacy_left_returns) & set(legacy_right_returns))
    expected_left = [legacy_left_returns[timestamp] for timestamp in common_timestamps]
    expected_right = [legacy_right_returns[timestamp] for timestamp in common_timestamps]

    actual_left, actual_right = portfolio_risk._aligned_return_pairs(
        legacy_left_returns, legacy_right_returns
    )

    assert actual_left == expected_left
    assert actual_right == expected_right


def test_build_portfolio_risk_features_shared_scan_cache_matches_uncached_and_cuts_lookups(
    monkeypatch,
) -> None:
    snapshot_candidate_aaa = SimpleNamespace(
        held_positions=(SimpleNamespace(symbol="BBB", market_value=1_000_000),),
        cash_orderable=2_000_000,
    )
    snapshot_candidate_bbb = SimpleNamespace(
        held_positions=(SimpleNamespace(symbol="AAA", market_value=1_500_000),),
        cash_orderable=2_000_000,
    )

    cached_call_log: list[tuple[str, int]] = []
    monkeypatch.setattr(
        portfolio_risk,
        "get_recent_symbol_price_history",
        _make_counting_history_fn(cached_call_log),
    )

    scan_cache: dict = {}
    cached_result_aaa = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="AAA",
        current_price=10_000,
        portfolio_snapshot=snapshot_candidate_aaa,
        candidate_qty=1,
        scan_cache=scan_cache,
    )
    cached_result_bbb = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="BBB",
        current_price=5_000,
        portfolio_snapshot=snapshot_candidate_bbb,
        candidate_qty=1,
        scan_cache=scan_cache,
    )

    uncached_call_log_aaa: list[tuple[str, int]] = []
    monkeypatch.setattr(
        portfolio_risk,
        "get_recent_symbol_price_history",
        _make_counting_history_fn(uncached_call_log_aaa),
    )
    uncached_result_aaa = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="AAA",
        current_price=10_000,
        portfolio_snapshot=snapshot_candidate_aaa,
        candidate_qty=1,
        scan_cache=None,
    )

    uncached_call_log_bbb: list[tuple[str, int]] = []
    monkeypatch.setattr(
        portfolio_risk,
        "get_recent_symbol_price_history",
        _make_counting_history_fn(uncached_call_log_bbb),
    )
    uncached_result_bbb = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="BBB",
        current_price=5_000,
        portfolio_snapshot=snapshot_candidate_bbb,
        candidate_qty=1,
        scan_cache=None,
    )

    assert cached_result_aaa == uncached_result_aaa
    assert cached_result_bbb == uncached_result_bbb

    total_uncached_calls = len(uncached_call_log_aaa) + len(uncached_call_log_bbb)
    assert total_uncached_calls == 4
    assert len(cached_call_log) == 2
    assert len(cached_call_log) < total_uncached_calls


def test_build_portfolio_risk_features_default_scan_cache_matches_explicit_cache(
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        held_positions=(SimpleNamespace(symbol="BBB", market_value=1_000_000),),
        cash_orderable=2_000_000,
    )

    monkeypatch.setattr(
        portfolio_risk,
        "get_recent_symbol_price_history",
        _make_counting_history_fn([]),
    )
    result_without_scan_cache = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="AAA",
        current_price=10_000,
        portfolio_snapshot=snapshot,
        candidate_qty=1,
    )

    monkeypatch.setattr(
        portfolio_risk,
        "get_recent_symbol_price_history",
        _make_counting_history_fn([]),
    )
    result_with_empty_scan_cache = portfolio_risk.build_portfolio_risk_features(
        candidate_symbol="AAA",
        current_price=10_000,
        portfolio_snapshot=snapshot,
        candidate_qty=1,
        scan_cache={},
    )

    assert result_without_scan_cache == result_with_empty_scan_cache


def test_fsum_mean_pstdev_match_statistics_within_tolerance():
    """S4-2 (B3): fsum 기반 _mean/_pstdev는 stdlib statistics 커널과
    대표 입력(리턴 스케일 float)에서 상대오차 1e-12 내로 일치해야 한다."""
    import statistics

    import pytest

    cases = [
        [0.0001, -0.0002, 0.0003, -0.0001],
        [(-1) ** i * (i + 1) * 1e-4 for i in range(59)],
        [0.02, 0.02],
        [1e-08, 2e-08, -1e-08, 5e-09, 0.0],
        [0.5, -0.25, 0.125, -0.0625, 0.03125],
    ]
    for values in cases:
        expected_mean = statistics.mean(values)
        expected_pstdev = statistics.pstdev(values)
        assert portfolio_risk._mean(values) == pytest.approx(
            expected_mean, rel=1e-12, abs=1e-15
        )
        assert portfolio_risk._pstdev(values) == pytest.approx(
            expected_pstdev, rel=1e-12, abs=1e-15
        )
