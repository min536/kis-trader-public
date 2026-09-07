"""Unit tests for backtester.ai_integration.provider.AISignalProvider.

Covers:
* disabled mode — all getters return neutral, no cache I/O
* mode gating — each mode returns neutral for non-matching signals
* fallback policies — passthrough / warn / reject
* veto_threshold applied correctly
* delta_scale applied correctly
* signal_coverage_rate correctness
* rerank behaves stably with partial cache hits
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from datetime import date

from backtester.ai_integration import AISignalProvider, MissingAISignalError


def _write_cache(tmp: str, day: str, payload: dict) -> str:
    path = os.path.join(tmp, f"{day}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


_DAY = "2024-03-15"
_DAY_OBJ = date(2024, 3, 15)


def _sample_payload() -> dict:
    return {
        "regime_multiplier": 0.85,
        "tickers": {
            "005930": {
                "delta": 3.2,
                "veto": False,
                "veto_confidence": 0.1,
                "rank": 2,
            },
            "000660": {
                "delta": -1.5,
                "veto": True,
                "veto_confidence": 0.82,
                "veto_reason": "sector_weakness",
                "rank": 1,
            },
            "035420": {
                "delta": 0.5,
                "veto": True,
                "veto_confidence": 0.4,  # below default threshold
                "rank": 3,
            },
        },
    }


class DisabledModeTests(unittest.TestCase):
    def test_disabled_returns_neutral_and_skips_io(self) -> None:
        # Use a non-existent cache_dir to prove no I/O is attempted.
        provider = AISignalProvider("/no/such/dir/does/not/exist", mode="disabled")
        self.assertEqual(provider.loaded_day_count, 0)
        self.assertEqual(provider.get_score_delta(_DAY_OBJ, "005930"), 0.0)
        self.assertIsNone(provider.get_veto(_DAY_OBJ, "000660"))
        self.assertEqual(provider.get_regime_multiplier(_DAY_OBJ), 1.0)
        self.assertEqual(
            provider.get_reranking(_DAY_OBJ, [("a",), ("b",)]),
            [("a",), ("b",)],
        )
        self.assertEqual(provider.coverage()["ticker_lookups"], 0)


class ScoreDeltaModeTests(unittest.TestCase):
    def test_delta_scaled_and_mode_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="score_delta", delta_scale=2.0)
            self.assertAlmostEqual(p.get_score_delta(_DAY, "005930"), 6.4)
            # veto is gated off in score_delta mode
            self.assertIsNone(p.get_veto(_DAY, "000660"))
            # regime is gated off
            self.assertEqual(p.get_regime_multiplier(_DAY), 1.0)

    def test_combined_mode_activates_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="combined")
            self.assertAlmostEqual(p.get_score_delta(_DAY, "005930"), 3.2)
            self.assertEqual(p.get_veto(_DAY, "000660"), "sector_weakness")
            self.assertAlmostEqual(p.get_regime_multiplier(_DAY), 0.85)


class VetoTests(unittest.TestCase):
    def test_veto_confidence_below_threshold_is_not_vetoed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="veto", veto_threshold=0.75)
            self.assertEqual(p.get_veto(_DAY, "000660"), "sector_weakness")
            self.assertIsNone(p.get_veto(_DAY, "035420"))  # 0.4 < 0.75
            self.assertIsNone(p.get_veto(_DAY, "005930"))  # veto=False

    def test_default_reason_when_flag_set_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "regime_multiplier": 1.0,
                "tickers": {"A": {"veto": True, "veto_confidence": 0.9}},
            }
            _write_cache(tmp, _DAY, payload)
            p = AISignalProvider(tmp, mode="veto")
            self.assertEqual(p.get_veto(_DAY, "A"), "ai_veto")

    def test_lower_veto_threshold_triggers_more_vetoes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="veto", veto_threshold=0.3)
            self.assertEqual(p.get_veto(_DAY, "035420"), "ai_veto")


class FallbackTests(unittest.TestCase):
    def test_passthrough_returns_neutral_on_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="combined", fallback="passthrough")
            self.assertEqual(p.get_score_delta(_DAY, "NOT_CACHED"), 0.0)
            self.assertIsNone(p.get_veto(_DAY, "NOT_CACHED"))
            self.assertEqual(p.get_regime_multiplier("2099-01-01"), 1.0)

    def test_reject_raises_on_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="combined", fallback="reject")
            with self.assertRaises(MissingAISignalError):
                p.get_score_delta(_DAY, "NOT_CACHED")
            with self.assertRaises(MissingAISignalError):
                p.get_regime_multiplier("2099-01-01")

    def test_warn_logs_once_per_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="score_delta", fallback="warn")
            with self.assertLogs(
                "backtester.ai_integration.provider", level="WARNING"
            ) as cm:
                p.get_score_delta(_DAY, "MISS")
                p.get_score_delta(_DAY, "MISS")  # suppressed on second call
                p.get_score_delta(_DAY, "OTHER")  # new key → new warning
            self.assertEqual(len(cm.records), 2)


class CoverageTests(unittest.TestCase):
    def test_signal_coverage_rate_matches_hits_over_lookups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="combined")
            p.get_score_delta(_DAY, "005930")  # hit
            p.get_score_delta(_DAY, "MISS")    # miss
            p.get_veto(_DAY, "000660")         # hit
            p.get_veto(_DAY, "MISS")           # miss
            p.get_regime_multiplier(_DAY)      # regime hit
            cov = p.coverage()
            self.assertEqual(cov["ticker_lookups"], 4)
            self.assertEqual(cov["ticker_hits"], 2)
            self.assertAlmostEqual(cov["signal_coverage_rate"], 0.5)
            self.assertEqual(cov["regime_hits"], 1)

    def test_disabled_mode_never_increments_lookups(self) -> None:
        p = AISignalProvider("/nope", mode="disabled")
        p.get_score_delta(_DAY, "005930")
        p.get_regime_multiplier(_DAY)
        self.assertEqual(p.coverage()["ticker_lookups"], 0)
        self.assertEqual(p.coverage()["regime_lookups"], 0)


class RerankingTests(unittest.TestCase):
    def test_rerank_sorts_by_cached_rank_ascending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="rerank")
            # Runner-style candidates: (score, symbol, snapshot, buy_result)
            cands = [
                (5.0, "005930", None, None),
                (4.0, "000660", None, None),
                (3.0, "035420", None, None),
            ]
            reordered = p.get_reranking(_DAY, cands)
            self.assertEqual(
                [c[1] for c in reordered],
                ["000660", "005930", "035420"],  # ranks 1,2,3
            )

    def test_rerank_pushes_missing_to_back_stably(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="rerank", fallback="passthrough")
            cands = [
                (9.0, "UNRANKED_A", None, None),
                (8.0, "005930", None, None),    # rank 2
                (7.0, "UNRANKED_B", None, None),
                (6.0, "000660", None, None),    # rank 1
            ]
            reordered = p.get_reranking(_DAY, cands)
            self.assertEqual(
                [c[1] for c in reordered],
                ["000660", "005930", "UNRANKED_A", "UNRANKED_B"],
            )

    def test_rerank_is_noop_when_mode_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="score_delta")
            cands = [("a",), ("b",), ("c",)]
            self.assertEqual(
                p.get_reranking(_DAY, cands, ticker_of=lambda c: c[0]),
                cands,
            )

    def test_rerank_custom_ticker_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, _DAY, _sample_payload())
            p = AISignalProvider(tmp, mode="rerank")
            cands = [
                {"name": "A", "ticker": "005930"},
                {"name": "B", "ticker": "000660"},
            ]
            reordered = p.get_reranking(_DAY, cands)
            self.assertEqual([c["ticker"] for c in reordered], ["000660", "005930"])


class ValidationTests(unittest.TestCase):
    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AISignalProvider("/tmp", mode="bogus")  # type: ignore[arg-type]

    def test_invalid_fallback_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AISignalProvider("/tmp", fallback="loud")  # type: ignore[arg-type]


class CacheLoadingTests(unittest.TestCase):
    def test_loads_multiple_days_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_cache(tmp, "2024-03-15", _sample_payload())
            _write_cache(tmp, "2024-03-16", _sample_payload())
            p = AISignalProvider(tmp, mode="combined")
            self.assertEqual(p.loaded_day_count, 2)

    def test_missing_cache_dir_is_silent(self) -> None:
        p = AISignalProvider("/no/such/path/xyz", mode="combined")
        self.assertEqual(p.loaded_day_count, 0)
        # passthrough fallback → neutral values
        self.assertEqual(p.get_score_delta(_DAY, "X"), 0.0)

    def test_malformed_file_skipped_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "2024-03-15.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{ not valid json")
            with self.assertLogs(
                "backtester.ai_integration.provider", level="WARNING"
            ):
                p = AISignalProvider(tmp, mode="combined")
            self.assertEqual(p.loaded_day_count, 0)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
