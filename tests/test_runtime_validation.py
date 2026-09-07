from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.auth import runtime_validation


class ReadEnvFileValuesTests(unittest.TestCase):
    def test_parses_strips_quotes_and_skips_comments_and_blanks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / "session.env"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "",
                        "PLAIN=value",
                        'QUOTED="quoted value"',
                        "PADDED = padded ",
                        "NO_EQUALS_LINE",
                    ]
                ),
                encoding="utf-8",
            )
            values = runtime_validation.read_env_file_values(env_path)

        self.assertEqual(
            values,
            {"PLAIN": "value", "QUOTED": "quoted value", "PADDED": "padded"},
        )
        self.assertEqual(runtime_validation.read_env_file_values(None), {})


class LoadEnvFileTests(unittest.TestCase):
    def test_records_values_and_setdefaults_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "RV_TEST_NEW_KEY=from_file\nRV_TEST_EXISTING_KEY=from_file\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "RV_TEST_EXISTING_KEY": "from_process",
                    runtime_validation.DISABLE_DOTENV_ENV_NAME: "",
                },
                clear=False,
            ), mock.patch.dict(
                runtime_validation.LOADED_ENV_FILE_VALUES, {}, clear=True
            ):
                os.environ.pop("RV_TEST_NEW_KEY", None)
                runtime_validation.load_env_file(env_path)

                self.assertEqual(
                    runtime_validation.LOADED_ENV_FILE_VALUES,
                    {
                        "RV_TEST_NEW_KEY": "from_file",
                        "RV_TEST_EXISTING_KEY": "from_file",
                    },
                )
                self.assertEqual(os.environ["RV_TEST_NEW_KEY"], "from_file")
                self.assertEqual(os.environ["RV_TEST_EXISTING_KEY"], "from_process")
            os.environ.pop("RV_TEST_NEW_KEY", None)

    def test_disable_dotenv_skips_env_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ("." + "env")
            env_path.write_text("RV_TEST_DISABLED=from_file\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {runtime_validation.DISABLE_DOTENV_ENV_NAME: "1"},
                clear=False,
            ), mock.patch.dict(
                runtime_validation.LOADED_ENV_FILE_VALUES, {}, clear=True
            ):
                os.environ.pop("RV_TEST_DISABLED", None)
                runtime_validation.load_env_file(env_path)

                self.assertEqual(runtime_validation.LOADED_ENV_FILE_VALUES, {})
                self.assertNotIn("RV_TEST_DISABLED", os.environ)


class SessionEnvFileTests(unittest.TestCase):
    def test_path_and_values_follow_session_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / "session.env"
            env_path.write_text("RV_SESSION_KEY=session_value\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {runtime_validation.SESSION_ENV_FILE_ENV_NAME: str(env_path)},
                clear=False,
            ):
                self.assertEqual(
                    runtime_validation.get_session_env_file_path(), env_path
                )
                self.assertEqual(
                    runtime_validation.get_session_env_file_values(),
                    {"RV_SESSION_KEY": "session_value"},
                )

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(runtime_validation.SESSION_ENV_FILE_ENV_NAME, None)
            self.assertIsNone(runtime_validation.get_session_env_file_path())
            self.assertEqual(runtime_validation.get_session_env_file_values(), {})


