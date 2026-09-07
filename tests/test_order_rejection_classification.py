from __future__ import annotations

import pytest

from app.core.order_log import classify_order_rejection


def test_top_level_msg_cd_40910000_is_fatal_account() -> None:
    assert classify_order_rejection({"msg_cd": "40910000"}) == "fatal_account"


def test_nested_order_response_msg_cd_is_fatal_account() -> None:
    assert (
        classify_order_rejection({"order_response": {"msg_cd": "40910000"}})
        == "fatal_account"
    )


def test_nested_raw_response_msg_cd_is_fatal_account() -> None:
    assert (
        classify_order_rejection({"raw_response": {"msg_cd": "40910000"}})
        == "fatal_account"
    )


def test_reason_string_with_trailing_paren_code_is_fatal_account() -> None:
    # Primary production path: summarize_order_reason output.
    reason = "모의투자 주문이 불가한 계좌입니다. (40910000)"
    assert classify_order_rejection(reason) == "fatal_account"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"msg_cd": "40590000"},  # ordinary "접수" code
        {"msg_cd": ""},
        {"order_response": {"msg_cd": "40600000"}},
        "일반 실패 사유 (40600000)",
        "코드 없는 사유",
        "trailing paren but empty ()",
        123,
        [{"msg_cd": "40910000"}],  # unexpected list shape -> not fatal
    ],
)
def test_non_fatal_and_unknown_shapes_are_other(raw) -> None:
    assert classify_order_rejection(raw) == "other"


def test_classifier_never_raises_on_hostile_input() -> None:
    class Hostile:
        def __str__(self) -> str:  # pragma: no cover - defensive
            raise RuntimeError("boom")

    # Must be total: a mapping whose .get raises must degrade to "other".
    class HostileMapping(dict):
        def get(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("boom")

    assert classify_order_rejection(HostileMapping()) == "other"
    assert classify_order_rejection(Hostile()) == "other"
