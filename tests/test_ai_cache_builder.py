"""Tests for backtester.ai_integration.cache_builder."""
from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from backtester.ai_integration.cache_builder import (
    build_cache_from_jsonl,
    build_day_payload,
    compute_signals,
    validate_cache_dir,
)
from backtester.ai_integration.provider import AISignalProvider


# ── helpers ───────────────────────────────────────────────────────────────

def _rec(
    date: str,
    ticker: str,
    *,
    base_score: float = 2.0,
    momentum: float = 0.30,
    trend: float = 0.35,
    recovery: float = 0.20,
    overheat: float = 0.0,
    exhaustion: float = 0.0,
    **extra_features,
) -> dict:
    features = {
        "momentum_quality_score": momentum,
        "trend_quality_score": trend,
        "range_recovery_bonus": recovery,
        "overheat_penalty": overheat,
        "pullback_exhaustion_penalty": exhaustion,
        **extra_features,
    }
    return {
        "date": date,
        "ticker": ticker,
        "base_score": base_score,
        "features": features,
        "decision": "approved",
        "decision_reason": None,
        "rule_gate_passed": True,
        "score_gate_passed": True,
        "candidate_rank": None,
        "executed": False,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    lines = [json.dumps(r, sort_keys=True) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── compute_signals ───────────────────────────────────────────────────────


class ComputeSignalsTests(unittest.TestCase):
    def test_neutral_features_produce_near_zero_delta(self) -> None:
        rec = _rec("2024-01-02", "AAA", momentum=0.30, trend=0.0, recovery=0.0)
        sig = compute_signals(rec)
        self.assertAlmostEqual(sig["delta"], 0.0, places=3)
        self.assertFalse(sig["veto"])

    def test_strong_momentum_produces_positive_delta(self) -> None:
        rec = _rec("2024-01-02", "AAA", momentum=0.70, trend=0.80, recovery=0.50)
        sig = compute_signals(rec)
        self.assertGreater(sig["delta"], 0.0)

    def test_overheat_veto_fires(self) -> None:
        rec = _rec("2024-01-02", "AAA", overheat=0.40)
        sig = compute_signals(rec)
        self.assertTrue(sig["veto"])
        self.assertEqual(sig["veto_reason"], "overheat")
        self.assertGreater(sig["veto_confidence"], 0.0)
        self.assertLessEqual(sig["veto_confidence"], 1.0)

    def test_pullback_exhaustion_veto_fires(self) -> None:
        rec = _rec("2024-01-02", "AAA", exhaustion=0.35)
        sig = compute_signals(rec)
        self.assertTrue(sig["veto"])
        self.assertEqual(sig["veto_reason"], "pullback_exhaustion")

    def test_weak_signal_veto_fires(self) -> None:
        rec = _rec("2024-01-02", "AAA", base_score=0.5, momentum=0.05)
        sig = compute_signals(rec)
        self.assertTrue(sig["veto"])
        self.assertEqual(sig["veto_reason"], "weak_signal")

    def test_delta_clamped_to_plus_five(self) -> None:
        rec = _rec("2024-01-02", "AAA",
                   momentum=1.0, trend=1.0, recovery=1.0,
                   overheat=0.0, exhaustion=0.0)
        sig = compute_signals(rec)
        self.assertLessEqual(sig["delta"], 5.0)

    def test_delta_clamped_to_minus_five(self) -> None:
        rec = _rec("2024-01-02", "AAA",
                   momentum=0.0, trend=0.0, recovery=0.0,
                   overheat=1.0, exhaustion=1.0)
        sig = compute_signals(rec)
        self.assertGreaterEqual(sig["delta"], -5.0)

    def test_no_veto_has_zero_to_one_confidence(self) -> None:
        rec = _rec("2024-01-02", "AAA")
        sig = compute_signals(rec)
        self.assertFalse(sig["veto"])
        self.assertGreaterEqual(sig["veto_confidence"], 0.0)
        self.assertLessEqual(sig["veto_confidence"], 1.0)


# ── build_day_payload ─────────────────────────────────────────────────────


class BuildDayPayloadTests(unittest.TestCase):
    def test_empty_records_returns_default_payload(self) -> None:
        payload = build_day_payload([])
        self.assertEqual(payload["regime_multiplier"], 1.0)
        self.assertEqual(payload["tickers"], {})

    def test_tickers_all_present(self) -> None:
        records = [_rec("2024-01-02", t) for t in ("AAA", "BBB", "CCC")]
        payload = build_day_payload(records)
        self.assertEqual(set(payload["tickers"]), {"AAA", "BBB", "CCC"})

    def test_ranks_are_unique_and_contiguous(self) -> None:
        records = [_rec("2024-01-02", t) for t in ("AAA", "BBB", "CCC", "DDD")]
        payload = build_day_payload(records)
        ranks = [payload["tickers"][t]["rank"] for t in payload["tickers"]]
        self.assertEqual(sorted(ranks), [1, 2, 3, 4])

    def test_higher_base_score_gets_better_rank(self) -> None:
        records = [
            _rec("2024-01-02", "LOSER", base_score=0.5, momentum=0.05, trend=0.0),
            _rec("2024-01-02", "WINNER", base_score=4.0, momentum=0.70, trend=0.80),
        ]
        payload = build_day_payload(records)
        self.assertLess(
            payload["tickers"]["WINNER"]["rank"],
            payload["tickers"]["LOSER"]["rank"],
        )

    def test_regime_multiplier_in_valid_range(self) -> None:
        records = [_rec("2024-01-02", t) for t in ("A", "B", "C")]
        payload = build_day_payload(records)
        rm = payload["regime_multiplier"]
        self.assertGreaterEqual(rm, 0.8)
        self.assertLessEqual(rm, 1.2)

    def test_duplicate_tickers_keeps_highest_base_score(self) -> None:
        records = [
            _rec("2024-01-02", "AAA", base_score=1.0),
            _rec("2024-01-02", "AAA", base_score=5.0),
        ]
        payload = build_day_payload(records)
        self.assertEqual(len(payload["tickers"]), 1)

    def test_combined_payload_has_all_required_keys(self) -> None:
        payload = build_day_payload([_rec("2024-01-02", "AAA")])
        t = payload["tickers"]["AAA"]
        for key in ("delta", "veto", "veto_confidence", "veto_reason", "rank"):
            self.assertIn(key, t)
        self.assertIsInstance(t["veto"], bool)
        self.assertIsInstance(t["rank"], int)
        self.assertIsInstance(t["delta"], float)


# ── build_cache_from_jsonl ────────────────────────────────────────────────


class BuildCacheTests(unittest.TestCase):
    def test_groups_records_into_correct_per_day_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = (
                [_rec("2024-01-02", f"T{i}") for i in range(3)]
                + [_rec("2024-01-03", f"T{i}") for i in range(2)]
                + [_rec("2024-01-04", f"T{i}") for i in range(4)]
            )
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)

            written = build_cache_from_jsonl(jsonl, tmp / "cache")

            self.assertEqual(written, ["2024-01-02", "2024-01-03", "2024-01-04"])
            for date in ("2024-01-02", "2024-01-03", "2024-01-04"):
                self.assertTrue((tmp / "cache" / f"{date}.json").exists())

    def test_deterministic_repeated_runs_produce_identical_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", f"T{i}", base_score=float(i)) for i in range(5)]
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)

            out1 = tmp / "cache1"
            out2 = tmp / "cache2"
            build_cache_from_jsonl(jsonl, out1)
            build_cache_from_jsonl(jsonl, out2)

            p1 = (out1 / "2024-01-02.json").read_text()
            p2 = (out2 / "2024-01-02.json").read_text()
            self.assertEqual(p1, p2)

    def test_overwrite_false_skips_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, [_rec("2024-01-02", "AAA")])

            build_cache_from_jsonl(jsonl, tmp / "cache")
            original = (tmp / "cache" / "2024-01-02.json").read_text()

            # Overwrite=False should not touch the existing file.
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                build_cache_from_jsonl(jsonl, tmp / "cache", overwrite=False)
            self.assertEqual((tmp / "cache" / "2024-01-02.json").read_text(), original)

    def test_overwrite_true_replaces_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, [_rec("2024-01-02", "AAA", base_score=1.0)])
            build_cache_from_jsonl(jsonl, tmp / "cache")

            _write_jsonl(jsonl, [_rec("2024-01-02", "AAA", base_score=9.0)])
            build_cache_from_jsonl(jsonl, tmp / "cache", overwrite=True)

            payload = json.loads((tmp / "cache" / "2024-01-02.json").read_text())
            self.assertAlmostEqual(
                payload["tickers"]["AAA"]["delta"],
                compute_signals(_rec("2024-01-02", "AAA", base_score=9.0))["delta"],
            )

    def test_limit_days_stops_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec(f"2024-01-{d:02d}", "AAA") for d in range(2, 12)]
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)
            written = build_cache_from_jsonl(jsonl, tmp / "cache", limit_days=3)
            self.assertEqual(len(written), 3)

    def test_skips_malformed_json_lines_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            jsonl = tmp / "features.jsonl"
            jsonl.write_text(
                json.dumps(_rec("2024-01-02", "AAA")) + "\n"
                + "NOT_JSON\n"
                + json.dumps(_rec("2024-01-02", "BBB")) + "\n",
                encoding="utf-8",
            )
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                written = build_cache_from_jsonl(jsonl, tmp / "cache")
            # Good records still produce a file.
            self.assertIn("2024-01-02", written)
            payload = json.loads((tmp / "cache" / "2024-01-02.json").read_text())
            self.assertIn("AAA", payload["tickers"])
            self.assertIn("BBB", payload["tickers"])


