"""Tests for synthetic AI providers: Perfect, Random, RuleProxy."""
from __future__ import annotations

import logging
import unittest
from datetime import date

from backtester.ai_integration import (
    PerfectAIProvider,
    RandomAIProvider,
    RuleProxyAIProvider,
)


_DAY = "2024-03-15"


# ── PerfectAIProvider ─────────────────────────────────────────────────────


class PerfectAIProviderTests(unittest.TestCase):
    def test_emits_look_ahead_warning_on_construction(self) -> None:
        with self.assertLogs(
            "backtester.ai_integration.baselines", level="WARNING"
        ) as cm:
            PerfectAIProvider({_DAY: {"AAA": 1.0}})
        self.assertTrue(any("LOOK-AHEAD" in r.message for r in cm.records))

    def test_delta_tracks_future_return(self) -> None:
        p = PerfectAIProvider(
            {_DAY: {"AAA": 3.5, "BBB": -1.0, "CCC": 0.0}},
            mode="score_delta",
        )
        self.assertAlmostEqual(p.get_score_delta(_DAY, "AAA"), 3.5)
        self.assertAlmostEqual(p.get_score_delta(_DAY, "BBB"), -1.0)
        self.assertAlmostEqual(p.get_score_delta(_DAY, "CCC"), 0.0)

    def test_rank_sorts_by_future_return_desc(self) -> None:
        p = PerfectAIProvider(
            {_DAY: {"LO": 1.0, "HI": 5.0, "MID": 2.5}},
            mode="rerank",
        )
        # Cast to runner-style tuples so the default ticker extractor applies.
        cands = [(0.0, t, None, None) for t in ("LO", "HI", "MID")]
        reordered = p.get_reranking(_DAY, cands)
        self.assertEqual([c[1] for c in reordered], ["HI", "MID", "LO"])

    def test_veto_fires_when_future_return_below_cutoff(self) -> None:
        p = PerfectAIProvider(
            {_DAY: {"OK": 1.0, "LOSER": -3.0}},
            mode="veto",
            veto_cutoff_pct=-2.0,
        )
        self.assertEqual(p.get_veto(_DAY, "LOSER"), "future_loss")
        self.assertIsNone(p.get_veto(_DAY, "OK"))

    def test_regime_multiplier_default_is_neutral(self) -> None:
        p = PerfectAIProvider({_DAY: {"A": 0.0}}, mode="regime")
        self.assertEqual(p.get_regime_multiplier(_DAY), 1.0)


# ── RandomAIProvider ──────────────────────────────────────────────────────


class RandomAIProviderTests(unittest.TestCase):
    def test_same_seed_produces_identical_signals(self) -> None:
        dates = ["2024-03-15", "2024-03-16"]
        tickers = ["AAA", "BBB", "CCC"]
        p1 = RandomAIProvider(dates, tickers, seed=42)
        p2 = RandomAIProvider(dates, tickers, seed=42)
        for day in dates:
            for t in tickers:
                self.assertEqual(
                    p1.get_score_delta(day, t),
                    p2.get_score_delta(day, t),
                )
                self.assertEqual(
                    p1.get_veto(day, t), p2.get_veto(day, t)
                )
            self.assertEqual(
                p1.get_regime_multiplier(day),
                p2.get_regime_multiplier(day),
            )

    def test_different_seeds_produce_different_signals(self) -> None:
        dates = ["2024-03-15"]
        tickers = ["A", "B", "C", "D"]
        p1 = RandomAIProvider(dates, tickers, seed=1)
        p2 = RandomAIProvider(dates, tickers, seed=2)
        deltas_1 = [p1.get_score_delta(dates[0], t) for t in tickers]
        deltas_2 = [p2.get_score_delta(dates[0], t) for t in tickers]
        self.assertNotEqual(deltas_1, deltas_2)

    def test_delta_within_configured_range(self) -> None:
        p = RandomAIProvider(
            ["2024-03-15"], [f"T{i}" for i in range(50)],
            seed=7, delta_range=2.0,
        )
        for i in range(50):
            d = p.get_score_delta("2024-03-15", f"T{i}")
            self.assertGreaterEqual(d, -2.0)
            self.assertLessEqual(d, 2.0)

    def test_rerank_is_a_permutation(self) -> None:
        tickers = [f"T{i}" for i in range(10)]
        p = RandomAIProvider(["2024-03-15"], tickers, seed=3, mode="rerank")
        cands = [(0.0, t, None, None) for t in tickers]
        reordered = p.get_reranking("2024-03-15", cands)
        self.assertEqual(
            sorted(c[1] for c in reordered),
            sorted(tickers),
        )


# ── RuleProxyAIProvider ───────────────────────────────────────────────────


class RuleProxyAIProviderTests(unittest.TestCase):
    def _cache(self, **rows: dict) -> dict:
        return {_DAY: rows}

    def test_high_rsi_produces_negative_delta(self) -> None:
        p = RuleProxyAIProvider(
            self._cache(AAA={"rsi": 70.0, "momentum": 1.0})
        )
        self.assertEqual(p.get_score_delta(_DAY, "AAA"), -5.0)

    def test_low_rsi_with_positive_momentum_produces_positive_delta(self) -> None:
        p = RuleProxyAIProvider(
            self._cache(AAA={"rsi": 30.0, "momentum": 0.5})
        )
        self.assertEqual(p.get_score_delta(_DAY, "AAA"), 5.0)

    def test_low_rsi_with_nonpositive_momentum_is_neutral(self) -> None:
        p = RuleProxyAIProvider(
            self._cache(AAA={"rsi": 30.0, "momentum": 0.0})
        )
        self.assertEqual(p.get_score_delta(_DAY, "AAA"), 0.0)

    def test_mid_rsi_is_neutral(self) -> None:
        p = RuleProxyAIProvider(
            self._cache(AAA={"rsi": 50.0, "momentum": 5.0})
        )
        self.assertEqual(p.get_score_delta(_DAY, "AAA"), 0.0)

    def test_thresholds_are_strict(self) -> None:
        # RSI == 65 should NOT trigger high-rsi (strict >)
        p = RuleProxyAIProvider(
            self._cache(X={"rsi": 65.0, "momentum": 1.0})
        )
        self.assertEqual(p.get_score_delta(_DAY, "X"), 0.0)
        # RSI == 45 should NOT trigger low-rsi (strict <)
        p2 = RuleProxyAIProvider(
            self._cache(Y={"rsi": 45.0, "momentum": 1.0})
        )
        self.assertEqual(p2.get_score_delta(_DAY, "Y"), 0.0)

    def test_veto_and_rerank_are_neutral(self) -> None:
        p = RuleProxyAIProvider(
            self._cache(A={"rsi": 80.0, "momentum": 1.0}),
            mode="combined",
        )
        self.assertIsNone(p.get_veto(_DAY, "A"))
        self.assertEqual(p.get_regime_multiplier(_DAY), 1.0)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
