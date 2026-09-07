from app.overseas_stock.market_snapshot import OverseasMarketSnapshot
from app.overseas_stock.selection import (
    OverseasCandidate,
    OverseasScoringParams,
    load_us_artifact,
    scan_overseas_universe,
    score_overseas_candidate,
    select_overseas_top,
)
from app.gate2.schema import default_artifact


def _make_snapshot(symbol="AAPL"):
    return OverseasMarketSnapshot(
        symbol=symbol,
        current_price=97.0,
        open_price=100.0,
        high_price=100.0,
        low_price=96.0,
        prev_close=100.0,
        prev_day_change_pct=-3.0,
    )


def _make_candidate(symbol, final_score, passed_threshold=True):
    snap = _make_snapshot(symbol)
    return OverseasCandidate(
        symbol=symbol,
        exchange="NASD",
        snapshot=snap,
        final_score=final_score,
        contributions={},
        missing=(),
        passed_threshold=passed_threshold,
        score_components={},
        conditions={},
    )


def test_score_overseas_candidate():
    snap = _make_snapshot("AAPL")
    artifact = default_artifact()
    params = OverseasScoringParams()
    candidate = score_overseas_candidate(snap, exchange="NASD", artifact=artifact, params=params)
    assert isinstance(candidate.final_score, float)
    assert isinstance(candidate.passed_threshold, bool)
    assert candidate.conditions["volume_rank_score"] == 50.0
    assert candidate.symbol == "AAPL"


def test_select_top_picks_highest_score():
    candidates = [
        _make_candidate("AAPL", 10.0),
        _make_candidate("MSFT", 30.0),
        _make_candidate("GOOG", 20.0),
    ]
    top = select_overseas_top(candidates)
    assert top is not None
    assert top.final_score == 30.0
    assert top.symbol == "MSFT"


def test_select_top_filters_below_threshold():
    candidates = [
        _make_candidate("AAPL", 99.0, passed_threshold=False),
        _make_candidate("MSFT", 5.0, passed_threshold=True),
    ]
    # With threshold: skip score-99 (failed) → return score-5
    top = select_overseas_top(candidates, require_threshold=True)
    assert top is not None
    assert top.final_score == 5.0
    # Without threshold: score-99 wins
    top2 = select_overseas_top(candidates, require_threshold=False)
    assert top2 is not None
    assert top2.final_score == 99.0


def test_select_top_empty_returns_none():
    assert select_overseas_top([]) is None


def _price_detail_stub(symbol, exchange="NASD"):
    return {
        "symb": symbol,
        "last": "97",
        "open": "100",
        "high": "100",
        "low": "96",
        "base": "100",
        "rate": "-3",
    }


def test_scan_universe_scores_all():
    candidates = scan_overseas_universe(
        ["AAPL", "MSFT"], fetch_detail=_price_detail_stub
    )
    assert len(candidates) == 2
    symbols = {c.symbol for c in candidates}
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_scan_skips_failing_symbol():
    def failing_stub(symbol, exchange="NASD"):
        if symbol == "BAD":
            raise RuntimeError("fetch failed")
        return _price_detail_stub(symbol, exchange)

    candidates = scan_overseas_universe(
        ["BAD", "AAPL"], fetch_detail=failing_stub
    )
    assert len(candidates) == 1
    assert candidates[0].symbol == "AAPL"


def test_load_us_artifact_returns_us_version():
    artifact = load_us_artifact()
    assert artifact.version == "score_v2_us_w0"
