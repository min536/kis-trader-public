import os
import unittest

import app.overseas_stock.balance as origin
from app.overseas_stock import balance_attempts
from app.overseas_stock.models import (
    OverseasBalanceAttemptResult,
    OverseasBalanceMatrixProbeResult,
    OverseasBalanceProbeResult,
    OverseasHolding,
    ProbeAttempt,
)


_MOVED_FUNCTIONS = (
    "_market_candidates",
    "_currency_candidates",
    "_tr_id_candidates",
    "_build_balance_attempts",
    "_build_account_context_candidates",
    "_build_balance_account_attempts",
    "_recommended_matrix_next_step",
    "_recommended_account_matrix_next_step",
    "_attempt_to_probe_result",
)
_MOVED_CONSTANTS = (
    "_ACCOUNT_MATRIX_FIXED_EXCHANGE",
    "_ACCOUNT_MATRIX_FIXED_CURRENCY",
)


class BalanceAttemptsFacadeIdentityTests(unittest.TestCase):
    """Pin: every moved symbol is re-exported through the facade."""

    def test_pin_all_moved_symbols(self) -> None:
        for name in _MOVED_FUNCTIONS:
            with self.subTest(name=name):
                self.assertIs(getattr(origin, name), getattr(balance_attempts, name))
        for name in _MOVED_CONSTANTS:
            with self.subTest(name=name):
                self.assertEqual(getattr(origin, name), getattr(balance_attempts, name))


class AccountMatrixConstantValueTests(unittest.TestCase):
    def test_fixed_exchange_and_currency_literals(self) -> None:
        self.assertEqual(balance_attempts._ACCOUNT_MATRIX_FIXED_EXCHANGE, "NASD")
        self.assertEqual(balance_attempts._ACCOUNT_MATRIX_FIXED_CURRENCY, "USD")


class CandidateHelperTests(unittest.TestCase):
    def test_candidate_helper_full_list_literals(self) -> None:
        cases = (
            ("market US", balance_attempts._market_candidates("US"), ("NASD", "NAS", "NYSE", "AMEX")),
            ("market ' us '", balance_attempts._market_candidates(" us "), ("NASD", "NAS", "NYSE", "AMEX")),
            ("market hk", balance_attempts._market_candidates("hk"), ("HK",)),
            ("currency US", balance_attempts._currency_candidates("US"), ("USD", "")),
            ("currency HK", balance_attempts._currency_candidates("HK"), ("USD", "")),
            ("tr_id mock", balance_attempts._tr_id_candidates(mock=True), ("VTTT3012R", "VTTS3012R")),
            ("tr_id live", balance_attempts._tr_id_candidates(mock=False), ("TTTS3012R", "JTTT3012R")),
        )
        for label, actual, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(actual, expected)


class RecommendedMatrixNextStepTests(unittest.TestCase):
    def test_all_branches_full_string_literals(self) -> None:
        cases = (
            (
                "symbol_match",
                OverseasBalanceMatrixProbeResult(ok=True, supported=True, any_symbol_match=True),
                "NVDA 매칭이 확인됐습니다. 다음 단계로 watch-only reconciliation 토대를 붙이세요.",
            ),
            (
                "valid_context_with_rows",
                OverseasBalanceMatrixProbeResult(
                    ok=True, supported=True, any_valid_account_context=True, any_raw_rows=True
                ),
                "유효한 계좌 컨텍스트와 raw rows는 확인됐지만 NVDA 매칭이 없습니다. symbol/code mapping을 먼저 대조하세요.",
            ),
            (
                "valid_context_without_rows",
                OverseasBalanceMatrixProbeResult(
                    ok=True, supported=True, any_valid_account_context=True, any_raw_rows=False
                ),
                "유효한 계좌 컨텍스트는 있었지만 rows가 0건입니다. broker 화면의 실제 시장/계좌와 probe 파라미터를 대조하세요.",
            ),
            (
                "no_valid_context",
                OverseasBalanceMatrixProbeResult(ok=False, supported=None),
                "아직 유효한 account context가 확인되지 않았습니다. 계좌번호, 상품코드, mock 해외 계좌 지원 여부를 먼저 재확인하세요.",
            ),
        )
        for label, result, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(
                    balance_attempts._recommended_matrix_next_step(result), expected
                )