# ── generated files are loadable by AISignalProvider ─────────────────────


class AISignalProviderRoundTripTests(unittest.TestCase):
    def test_generated_files_loaded_by_provider_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", f"T{i}") for i in range(4)]
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)
            build_cache_from_jsonl(jsonl, tmp / "cache")

            provider = AISignalProvider(str(tmp / "cache"), mode="combined")
            # All generated tickers should resolve without fallback errors.
            for i in range(4):
                delta = provider.get_score_delta("2024-01-02", f"T{i}")
                self.assertIsInstance(delta, float)
            rm = provider.get_regime_multiplier("2024-01-02")
            self.assertGreaterEqual(rm, 0.8)
            self.assertLessEqual(rm, 1.2)

    def test_provider_respects_veto_confidence_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # overheat=0.5 → veto=True, veto_confidence ≈ 0.625
            records = [_rec("2024-01-02", "HOT", overheat=0.50)]
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)
            build_cache_from_jsonl(jsonl, tmp / "cache")

            # Provider default veto_threshold=0.75; confidence≈0.625 < 0.75 → no veto
            p = AISignalProvider(str(tmp / "cache"), mode="veto", veto_threshold=0.75)
            self.assertIsNone(p.get_veto("2024-01-02", "HOT"))

            # Lower threshold → veto fires
            p2 = AISignalProvider(str(tmp / "cache"), mode="veto", veto_threshold=0.50)
            self.assertIsNotNone(p2.get_veto("2024-01-02", "HOT"))


