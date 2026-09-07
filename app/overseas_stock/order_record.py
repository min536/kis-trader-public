def build_overseas_order_record(
    *, side, symbol, exchange, qty, unit_price, env, tr_id, result, now_iso, pid
):
    return {
        "timestamp": now_iso,
        "pid": int(pid),
        "market": "overseas",
        "env": env,
        "action": "overseas_order_submitted",
        "side": side,
        "symbol": symbol,
        "exchange": exchange,
        "qty": int(qty),
        "unit_price": str(unit_price),
        "tr_id": tr_id,
        "result": result,
    }
