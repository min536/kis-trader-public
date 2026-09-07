from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from app.auth.settings import (
    build_startup_sanity_report,
    build_runtime_parameter_validation_report,
    get_settings,
    resolve_kis_credential_profile,
)


class GetSettingsEnvAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self._keys = (
            "KIS_ENV",
            "KIS_APP_KEY",
            "KIS_APP_SECRET",
            "KIS_BASE_URL",
            "KIS_CANO",
            "KIS_ACNT_PRDT_CD",
            "KIS_APP_MOCK_KEY",
            "KIS_APP_MOCK_SECRET",
            "KIS_BASE_MOCK_URL",
            "KIS_CANO_MOCK",
            "KIS_ACNT_PRDT_CD_MOCK",
            "KIS_APP_LIVE_KEY",
            "KIS_APP_LIVE_SECRET",
            "KIS_BASE_LIVE_URL",
            "KIS_CANO_LIVE",
            "KIS_ACNT_PRDT_CD_LIVE",
            "KIS_APP_live_KEY",
            "KIS_APP_live_SECRET",
            "KIS_BASE_live_URL",
            "KIS_CANO_live",
            "KIS_ACNT_PRDT_CD_live",
            "BUY_SCAN_INTERVAL_SECONDS",
            "SELL_CHECK_INTERVAL_SECONDS",
            "SCAN_SYMBOLS_MAX_PER_CYCLE",
            "BUY_SCAN_DEEP_EVAL_LIMIT",
            "LIVE_SNAPSHOT_TTL_SECONDS",
            "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
            "BUY_SCAN_PREFETCH_DEADLINE_ENABLED",
            "BUY_SCAN_PREFETCH_OVERLAP_ENABLED",
            "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
            "BUY_SCAN_TOTAL_BUDGET_SECONDS",
            "API_SOFT_MAX_REQUESTS_PER_SECOND",
            "API_SOFT_MAX_QUOTES_PER_TICK",
            "API_MIN_INTER_REQUEST_SECONDS",
            "API_BUY_SCAN_MIN_REQUEST_RESERVE",
            "API_BUY_SCAN_MIN_QUOTE_RESERVE",
            "ORDER_GATE_ENABLED",
            "SESSION_CYCLE_HARD_BUDGET_SECONDS",
            "STRICT_RUNTIME_PARAM_OVERRIDES",
            "BYPASS_STARTUP_SANITY_CHECK",
            "KIS_SESSION_ENV_FILE",
            "KIS_ENFORCE_REGULAR_SESSION_RUNTIME",
            "BUY_EXCLUDED_SYMBOLS",
        )
        self._original = {key: os.environ.get(key) for key in self._keys}

    def tearDown(self) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _reset_kis_env(self) -> None:
        for key in self._keys:
            os.environ.pop(key, None)

    def test_legacy_generic_kis_env_still_works(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"

        settings = get_settings()

        self.assertEqual(settings.app_key, "legacy-key")
        self.assertEqual(settings.app_secret, "legacy-secret")
        self.assertEqual(settings.base_url, "https://openapivts.koreainvestment.com:9443")
        self.assertEqual(settings.cano, "12345678")
        self.assertEqual(settings.acnt_prdt_cd, "01")

    def test_mock_scoped_credentials_follow_mock_base_url(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_APP_MOCK_KEY"] = "mock-key"
        os.environ["KIS_APP_MOCK_SECRET"] = "mock-secret"
        os.environ["KIS_CANO"] = "87654321"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"

        settings = get_settings()

        self.assertEqual(settings.app_key, "mock-key")
        self.assertEqual(settings.app_secret, "mock-secret")
        self.assertEqual(settings.base_url, "https://openapivts.koreainvestment.com:9443")

    def test_explicit_live_env_uses_live_scoped_values(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_ENV"] = "live"
        os.environ["KIS_BASE_LIVE_URL"] = "https://openapi.koreainvestment.com:9443"
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"
        os.environ["KIS_CANO_LIVE"] = "11112222"
        os.environ["KIS_ACNT_PRDT_CD_LIVE"] = "03"
        os.environ["KIS_APP_MOCK_KEY"] = "mock-key"
        os.environ["KIS_APP_MOCK_SECRET"] = "mock-secret"

        settings = get_settings()

        self.assertEqual(settings.app_key, "live-key")
        self.assertEqual(settings.app_secret, "live-secret")
        self.assertEqual(settings.base_url, "https://openapi.koreainvestment.com:9443")
        self.assertEqual(settings.cano, "11112222")
        self.assertEqual(settings.acnt_prdt_cd, "03")

    def test_mixed_case_live_aliases_are_supported(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_ENV"] = "live"
        os.environ["KIS_APP_live_KEY"] = "live-key-mixed"
        os.environ["KIS_APP_live_SECRET"] = "live-secret-mixed"
        os.environ["KIS_BASE_live_URL"] = "https://openapi.koreainvestment.com:9443"
        os.environ["KIS_CANO_live"] = "22223333"
        os.environ["KIS_ACNT_PRDT_CD_live"] = "04"

        settings = get_settings()

        self.assertEqual(settings.app_key, "live-key-mixed")
        self.assertEqual(settings.app_secret, "live-secret-mixed")
        self.assertEqual(settings.base_url, "https://openapi.koreainvestment.com:9443")
        self.assertEqual(settings.cano, "22223333")
        self.assertEqual(settings.acnt_prdt_cd, "04")

    def test_buy_scan_prefetch_overlap_defaults_false_when_env_absent(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"

        settings = get_settings()

        self.assertFalse(settings.buy_scan_prefetch_overlap_enabled)

    def test_buy_scan_prefetch_overlap_true_when_env_true(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_SCAN_PREFETCH_OVERLAP_ENABLED"] = "true"

        settings = get_settings()

        self.assertTrue(settings.buy_scan_prefetch_overlap_enabled)

    def test_buy_scan_prefetch_overlap_true_when_env_one(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_SCAN_PREFETCH_OVERLAP_ENABLED"] = "1"

        settings = get_settings()

        self.assertTrue(settings.buy_scan_prefetch_overlap_enabled)

    def test_runtime_parameter_validation_reports_default_sources(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"

        settings = get_settings()
        report = build_runtime_parameter_validation_report(settings)

        entry_by_name = {entry["name"]: entry for entry in report["entries"]}
        self.assertEqual(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["source"],
            "default",
        )
        self.assertEqual(
            entry_by_name["BUY_SCAN_SHALLOW_TOP_K"]["source"],
            "default",
        )
        self.assertTrue(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["matches_recommended_default"]
        )
        self.assertTrue(
            entry_by_name["BUY_SCAN_SHALLOW_TOP_K"]["matches_recommended_default"]
        )
        self.assertEqual(
            entry_by_name["API_MIN_INTER_REQUEST_SECONDS"]["effective_value"],
            1.1,
        )
        self.assertEqual(
            entry_by_name["API_SOFT_MAX_REQUESTS_PER_SECOND"]["effective_value"],
            4,
        )
        self.assertEqual(
            entry_by_name["API_SOFT_MAX_QUOTES_PER_TICK"]["effective_value"],
            20,
        )
        self.assertTrue(
            entry_by_name["API_MIN_INTER_REQUEST_SECONDS"]["matches_recommended_default"]
        )
        self.assertFalse(report["strict_mode_enabled"])
        self.assertFalse(report["strict_blocked"])
        self.assertEqual(report["warnings"], [])

    def test_buy_excluded_symbols_are_parsed_and_deduplicated(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_EXCLUDED_SYMBOLS"] = "252710, 005930, 252710"

        settings = get_settings()

        self.assertEqual(settings.buy_excluded_symbols, ("252710", "005930"))

    def test_runtime_parameter_validation_reports_environment_override_and_strict_block(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_SCAN_INTERVAL_SECONDS"] = "121"
        os.environ["STRICT_RUNTIME_PARAM_OVERRIDES"] = "true"

        settings = get_settings()
        report = build_runtime_parameter_validation_report(settings)

        entry_by_name = {entry["name"]: entry for entry in report["entries"]}
        self.assertEqual(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["effective_value"],
            121,
        )
        self.assertEqual(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["source"],
            "environment override",
        )
        self.assertFalse(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["matches_recommended_default"]
        )
        self.assertTrue(report["strict_mode_enabled"])
        self.assertTrue(report["strict_blocked"])
        self.assertTrue(report["warnings"])

    def test_startup_sanity_report_blocks_unsafe_trade_configuration(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_SCAN_INTERVAL_SECONDS"] = "50"
        os.environ["SCAN_SYMBOLS_MAX_PER_CYCLE"] = "50"
        os.environ["API_BUY_SCAN_MIN_QUOTE_RESERVE"] = "1"

        settings = get_settings()
        sanity = build_startup_sanity_report(settings)

        self.assertTrue(sanity["blocked"])
        self.assertFalse(sanity["bypass_enabled"])
        self.assertEqual(len(sanity["errors"]), 3)

    def test_startup_sanity_report_blocks_low_request_spacing_floor(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["API_MIN_INTER_REQUEST_SECONDS"] = "0.79"

        settings = get_settings()
        sanity = build_startup_sanity_report(settings)

        self.assertTrue(sanity["blocked"])
        self.assertTrue(
            any("API_MIN_INTER_REQUEST_SECONDS" in error for error in sanity["errors"])
        )

    def test_startup_sanity_report_allows_bypass_flag(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        os.environ["BUY_SCAN_INTERVAL_SECONDS"] = "50"
        os.environ["BYPASS_STARTUP_SANITY_CHECK"] = "true"

        settings = get_settings()
        sanity = build_startup_sanity_report(settings)

        self.assertTrue(sanity["bypass_enabled"])
        self.assertFalse(sanity["blocked"])
        self.assertTrue(sanity["errors"])

    def test_runtime_parameter_validation_reports_session_config_file_source(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("BUY_SCAN_INTERVAL_SECONDS=60\n")
            session_env_path = handle.name
        self.addCleanup(lambda: os.path.exists(session_env_path) and os.remove(session_env_path))

        os.environ["KIS_SESSION_ENV_FILE"] = session_env_path
        os.environ["BUY_SCAN_INTERVAL_SECONDS"] = "60"

        settings = get_settings()
        report = build_runtime_parameter_validation_report(settings)
        entry_by_name = {entry["name"]: entry for entry in report["entries"]}

        self.assertEqual(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["source"],
            "session config file",
        )
        self.assertTrue(
            entry_by_name["BUY_SCAN_INTERVAL_SECONDS"]["matches_recommended_default"]
        )
        self.assertEqual(report["session_env_file_path"], session_env_path)

    def test_disable_dotenv_flag_prevents_settings_import_loader_call(self) -> None:
        code = textwrap.dedent(
            """
            import os
            for key in tuple(os.environ):
                if key.startswith("KIS_") or key.startswith("BUY_SCAN_"):
                    os.environ.pop(key, None)
            os.environ["KIS_TRADER_DISABLE_DOTENV"] = "1"
            os.environ["KIS_APP_KEY"] = "test-key"
            os.environ["KIS_APP_SECRET"] = "test-secret"
            os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
            os.environ["KIS_CANO"] = "00000000"
            os.environ["KIS_ACNT_PRDT_CD"] = "01"
            import app.auth.runtime_validation as runtime_validation
            def boom(_path):
                raise AssertionError("loader should not be called")
            runtime_validation.load_env_file = boom
            import app.auth.settings as settings
            loaded = settings.get_settings()
            assert loaded.app_key == "test-key"
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.getcwd(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_sanity_report_blocks_regular_session_runtime_profile_mismatch(self) -> None:
        self._reset_kis_env()
        os.environ["KIS_APP_KEY"] = "legacy-key"
        os.environ["KIS_APP_SECRET"] = "legacy-secret"
        os.environ["KIS_BASE_URL"] = "https://openapivts.koreainvestment.com:9443"
        os.environ["KIS_CANO"] = "12345678"
        os.environ["KIS_ACNT_PRDT_CD"] = "01"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("BUY_SCAN_INTERVAL_SECONDS=180\n")
            handle.write("SELL_CHECK_INTERVAL_SECONDS=20\n")
            session_env_path = handle.name
        self.addCleanup(lambda: os.path.exists(session_env_path) and os.remove(session_env_path))

        os.environ["KIS_SESSION_ENV_FILE"] = session_env_path
        os.environ["KIS_ENFORCE_REGULAR_SESSION_RUNTIME"] = "true"
        os.environ["BUY_SCAN_INTERVAL_SECONDS"] = "120"

        settings = get_settings()
        report = build_runtime_parameter_validation_report(settings)
        sanity = build_startup_sanity_report(
            settings,
            runtime_parameter_report=report,
        )

        self.assertTrue(report["regular_session_runtime_profile_enforced"])
        self.assertTrue(sanity["blocked"])
        self.assertTrue(
            any(
                "정규 세션 런타임 제어값이 권장 세션 프로파일과 다릅니다."
                in error
                for error in sanity["errors"]
            )
        )


class ResolveKisCredentialProfileTests(unittest.TestCase):
    """Credential-profile resolution for the read-only live BUY quote lane."""

    def setUp(self) -> None:
        self._keys = (
            "KIS_ENV",
            "KIS_APP_KEY",
            "KIS_APP_SECRET",
            "KIS_BASE_URL",
            "KIS_APP_MOCK_KEY",
            "KIS_APP_MOCK_SECRET",
            "KIS_BASE_MOCK_URL",
            "KIS_MOCK_BASE_URL",
            "KIS_APP_LIVE_KEY",
            "KIS_APP_live_KEY",
            "KIS_LIVE_APP_KEY",
            "KIS_APP_LIVE_SECRET",
            "KIS_APP_live_SECRET",
            "KIS_LIVE_APP_SECRET",
            "KIS_BASE_LIVE_URL",
            "KIS_BASE_live_URL",
            "KIS_LIVE_BASE_URL",
        )
        self._original = {key: os.environ.get(key) for key in self._keys}
        for key in self._keys:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_live_profile_defaults_base_url_when_unset(self) -> None:
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"

        profile = resolve_kis_credential_profile(
            "live",
            allow_generic_fallback=False,
        )

        self.assertEqual(profile.env, "live")
        self.assertEqual(profile.app_key, "live-key")
        self.assertEqual(profile.app_secret, "live-secret")
        self.assertEqual(
            profile.base_url,
            "https://openapi.koreainvestment.com:9443",
        )

    def test_live_base_url_default_single_source(self) -> None:
        from app.auth import settings as settings_module
        from scripts import live_snapshot

        self.assertIs(
            live_snapshot.LIVE_BASE_URL_DEFAULT,
            settings_module.LIVE_BASE_URL_DEFAULT,
        )

    def test_live_profile_defaults_base_url_with_generic_fallback(self) -> None:
        os.environ["KIS_APP_LIVE_KEY"] = "live-key"
        os.environ["KIS_APP_LIVE_SECRET"] = "live-secret"

        profile = resolve_kis_credential_profile("live")

        self.assertEqual(profile.env, "live")
        self.assertEqual(
            profile.base_url,
            "https://openapi.koreainvestment.com:9443",
        )

    def test_live_profile_missing_keys_raises_for_keys_not_base_url(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_kis_credential_profile("live", allow_generic_fallback=False)

        message = str(ctx.exception)
        self.assertIn("credential profile is incomplete", message)
        self.assertIn("KIS_APP_LIVE_KEY", message)
        self.assertIn("KIS_APP_LIVE_SECRET", message)
        self.assertNotIn("KIS_BASE_LIVE_URL", message)

    def test_mock_profile_does_not_default_base_url(self) -> None:
        os.environ["KIS_APP_MOCK_KEY"] = "mock-key"
        os.environ["KIS_APP_MOCK_SECRET"] = "mock-secret"

        with self.assertRaises(ValueError) as ctx:
            resolve_kis_credential_profile("mock", allow_generic_fallback=False)

        self.assertIn("KIS_BASE_MOCK_URL", str(ctx.exception))

    def test_default_live_base_url_passes_live_validation(self) -> None:
        from app.auth.settings import (
            LIVE_BASE_URL_DEFAULT,
            classify_kis_base_url_env,
            validate_kis_base_url,
        )

        self.assertEqual(
            validate_kis_base_url(LIVE_BASE_URL_DEFAULT, expected_env="live"),
            "https://openapi.koreainvestment.com:9443",
        )
        self.assertEqual(classify_kis_base_url_env(LIVE_BASE_URL_DEFAULT), "live")


if __name__ == "__main__":
    unittest.main()
