import contextlib
import json
import types
from unittest import mock

import pytest

import app.overseas_stock.runtime_env as runtime_env
from app.overseas_stock import order
from app.overseas_stock.order import OverseasOrderError
from app.overseas_stock.runtime_env import OverseasLiveBlockedError

MOCK_SETTINGS = types.SimpleNamespace(
    base_url="https://openapivts.koreainvestment.com:29443",
    cano="12345678",
    acnt_prdt_cd="01",
)
LIVE_SETTINGS = types.SimpleNamespace(
    base_url="https://openapi.koreainvestment.com:9443",
    cano="12345678",
    acnt_prdt_cd="01",
)
OK_RESULT = {"rt_cd": "0", "msg1": "정상처리", "output": {"ODNO": "0000000123"}}


@contextlib.contextmanager
def _patched_io(result=None):
    result = OK_RESULT if result is None else result
    with mock.patch.object(order, "issue_access_token", return_value="TOK"), \
         mock.patch.object(order, "issue_hashkey", return_value="HASH"), \
         mock.patch.object(order, "build_auth_headers", return_value={}) as bah, \
         mock.patch.object(order, "request_json", return_value=result) as req, \
         mock.patch.object(order.time, "sleep"), \
         mock.patch.object(runtime_env, "_resolve_kis_env", return_value=None):
        yield types.SimpleNamespace(request=req, headers=bah)


def test_overseas_order_log_path_is_distinct_from_domestic():
    from app.auth.account_scope import get_order_log_path

    ov = order.get_overseas_order_log_path(MOCK_SETTINGS)
    dom = get_order_log_path(MOCK_SETTINGS)
    assert ov.name.startswith("overseas_orders_")
    assert ov != dom


def test_place_order_blocked_on_live_env():
    with pytest.raises(OverseasLiveBlockedError):
        order.place_overseas_limit_order(
            symbol="AAPL", exchange="NASD", qty=1, unit_price=145.0,
            side="buy", settings=LIVE_SETTINGS,
        )


def test_place_buy_order_payload_shape(tmp_path):
    with _patched_io() as io, \
         mock.patch.object(
             order, "get_overseas_order_log_path", return_value=tmp_path / "ov.jsonl"
         ):
        order.place_overseas_limit_order(
            symbol="AAPL", exchange="NAS", qty=3, unit_price=145.5,
            side="buy", settings=MOCK_SETTINGS,
        )
    payload = io.request.call_args.kwargs["payload"]
    assert payload["ORD_DVSN"] == "00"
    assert payload["SLL_TYPE"] == ""
    assert payload["OVRS_EXCG_CD"] == "NASD"
    assert payload["PDNO"] == "AAPL"
    assert payload["ORD_QTY"] == "3"
    assert payload["OVRS_ORD_UNPR"] == "145.5"
    assert io.headers.call_args.kwargs["tr_id"] == "VTTT1002U"


def test_place_sell_order_sll_type(tmp_path):
    with _patched_io() as io, \
         mock.patch.object(
             order, "get_overseas_order_log_path", return_value=tmp_path / "ov.jsonl"
         ):
        order.place_overseas_limit_order(
            symbol="TSLA", exchange="NASD", qty=1, unit_price=200.0,
            side="sell", settings=MOCK_SETTINGS,
        )
    payload = io.request.call_args.kwargs["payload"]
    assert payload["SLL_TYPE"] == "00"
    assert payload["ORD_DVSN"] == "00"
    assert io.headers.call_args.kwargs["tr_id"] == "VTTT1006U"


def test_place_order_rejects_zero_price():
    with _patched_io():
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=0,
                side="buy", settings=MOCK_SETTINGS,
            )


def test_place_order_rejects_negative_price():
    with _patched_io():
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=-5,
                side="buy", settings=MOCK_SETTINGS,
            )


def test_place_order_rejects_bad_side():
    with _patched_io():
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=145.0,
                side="hold", settings=MOCK_SETTINGS,
            )


def test_place_order_rejects_infinite_price():
    with _patched_io() as io:
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=float("inf"),
                side="buy", settings=MOCK_SETTINGS,
            )
    assert io.request.call_count == 0


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan")])
def test_place_order_rejects_non_numeric_price(bad):
    with _patched_io() as io:
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=bad,
                side="buy", settings=MOCK_SETTINGS,
            )
    assert io.request.call_count == 0