# ── validate_cache_dir ────────────────────────────────────────────────────


class ValidateCacheTests(unittest.TestCase):
    def test_valid_files_return_no_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec(f"2024-01-{d:02d}", f"T{i}") for d in range(2, 5) for i in range(3)]
            jsonl = tmp / "features.jsonl"
            _write_jsonl(jsonl, records)
            build_cache_from_jsonl(jsonl, tmp / "cache")
            issues = validate_cache_dir(tmp / "cache")
            self.assertEqual(issues, [])

    def test_invalid_json_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "2024-01-02.json").write_text("not json", encoding="utf-8")
            issues = validate_cache_dir(tmp)
            self.assertTrue(any("invalid JSON" in iss["issue"] for iss in issues))

    def test_out_of_range_regime_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            payload = {"regime_multiplier": 5.0, "tickers": {}}
            (tmp / "2024-01-02.json").write_text(json.dumps(payload), encoding="utf-8")
            issues = validate_cache_dir(tmp)
            self.assertTrue(any("regime_multiplier" in iss["issue"] for iss in issues))

    def test_non_bool_veto_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            payload = {
                "regime_multiplier": 1.0,
                "tickers": {"AAA": {"delta": 0.5, "veto": "yes", "veto_confidence": 0.5, "rank": 1}},
            }
            (tmp / "2024-01-02.json").write_text(json.dumps(payload), encoding="utf-8")
            issues = validate_cache_dir(tmp)
            self.assertTrue(any("veto must be bool" in iss["issue"] for iss in issues))

    def test_duplicate_ranks_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            payload = {
                "regime_multiplier": 1.0,
                "tickers": {
                    "AAA": {"delta": 1.0, "veto": False, "veto_confidence": 0.0, "rank": 1},
                    "BBB": {"delta": 0.5, "veto": False, "veto_confidence": 0.0, "rank": 1},
                },
            }
            (tmp / "2024-01-02.json").write_text(json.dumps(payload), encoding="utf-8")
            issues = validate_cache_dir(tmp)
            self.assertTrue(any("duplicate rank" in iss["issue"] for iss in issues))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
