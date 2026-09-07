"""Integration tests for AI injection points inside _run_buy_pass.

The existing tests/test_backtest_runner.py is blocked by a pre-existing
signature-drift issue in `evaluate_buy_decision` that is unrelated to
the AI layer. These tests therefore drive `_run_buy_pass` directly and
monkey-patch the three strategy functions imported into runner.py so
the AI glue is exercised in isolation:

Injection order verified:
  1. score_delta  — after calculate_selection_score
  2. veto         — before evaluate_buy_decision (short-circuits symbol)
  3. rerank       — after candidate filtering, before top-pick
  4. regime       — after position sizing
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date
from unittest.mock import MagicMock, patch

from backtester.ai_integration import AISignalProvider
from backtester.engine_backtest import runner as runner_mod
from backtester.engine_backtest.runner import DayRecord, _run_buy_pass


_DAY = date(2024, 3, 15)


# ── fixtures ──────────────────────────────────────────────────────────────


@dataclass
class _FakeSnapshot:
    symbol: str
    current_price: int = 10_000
    open_price: int = 10_000
    low_price: int = 9_800
    prev_day_change_pct: float = 0.0


class _FakeDataProvider:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def get_snapshot(self, symbol: str, _when: date) -> _FakeSnapshot:
        return _FakeSnapshot(symbol=symbol)


class _FakeSettings:
    qty = 10
    buy_min_score = 0.0
    buy_max_account_exposure_pct = 100.0
    buy_max_budget_per_trade_krw = 5_000_000
    buy_max_qty_per_trade = 100
    buy_rule_required_pass_count = 1
    buy_rule_enable_intraday_pullback = True
    buy_rule_enable_rebound_from_low = True
    buy_rule_enable_controlled_down_day = True
    buy_rule_enable_gap_down_open = True
    buy_rule_enable_range_recovery = True
    buy_rule_enable_live_volume_rank = False
    buy_rule_enable_live_volume_power_rank = False
    buy_rule_rebound_from_low_pct = 0.01
    buy_rule_controlled_down_day_min = -6.0
    buy_rule_controlled_down_day_max = 0.0
    buy_rule_gap_down_open_min_pct = 0.0
    buy_rule_gap_down_open_max_pct = 3.0
    buy_rule_range_recovery_min_ratio = 0.2


class _FakePortfolio:
    def __init__(self) -> None:
        self.positions: dict[str, object] = {}
        self.cash = 10_000_000
        self.executed: list[tuple[str, int, int]] = []  # (symbol, qty, price)

    def total_value(self, _prices: dict[str, int]) -> int:
        return self.cash

    def to_portfolio_snapshot(self, _prices: dict[str, int]):
        return MagicMock()

    def to_execution_snapshot(self, *, symbol: str, price: int, qty: int):
        return MagicMock(symbol=symbol, price=price, qty=qty)

    def execute_buy(
        self,
        *,
        symbol: str,
        qty: int,
        price: int,
        trading_date: date,
        settings,
    ) -> None:
        self.executed.append((symbol, qty, price))


def _make_buy_result(passed: bool = True) -> MagicMock:
    # Sufficiently shaped BuyDecision stand-in: the runner only reads
    # `.rule_results`, `.passed_count`, `.enabled_count`, `.should_attempt_buy`.
    result = MagicMock()
    result.rule_results = []
    result.passed_count = 3 if passed else 0
    result.enabled_count = 3
    result.should_attempt_buy = passed
    return result


def _make_sizing(recommended_qty: int = 10) -> MagicMock:
    sizing = MagicMock()
    sizing.recommended_qty = recommended_qty
    sizing.max_affordable_qty = recommended_qty
    sizing.budget_limited_qty = recommended_qty
    sizing.exposure_limited_qty = recommended_qty
    sizing.max_qty_limited_qty = recommended_qty
    sizing.reason = "ok"
    sizing.details = {}
    return sizing


def _write_ai_cache(tmp: str, payload: dict, day: str = "2024-03-15") -> None:
    with open(os.path.join(tmp, f"{day}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class _StrategyPatcher:
    """Patch the three strategy functions imported into runner.py."""

    def __init__(
        self,
        *,
        scores: dict[str, tuple[float, dict[str, float]]],
        buy_passed: bool = True,
        sizing_qty: int = 10,
    ) -> None:
        self.scores = scores
        self.buy_passed = buy_passed
        self.sizing_qty = sizing_qty
        self._patches: list = []

    def __enter__(self) -> "_StrategyPatcher":
        self._patches = [
            patch.object(
                runner_mod,
                "evaluate_buy_decision",
                side_effect=lambda **kw: _make_buy_result(self.buy_passed),
            ),
            patch.object(
                runner_mod,
                "calculate_selection_score",
                side_effect=lambda **kw: self.scores[kw["snapshot"].symbol],
            ),
            patch.object(
                runner_mod,
                "calculate_position_sizing",
                side_effect=lambda **kw: _make_sizing(self.sizing_qty),
            ),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for p in self._patches:
            p.stop()


def _run(
    symbols: list[str],
    *,
    scores: dict[str, tuple[float, dict[str, float]]],
    ai_provider: AISignalProvider | None = None,
    buy_passed: bool = True,
    sizing_qty: int = 10,
) -> tuple[_FakePortfolio, DayRecord]:
    portfolio = _FakePortfolio()
    record = DayRecord(date=_DAY, portfolio_value=portfolio.cash, cash=portfolio.cash)
    state = {"symbols_bought_today": [], "symbols_sold_today": []}
    with _StrategyPatcher(scores=scores, buy_passed=buy_passed, sizing_qty=sizing_qty):
        _run_buy_pass(
            portfolio=portfolio,
            data_provider=_FakeDataProvider(symbols),
            symbols=symbols,
            settings=_FakeSettings(),
            state=state,
            trading_date=_DAY,
            prices={s: 10_000 for s in symbols},
            record=record,
            ai_provider=ai_provider,
        )
    return portfolio, record


# ── tests ─────────────────────────────────────────────────────────────────


class DisabledParityTests(unittest.TestCase):
    def test_no_provider_equivalent_to_disabled_provider(self) -> None:
        scores = {
            "AAA": (5.0, {"trend_quality_score": 1.0}),
            "BBB": (4.0, {"trend_quality_score": 0.5}),
        }
        no_p, rec_no = _run(["AAA", "BBB"], scores=scores, ai_provider=None)
        disabled = AISignalProvider("/no/cache", mode="disabled")
        d_p, rec_d = _run(["AAA", "BBB"], scores=scores, ai_provider=disabled)

        # Identical executions and identical record payloads
        self.assertEqual(no_p.executed, d_p.executed)
        self.assertEqual(rec_no.to_dict(), rec_d.to_dict())


class ScoreDeltaInjectionTests(unittest.TestCase):
    def test_delta_flips_winner(self) -> None:
        # Raw scores: AAA beats BBB. AI delta boosts BBB past AAA.
        scores = {
            "AAA": (5.0, {"k": 1.0}),
            "BBB": (4.0, {"k": 1.0}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 1.0,
                    "tickers": {
                        "AAA": {"delta": 0.0},
                        "BBB": {"delta": 2.0},  # 4 + 2 = 6 > 5
                    },
                },
            )
            provider = AISignalProvider(tmp, mode="score_delta")
            portfolio, _ = _run(["AAA", "BBB"], scores=scores, ai_provider=provider)
        self.assertEqual([row[0] for row in portfolio.executed], ["BBB"])


class VetoInjectionTests(unittest.TestCase):
    def test_veto_short_circuits_before_buy_decision(self) -> None:
        scores = {"AAA": (5.0, {}), "BBB": (4.0, {})}
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 1.0,
                    "tickers": {
                        "AAA": {"veto": True, "veto_confidence": 0.9,
                                "veto_reason": "sector_weakness"},
                    },
                },
            )
            provider = AISignalProvider(tmp, mode="veto")
            portfolio, record = _run(
                ["AAA", "BBB"], scores=scores, ai_provider=provider
            )
        # AAA is vetoed → BBB wins despite lower raw score
        self.assertEqual([row[0] for row in portfolio.executed], ["BBB"])
        self.assertIn("AAA", record.skipped_symbols)
        self.assertIn(
            "ai_veto:sector_weakness", record.buy_rejection_reason_counts
        )

    def test_veto_below_threshold_does_not_block(self) -> None:
        scores = {"AAA": (5.0, {}), "BBB": (4.0, {})}
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 1.0,
                    "tickers": {
                        "AAA": {"veto": True, "veto_confidence": 0.4},
                    },
                },
            )
            provider = AISignalProvider(
                tmp, mode="veto", veto_threshold=0.75
            )
            portfolio, _ = _run(["AAA", "BBB"], scores=scores, ai_provider=provider)
        # Below-threshold veto is ignored → AAA wins on raw score
        self.assertEqual([row[0] for row in portfolio.executed], ["AAA"])


class RerankInjectionTests(unittest.TestCase):
    def test_rerank_overrides_score_order(self) -> None:
        scores = {
            "AAA": (5.0, {}),
            "BBB": (4.0, {}),
            "CCC": (3.0, {}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 1.0,
                    "tickers": {
                        "AAA": {"rank": 3},
                        "BBB": {"rank": 1},
                        "CCC": {"rank": 2},
                    },
                },
            )
            provider = AISignalProvider(tmp, mode="rerank")
            portfolio, _ = _run(
                ["AAA", "BBB", "CCC"], scores=scores, ai_provider=provider
            )
        # Rerank puts BBB at position 1 → BBB executed despite lower score.
        self.assertEqual([row[0] for row in portfolio.executed], ["BBB"])


class RegimeInjectionTests(unittest.TestCase):
    def test_regime_scales_recommended_qty(self) -> None:
        scores = {"AAA": (5.0, {})}
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 0.5,
                    "tickers": {"AAA": {}},
                },
            )
            provider = AISignalProvider(tmp, mode="regime")
            portfolio, _ = _run(
                ["AAA"], scores=scores, ai_provider=provider, sizing_qty=10
            )
        self.assertEqual(portfolio.executed, [("AAA", 5, 10_000)])

    def test_regime_zero_blocks_entry(self) -> None:
        scores = {"AAA": (5.0, {})}
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 0.0,
                    "tickers": {"AAA": {}},
                },
            )
            provider = AISignalProvider(tmp, mode="regime")
            portfolio, record = _run(
                ["AAA"], scores=scores, ai_provider=provider, sizing_qty=10
            )
        self.assertEqual(portfolio.executed, [])
        self.assertIn(
            "position_sizing_zero", record.buy_rejection_reason_counts
        )


class CombinedModeTests(unittest.TestCase):
    def test_all_four_hooks_compose(self) -> None:
        scores = {
            "AAA": (5.0, {}),
            "BBB": (4.0, {}),
            "CCC": (3.0, {}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 0.5,
                    "tickers": {
                        # AAA vetoed → drops out
                        "AAA": {"veto": True, "veto_confidence": 0.9,
                                "veto_reason": "x"},
                        # CCC gets +10 delta → top of score order (13)
                        # BBB stays at 4.0
                        "BBB": {"delta": 0.0, "rank": 1},
                        "CCC": {"delta": 10.0, "rank": 2},
                    },
                },
            )
            provider = AISignalProvider(tmp, mode="combined")
            portfolio, _ = _run(
                ["AAA", "BBB", "CCC"],
                scores=scores,
                ai_provider=provider,
                sizing_qty=10,
            )
        # Veto drops AAA. Score order after delta: CCC(13), BBB(4).
        # Rerank promotes BBB (rank 1) over CCC (rank 2).
        # Regime halves qty: 10 → 5.
        self.assertEqual(portfolio.executed, [("BBB", 5, 10_000)])


class CoverageReportingTests(unittest.TestCase):
    def test_provider_coverage_reflects_runner_lookups(self) -> None:
        scores = {"AAA": (5.0, {}), "BBB": (4.0, {})}
        with tempfile.TemporaryDirectory() as tmp:
            _write_ai_cache(
                tmp,
                {
                    "regime_multiplier": 1.0,
                    "tickers": {"AAA": {"delta": 1.0}},  # BBB missing
                },
            )
            provider = AISignalProvider(tmp, mode="score_delta")
            _run(["AAA", "BBB"], scores=scores, ai_provider=provider)
        cov = provider.coverage()
        self.assertEqual(cov["ticker_lookups"], 2)
        self.assertEqual(cov["ticker_hits"], 1)
        self.assertAlmostEqual(cov["signal_coverage_rate"], 0.5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
