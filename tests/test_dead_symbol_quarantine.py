"""F4 — 죽은 심볼 자동 격리 (E4).

설계: docs/daily_error_triage_design_20260707.md §5.

동일 심볼이 연속 N=3회 필수필드 누락으로 skip되면 당일 유니버스에서 자동 제외
(dead_scan_symbols_today)하고, 이후 스캔에서 사전 필터링되어 경고 스팸이 멈춘다.
성공 스캔 시 카운터 리셋(진짜 연속), 날짜 변경 시 전체 리셋(당일 한정). 순수·never-raises.
"""

from app.scanner.dead_symbol_quarantine import (
    DEAD_SYMBOL_MISS_THRESHOLD,
    filter_dead_symbols,
    update_dead_symbol_tracking,
)


def test_three_consecutive_misses_quarantines():
    state: dict = {}
    result = None
    for _ in range(DEAD_SYMBOL_MISS_THRESHOLD):
        result = update_dead_symbol_tracking(
            state,
            parse_skipped_symbols=["008560"],
            scanned_symbols=["008560", "005930"],
            today="2026-07-08",
        )
    assert "008560" in result["dead_symbols"]
    assert state["dead_scan_symbols_today"] == ["008560"]
    # promotion fires exactly once (on the threshold cycle)
    assert result["newly_quarantined"] == ["008560"]


def test_newly_quarantined_only_on_transition():
    state: dict = {}
    seen_newly = []
    for _ in range(DEAD_SYMBOL_MISS_THRESHOLD + 2):
        r = update_dead_symbol_tracking(
            state,
            parse_skipped_symbols=["008560"],
            scanned_symbols=["008560"],
            today="2026-07-08",
        )
        seen_newly.append(list(r["newly_quarantined"]))
    # exactly one cycle reports it as newly quarantined (warn-once)
    assert sum(1 for n in seen_newly if "008560" in n) == 1


def test_successful_scan_resets_counter():
    state: dict = {}
    # two misses
    for _ in range(2):
        update_dead_symbol_tracking(
            state, parse_skipped_symbols=["008560"], scanned_symbols=["008560"], today="d1"
        )
    # a clean cycle (scanned, not skipped) resets the counter
    update_dead_symbol_tracking(
        state, parse_skipped_symbols=[], scanned_symbols=["008560"], today="d1"
    )
    # now two more misses should NOT yet quarantine (counter was reset)
    for _ in range(2):
        r = update_dead_symbol_tracking(
            state, parse_skipped_symbols=["008560"], scanned_symbols=["008560"], today="d1"
        )
    assert "008560" not in r["dead_symbols"]


def test_day_change_resets_tracking():
    state: dict = {}
    for _ in range(DEAD_SYMBOL_MISS_THRESHOLD):
        update_dead_symbol_tracking(
            state, parse_skipped_symbols=["008560"], scanned_symbols=["008560"], today="d1"
        )
    assert state["dead_scan_symbols_today"] == ["008560"]
    # new day wipes the quarantine
    r = update_dead_symbol_tracking(
        state, parse_skipped_symbols=[], scanned_symbols=["005930"], today="d2"
    )
    assert r["dead_symbols"] == []
    assert state["dead_scan_symbols_today"] == []


def test_filter_dead_symbols_splits_live_and_excluded():
    state = {"dead_scan_symbols_today": ["008560"]}
    live, excluded = filter_dead_symbols(["005930", "008560", "000660"], state)
    assert live == ["005930", "000660"]
    assert excluded == ["008560"]


def test_filter_no_dead_is_passthrough():
    state: dict = {}
    live, excluded = filter_dead_symbols(["005930", "000660"], state)
    assert live == ["005930", "000660"]
    assert excluded == []


def test_update_is_defensive_on_bad_state():
    # non-dict tracking / missing keys must not raise
    state = {"dead_scan_tracking": "corrupt"}
    r = update_dead_symbol_tracking(
        state, parse_skipped_symbols=["x"], scanned_symbols=["x"], today="d1"
    )
    assert r["dead_symbols"] == []  # first miss only


def test_filter_defensive_on_bad_state():
    live, excluded = filter_dead_symbols(["a"], {"dead_scan_symbols_today": "corrupt"})
    assert live == ["a"]
    assert excluded == []