def test_place_order_normalizes_symbol(tmp_path):
    with _patched_io() as io, mock.patch.object(
        order, "get_overseas_order_log_path", return_value=tmp_path / "ov.jsonl"
    ):
        record = order.place_overseas_limit_order(
            symbol="  aapl ", exchange="NASD", qty=1, unit_price=145.0,
            side="buy", settings=MOCK_SETTINGS,
        )
    assert io.request.call_args.kwargs["payload"]["PDNO"] == "AAPL"
    assert record["symbol"] == "AAPL"


def test_place_order_rejects_empty_symbol():
    with _patched_io() as io:
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="   ", exchange="NASD", qty=1, unit_price=145.0,
                side="buy", settings=MOCK_SETTINGS,
            )
    assert io.request.call_count == 0


def test_place_order_rejects_fractional_qty():
    with _patched_io() as io:
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1.9, unit_price=145.0,
                side="buy", settings=MOCK_SETTINGS,
            )
    assert io.request.call_count == 0


def test_place_order_writes_log_record(tmp_path):
    log_file = tmp_path / "overseas_orders_test.jsonl"
    with _patched_io(), \
         mock.patch.object(
             order, "get_overseas_order_log_path", return_value=log_file
         ):
        record = order.place_overseas_limit_order(
            symbol="AAPL", exchange="NASD", qty=2, unit_price=145.0,
            side="buy", settings=MOCK_SETTINGS, pid=4242,
        )
    assert record["market"] == "overseas"
    assert record["env"] == "mock"
    assert record["pid"] == 4242
    assert record["side"] == "buy"
    assert record["symbol"] == "AAPL"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["market"] == "overseas"
    assert logged["tr_id"] == "VTTT1002U"


def test_egress_tripwire_rejects_non_limit_division():
    # R4: only a limit order ("00") with a valid sell-type may pass the egress
    # guard; a future edit that leaks a market/LOO/LOC division must fail here.
    order._assert_limit_only({"ORD_DVSN": "00", "SLL_TYPE": ""})
    order._assert_limit_only({"ORD_DVSN": "00", "SLL_TYPE": "00"})
    with pytest.raises(OverseasOrderError):
        order._assert_limit_only({"ORD_DVSN": "32", "SLL_TYPE": ""})
    with pytest.raises(OverseasOrderError):
        order._assert_limit_only({"ORD_DVSN": "00", "SLL_TYPE": "99"})


def test_place_order_invokes_egress_tripwire():
    # R4: the egress guard must run on the live order path before the POST, so a
    # leaked non-limit division is blocked before reaching the broker.
    with _patched_io() as io, mock.patch.object(
        order, "_assert_limit_only", side_effect=OverseasOrderError("boom")
    ):
        with pytest.raises(OverseasOrderError):
            order.place_overseas_limit_order(
                symbol="AAPL", exchange="NASD", qty=1, unit_price=145.0,
                side="buy", settings=MOCK_SETTINGS,
            )
    assert io.request.call_count == 0


def test_place_order_submits_exactly_once(tmp_path):
    # R10: a single, non-retry POST — never auto-resend an order.
    with _patched_io() as io, mock.patch.object(
        order, "get_overseas_order_log_path", return_value=tmp_path / "ov.jsonl"
    ):
        order.place_overseas_limit_order(
            symbol="AAPL", exchange="NASD", qty=1, unit_price=145.0,
            side="buy", settings=MOCK_SETTINGS,
        )
    assert io.request.call_count == 1


def test_log_write_failure_does_not_propagate():
    # R10: the order already reached the broker; a log-write failure must NOT
    # surface as an order failure (which could trigger a caller re-submit).
    with _patched_io() as io, mock.patch.object(
        order, "_append_order_log", side_effect=OSError("disk full")
    ):
        record = order.place_overseas_limit_order(
            symbol="AAPL", exchange="NASD", qty=1, unit_price=145.0,
            side="buy", settings=MOCK_SETTINGS, pid=99,
        )
    assert io.request.call_count == 1
    assert record["symbol"] == "AAPL"
