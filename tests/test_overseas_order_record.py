from app.overseas_stock.order_record import build_overseas_order_record


def _sample(**overrides):
    base = dict(
        side="buy",
        symbol="AAPL",
        exchange="NASD",
        qty=10,
        unit_price="190.50",
        env="mock",
        tr_id="VTTT1002U",
        result={"rt_cd": "0", "msg1": "ok"},
        now_iso="2026-06-13T12:00:00Z",
        pid=4242,
    )
    base.update(overrides)
    return base


def test_full_shape_present():
    rec = build_overseas_order_record(**_sample())
    assert rec == {
        "timestamp": "2026-06-13T12:00:00Z",
        "pid": 4242,
        "market": "overseas",
        "env": "mock",
        "action": "overseas_order_submitted",
        "side": "buy",
        "symbol": "AAPL",
        "exchange": "NASD",
        "qty": 10,
        "unit_price": "190.50",
        "tr_id": "VTTT1002U",
        "result": {"rt_cd": "0", "msg1": "ok"},
    }


def test_action_is_overseas_order_submitted():
    rec = build_overseas_order_record(**_sample())
    assert rec["action"] == "overseas_order_submitted"


def test_market_is_overseas():
    rec = build_overseas_order_record(**_sample())
    assert rec["market"] == "overseas"


def test_qty_coerced_to_int():
    rec = build_overseas_order_record(**_sample(qty="7"))
    assert rec["qty"] == 7
    assert isinstance(rec["qty"], int)


def test_pid_coerced_to_int():
    rec = build_overseas_order_record(**_sample(pid="9001"))
    assert rec["pid"] == 9001
    assert isinstance(rec["pid"], int)


def test_result_passed_through_unchanged():
    result = {"rt_cd": "0", "output": {"ODNO": "0000123456"}, "nested": [1, 2, 3]}
    rec = build_overseas_order_record(**_sample(result=result))
    assert rec["result"] == result
    assert rec["result"] is result


def test_input_result_dict_not_mutated():
    result = {"rt_cd": "0", "msg1": "ok"}
    snapshot = dict(result)
    build_overseas_order_record(**_sample(result=result))
    assert result == snapshot
