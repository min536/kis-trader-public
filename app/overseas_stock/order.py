import json
import os
import sys
import time

from app.auth.account_scope import get_account_signature
from app.auth.settings import PROJECT_ROOT, get_settings
from app.auth.token import (
    build_auth_headers,
    issue_access_token,
    issue_hashkey,
    request_json,
)
from app.core.time_utils import get_korean_now
from app.overseas_stock.config import load_overseas_config
from app.overseas_stock.exchanges import normalize_trading_exchange
from app.overseas_stock.market_data import format_overseas_price
from app.overseas_stock.order_record import build_overseas_order_record
from app.overseas_stock.runtime_env import require_mock_env
from app.overseas_stock.tr_ids import resolve_order_tr_id

ORDER_ENDPOINT = "/uapi/overseas-stock/v1/trading/order"


class OverseasOrderError(ValueError):
    pass


def get_overseas_order_log_path(settings=None):
    # R6: distinct from domestic get_order_log_path (which has no asset-class
    # dimension), so overseas fills never collide with domestic order history.
    signature = get_account_signature(settings)
    return PROJECT_ROOT / "logs" / f"overseas_orders_{signature}.jsonl"


def _assert_limit_only(payload):
    # R4 egress tripwire: only a limit order ("00") with a valid sell-type may
    # leave this module. A future edit that lets any input influence the order
    # division / sell-type fails here instead of reaching the broker.
    if payload["ORD_DVSN"] != "00" or payload["SLL_TYPE"] not in ("", "00"):
        raise OverseasOrderError("order invariant violated: non-limit division")


def _append_order_log(record, *, settings=None):
    path = get_overseas_order_log_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def place_overseas_limit_order(
    *, symbol, exchange, qty, unit_price, side, settings=None, token=None, pid=None
):
    s = settings or get_settings()
    # R1/R2/R3/R8: hard mock-gate FIRST and OUTSIDE any try/except — a live
    # env (by base_url or KIS_ENV) must hard-fail and never be swallowed.
    env = require_mock_env(s)

    if side not in ("buy", "sell"):
        raise OverseasOrderError(f"side must be 'buy' or 'sell', got {side!r}")
    pdno = str(symbol or "").strip().upper()
    if not pdno:
        raise OverseasOrderError(f"symbol must not be empty, got {symbol!r}")
    ovrs_excg_cd = normalize_trading_exchange(exchange)
    try:
        ord_unpr = format_overseas_price(unit_price)
    except (ValueError, ArithmeticError) as exc:
        raise OverseasOrderError(f"invalid unit_price {unit_price!r}: {exc}") from exc
    if isinstance(qty, float) and not qty.is_integer():
        raise OverseasOrderError(f"qty must be a whole number of shares, got {qty!r}")
    try:
        qty_int = int(qty)
    except (TypeError, ValueError) as exc:
        raise OverseasOrderError(f"qty must be a whole number, got {qty!r}") from exc
    if qty_int <= 0:
        raise OverseasOrderError(f"qty must be positive, got {qty!r}")

    tr_id = resolve_order_tr_id(env, side, ovrs_excg_cd)  # R7: mock-only
    sll_type = "" if side == "buy" else "00"
    ord_dvsn = "00"  # R4: limit only — mock rejects MOO/LOO/MOC/LOC.

    cfg = load_overseas_config(s)
    tok = token or issue_access_token()
    url = f"{s.base_url}{ORDER_ENDPOINT}"
    payload = {
        "CANO": cfg.cano,
        "ACNT_PRDT_CD": cfg.acnt_prdt_cd,
        "OVRS_EXCG_CD": ovrs_excg_cd,
        "PDNO": pdno,
        "ORD_QTY": str(qty_int),
        "OVRS_ORD_UNPR": ord_unpr,
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "SLL_TYPE": sll_type,
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": ord_dvsn,
    }
    _assert_limit_only(payload)

    time.sleep(1.0)
    hashkey = issue_hashkey(payload, token=tok)
    headers = build_auth_headers(tok, tr_id=tr_id, hashkey=hashkey)
    result = request_json(
        "POST",
        url,
        headers=headers,
        payload=payload,
        error_label="해외 주식 주문",
    )

    record = build_overseas_order_record(
        side=side,
        symbol=pdno,
        exchange=ovrs_excg_cd,
        qty=qty_int,
        unit_price=ord_unpr,
        env=env,
        tr_id=tr_id,
        result=result,
        now_iso=get_korean_now().isoformat(),
        pid=pid if pid is not None else os.getpid(),
    )
    try:
        _append_order_log(record, settings=s)
    except OSError as exc:
        # The order already reached the broker; a log-write failure must NOT
        # propagate as an order failure (which could trigger a re-submit).
        print(f"warning: overseas order log write failed: {exc}", file=sys.stderr)
    return record
