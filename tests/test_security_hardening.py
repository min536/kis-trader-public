from __future__ import annotations

from datetime import date
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.auth.account_scope import (
    get_account_scope_context,
    get_account_signature,
    get_order_log_read_paths,
)
from app.auth import token as token_module
from app.auth.settings import PROJECT_ROOT, get_settings, get_token_cache_path
from app.core.order_log import (
    OrderLogReadError,
    count_today_buy_order_submissions,
)
from app.execution.order_guard import (
    evaluate_buy_order_guard,
    evaluate_rebalance_sell_guard,
    evaluate_sell_order_guard,
)
from app.execution.schema import ExecutionSnapshot
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioSnapshot
from app.risk.guards import RiskGuardInput, evaluate_buy_risk_guards
from app.risk.guards import evaluate_sell_risk_guards


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _base_env(base_url: str) -> dict[str, str]:
    return {
        "KIS_APP_KEY": "app-key",
        "KIS_APP_SECRET": "app-secret",
        "KIS_BASE_URL": base_url,
        "KIS_CANO": "12345678",
        "KIS_ACNT_PRDT_CD": "01",
    }


def _risk_input() -> RiskGuardInput:
    return RiskGuardInput(
        market_snapshot=MarketSnapshot(
            symbol="005930",
            current_price=10000,
            open_price=9900,
            low_price=9800,
            prev_day_change_pct=0.0,
        ),
        portfolio_snapshot=PortfolioSnapshot(
            positions=(),
            cash_total=1_000_000,
            cash_orderable=1_000_000,
            cash_next_day=1_000_000,
            total_evaluation_amount=1_000_000,
        ),
        execution_snapshot=ExecutionSnapshot(
            symbol="005930",
            orderable_cash=1_000_000,
            orderable_qty=10,
            current_price=10000,
            expected_notional_krw=10000,
        ),
        side="BUY",
        enabled=True,
        daily_max_order_submissions=10,
        daily_max_notional_krw=1_000_000,
    )


def _sell_risk_input() -> RiskGuardInput:
    guard_input = _risk_input()
    return RiskGuardInput(
        market_snapshot=guard_input.market_snapshot,
        portfolio_snapshot=guard_input.portfolio_snapshot,
        execution_snapshot=guard_input.execution_snapshot,
        side="SELL",
        enabled=True,
        daily_max_order_submissions=10,
        daily_max_notional_krw=1_000_000,
    )


