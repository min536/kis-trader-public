"""Tests for Step 6 — YAML `ai_integration` block + sweep wiring."""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from backtester.ai_integration import (
    AI_DOTTED_PREFIX,
    AIIntegrationConfig,
    AISignalProvider,
)


# ── AIIntegrationConfig — parsing ─────────────────────────────────────────


class FromMappingTests(unittest.TestCase):
    def test_none_gives_defaults_and_ai_is_off(self) -> None:
        cfg = AIIntegrationConfig.from_mapping(None)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.mode, "disabled")
        self.assertFalse(cfg.is_active)

    def test_empty_mapping_gives_defaults(self) -> None:
        cfg = AIIntegrationConfig.from_mapping({})
        self.assertFalse(cfg.is_active)

    def test_full_mapping_parses_all_fields(self) -> None:
        cfg = AIIntegrationConfig.from_mapping(
            {
                "enabled": True,
                "mode": "combined",
                "cache_dir": "/tmp/ai",
                "veto_threshold": 0.8,
                "delta_scale": 0.5,
                "fallback": "warn",
            }
        )
        self.assertTrue(cfg.is_active)
        self.assertEqual(cfg.mode, "combined")
        self.assertEqual(cfg.cache_dir, "/tmp/ai")
        self.assertAlmostEqual(cfg.veto_threshold, 0.8)
        self.assertAlmostEqual(cfg.delta_scale, 0.5)
        self.assertEqual(cfg.fallback, "warn")

    def test_enabled_true_but_mode_disabled_is_inactive(self) -> None:
        cfg = AIIntegrationConfig.from_mapping(
            {"enabled": True, "mode": "disabled"}
        )
        self.assertFalse(cfg.is_active)

    def test_unknown_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig.from_mapping({"bogus_field": 1})


class ValidationTests(unittest.TestCase):
    def test_bad_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig(mode="nope")  # type: ignore[arg-type]

    def test_bad_fallback_raises(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig(fallback="explode")  # type: ignore[arg-type]

    def test_veto_threshold_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig(veto_threshold=1.5)
        with self.assertRaises(ValueError):
            AIIntegrationConfig(veto_threshold=-0.1)

    def test_negative_delta_scale_raises(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig(delta_scale=-0.1)

    def test_enabled_requires_cache_dir(self) -> None:
        with self.assertRaises(ValueError):
            AIIntegrationConfig(enabled=True, mode="combined", cache_dir="")


# ── with_override (CLI sweep) ─────────────────────────────────────────────


class WithOverrideTests(unittest.TestCase):
    def _base(self) -> AIIntegrationConfig:
        return AIIntegrationConfig(
            enabled=True,
            mode="score_delta",
            cache_dir="/tmp/ai",
            veto_threshold=0.75,
            delta_scale=1.0,
        )

    def test_override_delta_scale(self) -> None:
        base = self._base()
        out = base.with_override(f"{AI_DOTTED_PREFIX}delta_scale", "0.25")
        self.assertAlmostEqual(out.delta_scale, 0.25)
        # base unchanged (frozen dataclass)
        self.assertAlmostEqual(base.delta_scale, 1.0)

    def test_override_veto_threshold(self) -> None:
        out = self._base().with_override(
            f"{AI_DOTTED_PREFIX}veto_threshold", 0.9
        )
        self.assertAlmostEqual(out.veto_threshold, 0.9)

    def test_override_mode(self) -> None:
        out = self._base().with_override(f"{AI_DOTTED_PREFIX}mode", "veto")
        self.assertEqual(out.mode, "veto")

    def test_override_enabled_accepts_bool_strings(self) -> None:
        base = AIIntegrationConfig()  # disabled defaults
        on = base.with_override(f"{AI_DOTTED_PREFIX}enabled", "true")
        self.assertTrue(on.enabled)
        off = self._base().with_override(f"{AI_DOTTED_PREFIX}enabled", "false")
        self.assertFalse(off.enabled)

    def test_override_bad_prefix_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._base().with_override("sell_stop_loss_pct", -2.0)

    def test_override_unknown_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._base().with_override(
                f"{AI_DOTTED_PREFIX}not_a_field", "x"
            )

    def test_override_validates_new_value(self) -> None:
        with self.assertRaises(ValueError):
            self._base().with_override(
                f"{AI_DOTTED_PREFIX}veto_threshold", 2.0
            )


# ── build_provider ────────────────────────────────────────────────────────


class BuildProviderTests(unittest.TestCase):
    def test_disabled_builds_none(self) -> None:
        self.assertIsNone(AIIntegrationConfig().build_provider())

    def test_enabled_disabled_mode_still_none(self) -> None:
        cfg = AIIntegrationConfig(enabled=True, mode="disabled")
        self.assertIsNone(cfg.build_provider())

    def test_active_builds_real_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AIIntegrationConfig(
                enabled=True,
                mode="score_delta",
                cache_dir=tmp,
                veto_threshold=0.5,
                delta_scale=2.0,
                fallback="passthrough",
            )
            provider = cfg.build_provider()
            self.assertIsInstance(provider, AISignalProvider)
            # Provider configuration survives the round-trip.
            self.assertEqual(provider.mode, "score_delta")
            self.assertAlmostEqual(provider.veto_threshold, 0.5)
            self.assertAlmostEqual(provider.delta_scale, 2.0)


# ── YAML loader end-to-end ────────────────────────────────────────────────


class YamlLoaderTests(unittest.TestCase):
    def _write_yaml(self, tmp: Path, body: str) -> Path:
        p = tmp / "strat.kis.yaml"
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return p

    def test_yaml_without_ai_block_gives_default_config(self) -> None:
        from backtester.engine_backtest.settings_factory import (
            make_settings_from_yaml,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_yaml(
                Path(tmp),
                """
                metadata:
                  name: no_ai
                risk:
                  stop_loss:
                    enabled: true
                    percent: 3.0
                """,
            )
            _settings, meta = make_settings_from_yaml(path)
            ai_cfg: AIIntegrationConfig = meta["ai_integration_config"]
            self.assertFalse(ai_cfg.is_active)
            self.assertFalse(meta["ai_integration"]["enabled"])

    def test_yaml_with_ai_block_populates_config(self) -> None:
        from backtester.engine_backtest.settings_factory import (
            make_settings_from_yaml,
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "signals"
            cache_dir.mkdir()
            path = self._write_yaml(
                Path(tmp),
                f"""
                metadata:
                  name: with_ai
                ai_integration:
                  enabled: true
                  mode: combined
                  cache_dir: {cache_dir}
                  veto_threshold: 0.8
                  delta_scale: 0.5
                  fallback: warn
                """,
            )
            _settings, meta = make_settings_from_yaml(path)
            ai_cfg: AIIntegrationConfig = meta["ai_integration_config"]
            self.assertTrue(ai_cfg.is_active)
            self.assertEqual(ai_cfg.mode, "combined")
            self.assertAlmostEqual(ai_cfg.veto_threshold, 0.8)
            self.assertAlmostEqual(ai_cfg.delta_scale, 0.5)
            self.assertEqual(ai_cfg.fallback, "warn")
            self.assertIsNotNone(ai_cfg.build_provider())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