class DetectRuntimeParameterSourceTests(unittest.TestCase):
    def test_classifies_default_session_initial_and_config_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = Path(tmp_dir) / "session.env"
            session_path.write_text("RV_PARAM=from_session\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {}, clear=False), mock.patch.dict(
                runtime_validation.INITIAL_ENV, {}, clear=False
            ), mock.patch.dict(
                runtime_validation.LOADED_ENV_FILE_VALUES, {}, clear=True
            ):
                os.environ[
                    runtime_validation.SESSION_ENV_FILE_ENV_NAME
                ] = str(session_path)
                runtime_validation.INITIAL_ENV.pop("RV_PARAM", None)

                os.environ.pop("RV_PARAM", None)
                self.assertEqual(
                    runtime_validation.detect_runtime_parameter_source("RV_PARAM"),
                    "default",
                )

                os.environ["RV_PARAM"] = "from_session"
                self.assertEqual(
                    runtime_validation.detect_runtime_parameter_source("RV_PARAM"),
                    "session config file",
                )

                os.environ["RV_PARAM"] = "from_initial"
                runtime_validation.INITIAL_ENV["RV_PARAM"] = "from_initial"
                self.assertEqual(
                    runtime_validation.detect_runtime_parameter_source("RV_PARAM"),
                    "environment override",
                )

                runtime_validation.INITIAL_ENV.pop("RV_PARAM", None)
                os.environ["RV_PARAM"] = "from_config"
                runtime_validation.LOADED_ENV_FILE_VALUES["RV_PARAM"] = "from_config"
                self.assertEqual(
                    runtime_validation.detect_runtime_parameter_source("RV_PARAM"),
                    "config file",
                )

                os.environ["RV_PARAM"] = "unmatched"
                self.assertEqual(
                    runtime_validation.detect_runtime_parameter_source("RV_PARAM"),
                    "environment override",
                )
            os.environ.pop("RV_PARAM", None)