class BrokerBaseUrlHardeningTests(unittest.TestCase):
    def test_get_settings_rejects_untrusted_broker_host(self) -> None:
        with mock.patch.dict(os.environ, _base_env("https://evil.example:9443"), clear=True):
            with self.assertRaisesRegex(ValueError, "host is not allowed"):
                get_settings()

    def test_get_settings_rejects_plain_http_broker_url(self) -> None:
        with mock.patch.dict(
            os.environ,
            _base_env("http://openapivts.koreainvestment.com:9443"),
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "https URL"):
                get_settings()

    def test_get_settings_rejects_localhost_and_loopback_broker_urls(self) -> None:
        for base_url in (
            "https://localhost:9443",
            "https://127.0.0.1:9443",
        ):
            with self.subTest(base_url=base_url):
                with mock.patch.dict(os.environ, _base_env(base_url), clear=True):
                    with self.assertRaisesRegex(ValueError, "host is not allowed"):
                        get_settings()

    def test_get_settings_rejects_userinfo_empty_and_malformed_broker_urls(self) -> None:
        for base_url, expected_message in (
            ("https://user:pass@openapivts.koreainvestment.com:9443", "must not include credentials"),
            ("", "환경변수가 비어 있습니다"),
            ("not-a-url", "https URL"),
        ):
            with self.subTest(base_url=base_url):
                with mock.patch.dict(os.environ, _base_env(base_url), clear=True):
                    with self.assertRaisesRegex(ValueError, expected_message):
                        get_settings()

    def test_get_settings_rejects_kis_env_host_mismatch(self) -> None:
        env = {
            **_base_env("https://openapivts.koreainvestment.com:9443"),
            "KIS_ENV": "live",
            "KIS_APP_LIVE_KEY": "live-key",
            "KIS_APP_LIVE_SECRET": "live-secret",
            "KIS_CANO_LIVE": "12345678",
            "KIS_ACNT_PRDT_CD_LIVE": "01",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "does not match"):
                get_settings()

    def test_direct_token_helper_validates_base_url_before_network(self) -> None:
        helper = getattr(token_module, "issue_" + "access_" + "token_for")
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "credential-cache.json"
            with (
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen") as urlopen,
            ):
                with self.assertRaisesRegex(ValueError, "host is not allowed"):
                    helper(
                        base_url="https://evil.example:9443",
                        app_key="k",
                        app_secret="s",
                        env="live",
                        force_refresh=True,
                    )
            urlopen.assert_not_called()

    def test_direct_token_helper_accepts_mock_and_live_kis_urls(self) -> None:
        helper = getattr(token_module, "issue_" + "access_" + "token_for")
        cases = (
            ("mock", "https://openapivts.koreainvestment.com:9443"),
            ("live", "https://openapi.koreainvestment.com:9443"),
        )
        for env, base_url in cases:
            with self.subTest(env=env):
                with tempfile.TemporaryDirectory() as tmpdir:
                    cache_path = Path(tmpdir) / "credential-cache.json"
                    captured: dict[str, object] = {}

                    def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                        captured["url"] = req.full_url
                        return _FakeResponse({"access_" + "token": f"{env}-cached"})

                    with (
                        mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                        mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
                    ):
                        token = helper(
                            base_url=base_url,
                            app_key="k",
                            app_secret="s",
                            env=env,
                            force_refresh=True,
                        )

                self.assertEqual(token, f"{env}-cached")
                self.assertEqual(captured["url"], f"{base_url}/oauth2/tokenP")

    def test_direct_token_helper_validates_base_url_before_cache_hit(self) -> None:
        helper = getattr(token_module, "issue_" + "access_" + "token_for")
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "credential-cache.json"
            cache_path.write_text(
                json.dumps({"access_" + "token": "cached", "issued_at": 9_999_999_999}),
                encoding="utf-8",
            )
            with (
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen") as urlopen,
            ):
                with self.assertRaisesRegex(ValueError, "host is not allowed"):
                    helper(
                        base_url="https://evil.example:9443",
                        app_key="k",
                        app_secret="s",
                        env="live",
                        force_refresh=False,
                    )
            urlopen.assert_not_called()


class CredentialCacheHardeningTests(unittest.TestCase):
    def test_default_credential_cache_path_is_outside_project_root(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cache_path = get_token_cache_path("mock")

        self.assertFalse(cache_path.is_relative_to(PROJECT_ROOT))
        self.assertEqual(cache_path.name, "kis_auth_mock.json")

    def test_credential_cache_dir_override_is_env_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {"KIS_CREDENTIAL_CACHE_DIR": tmpdir},
                clear=True,
            ):
                live_path = get_token_cache_path("live")

        self.assertEqual(live_path.parent, Path(tmpdir))
        self.assertEqual(live_path.name, "kis_auth_live.json")

    def test_token_cache_save_creates_owner_only_file_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache-dir" / "credential-cache.json"
            with (
                mock.patch.dict(
                    os.environ,
                    {"KIS_TRADER_DISABLE_CREDENTIAL_FILES": ""},
                    clear=False,
                ),
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
            ):
                token_module._save_token_cache("secret-value", env="mock")

            cache_mode = stat.S_IMODE(cache_path.stat().st_mode)
            parent_mode = stat.S_IMODE(cache_path.parent.stat().st_mode)
            saved = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(cache_mode, 0o600)
        self.assertEqual(parent_mode, 0o700)
        self.assertEqual(saved["access_" + "token"], "secret-value")

    def test_token_cache_load_hardens_existing_file_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "credential-cache.json"
            cache_path.write_text(
                json.dumps({"access_" + "token": "cached", "issued_at": 1}),
                encoding="utf-8",
            )
            os.chmod(cache_path, 0o644)
            with (
                mock.patch.dict(
                    os.environ,
                    {"KIS_TRADER_DISABLE_CREDENTIAL_FILES": ""},
                    clear=False,
                ),
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
            ):
                loaded = token_module._load_token_cache("mock")

            cache_mode = stat.S_IMODE(cache_path.stat().st_mode)

        self.assertEqual(cache_mode, 0o600)
        self.assertEqual(loaded["access_" + "token"], "cached")


