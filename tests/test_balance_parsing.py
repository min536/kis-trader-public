import unittest

import app.overseas_stock.balance as origin
from app.auth.token import ApiHttpError
from app.overseas_stock import balance_parsing
from app.overseas_stock.models import OverseasBalanceAttemptResult, OverseasHolding


_MOVED_SYMBOLS = (
    "_parse_number",
    "_extract_api_error",
    "_infer_supported",
    "_is_rate_limited_text",
    "_holding_symbol",
    "_safe_row_preview",
    "_response_status",
    "_normalize_holdings",
    "_valid_account_from_status",
    "_account_validation_details",
    "_interpret_balance_result",
    "_attempt_priority",
    "_best_attempt",
)


class BalanceParsingFacadeIdentityTests(unittest.TestCase):
    """Pin: every moved symbol is the same object via the facade re-export."""

    def test_pin_all_moved_symbols_are_same_object(self) -> None:
        for name in _MOVED_SYMBOLS:
            with self.subTest(name=name):
                self.assertIs(getattr(origin, name), getattr(balance_parsing, name))


class BalanceParsingScalarBehaviorTests(unittest.TestCase):
    """Literal characterization of the simple pure helpers in one parametrized test."""

    def test_scalar_helpers_match_literal_expectations(self) -> None:
        parse_cases = [
            (None, None),
            ("   ", None),
            ("1,234.5", 1234.5),
            ("42", 42.0),
            ("abc", None),
            (7, 7.0),
        ]
        for value, expected in parse_cases:
            with self.subTest(helper="_parse_number", value=value):
                self.assertEqual(balance_parsing._parse_number(value), expected)

        symbol_cases = [
            ({"ovrs_pdno": "NVDA", "pdno": "AAPL"}, "NVDA"),
            ({"symbol": "  TSLA  "}, "TSLA"),
            ({}, ""),
        ]
        for row, expected in symbol_cases:
            with self.subTest(helper="_holding_symbol", row=row):
                self.assertEqual(balance_parsing._holding_symbol(row), expected)

        status_cases = [
            (
                {"rt_cd": "0", "msg_cd": "MCA", "msg1": "ok", "extra": "ignored"},
                {"rt_cd": "0", "msg_cd": "MCA", "msg1": "ok"},
            ),
            ({}, {"rt_cd": None, "msg_cd": None, "msg1": None}),
        ]
        for payload, expected in status_cases:
            with self.subTest(helper="_response_status", payload=payload):
                self.assertEqual(balance_parsing._response_status(payload), expected)

        rate_cases = [
            ("error EGW00201 occurred", True),
            ("초당 거래건수 초과", True),
            ("Rate Limit exceeded", True),
            ("ok", False),
        ]
        for text, expected in rate_cases:
            with self.subTest(helper="_is_rate_limited_text", text=text):
                self.assertEqual(balance_parsing._is_rate_limited_text(text), expected)

        preview_row = {
            "ovrs_pdno": "NVDA",
            "ovrs_cblc_qty": "10",
            "ovrs_now_pric": "",
            "tr_crcy_cd": None,
            "unrelated": "drop-me",
            "ovrs_item_name": [],
        }
        self.assertEqual(
            balance_parsing._safe_row_preview(preview_row),
            {"ovrs_pdno": "NVDA", "ovrs_cblc_qty": "10"},
        )
        self.assertEqual(balance_parsing._safe_row_preview({}), {})


