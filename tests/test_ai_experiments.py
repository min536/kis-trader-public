"""Smoke tests for the AI-integration experiment orchestrator.

The orchestrator is a thin layer. These tests patch ``run_backtest`` at
the experiment module's import site so we exercise the orchestration
wiring (presets, provider construction, manifest, evaluator output,
comparison summary) without depending on the live trading engine's
signatures.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from backtester.ai_integration import AIIntegrationConfig
from backtester.ai_integration.experiments import (
    PRESETS,
    build_forward_returns,
    build_rule_proxy_features,
    run_experiment_suite,
)
from backtester.ai_integration.experiments import runner as exp_runner
from backtester.engine_backtest.data_provider import BacktestDataProvider
from backtester.engine_backtest.runner import BacktestResult


# ── fixtures ──────────────────────────────────────────────────────────────


def _write_csv(path: Path, *, symbols=("AAA", "BBB"), n_days: int = 30) -> None:
    """Write a minimal CSV that BacktestDataProvider.from_csv can load."""
    header = "date,symbol,open,high,low,close,volume,prev_close\n"
    lines = [header]
    start = date(2024, 1, 2)
    for sym in symbols:
        # Symbol-specific monotonic ramp so forward_returns are non-degenerate.
        base = 10_000 + 1_000 * (hash(sym) % 3)
        prev = base
        for i in range(n_days):
            d = start + timedelta(days=i)
            close_p = base + 100 * i * (1 if sym == "AAA" else -1) + (i % 5) * 20
            close_p = max(close_p, 1_000)
            lines.append(
                f"{d.isoformat()},{sym},{close_p},{close_p+50},{close_p-50},"
                f"{close_p},1000,{prev}\n"
            )
            prev = close_p
    path.write_text("".join(lines), encoding="utf-8")


def _fake_result(provider) -> BacktestResult:
    """A deterministic BacktestResult that depends on whether AI is active.

    Non-baseline providers produce a slightly higher final value so the
    Sharpe/return deltas are non-zero in the evaluator output.
    """
    initial = 10_000_000
    if provider is None:
        final = 10_100_000
        curve_vals = [initial, 10_020_000, 10_060_000, 10_100_000]
    else:
        final = 10_250_000
        curve_vals = [initial, 10_050_000, 10_150_000, 10_250_000]
    start = date(2024, 1, 2)
    equity = [(start + timedelta(days=i), v) for i, v in enumerate(curve_vals)]
    return BacktestResult(
        initial_cash=initial,
        final_value=final,
        trade_log=[],
        daily_records=[],
        equity_curve=equity,
    )


# ── orchestration smoke ───────────────────────────────────────────────────


class ExperimentManifestTests(unittest.TestCase):
    def test_baseline_and_synthetic_runs_write_expected_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data)

            with patch.object(exp_runner, "run_backtest",
                              side_effect=lambda **kw: _fake_result(kw.get("ai_provider"))):
                manifest = run_experiment_suite(
                    data_path=data,
                    presets=["baseline", "random", "ruleproxy", "perfect"],
                    output_root=tmp / "out",
                    experiment_name="smoke",
                )

            root = tmp / "out" / "smoke"
            self.assertTrue((root / "experiment_manifest.json").exists())
            self.assertTrue((root / "comparison_summary.txt").exists())
            for preset in ("baseline", "random", "ruleproxy", "perfect"):
                self.assertTrue((root / preset / "report.json").exists(), preset)
                self.assertTrue((root / preset / "summary.txt").exists(), preset)
            # Only non-baseline runs emit evaluator output.
            self.assertFalse((root / "baseline" / "ai_evaluation.json").exists())
            for preset in ("random", "ruleproxy", "perfect"):
                self.assertTrue((root / preset / "ai_evaluation.json").exists(), preset)

            on_disk = json.loads((root / "experiment_manifest.json").read_text())
            self.assertEqual(on_disk, manifest)
            self.assertEqual(
                [r["preset"] for r in manifest["runs"]],
                ["baseline", "random", "ruleproxy", "perfect"],
            )
            # Non-baseline entries are tagged synthetic + carry provider metadata.
            non_baseline = [r for r in manifest["runs"] if r["preset"] != "baseline"]
            for r in non_baseline:
                self.assertTrue(r["is_synthetic"])
                self.assertIsNotNone(r["provider_type"])
                self.assertIsNotNone(r["ai_config"])

    def test_unknown_preset_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data)
            with self.assertRaises(ValueError):
                run_experiment_suite(
                    data_path=data,
                    presets=["baseline", "not_a_preset"],
                    output_root=tmp / "out",
                    experiment_name="bad",
                )

    def test_baseline_auto_prepended_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data)
            with patch.object(exp_runner, "run_backtest",
                              side_effect=lambda **kw: _fake_result(kw.get("ai_provider"))):
                manifest = run_experiment_suite(
                    data_path=data,
                    presets=["random"],  # no baseline in input
                    output_root=tmp / "out",
                    experiment_name="auto",
                )
            self.assertEqual(
                [r["preset"] for r in manifest["runs"]],
                ["baseline", "random"],
            )

    def test_evaluation_payload_contains_all_required_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data)
            with patch.object(exp_runner, "run_backtest",
                              side_effect=lambda **kw: _fake_result(kw.get("ai_provider"))):
                run_experiment_suite(
                    data_path=data,
                    presets=["baseline", "perfect"],
                    output_root=tmp / "out",
                    experiment_name="eval",
                )
            ev_path = tmp / "out" / "eval" / "perfect" / "ai_evaluation.json"
            payload = json.loads(ev_path.read_text())
            for key in (
                "sharpe_baseline",
                "sharpe_ai",
                "sharpe_delta",
                "veto_precision",
                "veto_recall",
                "ndcg_at_3",
                "score_separation_ks",
                "signal_coverage_rate",
            ):
                self.assertIn(key, payload)


# ── determinism & building blocks ─────────────────────────────────────────


class DeterminismTests(unittest.TestCase):
    def test_random_preset_same_seed_same_comparison(self) -> None:
        """Two RandomAIProvider runs with identical inputs/seed match bit-for-bit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data)
            outputs = []
            with patch.object(exp_runner, "run_backtest",
                              side_effect=lambda **kw: _fake_result(kw.get("ai_provider"))):
                for i in range(2):
                    run_experiment_suite(
                        data_path=data,
                        presets=["baseline", "random"],
                        output_root=tmp / f"out{i}",
                        random_seed=42,
                        experiment_name="det",
                    )
                    outputs.append(
                        (tmp / f"out{i}" / "det" / "random" / "ai_evaluation.json").read_text()
                    )
            self.assertEqual(outputs[0], outputs[1])