class RecommendedAccountMatrixNextStepTests(unittest.TestCase):
    def test_all_branches_full_string_literals(self) -> None:
        cases = (
            (
                "valid_context",
                OverseasBalanceMatrixProbeResult(
                    ok=True, supported=True, any_valid_account_context=True
                ),
                "account-valid response가 확인됐습니다. 다음 단계는 holdings normalization과 symbol matching 검증입니다.",
            ),
            (
                "validation_failures",
                OverseasBalanceMatrixProbeResult(
                    ok=False, supported=None,
                    account_validation_failures=[{"code": "EGW00123"}],
                ),
                "quote works but balance account validation fails; do not trust holdings absence. broker UI에서 해외 mock 계좌번호/상품코드 매핑을 먼저 확인하세요.",
            ),
            (
                "unclear",
                OverseasBalanceMatrixProbeResult(ok=False, supported=None),
                "mock overseas balance support still unclear. broker UI에서 해외 mock balance 조회 가능 여부를 먼저 확인하세요.",
            ),
        )
        for label, result, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(
                    balance_attempts._recommended_account_matrix_next_step(result), expected
                )


_BALANCE_ENDPOINT = "/uapi/overseas-stock/v1/trading/inquire-balance"


class AttemptToProbeResultTests(unittest.TestCase):
    def test_full_conversion_skips_non_dict_preview_rows(self) -> None:
        preview = [
            {
                "symbol": " NVDA ",
                "market": "US",
                "exchange_code": "NASD",
                "quantity": 2.0,
                "avg_price": 100.5,
                "current_price": 110.25,
                "market_value": 220.5,
                "unrealized_pnl": 19.5,
                "currency": "USD",
                "raw_identity_fields": {"ovrs_pdno": "NVDA"},
            },
            "not-a-dict",
        ]
        attempt = OverseasBalanceAttemptResult(
            ok=True,
            account_valid=True,
            supported=True,
            rate_limited=False,
            diagnostics=["d1", "d2"],
            endpoint_used=_BALANCE_ENDPOINT,
            tr_id_used="VTTT3012R",
            request_context={"OVRS_EXCG_CD": "NASD"},
            response_status={"rt_cd": "0"},
            raw_row_count=2,
            normalized_row_count=1,
            filtered_out_row_count=1,
            filter_reason_counts={"missing_symbol": 1},
            raw_rows_preview=[{"ovrs_pdno": "NVDA"}],
            normalized_holdings_preview=preview,
            interpretation="ok_with_rows",
        )

        result = balance_attempts._attempt_to_probe_result(
            attempt, supported=True, can_verify_mock_support=True
        )

        self.assertEqual(
            result,
            OverseasBalanceProbeResult(
                ok=True,
                supported=True,
                can_verify_mock_support=True,
                account_valid=True,
                rate_limited=False,
                diagnostics=["d1", "d2"],
                holdings=[
                    OverseasHolding(
                        symbol="NVDA",
                        market="US",
                        exchange_code="NASD",
                        quantity=2.0,
                        avg_price=100.5,
                        current_price=110.25,
                        market_value=220.5,
                        unrealized_pnl=19.5,
                        currency="USD",
                        raw_identity_fields={"ovrs_pdno": "NVDA"},
                    )
                ],
                matched_holding=None,
                attempts=[],
                endpoint_used=_BALANCE_ENDPOINT,
                tr_id_used="VTTT3012R",
                request_context={"OVRS_EXCG_CD": "NASD"},
                response_status={"rt_cd": "0"},
                raw_row_count=2,
                normalized_row_count=1,
                filtered_out_row_count=1,
                filter_reason_counts={"missing_symbol": 1},
                raw_rows_preview=[{"ovrs_pdno": "NVDA"}],
                normalized_holdings_preview=preview,
                interpretation="ok_with_rows",
                raw=None,
            ),
        )


_ACCOUNT_ENV_KEYS = (
    "KIS_ENV",
    "KIS_CANO",
    "KIS_CANO_MOCK",
    "KIS_MOCK_CANO",
    "KIS_ACNT_PRDT_CD",
    "KIS_ACNT_PRDT_CD_MOCK",
    "KIS_MOCK_ACNT_PRDT_CD",
)

_EXPECTED_ACCOUNT_CANDIDATES = [
    {"source": "settings_direct", "cano": "1234567890", "acnt_prdt_cd": "01"},
    {"source": "product_code_no_leading_zero", "cano": "1234567890", "acnt_prdt_cd": "1"},
    {"source": "split_from_cano_suffix", "cano": "12345678", "acnt_prdt_cd": "90"},
]


class _AccountEnvTestCase(unittest.TestCase):
    """Pin cano/acnt env to literals (mock scope) so builder outputs are deterministic."""

    def setUp(self) -> None:
        self._original = {key: os.environ.get(key) for key in _ACCOUNT_ENV_KEYS}
        os.environ["KIS_ENV"] = "mock"
        for key in ("KIS_CANO", "KIS_CANO_MOCK", "KIS_MOCK_CANO"):
            os.environ[key] = "1234567890"
        for key in ("KIS_ACNT_PRDT_CD", "KIS_ACNT_PRDT_CD_MOCK", "KIS_MOCK_ACNT_PRDT_CD"):
            os.environ[key] = "01"

    def tearDown(self) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AccountContextCandidatesTests(_AccountEnvTestCase):
    def test_full_candidate_list_dedup_and_order(self) -> None:
        self.assertEqual(
            balance_attempts._build_account_context_candidates(),
            _EXPECTED_ACCOUNT_CANDIDATES,
        )