class BalanceParsingBranchBehaviorTests(unittest.TestCase):
    """Literal characterization of the branch-heavy helpers in one parametrized test."""

    def _interpret(self, **overrides):
        kwargs = dict(
            symbol="NVDA",
            request_exchange_code="NASD",
            response_status={"rt_cd": "0", "msg_cd": "", "msg1": ""},
            raw_row_count=0,
            normalized_row_count=0,
            filtered_out_row_count=0,
            matched_holding=None,
            seen_symbols=[],
        )
        kwargs.update(overrides)
        return balance_parsing._interpret_balance_result(**kwargs)

    def test_branch_helpers_match_literal_expectations(self) -> None:
        api_error_cases = [
            (
                ApiHttpError("boom", status_code=500, data={"msg_cd": "MCD", "msg1": "broker said no"}),
                ("MCD", "broker said no"),
            ),
            (ApiHttpError("plain failure", status_code=500, data=None), (None, "plain failure")),
            (ValueError("kaboom"), (None, "kaboom")),
            (ValueError(""), (None, "ValueError")),
        ]
        for exc, expected in api_error_cases:
            with self.subTest(helper="_extract_api_error", exc=exc):
                self.assertEqual(balance_parsing._extract_api_error(exc), expected)

        infer_cases = [
            (("X", "not supported here"), (False, True)),
            ((None, "미지원 항목"), (False, True)),
            ((None, "some other error"), (None, False)),
            ((None, ""), (None, False)),
        ]
        for args, expected in infer_cases:
            with self.subTest(helper="_infer_supported", args=args):
                self.assertEqual(balance_parsing._infer_supported(*args), expected)

        valid_account_cases = [
            ({"rt_cd": "0"}, True),
            ({"rt_cd": "1", "msg_cd": "OPSQ2000", "msg1": "bad"}, False),
            ({"rt_cd": "1", "msg_cd": "ZZZ", "msg1": "mystery"}, None),
        ]
        for status, expected in valid_account_cases:
            with self.subTest(helper="_valid_account_from_status", status=status):
                self.assertIs(balance_parsing._valid_account_from_status(status), expected)

        validation_cases = [
            (
                {"msg_cd": "OPSQ2000", "msg1": "INVALID_CHECK_ACNO oops"},
                (True, "OPSQ2000", "INVALID_CHECK_ACNO oops"),
            ),
            ({"msg_cd": "MCA", "msg1": "fine"}, (False, "MCA", "fine")),
            ({}, (False, None, None)),
        ]
        for status, expected in validation_cases:
            with self.subTest(helper="_account_validation_details", status=status):
                self.assertEqual(balance_parsing._account_validation_details(status), expected)

        interpret_cases = [
            (
                dict(response_status={"rt_cd": "1", "msg_cd": "OPSQ2000", "msg1": "INVALID_CHECK_ACNO"}),
                "account validation failed (INVALID_CHECK_ACNO)",
            ),
            (
                dict(response_status={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate"}),
                "rate limited before holdings verification",
            ),
            (
                dict(response_status={"rt_cd": "1", "msg_cd": "", "msg1": "미지원"}),
                "endpoint unsupported",
            ),
            (
                dict(response_status={"rt_cd": "7", "msg_cd": "ZZ", "msg1": "weird"}),
                "endpoint responded with error rt_cd=7; holdings result not trustworthy",
            ),
            (dict(matched_holding=OverseasHolding(symbol="NVDA")), "holdings present"),
            (dict(raw_row_count=0), "endpoint succeeded and returned no holdings rows"),
            (
                dict(raw_row_count=3, normalized_row_count=0, filtered_out_row_count=3),
                "raw holdings rows existed but none normalized",
            ),
            (
                dict(raw_row_count=2, normalized_row_count=2, seen_symbols=["AAPL", "TSLA"]),
                "valid account with rows but symbol mismatch",
            ),
        ]
        for overrides, expected in interpret_cases:
            with self.subTest(helper="_interpret_balance_result", overrides=overrides):
                self.assertEqual(self._interpret(**overrides), expected)


class BalanceParsingNormalizeAndPriorityTests(unittest.TestCase):
    def _attempt(self, **overrides) -> OverseasBalanceAttemptResult:
        kwargs = dict(
            ok=False,
            account_valid=None,
            supported=None,
            rate_limited=False,
            response_status={"rt_cd": "0"},
            raw_row_count=0,
            normalized_row_count=0,
            matched_symbol=False,
        )
        kwargs.update(overrides)
        return OverseasBalanceAttemptResult(**kwargs)

    def test_normalize_holdings_and_priority_full_equivalence(self) -> None:
        empty = balance_parsing._normalize_holdings(
            {"output1": "not-a-list"},
            market="US",
            exchange_code="NASD",
            endpoint="/ep",
            tr_id="TR",
        )
        self.assertEqual(empty, ([], {}, [], 0))

        payload = {
            "output1": [
                {
                    "ovrs_pdno": "NVDA",
                    "ovrs_excg_cd": "NAS",
                    "ovrs_item_name": "NVIDIA",
                    "rsym": "RNVDA",
                    "ovrs_cblc_qty": "5",
                    "pchs_avg_pric": "100.50",
                    "ovrs_now_pric": "120.00",
                    "ovrs_stck_evlu_amt": "600.00",
                    "frcr_evlu_pfls_amt": "97.50",
                    "tr_crcy_cd": "USD",
                },
                "junk-non-dict",
                {"ovrs_cblc_qty": "9"},
                {"ovrs_pdno": "AAPL"},
            ]
        }
        holdings, filter_counts, filtered_rows, raw_count = balance_parsing._normalize_holdings(
            payload,
            market="US",
            exchange_code="NASD",
            endpoint="/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id="TTTS3012R",
        )
        self.assertEqual(raw_count, 4)
        self.assertEqual(
            holdings,
            [
                OverseasHolding(
                    symbol="NVDA",
                    market="US",
                    exchange_code="NAS",
                    quantity=5.0,
                    avg_price=100.50,
                    current_price=120.00,
                    market_value=600.00,
                    unrealized_pnl=97.50,
                    currency="USD",
                    source_endpoint="/uapi/overseas-stock/v1/trading/inquire-balance",
                    source_tr_id="TTTS3012R",
                    raw_identity_fields={
                        "symbol": "NVDA",
                        "raw_exchange_code": "NAS",
                        "name": "NVIDIA",
                        "raw_symbol_alt": "RNVDA",
                    },
                )
            ],
        )
        self.assertEqual(
            filter_counts,
            {"non_dict_row": 1, "missing_symbol": 1, "missing_quantity": 1},
        )
        self.assertEqual(
            filtered_rows,
            [
                {"reason": "non_dict_row", "row": "junk-non-dict"},
                {"reason": "missing_symbol", "row": {"ovrs_cblc_qty": "9"}},
                {"reason": "missing_quantity", "row": {"ovrs_pdno": "AAPL"}},
            ],
        )

        matched = self._attempt(account_valid=True, matched_symbol=True)
        raw_rows = self._attempt(account_valid=True, raw_row_count=4)
        self.assertGreater(
            balance_parsing._attempt_priority(matched),
            balance_parsing._attempt_priority(raw_rows),
        )
        self.assertIsNone(balance_parsing._best_attempt([]))
        low = self._attempt(account_valid=None, rate_limited=True)
        high = self._attempt(account_valid=True, matched_symbol=True)
        self.assertIs(balance_parsing._best_attempt([low, high]), high)


if __name__ == "__main__":
    unittest.main()