class ForwardReturnsTests(unittest.TestCase):
    def test_forward_returns_have_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data, n_days=20)
            provider = BacktestDataProvider.from_csv(data)
            fwd = build_forward_returns(provider, horizon_trading_days=5)
            # We have 20 days per symbol, so last 5 days have no T+5 close.
            self.assertGreater(len(fwd), 0)
            any_day = next(iter(fwd))
            self.assertTrue(set(fwd[any_day].keys()).issubset({"AAA", "BBB"}))
            for row in fwd.values():
                for pct in row.values():
                    self.assertIsInstance(pct, float)

    def test_rule_proxy_features_have_rsi_and_momentum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            _write_csv(data, n_days=40)
            provider = BacktestDataProvider.from_csv(data)
            feats = build_rule_proxy_features(provider)
            self.assertGreater(len(feats), 0)
            last_day = sorted(feats)[-1]
            for sym, row in feats[last_day].items():
                self.assertIn("rsi", row)
                self.assertIn("momentum", row)
                self.assertGreaterEqual(row["rsi"], 0.0)
                self.assertLessEqual(row["rsi"], 100.0)


class RealBuyPassSmokeTests(unittest.TestCase):
    """End-to-end: baseline experiment actually drives the real run_backtest,
    including a live ``evaluate_buy_decision`` / ``evaluate_sell_decision``
    call chain. This is the regression guard against the kwargs drift
    (``enable_live_volume_rank`` etc.) that the orchestrator exposed.
    No mocking of the strategy functions.
    """

    def test_baseline_experiment_executes_buy_and_sell_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "prices.csv"
            # Enough days so the buy scan actually fires and forward_returns
            # / rule-proxy features have data to produce.
            _write_csv(data, symbols=("AAA", "BBB"), n_days=40)
            # NO patching — exercises the real engine paths.
            manifest = run_experiment_suite(
                data_path=data,
                presets=["baseline"],
                output_root=tmp / "out",
                experiment_name="realrun",
                initial_cash=10_000_000,
            )
            root = tmp / "out" / "realrun"
            self.assertTrue((root / "baseline" / "report.json").exists())
            report = json.loads((root / "baseline" / "report.json").read_text())
            # Real runner populates equity curve entries equal to number of
            # trading days in the union calendar.
            self.assertGreater(len(report["equity_curve"]), 0)
            self.assertEqual(manifest["runs"][0]["preset"], "baseline")


