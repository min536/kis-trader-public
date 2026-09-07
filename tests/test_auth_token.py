from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from app.auth import token as token_module
from app.auth.token import (
    _is_retryable_url_error_request,
    issue_access_token_for,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class IssueAccessTokenForTests(unittest.TestCase):
    def test_issue_access_token_for_uses_env_specific_cache_without_account_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".token_cache_live.json"
            captured: dict[str, object] = {}

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                captured["url"] = req.full_url
                captured["payload"] = json.loads((req.data or b"{}").decode("utf-8"))
                captured["timeout"] = timeout
                return _FakeResponse({"access_token": "live-token"})

            with (
                mock.patch.dict(
                    os.environ,
                    {"KIS_TRADER_DISABLE_CREDENTIAL_FILES": ""},
                    clear=False,
                ),
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
                mock.patch(
                    "app.auth.token.get_settings",
                    side_effect=AssertionError("get_settings should not be called"),
                ),
            ):
                token = issue_access_token_for(
                    base_url="https://openapi.koreainvestment.com:9443",
                    app_key="live-app-key",
                    app_secret="live-app-secret",
                    env="live",
                    force_refresh=True,
                )

            self.assertEqual(token, "live-token")
            self.assertEqual(
                captured["url"],
                "https://openapi.koreainvestment.com:9443/oauth2/tokenP",
            )
            self.assertEqual(
                captured["payload"],
                {
                    "grant_type": "client_credentials",
                    "appkey": "live-app-key",
                    "appsecret": "live-app-secret",
                },
            )

            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["access_token"], "live-token")
            self.assertIn("issued_at", saved)


class RetryableUrlErrorRequestTests(unittest.TestCase):
    def test_get_and_head_are_always_retryable(self) -> None:
        self.assertTrue(
            _is_retryable_url_error_request(
                method="GET",
                url="https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash",
            )
        )
        self.assertTrue(
            _is_retryable_url_error_request(method="HEAD", url="https://example.com/")
        )

    def test_oauth2_post_is_retryable(self) -> None:
        self.assertTrue(
            _is_retryable_url_error_request(
                method="POST",
                url="https://openapi.koreainvestment.com:9443/oauth2/tokenP",
            )
        )

    def test_hashkey_post_is_retryable(self) -> None:
        self.assertTrue(
            _is_retryable_url_error_request(
                method="POST",
                url="https://openapi.koreainvestment.com:9443/uapi/hashkey",
            )
        )

    def test_trading_post_is_not_retryable(self) -> None:
        self.assertFalse(
            _is_retryable_url_error_request(
                method="POST",
                url="https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash",
            )
        )


class RequestJsonRetryThrottleTests(unittest.TestCase):
    def test_transient_retry_passes_through_throttle_before_next_attempt(self) -> None:
        call_count = {"n": 0}
        timeouts: list[float] = []

        def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
            call_count["n"] += 1
            timeouts.append(timeout)
            if call_count["n"] == 1:
                raise error.URLError(
                    "<urlopen error [Errno 8] nodename nor servname provided, or not known>"
                )
            return _FakeResponse({"rt_cd": "0"})

        with (
            mock.patch.object(token_module.request, "urlopen", side_effect=fake_urlopen),
            mock.patch.object(token_module.time, "sleep", return_value=None),
            mock.patch.object(token_module, "throttle") as mocked_throttle,
        ):
            result = token_module.request_json(
                "GET",
                "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price",
                headers={},
                error_label="현재가 조회",
            )

        self.assertEqual(result.get("rt_cd"), "0")
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(timeouts, [token_module.REQUEST_TIMEOUT_SECONDS] * 2)
        self.assertGreater(token_module.REQUEST_TIMEOUT_SECONDS, 0)
        self.assertEqual(mocked_throttle.call_count, 2)
        mocked_throttle.assert_has_calls(
            [
                mock.call(category="quote"),
                mock.call(category="quote"),
            ]
        )

    def test_timeout_error_retries_like_transient_url_error(self) -> None:
        call_count = {"n": 0}

        def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise TimeoutError("The read operation timed out")
            return _FakeResponse({"rt_cd": "0"})

        with (
            mock.patch.object(token_module.request, "urlopen", side_effect=fake_urlopen),
            mock.patch.object(token_module.time, "sleep", return_value=None),
            mock.patch.object(token_module, "throttle") as mocked_throttle,
        ):
            result = token_module.request_json(
                "GET",
                "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price",
                headers={},
                error_label="현재가 조회",
            )

        self.assertEqual(result.get("rt_cd"), "0")
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(mocked_throttle.call_count, 2)


class IssueAccessTokenForRetryTests(unittest.TestCase):
    def test_retries_once_on_transient_dns_error_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".token_cache_live.json"
            call_count = {"n": 0}

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise error.URLError(
                        "<urlopen error [Errno 8] nodename nor servname provided, or not known>"
                    )
                return _FakeResponse({"access_token": "retried-token"})

            with (
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
                mock.patch("app.auth.token.time.sleep", return_value=None),
            ):
                token = issue_access_token_for(
                    base_url="https://openapi.koreainvestment.com:9443",
                    app_key="k",
                    app_secret="s",
                    env="live",
                    force_refresh=True,
                )

            self.assertEqual(token, "retried-token")
            self.assertEqual(call_count["n"], 2)

    def test_issue_access_token_for_retries_timeout_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".token_cache_live.json"
            call_count = {"n": 0}

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise TimeoutError("The read operation timed out")
                return _FakeResponse({"access_token": "timeout-retried-token"})

            with (
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
                mock.patch("app.auth.token.time.sleep", return_value=None),
            ):
                token = issue_access_token_for(
                    base_url="https://openapi.koreainvestment.com:9443",
                    app_key="k",
                    app_secret="s",
                    env="live",
                    force_refresh=True,
                )

            self.assertEqual(token, "timeout-retried-token")
            self.assertEqual(call_count["n"], 2)

    def test_retries_until_budget_exhausted_before_raising_transient_dns_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / ".token_cache_live.json"
            sleep_calls: list[float] = []

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                raise error.URLError(
                    "<urlopen error [Errno 8] nodename nor servname provided, or not known>"
                )

            with (
                mock.patch("app.auth.token.get_token_cache_path", return_value=cache_path),
                mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
                mock.patch("app.auth.token.time.sleep", side_effect=sleep_calls.append),
            ):
                attempts = len(token_module.TRANSIENT_REQUEST_RETRY_DELAYS_SECONDS)
                with self.assertRaisesRegex(
                    RuntimeError, f"일시 재시도 {attempts}회 후"
                ):
                    issue_access_token_for(
                        base_url="https://openapi.koreainvestment.com:9443",
                        app_key="k",
                        app_secret="s",
                        env="live",
                        force_refresh=True,
                    )

            self.assertEqual(
                sleep_calls,
                list(token_module.TRANSIENT_REQUEST_RETRY_DELAYS_SECONDS),
            )


if __name__ == "__main__":
    unittest.main()