class RuntimeFlagTests(unittest.TestCase):
    def test_strict_bypass_and_regular_session_flags_read_env(self) -> None:
        flag_env_names = (
            runtime_validation.STRICT_RUNTIME_OVERRIDE_ENV_NAMES
            + runtime_validation.STARTUP_SANITY_BYPASS_ENV_NAMES
            + (runtime_validation.ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME,)
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            for env_name in flag_env_names:
                os.environ.pop(env_name, None)

            self.assertFalse(
                runtime_validation.is_strict_runtime_override_validation_enabled()
            )
            self.assertFalse(runtime_validation.is_startup_sanity_bypass_enabled())
            self.assertFalse(
                runtime_validation.is_regular_session_runtime_profile_enforced()
            )

            os.environ[runtime_validation.STRICT_RUNTIME_OVERRIDE_ENV_NAMES[1]] = "true"
            self.assertTrue(
                runtime_validation.is_strict_runtime_override_validation_enabled()
            )
            os.environ[runtime_validation.STRICT_RUNTIME_OVERRIDE_ENV_NAMES[0]] = "false"
            self.assertFalse(
                runtime_validation.is_strict_runtime_override_validation_enabled()
            )

            os.environ[runtime_validation.STARTUP_SANITY_BYPASS_ENV_NAMES[0]] = "1"
            self.assertTrue(runtime_validation.is_startup_sanity_bypass_enabled())

            os.environ[
                runtime_validation.ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME
            ] = "on"
            self.assertTrue(
                runtime_validation.is_regular_session_runtime_profile_enforced()
            )
        for env_name in flag_env_names:
            os.environ.pop(env_name, None)


_RECOMMENDED_SETTINGS_FIELDS = {
    "buy_scan_interval_seconds": 60,
    "sell_check_interval_seconds": 30,
    "scan_symbols_max_per_cycle": 200,
    "buy_scan_shallow_top_k": 200,
    "buy_scan_deep_eval_limit": 200,
    "buy_scan_core_max": 12,
    "live_snapshot_ttl_seconds": 420,
    "live_snapshot_refresh_interval_seconds": 180,
    "buy_scan_quote_prefetch_deadline_seconds": 18.0,
    "buy_scan_quote_request_timeout_seconds": 2.0,
    "buy_scan_quote_max_attempts": 1,
    "buy_scan_total_budget_seconds": 25.0,
    "api_soft_max_requests_per_second": 4,
    "api_soft_max_quotes_per_tick": 20,
    "api_min_inter_request_seconds": 1.1,
    "api_buy_scan_min_request_reserve": 3,
    "api_buy_scan_min_quote_reserve": 4,
    "session_cycle_hard_budget_seconds": 60.0,
    "lane_scheduler_enabled": False,
}


def _make_settings(**overrides):
    from types import SimpleNamespace

    return SimpleNamespace(**{**_RECOMMENDED_SETTINGS_FIELDS, **overrides})


def _clean_flag_env() -> None:
    for env_name in (
        runtime_validation.STRICT_RUNTIME_OVERRIDE_ENV_NAMES
        + runtime_validation.STARTUP_SANITY_BYPASS_ENV_NAMES
        + (
            runtime_validation.ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME,
            runtime_validation.SESSION_ENV_FILE_ENV_NAME,
            "BUY_SCAN_QUOTE_KIS_ENV",
            "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
            "BUY_SCAN_TOTAL_BUDGET_SECONDS",
            "SESSION_CYCLE_HARD_BUDGET_SECONDS",
        )
    ):
        os.environ.pop(env_name, None)


class BuildRuntimeParameterValidationReportTests(unittest.TestCase):
    def test_recommended_defaults_produce_clean_report(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            report = runtime_validation.build_runtime_parameter_validation_report(
                _make_settings()
            )

        self.assertEqual(len(report["entries"]), 19)
        self.assertEqual(report["warnings"], [])
        self.assertFalse(report["strict_mode_enabled"])
        self.assertEqual(report["strict_violations"], [])
        self.assertFalse(report["strict_blocked"])
        self.assertFalse(report["regular_session_runtime_profile_enforced"])
        self.assertEqual(report["session_env_file_path"], "")
        for entry in report["entries"]:
            self.assertTrue(entry["matches_recommended_default"])

    def test_overridden_parameter_warns_and_blocks_in_strict_mode(self) -> None:
        with mock.patch.dict(
            os.environ, {"BUY_SCAN_INTERVAL_SECONDS": "90"}, clear=False
        ), mock.patch.dict(runtime_validation.INITIAL_ENV, {}, clear=False):
            _clean_flag_env()
            runtime_validation.INITIAL_ENV["BUY_SCAN_INTERVAL_SECONDS"] = "90"
            os.environ["STRICT_RUNTIME_PARAM_OVERRIDES"] = "true"

            report = runtime_validation.build_runtime_parameter_validation_report(
                _make_settings(buy_scan_interval_seconds=90)
            )
            os.environ.pop("STRICT_RUNTIME_PARAM_OVERRIDES", None)

        entry = next(
            item
            for item in report["entries"]
            if item["name"] == "BUY_SCAN_INTERVAL_SECONDS"
        )
        self.assertEqual(entry["attribute"], "buy_scan_interval_seconds")
        self.assertEqual(entry["effective_value"], 90)
        self.assertEqual(entry["recommended_value"], 60)
        self.assertEqual(entry["source"], "environment override")
        self.assertFalse(entry["matches_recommended_default"])
        self.assertEqual(
            report["warnings"],
            [
                "BUY_SCAN_INTERVAL_SECONDS=90 (recommended=60, "
                "source=environment override)"
            ],
        )
        self.assertTrue(report["strict_mode_enabled"])
        self.assertEqual(report["strict_violations"], report["warnings"])
        self.assertTrue(report["strict_blocked"])


class BuildStartupSanityReportTests(unittest.TestCase):
    def test_recommended_settings_pass_clean(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            os.environ["BUY_SCAN_QUOTE_KIS_ENV"] = "live"
            sanity = runtime_validation.build_startup_sanity_report(_make_settings())

        self.assertEqual(sanity["warnings"], [])
        self.assertEqual(sanity["errors"], [])
        self.assertFalse(sanity["bypass_enabled"])
        self.assertFalse(sanity["blocked"])

    def test_mock_session_rejects_spacing_below_current_one_per_second_limit(
        self,
    ) -> None:
        settings = _make_settings(
            base_url="https://openapivts.koreainvestment.com:9443",
            api_min_inter_request_seconds=0.9,
        )

        sanity = runtime_validation.build_startup_sanity_report(settings)

        self.assertTrue(sanity["blocked"])
        self.assertTrue(
            any("required>=1.05" in error for error in sanity["errors"])
        )

    def test_unsafe_settings_collect_errors_and_block_unless_bypassed(self) -> None:
        unsafe = _make_settings(
            buy_scan_interval_seconds=50,
            scan_symbols_max_per_cycle=50,
            api_buy_scan_min_quote_reserve=1,
            api_buy_scan_min_request_reserve=0,
            api_min_inter_request_seconds=0.2,
            live_snapshot_ttl_seconds=120,
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            sanity = runtime_validation.build_startup_sanity_report(
                unsafe, runtime_parameter_report={"warnings": ["w1"]}
            )

            os.environ["BYPASS_STARTUP_SANITY_CHECK"] = "true"
            bypassed = runtime_validation.build_startup_sanity_report(
                unsafe, runtime_parameter_report={"warnings": []}
            )
            os.environ.pop("BYPASS_STARTUP_SANITY_CHECK", None)

        self.assertEqual(len(sanity["errors"]), 6)
        self.assertEqual(sanity["warnings"], ["w1"])
        self.assertTrue(sanity["blocked"])
        self.assertTrue(bypassed["bypass_enabled"])
        self.assertFalse(bypassed["blocked"])

    def test_regular_session_profile_enforcement_turns_mismatch_into_error(
        self,
    ) -> None:
        report = {
            "warnings": [],
            "regular_session_runtime_profile_enforced": True,
            "entries": [
                {
                    "name": "BUY_SCAN_INTERVAL_SECONDS",
                    "effective_value": 60,
                    "recommended_value": 60,
                    "source": "default",
                    "matches_recommended_default": True,
                },
                {
                    "name": "SCAN_SYMBOLS_MAX_PER_CYCLE",
                    "effective_value": 20,
                    "recommended_value": 200,
                    "source": "config file",
                    "matches_recommended_default": False,
                },
            ],
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            os.environ["BUY_SCAN_QUOTE_KIS_ENV"] = "live"
            sanity = runtime_validation.build_startup_sanity_report(
                _make_settings(), runtime_parameter_report=report
            )

        self.assertEqual(
            sanity["errors"],
            [
                "정규 세션 런타임 제어값이 권장 세션 프로파일과 다릅니다. "
                "(SCAN_SYMBOLS_MAX_PER_CYCLE=20, recommended=200, source=config file)"
            ],
        )
        self.assertTrue(sanity["blocked"])

    def test_regular_session_profile_allows_session_lane_scheduler_activation(
        self,
    ) -> None:
        report = {
            "warnings": [
                "LANE_SCHEDULER_ENABLED=True (recommended=0, source=session config file)"
            ],
            "regular_session_runtime_profile_enforced": True,
            "entries": [
                {
                    "name": "LANE_SCHEDULER_ENABLED",
                    "effective_value": True,
                    "recommended_value": 0,
                    "source": "session config file",
                    "matches_recommended_default": False,
                },
            ],
        }
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            os.environ["BUY_SCAN_QUOTE_KIS_ENV"] = "live"
            sanity = runtime_validation.build_startup_sanity_report(
                _make_settings(lane_scheduler_enabled=True),
                runtime_parameter_report=report,
            )

        self.assertEqual(sanity["warnings"], report["warnings"])
        self.assertEqual(sanity["errors"], [])
        self.assertFalse(sanity["blocked"])


class AccountNumberFormatSanityTests(unittest.TestCase):
    """Startup sanity flags malformed KIS_CANO / KIS_ACNT_PRDT_CD.

    Guards the credential-rotation footgun (R-2): pasting a competition
    account number with a hyphen, embedded space, or a merged product code
    would otherwise reach every order/balance call unchecked. The check only
    fires when the attribute is present and non-empty, so test doubles that
    omit it (``_make_settings()``) are unaffected.
    """

    def _sanity_with_account(self, **account_fields):
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            os.environ["BUY_SCAN_QUOTE_KIS_ENV"] = "live"
            return runtime_validation.build_startup_sanity_report(
                _make_settings(**account_fields)
            )

    def test_valid_account_number_and_product_code_produce_no_findings(self) -> None:
        sanity = self._sanity_with_account(cano="12345678", acnt_prdt_cd="01")

        self.assertEqual(sanity["errors"], [])
        self.assertEqual(sanity["warnings"], [])
        self.assertFalse(sanity["blocked"])

    def test_hyphenated_account_number_is_blocking_error(self) -> None:
        sanity = self._sanity_with_account(cano="12345678-01", acnt_prdt_cd="01")

        self.assertEqual(len(sanity["errors"]), 1)
        self.assertIn("KIS_CANO", sanity["errors"][0])
        self.assertTrue(sanity["blocked"])

    def test_merged_product_code_length_is_blocking_error(self) -> None:
        sanity = self._sanity_with_account(cano="1234567801", acnt_prdt_cd="01")

        self.assertEqual(len(sanity["errors"]), 1)
        self.assertIn("KIS_CANO", sanity["errors"][0])
        self.assertTrue(sanity["blocked"])

    def test_embedded_space_account_number_is_blocking_error(self) -> None:
        sanity = self._sanity_with_account(cano="1234 5678", acnt_prdt_cd="01")

        self.assertEqual(len(sanity["errors"]), 1)
        self.assertIn("KIS_CANO", sanity["errors"][0])

    def test_nonstandard_product_code_is_warning_not_error(self) -> None:
        sanity = self._sanity_with_account(cano="12345678", acnt_prdt_cd="1")

        self.assertEqual(sanity["errors"], [])
        self.assertFalse(sanity["blocked"])
        self.assertTrue(
            any("KIS_ACNT_PRDT_CD" in warning for warning in sanity["warnings"])
        )

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        sanity = self._sanity_with_account(cano=" 12345678 ", acnt_prdt_cd=" 01 ")

        self.assertEqual(sanity["errors"], [])
        self.assertEqual(sanity["warnings"], [])

    def test_absent_account_attributes_do_not_raise_or_flag(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            _clean_flag_env()
            os.environ["BUY_SCAN_QUOTE_KIS_ENV"] = "live"
            sanity = runtime_validation.build_startup_sanity_report(_make_settings())

        self.assertEqual(sanity["errors"], [])
        self.assertEqual(sanity["warnings"], [])

    def test_account_number_masked_in_error_message(self) -> None:
        sanity = self._sanity_with_account(cano="98765432-99", acnt_prdt_cd="01")

        self.assertNotIn("98765432", sanity["errors"][0])


class SettingsAliasSeamTests(unittest.TestCase):
    """app.auth.settings re-exports the moved cluster under its legacy names."""

    def test_settings_exposes_runtime_validation_objects(self) -> None:
        from app.auth import settings as settings_module

        self.assertIs(settings_module._INITIAL_ENV, runtime_validation.INITIAL_ENV)
        self.assertIs(
            settings_module._LOADED_ENV_FILE_VALUES,
            runtime_validation.LOADED_ENV_FILE_VALUES,
        )
        self.assertIs(
            settings_module._RUNTIME_VALIDATED_PARAMETERS,
            runtime_validation.RUNTIME_VALIDATED_PARAMETERS,
        )
        self.assertIs(
            settings_module._RUNTIME_RECOMMENDED_DEFAULTS,
            runtime_validation.RUNTIME_RECOMMENDED_DEFAULTS,
        )
        self.assertIs(settings_module._load_env_file, runtime_validation.load_env_file)
        self.assertIs(
            settings_module._read_env_file_values,
            runtime_validation.read_env_file_values,
        )
        self.assertIs(
            settings_module._get_session_env_file_path,
            runtime_validation.get_session_env_file_path,
        )
        self.assertIs(
            settings_module._get_session_env_file_values,
            runtime_validation.get_session_env_file_values,
        )
        self.assertIs(
            settings_module._detect_runtime_parameter_source,
            runtime_validation.detect_runtime_parameter_source,
        )
        self.assertIs(
            settings_module._is_strict_runtime_override_validation_enabled,
            runtime_validation.is_strict_runtime_override_validation_enabled,
        )
        self.assertIs(
            settings_module._is_startup_sanity_bypass_enabled,
            runtime_validation.is_startup_sanity_bypass_enabled,
        )
        self.assertIs(
            settings_module._is_regular_session_runtime_profile_enforced,
            runtime_validation.is_regular_session_runtime_profile_enforced,
        )
        self.assertIs(
            settings_module.build_runtime_parameter_validation_report,
            runtime_validation.build_runtime_parameter_validation_report,
        )
        self.assertIs(
            settings_module.build_startup_sanity_report,
            runtime_validation.build_startup_sanity_report,
        )


if __name__ == "__main__":
    unittest.main()