class PresetRegistryTests(unittest.TestCase):
    def test_all_five_presets_registered(self) -> None:
        self.assertEqual(
            set(PRESETS),
            {"baseline", "random", "ruleproxy", "perfect", "cache"},
        )


# ── Mode-gated cache evaluation end-to-end ───────────────────────────────
#
# These tests exercise the full pipeline from cache files → AISignalProvider
# → AIEvaluator → ai_evaluation.json artifact, for each of the four
# meaningful single-mode runs.
#
# Cache files are written directly (bypassing the JSONL builder) to keep
# the fixture minimal and deterministic.  run_backtest is mocked so the
# tests are fast and don't depend on the trading engine.
#
# Each test asserts the exact null/non-null pattern documented in the
# mode-gating matrix:
#
#   mode          | ks    | veto_p/r | ndcg@3
#   --------------|-------|----------|-------
#   score_delta   |  ✓    |  null    |  null
#   veto          |  null |  ✓       |  null
#   rerank        |  null |  null    |  ✓
#   combined      |  ✓    |  ✓       |  ✓


def _write_cache_files(cache_dir: Path) -> None:
    """Write three per-day cache files with all signal types populated.

    Dates: 2024-01-02, 2024-01-03, 2024-01-04
    Tickers: AAA, BBB, CCC

    Signals are deliberately non-trivial so the evaluator actually
    has data to compute (non-empty winner/loser buckets, non-zero
    ranks, non-zero veto counts relative to forward returns).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    for i, day in enumerate(dates):
        payload = {
            "regime_multiplier": 1.0 + i * 0.05,
            "tickers": {
                # AAA: strong positive delta, rank 1, no veto
                "AAA": {
                    "delta": 2.5,
                    "veto": False,
                    "veto_confidence": 0.05,
                    "veto_reason": None,
                    "rank": 1,
                },
                # BBB: neutral delta, rank 2, no veto
                "BBB": {
                    "delta": 0.2,
                    "veto": False,
                    "veto_confidence": 0.10,
                    "veto_reason": None,
                    "rank": 2,
                },
                # CCC: negative delta, rank 3, vetoed (high confidence)
                "CCC": {
                    "delta": -2.0,
                    "veto": True,
                    "veto_confidence": 0.85,
                    "veto_reason": "overheat",
                    "rank": 3,
                },
            },
        }
        (cache_dir / f"{day}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


# Forward returns aligned with the cache tickers/dates.
# AAA is always a winner (positive return), CCC always a loser.
_FORWARD_RETURNS = {
    "2024-01-02": {"AAA": 3.0,  "BBB": 0.5,  "CCC": -2.5},
    "2024-01-03": {"AAA": 2.0,  "BBB": 0.1,  "CCC": -1.8},
    "2024-01-04": {"AAA": 1.5,  "BBB": -0.2, "CCC": -3.0},
}


def _run_cache_mode(
    tmp: Path,
    mode: str,
    *,
    cache_dir: Path,
    data_path: Path,
) -> dict:
    """Run baseline+cache suite for one mode; return parsed ai_evaluation.json."""
    cfg = AIIntegrationConfig(
        enabled=True,
        mode=mode,
        cache_dir=str(cache_dir),
        veto_threshold=0.75,    # CCC's 0.85 confidence clears this
    )
    with patch.object(
        exp_runner, "run_backtest",
        side_effect=lambda **kw: _fake_result(kw.get("ai_provider")),
    ), patch.object(
        # Inject fixed forward returns so evaluator has ground truth
        # regardless of what the data CSV contains.
        exp_runner, "build_forward_returns",
        return_value=_FORWARD_RETURNS,
    ):
        run_experiment_suite(
            data_path=data_path,
            presets=["baseline", "cache"],
            output_root=tmp / mode,
            experiment_name="run",
            cache_ai_config=cfg,
        )
    ev_path = tmp / mode / "run" / "cache" / "ai_evaluation.json"
    return json.loads(ev_path.read_text(encoding="utf-8"))


class ModeGatedCacheEvaluationTests(unittest.TestCase):
    """End-to-end verification that ai_evaluation.json reflects mode gating.

    Each test uses the same cache files and forward returns, varying only
    the active mode.  The null/non-null pattern of the metrics is what
    we're asserting.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp()
        cls._tmp_path = Path(cls._tmp)
        cls._cache_dir = cls._tmp_path / "cache"
        _write_cache_files(cls._cache_dir)
        cls._data_path = cls._tmp_path / "prices.csv"
        _write_csv(cls._data_path, symbols=("AAA", "BBB", "CCC"), n_days=20)

    def _ev(self, mode: str) -> dict:
        return _run_cache_mode(
            self._tmp_path,
            mode,
            cache_dir=self._cache_dir,
            data_path=self._data_path,
        )

    # ── score_delta ───────────────────────────────────────────────────────

    def test_score_delta_computes_ks_suppresses_veto_and_ndcg(self) -> None:
        ev = self._ev("score_delta")
        self.assertEqual(ev["active_mode"], "score_delta")
        # KS: active — delta separates winners from losers
        self.assertIsNotNone(ev["score_separation_ks"])
        self.assertGreater(ev["score_separation_ks"], 0.0)
        # veto and rank: suppressed
        self.assertIsNone(ev["veto_precision"])
        self.assertIsNone(ev["veto_recall"])
        self.assertIsNone(ev["ndcg_at_3"])

    # ── veto ─────────────────────────────────────────────────────────────

    def test_veto_computes_precision_recall_suppresses_ndcg_and_ks(self) -> None:
        ev = self._ev("veto")
        self.assertEqual(ev["active_mode"], "veto")
        # Veto: active — CCC is vetoed (confidence 0.85 ≥ threshold 0.75)
        # and CCC has negative forward return → precision/recall > 0
        self.assertIsNotNone(ev["veto_precision"])
        self.assertIsNotNone(ev["veto_recall"])
        self.assertGreater(ev["veto_precision"], 0.0)
        self.assertGreater(ev["veto_recall"], 0.0)
        # ndcg and delta: suppressed
        self.assertIsNone(ev["ndcg_at_3"])
        self.assertIsNone(ev["score_separation_ks"])

    # ── rerank ────────────────────────────────────────────────────────────

    def test_rerank_computes_ndcg_suppresses_veto_and_ks(self) -> None:
        ev = self._ev("rerank")
        self.assertEqual(ev["active_mode"], "rerank")
        # NDCG: active — rank 1 = AAA (highest forward return) → NDCG close to 1
        self.assertIsNotNone(ev["ndcg_at_3"])
        self.assertGreater(ev["ndcg_at_3"], 0.0)
        # veto and delta: suppressed
        self.assertIsNone(ev["veto_precision"])
        self.assertIsNone(ev["veto_recall"])
        self.assertIsNone(ev["score_separation_ks"])

    # ── combined ──────────────────────────────────────────────────────────

    def test_combined_computes_all_three_metric_families(self) -> None:
        ev = self._ev("combined")
        self.assertEqual(ev["active_mode"], "combined")
        # All three families: non-null
        self.assertIsNotNone(ev["score_separation_ks"])
        self.assertIsNotNone(ev["veto_precision"])
        self.assertIsNotNone(ev["veto_recall"])
        self.assertIsNotNone(ev["ndcg_at_3"])
        # Sanity: sharpe_delta and coverage always present
        self.assertIn("sharpe_delta", ev)
        self.assertIsNotNone(ev["signal_coverage_rate"])

    # ── manifest and comparison_summary ──────────────────────────────────

    def test_manifest_records_active_mode_and_cache_dir(self) -> None:
        ev_ignored = self._ev("combined")  # runs the suite as a side-effect
        manifest_path = self._tmp_path / "combined" / "run" / "experiment_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        cache_run = next(r for r in manifest["runs"] if r["preset"] == "cache")
        self.assertEqual(cache_run["active_mode"], "combined")
        self.assertFalse(cache_run["is_synthetic"])
        self.assertIsNotNone(cache_run["cache_dir"])
        self.assertEqual(cache_run["cache_dir"], str(self._cache_dir))

    def test_comparison_summary_contains_mode_column(self) -> None:
        self._ev("score_delta")  # runs the suite as a side-effect
        summary_path = self._tmp_path / "score_delta" / "run" / "comparison_summary.txt"
        text = summary_path.read_text()
        # "mode" column header must be present
        self.assertIn("mode", text)
        # "score_delta" must appear in the body rows
        self.assertIn("score_delta", text)

    def test_comparison_summary_shows_dash_for_suppressed_metrics(self) -> None:
        """score_delta mode → ndcg@3 and veto columns should show —."""
        self._ev("score_delta")
        summary_path = self._tmp_path / "score_delta" / "run" / "comparison_summary.txt"
        text = summary_path.read_text()
        # The cache row must contain at least one suppressed-metric dash.
        self.assertIn("—", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
