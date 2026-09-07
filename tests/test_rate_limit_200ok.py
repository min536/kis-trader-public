from __future__ import annotations

import json
import unittest
from unittest import mock
from urllib import error

from app.auth import token as token_module


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_) -> bool:
        return False


class RateLimit200OkTests(unittest.TestCase):
    """note_rate_limit() must fire even when HTTP status is 200 but the body
    contains an EGW00201 / 초당거래건수 rate-limit payload."""

    def _make_rate_limit_body(self) -> dict:
        return {
            "rt_cd": "1",
            "msg_cd": "EGW00201",
            "msg1": "초당 거래건수를 초과하였습니다.",
            "output": None,
        }

    def test_note_rate_limit_called_on_200_ok_rate_limit_body(self) -> None:
        noted_sources: list[str | None] = []

        def fake_urlopen(req, timeout=0.0) -> _FakeResponse:
            return _FakeResponse(self._make_rate_limit_body(), status=200)

        with (
            mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
            mock.patch(
                "app.auth.token.note_rate_limit",
                side_effect=lambda **kw: noted_sources.append(kw.get("source")),
            ),
            mock.patch("app.auth.token.throttle", return_value={"category": "quote"}),
        ):
            result = token_module.request_json(
                "GET",
                "https://openapi.koreainvestment.com:9443/uapi/quotations/v1/inquire-price",
                headers={},
                error_label="현재가 조회",
            )

        self.assertEqual(noted_sources, ["quote"])
        # The response is still returned normally (callers handle rt_cd != "0")
        self.assertEqual(result.get("msg_cd"), "EGW00201")

    def test_note_rate_limit_called_on_400_rate_limit_body(self) -> None:
        noted_sources: list[str | None] = []

        def fake_urlopen(req, timeout=0.0):
            raise error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                {},  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )

        # HTTPError.read() needs to return bytes
        http_err = error.HTTPError(
            "https://example.com", 429, "Too Many",
            {},  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )
        http_err.read = lambda: json.dumps(self._make_rate_limit_body()).encode()  # type: ignore[method-assign]

        def fake_urlopen2(req, timeout=0.0):
            raise http_err

        with (
            mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen2),
            mock.patch(
                "app.auth.token.note_rate_limit",
                side_effect=lambda **kw: noted_sources.append(kw.get("source")),
            ),
            mock.patch("app.auth.token.throttle", return_value={"category": "quote"}),
        ):
            with self.assertRaises(token_module.ApiHttpError):
                token_module.request_json(
                    "GET",
                    "https://openapi.koreainvestment.com:9443/uapi/quotations/v1/inquire-price",
                    headers={},
                    error_label="현재가 조회",
                )

        self.assertEqual(noted_sources, ["quote"])

    def test_note_rate_limit_not_called_for_normal_200(self) -> None:
        noted: list[bool] = []

        def fake_urlopen(req, timeout=0.0) -> _FakeResponse:
            return _FakeResponse({"rt_cd": "0", "output": {"stck_prpr": "75000"}})

        with (
            mock.patch("app.auth.token.request.urlopen", side_effect=fake_urlopen),
            mock.patch("app.auth.token.note_rate_limit", side_effect=lambda **kw: noted.append(True)),
            mock.patch("app.auth.token.throttle", return_value={"category": "quote"}),
        ):
            token_module.request_json(
                "GET",
                "https://openapi.koreainvestment.com:9443/uapi/quotations/v1/inquire-price",
                headers={},
                error_label="현재가 조회",
            )

        self.assertFalse(noted, "note_rate_limit() should NOT be called for a normal 200 response")


if __name__ == "__main__":
    unittest.main()