class AccountIdentifierMaskingTests(unittest.TestCase):
    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443",
            cano="12345678",
            acnt_prdt_cd="01",
            app_key="app-key",
            app_secret="app-secret",
        )

    def test_account_signature_is_non_raw_stable_alias(self) -> None:
        signature = get_account_signature(self._settings())

        self.assertRegex(signature, r"^mock_acct_[0-9a-f]{16}$")
        self.assertNotIn("12345678", signature)
        self.assertNotIn("_01", signature)
        self.assertEqual(signature, get_account_signature(self._settings()))

    def test_account_scope_context_excludes_raw_account_fields(self) -> None:
        context = get_account_scope_context(self._settings())

        self.assertIn("masked_account_display", context)
        self.assertNotIn("cano", context)
        self.assertNotIn("acnt_prdt_cd", context)
        self.assertNotIn("12345678", json.dumps(context, ensure_ascii=False))

    def test_order_log_read_paths_include_legacy_account_path(self) -> None:
        paths = get_order_log_read_paths(self._settings())

        self.assertEqual(len(paths), 2)
        self.assertIn("mock_acct_", paths[0].name)
        self.assertIn("12345678", paths[1].name)


class OrderLogFailClosedTests(unittest.TestCase):
    def test_strict_order_log_reader_raises_on_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            log_path.write_text(
                '{"timestamp":"2026-05-01T09:01:00+09:00","action":"order_submitted"}\n'
                "{not-json}\n",
                encoding="utf-8",
            )
            with mock.patch("app.core.order_log.get_order_log_path", return_value=log_path):
                with self.assertRaises(OrderLogReadError):
                    count_today_buy_order_submissions(
                        today=date(2026, 5, 1),
                        strict=True,
                    )

    def test_strict_order_log_reader_allows_missing_first_run_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            with mock.patch("app.core.order_log.get_order_log_path", return_value=log_path):
                count = count_today_buy_order_submissions(
                    today=date(2026, 5, 1),
                    strict=True,
                )

        self.assertEqual(count, 0)

    def test_strict_order_log_reader_includes_legacy_order_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            current_path = Path(tmpdir) / "orders_current.jsonl"
            legacy_path = Path(tmpdir) / "orders_legacy.jsonl"
            current_path.write_text(
                '{"timestamp":"2026-05-01T09:01:00+09:00","action":"order_submitted"}\n',
                encoding="utf-8",
            )
            legacy_path.write_text(
                '{"timestamp":"2026-05-01T09:02:00+09:00","action":"order_submitted"}\n',
                encoding="utf-8",
            )
            with (
                mock.patch("app.core.order_log.get_order_log_path", return_value=current_path),
                mock.patch(
                    "app.core.order_log.get_order_log_read_paths",
                    return_value=(current_path, legacy_path),
                ),
            ):
                count = count_today_buy_order_submissions(
                    today=date(2026, 5, 1),
                    strict=True,
                )

        self.assertEqual(count, 2)

    def test_strict_order_log_reader_raises_on_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            log_path.write_text("", encoding="utf-8")
            with (
                mock.patch("app.core.order_log.get_order_log_path", return_value=log_path),
                mock.patch("app.core.order_log.iter_lines_bounded", side_effect=OSError("denied")),
            ):
                with self.assertRaisesRegex(OrderLogReadError, "주문 로그를 읽을 수 없어"):
                    count_today_buy_order_submissions(
                        today=date(2026, 5, 1),
                        strict=True,
                    )

    def test_strict_order_log_reader_raises_on_read_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            log_path.write_text(
                '{"timestamp":"2026-05-01T09:01:00+09:00","action":"order_submitted"}\n',
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}),
                mock.patch("app.core.order_log.get_order_log_path", return_value=log_path),
            ):
                with self.assertRaises(OrderLogReadError) as ctx:
                    count_today_buy_order_submissions(
                        today=date(2026, 5, 1),
                        strict=True,
                    )

        self.assertEqual(ctx.exception.reason_code, "order_log_too_large")

    def test_buy_risk_guard_blocks_when_order_log_is_untrusted(self) -> None:
        error = OrderLogReadError("order_log_malformed", "bad order log")
        with mock.patch(
            "app.risk.guards.count_today_buy_order_submissions",
            side_effect=error,
        ):
            result = evaluate_buy_risk_guards(guard_input=_risk_input())

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_buy_order_log_untrusted")
        self.assertIn("order_log_integrity", {guard.guard_name for guard in result.guard_results})

    def test_sell_risk_guard_blocks_when_order_log_amount_is_untrusted(self) -> None:
        error = OrderLogReadError("order_log_read_failed", "bad order log")
        with (
            mock.patch(
                "app.risk.guards.count_today_sell_order_submissions",
                return_value=0,
            ),
            mock.patch(
                "app.risk.guards.sum_today_sell_order_submission_notional_krw",
                side_effect=error,
            ),
        ):
            result = evaluate_sell_risk_guards(guard_input=_sell_risk_input())

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "blocked_sell_order_log_untrusted")
        self.assertEqual(
            result.details["order_log_error_code"],
            "order_log_read_failed",
        )


