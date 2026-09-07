"""Tests for make_settings_from_yaml() in settings_factory."""
from __future__ import annotations

import textwrap
import unittest
from pathlib import Path


def _write_yaml(content: str, path: Path) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


class MakeSettingsFromYamlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/_test_settings_factory_yaml")
        self._tmp.mkdir(parents=True, exist_ok=True)

    def _yaml(self, filename: str, content: str) -> Path:
        return _write_yaml(content, self._tmp / filename)

    def test_risk_section_maps_to_sell_fields(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("risk_test.yaml", """
            version: "1.0"
            metadata:
              name: risk_test
            risk:
              stop_loss:
                enabled: true
                percent: 3.5
              take_profit:
                enabled: true
                percent: 8.0
              trailing_stop:
                enabled: true
                percent: 2.5
        """)
        s, meta = make_settings_from_yaml(path)
        self.assertAlmostEqual(s.sell_stop_loss_pct, -3.5)
        self.assertAlmostEqual(s.sell_take_profit_pct, 8.0)
        self.assertAlmostEqual(s.sell_trailing_stop_pct, 2.5)

    def test_stop_loss_percent_always_negated(self) -> None:
        """YAML percent: 4.0 should produce sell_stop_loss_pct = -4.0 (not +4.0)."""
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("neg_test.yaml", """
            risk:
              stop_loss:
                percent: 4.0
        """)
        s, _ = make_settings_from_yaml(path)
        self.assertLess(s.sell_stop_loss_pct, 0)
        self.assertAlmostEqual(s.sell_stop_loss_pct, -4.0)

    def test_settings_overrides_section(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("overrides_test.yaml", """
            settings_overrides:
              buy_min_score: 4.0
              buy_rule_required_pass_count: 2
        """)
        s, meta = make_settings_from_yaml(path)
        self.assertAlmostEqual(s.buy_min_score, 4.0)
        self.assertEqual(s.buy_rule_required_pass_count, 2)
        self.assertIn("buy_min_score", meta["applied_overrides"])

    def test_metadata_extracted_correctly(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("meta_test.yaml", """
            metadata:
              name: my_strategy
              description: test strategy
              tags:
                - test
                - backtester
        """)
        _, meta = make_settings_from_yaml(path)
        self.assertEqual(meta["name"], "my_strategy")
        self.assertEqual(meta["description"], "test strategy")
        self.assertIn("test", meta["tags"])

    def test_name_defaults_to_file_stem_when_missing(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("my_cool_strategy.yaml", "version: '1.0'\n")
        _, meta = make_settings_from_yaml(path)
        self.assertEqual(meta["name"], "my_cool_strategy")

    def test_empty_yaml_uses_env_defaults(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings, make_settings_from_yaml

        path = self._yaml("empty.yaml", "{}\n")
        s_yaml, _ = make_settings_from_yaml(path)
        s_env = make_settings()
        # sell_stop_loss_pct should be same as env default
        self.assertAlmostEqual(s_yaml.sell_stop_loss_pct, s_env.sell_stop_loss_pct)

    def test_risk_and_overrides_combined(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("combo_test.yaml", """
            risk:
              stop_loss:
                percent: 3.0
            settings_overrides:
              buy_min_score: 3.8
        """)
        s, meta = make_settings_from_yaml(path)
        self.assertAlmostEqual(s.sell_stop_loss_pct, -3.0)
        self.assertAlmostEqual(s.buy_min_score, 3.8)

    def test_source_path_in_meta(self) -> None:
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        path = self._yaml("path_test.yaml", "version: '1.0'\n")
        _, meta = make_settings_from_yaml(path)
        self.assertIn("source_path", meta)

    def test_actual_tuned_yaml_loads_correctly(self) -> None:
        """Smoke test against one of the committed YAML files."""
        from backtester.engine_backtest.settings_factory import make_settings_from_yaml

        yaml_path = (
            Path(__file__).parent.parent
            / "backtester/strategies/kis_trader_continuation_family_approx_tuned_balanced.kis.yaml"
        )
        if not yaml_path.exists():
            self.skipTest("tuned YAML file not found")
        s, meta = make_settings_from_yaml(yaml_path)
        self.assertLess(s.sell_stop_loss_pct, 0)
        self.assertGreater(s.sell_take_profit_pct, 0)
        self.assertEqual(meta["name"], "kis_trader_continuation_family_approx_tuned_balanced")


if __name__ == "__main__":
    unittest.main()
