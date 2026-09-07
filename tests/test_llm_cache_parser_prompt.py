"""Tests for backtester.ai_integration.llm_cache_builder parser and prompt.

Covers parser validation/errors, prompt builders, and few-shot examples.
No real LLM calls are made — all tests are fully offline.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtester.ai_integration.llm_cache_builder.batch import (
    BatchCacheGenerator,
)
from backtester.ai_integration.llm_cache_builder.client import MockLLMClient
from backtester.ai_integration.llm_cache_builder.parser import (
    ParseError,
    parse_response,
)
from backtester.ai_integration.llm_cache_builder.prompt import (
    EX1_TICKERS,
    EX2_TICKERS,
    _EX1_OUTPUT,
    _EX2_OUTPUT,
    build_correction_prompt,
    build_system_prompt,
    build_user_prompt,
)

from llm_cache_builder_testkit import _rec, _valid_response, _write_jsonl


# ── Parser tests ──────────────────────────────────────────────────────────


class ParserValidResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tickers = ["AAA", "BBB", "CCC"]
        self.raw = _valid_response(self.tickers)

    def test_returns_dict_with_required_keys(self) -> None:
        result = parse_response(self.raw, self.tickers)
        self.assertIn("regime_multiplier", result)
        self.assertIn("tickers", result)

    def test_all_tickers_present(self) -> None:
        result = parse_response(self.raw, self.tickers)
        self.assertEqual(set(result["tickers"]), set(self.tickers))

    def test_regime_multiplier_in_valid_range(self) -> None:
        result = parse_response(self.raw, self.tickers)
        rm = result["regime_multiplier"]
        self.assertGreaterEqual(rm, 0.8)
        self.assertLessEqual(rm, 1.2)

    def test_ticker_fields_have_correct_types(self) -> None:
        result = parse_response(self.raw, self.tickers)
        for ticker, row in result["tickers"].items():
            self.assertIsInstance(row["delta"], float)
            self.assertIsInstance(row["veto"], bool)
            self.assertIsInstance(row["veto_confidence"], float)
            self.assertIsInstance(row["rank"], int)

    def test_ranks_are_unique_and_contiguous(self) -> None:
        result = parse_response(self.raw, self.tickers)
        ranks = sorted(row["rank"] for row in result["tickers"].values())
        self.assertEqual(ranks, list(range(1, len(self.tickers) + 1)))

    def test_delta_clamped_to_plus_five(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": 99.9, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        self.assertLessEqual(result["tickers"]["AAA"]["delta"], 5.0)

    def test_delta_clamped_to_minus_five(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": -99.9, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        self.assertGreaterEqual(result["tickers"]["AAA"]["delta"], -5.0)

    def test_strips_markdown_code_fence(self) -> None:
        wrapped = "```json\n" + self.raw + "\n```"
        result = parse_response(wrapped, self.tickers)
        self.assertEqual(set(result["tickers"]), set(self.tickers))

    def test_non_contiguous_ranks_normalised(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": 1.0, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 10},
                "BBB": {"delta": 0.5, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 20},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA", "BBB"])
        ranks = sorted(r["rank"] for r in result["tickers"].values())
        self.assertEqual(ranks, [1, 2])

    def test_regime_multiplier_clamped_from_above(self) -> None:
        payload = {
            "regime_multiplier": 1.45,  # within hard range but above storage range
            "tickers": {
                "AAA": {"delta": 0.0, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        self.assertLessEqual(result["regime_multiplier"], 1.2)

    def test_veto_true_enforces_reason_and_confidence(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": -2.0, "veto": True, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        row = result["tickers"]["AAA"]
        self.assertTrue(row["veto"])
        self.assertIsNotNone(row["veto_reason"])
        self.assertGreater(row["veto_confidence"], 0.0)

    def test_unknown_veto_reason_coerced_to_other(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": -1.0, "veto": True, "veto_confidence": 0.8,
                        "veto_reason": "market_crash", "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        self.assertEqual(result["tickers"]["AAA"]["veto_reason"], "other")

    def test_veto_string_true_accepted(self) -> None:
        payload = {
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": -2.0, "veto": "true", "veto_confidence": 0.7,
                        "veto_reason": "overheat", "rank": 1},
            },
        }
        result = parse_response(json.dumps(payload), ["AAA"])
        self.assertIsInstance(result["tickers"]["AAA"]["veto"], bool)
        self.assertTrue(result["tickers"]["AAA"]["veto"])


class ParserErrorTests(unittest.TestCase):
    def _tickers(self) -> list[str]:
        return ["AAA"]

    def test_no_json_raises_parse_error(self) -> None:
        with self.assertRaises(ParseError):
            parse_response("this is not json", self._tickers())

    def test_missing_regime_multiplier_raises(self) -> None:
        raw = json.dumps({"tickers": {"AAA": {"delta": 0.0, "veto": False,
                          "veto_confidence": 0.0, "veto_reason": None, "rank": 1}}})
        with self.assertRaises(ParseError) as ctx:
            parse_response(raw, self._tickers())
        self.assertIn("regime_multiplier", str(ctx.exception))

    def test_missing_tickers_key_raises(self) -> None:
        raw = json.dumps({"regime_multiplier": 1.0})
        with self.assertRaises(ParseError) as ctx:
            parse_response(raw, self._tickers())
        self.assertIn("tickers", str(ctx.exception))

    def test_missing_ticker_raises(self) -> None:
        raw = json.dumps({"regime_multiplier": 1.0, "tickers": {}})
        with self.assertRaises(ParseError) as ctx:
            parse_response(raw, self._tickers())
        self.assertIn("AAA", str(ctx.exception))

    def test_duplicate_ranks_raise(self) -> None:
        raw = json.dumps({
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": 1.0, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},
                "BBB": {"delta": 0.5, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 1},  # duplicate!
            },
        })
        with self.assertRaises(ParseError) as ctx:
            parse_response(raw, ["AAA", "BBB"])
        self.assertIn("duplicate rank", str(ctx.exception))

    def test_out_of_range_regime_raises(self) -> None:
        raw = json.dumps({"regime_multiplier": 3.0, "tickers": {}})
        with self.assertRaises(ParseError):
            parse_response(raw, [])

    def test_missing_rank_raises(self) -> None:
        raw = json.dumps({
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": 0.0, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None},  # rank missing
            },
        })
        with self.assertRaises(ParseError) as ctx:
            parse_response(raw, ["AAA"])
        self.assertIn("rank", str(ctx.exception))

    def test_rank_below_one_raises(self) -> None:
        raw = json.dumps({
            "regime_multiplier": 1.0,
            "tickers": {
                "AAA": {"delta": 0.0, "veto": False, "veto_confidence": 0.0,
                        "veto_reason": None, "rank": 0},
            },
        })
        with self.assertRaises(ParseError):
            parse_response(raw, ["AAA"])


# ── Prompt tests ──────────────────────────────────────────────────────────


class PromptTests(unittest.TestCase):
    def test_system_prompt_is_nonempty_string(self) -> None:
        sp = build_system_prompt()
        self.assertIsInstance(sp, str)
        self.assertGreater(len(sp), 100)

    def test_user_prompt_contains_date(self) -> None:
        records = [_rec("2024-01-15", "AAA")]
        p = build_user_prompt("2024-01-15", records)
        self.assertIn("2024-01-15", p)

    def test_user_prompt_contains_ticker(self) -> None:
        records = [_rec("2024-01-15", "005930")]
        p = build_user_prompt("2024-01-15", records)
        self.assertIn("005930", p)

    def test_user_prompt_lists_all_tickers(self) -> None:
        records = [_rec("2024-01-15", t) for t in ("AAA", "BBB", "CCC")]
        p = build_user_prompt("2024-01-15", records)
        for t in ("AAA", "BBB", "CCC"):
            self.assertIn(t, p)

    def test_user_prompt_includes_feature_values(self) -> None:
        records = [_rec("2024-01-15", "AAA", momentum=0.75)]
        p = build_user_prompt("2024-01-15", records)
        self.assertIn("momentum_quality_score", p)

    def test_correction_prompt_references_error(self) -> None:
        cp = build_correction_prompt("missing ticker AAA")
        self.assertIn("missing ticker AAA", cp)


# ── Few-shot prompt tests ─────────────────────────────────────────────────


class FewShotPromptTests(unittest.TestCase):
    """Verify the system prompt contains correct, parseable few-shot examples."""

    def setUp(self) -> None:
        self.sp = build_system_prompt()

    # ── Structural presence ────────────────────────────────────────────────

    def test_system_prompt_contains_example_1_tickers(self) -> None:
        for ticker in EX1_TICKERS:
            self.assertIn(ticker, self.sp, f"Example 1 ticker {ticker!r} missing from system prompt")

    def test_system_prompt_contains_example_2_tickers(self) -> None:
        for ticker in EX2_TICKERS:
            self.assertIn(ticker, self.sp, f"Example 2 ticker {ticker!r} missing from system prompt")

    def test_system_prompt_contains_both_example_dates(self) -> None:
        self.assertIn("2024-01-15", self.sp)
        self.assertIn("2024-01-22", self.sp)

    def test_system_prompt_contains_example_section_header(self) -> None:
        self.assertIn("EXAMPLES", self.sp)

    def test_system_prompt_mentions_two_examples(self) -> None:
        self.assertIn("Example 1 of 2", self.sp)
        self.assertIn("Example 2 of 2", self.sp)

    def test_system_prompt_contains_example_output_json(self) -> None:
        # The hardcoded output JSON must appear verbatim in the system prompt.
        self.assertIn(_EX1_OUTPUT, self.sp)
        self.assertIn(_EX2_OUTPUT, self.sp)

    # ── Veto example correctness ───────────────────────────────────────────

    def test_system_prompt_shows_veto_true_with_reason(self) -> None:
        # Example 1 should have veto=true with overheat reason for 000660.
        self.assertIn('"veto": true', self.sp)
        self.assertIn('"overheat"', self.sp)

    def test_system_prompt_shows_weak_signal_veto(self) -> None:
        # Example 2 should have weak_signal veto for 051910.
        self.assertIn('"weak_signal"', self.sp)

    def test_system_prompt_shows_veto_false_with_null_reason(self) -> None:
        # Clean candidates must have veto_reason: null.
        self.assertIn('"veto_reason": null', self.sp)

    # ── Rank and regime correctness ────────────────────────────────────────

    def test_system_prompt_shows_regime_below_neutral(self) -> None:
        # Example 2 has regime_multiplier 0.91.
        self.assertIn("0.91", self.sp)

    def test_system_prompt_shows_regime_above_neutral(self) -> None:
        # Example 1 has regime_multiplier 1.05.
        self.assertIn("1.05", self.sp)

    def test_system_prompt_shows_rank_1_through_3(self) -> None:
        # Example 2 has three tickers ranked 1, 2, 3.
        for rank in ('"rank": 1', '"rank": 2', '"rank": 3'):
            self.assertIn(rank, self.sp)

    # ── Example outputs are valid and parseable ────────────────────────────

    def test_example_1_output_parses_correctly(self) -> None:
        result = parse_response(_EX1_OUTPUT, EX1_TICKERS)
        self.assertAlmostEqual(result["regime_multiplier"], 1.05)
        self.assertFalse(result["tickers"]["005930"]["veto"])
        self.assertTrue(result["tickers"]["000660"]["veto"])
        self.assertEqual(result["tickers"]["000660"]["veto_reason"], "overheat")
        self.assertGreater(result["tickers"]["000660"]["veto_confidence"], 0.0)

    def test_example_2_output_parses_correctly(self) -> None:
        result = parse_response(_EX2_OUTPUT, EX2_TICKERS)
        self.assertAlmostEqual(result["regime_multiplier"], 0.91)
        self.assertFalse(result["tickers"]["006400"]["veto"])
        self.assertFalse(result["tickers"]["035420"]["veto"])
        self.assertTrue(result["tickers"]["051910"]["veto"])
        self.assertEqual(result["tickers"]["051910"]["veto_reason"], "weak_signal")

    def test_example_1_ranks_are_unique_and_contiguous(self) -> None:
        result = parse_response(_EX1_OUTPUT, EX1_TICKERS)
        ranks = sorted(r["rank"] for r in result["tickers"].values())
        self.assertEqual(ranks, [1, 2])

    def test_example_2_ranks_are_unique_and_contiguous(self) -> None:
        result = parse_response(_EX2_OUTPUT, EX2_TICKERS)
        ranks = sorted(r["rank"] for r in result["tickers"].values())
        self.assertEqual(ranks, [1, 2, 3])

    def test_example_1_best_ranked_ticker_has_highest_delta(self) -> None:
        result = parse_response(_EX1_OUTPUT, EX1_TICKERS)
        t = result["tickers"]
        rank1 = min(t, key=lambda k: t[k]["rank"])
        self.assertEqual(rank1, "005930")
        self.assertGreater(t["005930"]["delta"], t["000660"]["delta"])

    def test_example_2_best_ranked_ticker_has_highest_delta(self) -> None:
        result = parse_response(_EX2_OUTPUT, EX2_TICKERS)
        t = result["tickers"]
        rank1 = min(t, key=lambda k: t[k]["rank"])
        self.assertEqual(rank1, "006400")

    # ── Input section matches build_user_prompt output ────────────────────

    def test_example_1_input_section_matches_build_user_prompt(self) -> None:
        from backtester.ai_integration.llm_cache_builder.prompt import (
            _EX1_DATE, _EX1_RECORDS,
        )
        expected_input = build_user_prompt(_EX1_DATE, _EX1_RECORDS)
        self.assertIn(expected_input, self.sp)

    def test_example_2_input_section_matches_build_user_prompt(self) -> None:
        from backtester.ai_integration.llm_cache_builder.prompt import (
            _EX2_DATE, _EX2_RECORDS,
        )
        expected_input = build_user_prompt(_EX2_DATE, _EX2_RECORDS)
        self.assertIn(expected_input, self.sp)

    # ── System prompt is still accepted by the batch pipeline ────────────

    def test_batch_generator_receives_few_shot_system_prompt(self) -> None:
        """MockLLMClient records the system prompt; verify it contains examples."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "AAA")])
            client = MockLLMClient([_valid_response(["AAA"])])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
            )
            gen.generate(tmp / "f.jsonl")
            system_sent = client.calls[0]["system"]
            self.assertIn("EXAMPLES", system_sent)
            self.assertIn("005930", system_sent)
            self.assertIn("051910", system_sent)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
