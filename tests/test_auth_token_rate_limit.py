from __future__ import annotations

import unittest
from unittest import mock
from urllib import error

from app.auth import token as token_module
from app.auth.token import ApiHttpError


class AuthTokenRateLimitTests(unittest.TestCase):
    def test_request_json_notes_rate_limit_http_error(self) -> None:
        response = mock.Mock()
        response.read.return_value = (
            b'{"msg_cd":"EGW00201","msg1":"\\ucd08\\ub2f9 \\uac70\\ub798\\uac74\\uc218\\ub97c \\ucd08\\uacfc"}'
        )
        http_error = error.HTTPError(
            url="https://example.test/uapi/domestic-stock/v1/quotations/inquire-price",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=response,
        )

        with (
            mock.patch.object(token_module, "throttle"),
            mock.patch.object(token_module.request, "urlopen", side_effect=http_error),
            mock.patch.object(token_module, "note_rate_limit") as mocked_note_rate_limit,
        ):
            with self.assertRaises(ApiHttpError):
                token_module.request_json(
                    "GET",
                    "https://example.test/uapi/domestic-stock/v1/quotations/inquire-price",
                    headers={},
                    error_label="현재가 조회",
                )

        mocked_note_rate_limit.assert_called_once_with(source="quote")

    def test_rate_limit_response_detects_common_kis_markers(self) -> None:
        self.assertTrue(token_module.is_rate_limit_response({"msg_cd": "EGW00201"}))
        self.assertTrue(token_module.is_rate_limit_response({"msg1": "초당 거래건수 초과"}))
        self.assertFalse(token_module.is_rate_limit_response({"msg_cd": "OK"}))


if __name__ == "__main__":
    unittest.main()