class BuildBalanceAttemptsTests(_AccountEnvTestCase):
    def test_non_us_mock_full_attempt_list(self) -> None:
        self.assertEqual(
            balance_attempts._build_balance_attempts(
                "NVDA", "HK", mock=True, matrix=False
            ),
            [
                ProbeAttempt(
                    name="balance:HK:VTTT3012R:USD",
                    endpoint=_BALANCE_ENDPOINT,
                    tr_id="VTTT3012R",
                    params={
                        "CANO": "1234567890",
                        "ACNT_PRDT_CD": "01",
                        "OVRS_EXCG_CD": "HK",
                        "TR_CRCY_CD": "USD",
                        "CTX_AREA_FK200": "",
                        "CTX_AREA_NK200": "",
                    },
                    note="match_symbol=NVDA",
                ),
                ProbeAttempt(
                    name="balance:HK:VTTS3012R:USD",
                    endpoint=_BALANCE_ENDPOINT,
                    tr_id="VTTS3012R",
                    params={
                        "CANO": "1234567890",
                        "ACNT_PRDT_CD": "01",
                        "OVRS_EXCG_CD": "HK",
                        "TR_CRCY_CD": "USD",
                        "CTX_AREA_FK200": "",
                        "CTX_AREA_NK200": "",
                    },
                    note="match_symbol=NVDA",
                ),
            ],
        )

    def test_us_matrix_full_attempt_name_order(self) -> None:
        attempts = balance_attempts._build_balance_attempts(
            "NVDA", "US", mock=True, matrix=True
        )
        self.assertEqual(
            [attempt.name for attempt in attempts],
            [
                "balance:NASD:VTTT3012R:USD",
                "balance:NASD:VTTT3012R:EMPTY",
                "balance:NASD:VTTS3012R:USD",
                "balance:NASD:VTTS3012R:EMPTY",
                "balance:NAS:VTTT3012R:USD",
                "balance:NAS:VTTT3012R:EMPTY",
                "balance:NAS:VTTS3012R:USD",
                "balance:NAS:VTTS3012R:EMPTY",
                "balance:NYSE:VTTT3012R:USD",
                "balance:NYSE:VTTT3012R:EMPTY",
                "balance:NYSE:VTTS3012R:USD",
                "balance:NYSE:VTTS3012R:EMPTY",
                "balance:AMEX:VTTT3012R:USD",
                "balance:AMEX:VTTT3012R:EMPTY",
                "balance:AMEX:VTTS3012R:USD",
                "balance:AMEX:VTTS3012R:EMPTY",
            ],
        )
        for attempt in attempts:
            self.assertEqual(attempt.endpoint, _BALANCE_ENDPOINT)
            self.assertEqual(attempt.params["CANO"], "1234567890")
            self.assertEqual(attempt.params["ACNT_PRDT_CD"], "01")
            self.assertEqual(attempt.note, "match_symbol=NVDA")


class BuildBalanceAccountAttemptsTests(_AccountEnvTestCase):
    def test_us_mock_full_attempts_and_candidates(self) -> None:
        attempts, candidates = balance_attempts._build_balance_account_attempts(
            market="US", mock=True
        )
        self.assertEqual(candidates, _EXPECTED_ACCOUNT_CANDIDATES)

        def expected_attempt(source: str, cano: str, acnt: str, tr_id: str) -> ProbeAttempt:
            return ProbeAttempt(
                name=f"balance-account:{source}:{tr_id}",
                endpoint=_BALANCE_ENDPOINT,
                tr_id=tr_id,
                params={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt,
                    "OVRS_EXCG_CD": "NASD",
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                },
                note=f"account_context_source={source}",
            )

        self.assertEqual(
            attempts,
            [
                expected_attempt("settings_direct", "1234567890", "01", "VTTT3012R"),
                expected_attempt("settings_direct", "1234567890", "01", "VTTS3012R"),
                expected_attempt("product_code_no_leading_zero", "1234567890", "1", "VTTT3012R"),
                expected_attempt("product_code_no_leading_zero", "1234567890", "1", "VTTS3012R"),
                expected_attempt("split_from_cano_suffix", "12345678", "90", "VTTT3012R"),
                expected_attempt("split_from_cano_suffix", "12345678", "90", "VTTS3012R"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