class RuntimeStateFailClosedTests(unittest.TestCase):
    def test_malformed_runtime_state_loads_as_untrusted(self) -> None:
        from app.runtime_state import load_runtime_state

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime_state.json"
            state_path.write_text("{not-json}", encoding="utf-8")
            with (
                mock.patch("app.runtime_state.get_runtime_state_path", return_value=state_path),
                mock.patch(
                    "app.runtime_state.get_account_scope_context",
                    return_value={
                        "account_signature": "mock_12345678_01",
                        "account_environment": "mock",
                        "masked_account_display": "1234***78-01",
                    },
                ),
                mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
            ):
                state = load_runtime_state()

        self.assertFalse(state["runtime_state_trusted"])
        self.assertEqual(state["runtime_state_safety_status"], "blocked")
        self.assertTrue(state["order_submission_blocked_by_runtime_state"])

    def test_valid_legacy_runtime_state_loads_as_trusted(self) -> None:
        from app.runtime_state import load_runtime_state

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account_signature": "mock_12345678_01",
                        "trading_date": "2026-05-01",
                        "recent_orders": [],
                        "buy_entries_by_symbol_today": {"005930": 1},
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch("app.runtime_state.get_runtime_state_path", return_value=state_path),
                mock.patch(
                    "app.runtime_state.get_account_scope_context",
                    return_value={
                        "account_signature": "mock_12345678_01",
                        "account_environment": "mock",
                        "masked_account_display": "1234***78-01",
                    },
                ),
                mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
            ):
                state = load_runtime_state()

        self.assertTrue(state["runtime_state_trusted"])
        self.assertEqual(state["runtime_state_safety_status"], "ok")
        self.assertEqual(state["buy_entries_by_symbol_today"], {"005930": 1})

    def test_runtime_state_loader_migrates_legacy_account_path(self) -> None:
        from app.runtime_state import load_runtime_state

        with tempfile.TemporaryDirectory() as tmpdir:
            current_path = Path(tmpdir) / "runtime_state_current.json"
            legacy_path = Path(tmpdir) / "runtime_state_legacy.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "account_signature": "mock_12345678_01",
                        "trading_date": "2026-05-01",
                        "buy_entries_by_symbol_today": {"005930": 1},
                    }
                ),
                encoding="utf-8",
            )
            account_scope = {
                "account_signature": "mock_acct_deadbeef12345678",
                "account_environment": "mock",
                "masked_account_display": "1234***78-01",
            }
            with (
                mock.patch("app.runtime_state.get_runtime_state_path", return_value=current_path),
                mock.patch(
                    "app.runtime_state.get_runtime_state_read_paths",
                    return_value=(current_path, legacy_path),
                ),
                mock.patch("app.runtime_state.get_account_scope_context", return_value=account_scope),
                mock.patch(
                    "app.runtime_state.get_legacy_account_signature",
                    return_value="mock_12345678_01",
                ),
                mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
            ):
                state = load_runtime_state()

        self.assertTrue(state["runtime_state_trusted"])
        self.assertEqual(state["account_signature"], account_scope["account_signature"])
        self.assertEqual(state["buy_entries_by_symbol_today"], {"005930": 1})

    def test_untrusted_runtime_state_marker_persists_after_reload(self) -> None:
        from app.runtime_state import load_runtime_state, save_runtime_state

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime_state.json"
            account_scope = {
                "account_signature": "mock_12345678_01",
                "account_environment": "mock",
                "masked_account_display": "1234***78-01",
            }
            state = {
                **account_scope,
                "trading_date": "2026-05-01",
                "runtime_state_trusted": False,
                "runtime_state_safety_status": "blocked",
                "runtime_state_safety_reason": "manual_test_block",
                "order_submission_blocked_by_runtime_state": True,
            }
            with (
                mock.patch("app.runtime_state.get_runtime_state_path", return_value=state_path),
                mock.patch("app.runtime_state.get_account_scope_context", return_value=account_scope),
                mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
            ):
                self.assertTrue(save_runtime_state(state))
                loaded = load_runtime_state()

        self.assertFalse(loaded["runtime_state_trusted"])
        self.assertEqual(loaded["runtime_state_safety_reason"], "manual_test_block")

    def test_account_mismatch_and_current_day_date_mismatch_block_runtime_state(self) -> None:
        from app.runtime_state import load_runtime_state

        cases = (
            ({"account_signature": "mock_other_01", "trading_date": "2026-05-01"}, "account_signature_mismatch"),
            ({"account_signature": "mock_12345678_01", "trading_date": "2026-04-30"}, "trading_date_mismatch_modified_today"),
        )
        for payload, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                with tempfile.TemporaryDirectory() as tmpdir:
                    state_path = Path(tmpdir) / "runtime_state.json"
                    state_path.write_text(json.dumps(payload), encoding="utf-8")
                    with (
                        mock.patch("app.runtime_state.get_runtime_state_path", return_value=state_path),
                        mock.patch(
                            "app.runtime_state.get_account_scope_context",
                            return_value={
                                "account_signature": "mock_12345678_01",
                                "account_environment": "mock",
                                "masked_account_display": "1234***78-01",
                            },
                        ),
                        mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
                        mock.patch("app.runtime_state._path_modified_today", return_value=True),
                    ):
                        state = load_runtime_state()

                self.assertFalse(state["runtime_state_trusted"])
                self.assertEqual(state["runtime_state_safety_reason"], expected_reason)

    def test_runtime_state_read_limit_loads_as_untrusted(self) -> None:
        from app.runtime_state import load_runtime_state

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account_signature": "mock_12345678_01",
                        "trading_date": "2026-05-01",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}),
                mock.patch("app.runtime_state.get_runtime_state_path", return_value=state_path),
                mock.patch(
                    "app.runtime_state.get_account_scope_context",
                    return_value={
                        "account_signature": "mock_12345678_01",
                        "account_environment": "mock",
                        "masked_account_display": "1234***78-01",
                    },
                ),
                mock.patch("app.runtime_state._today_text", return_value="2026-05-01"),
            ):
                state = load_runtime_state()

        self.assertFalse(state["runtime_state_trusted"])
        self.assertEqual(
            state["runtime_state_safety_reason"],
            "malformed_or_unreadable_runtime_state",
        )

    def test_order_guards_block_untrusted_runtime_state(self) -> None:
        state = {
            "runtime_state_trusted": False,
            "runtime_state_safety_status": "blocked",
            "runtime_state_safety_reason": "malformed_or_unreadable_runtime_state",
            "order_submission_blocked_by_runtime_state": True,
        }

        buy_result = evaluate_buy_order_guard(
            state=state,
            symbol="005930",
            qty=1,
            block_rebuy_symbols_bought_today=False,
            allow_one_buy_per_symbol_per_day=False,
            rebuy_cooldown_minutes=0,
            same_symbol_max_buys_per_day=0,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
        )
        sell_result = evaluate_sell_order_guard(
            state=state,
            symbol="005930",
            holding_qty=1,
            qty=1,
            block_resell_symbols_sold_today=False,
            allow_one_sell_trigger_per_symbol_per_day=False,
            order_cooldown_minutes=0,
            blocked_cooldown_minutes=0,
            trigger="stop_loss",
        )
        rebalance_result = evaluate_rebalance_sell_guard(
            state=state,
            max_submissions_per_day=1,
        )

        self.assertEqual(buy_result.action, "blocked_buy_runtime_state_untrusted")
        self.assertEqual(sell_result.action, "blocked_sell_runtime_state_untrusted")
        self.assertEqual(
            rebalance_result.action,
            "blocked_rebalance_runtime_state_untrusted",
        )


if __name__ == "__main__":
    unittest.main()
