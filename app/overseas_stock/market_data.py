from dataclasses import dataclass
from decimal import Decimal

from app.auth.settings import get_settings
from app.auth.token import build_auth_headers, issue_access_token, request_json, ApiHttpError
from app.overseas_stock.models import OverseasQuote, OverseasHolding
from app.overseas_stock.exchanges import resolve_us_instrument
from app.overseas_stock.tr_ids import resolve_quote_tr_id, resolve_balance_tr_id, resolve_psamount_tr_id, resolve_price_detail_tr_id
from app.overseas_stock.runtime_env import resolve_overseas_env
from app.overseas_stock.config import load_overseas_config
from app.overseas_stock.balance_parsing import _normalize_holdings


class OverseasDataError(RuntimeError):
    pass


def _num(v):
    if v is None:
        return None
    text = str(v).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def fetch_overseas_quote(symbol, *, exchange=None, token=None, settings=None):
    s = settings or get_settings()
    env = resolve_overseas_env(s)
    inst = resolve_us_instrument(symbol, exchange)
    tr_id = resolve_quote_tr_id(env)
    tok = token or issue_access_token()
    payload = request_json(
        "GET",
        f"{s.base_url}/uapi/overseas-price/v1/quotations/price",
        headers=build_auth_headers(tok, tr_id=tr_id),
        params={"AUTH": "", "EXCD": inst.excd, "SYMB": inst.symbol},
        error_label="해외 시세 조회",
    )
    out = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    last = None
    for key in ("last", "ovrs_nmix_prpr", "ovrs_now_pric", "stck_prpr", "prpr"):
        last = _num(out.get(key))
        if last is not None:
            break
    change = None
    for key in ("diff", "ovrs_prdy_vrss"):
        change = _num(out.get(key))
        if change is not None:
            break
    change_pct = None
    for key in ("rate", "ovrs_prdy_ctrt"):
        change_pct = _num(out.get(key))
        if change_pct is not None:
            break
    currency = out.get("curr") or out.get("tr_crcy_cd") or inst.currency
    return OverseasQuote(
        symbol=inst.symbol,
        market="US",
        exchange_code=inst.excd,
        last_price=last,
        change=change,
        change_pct=change_pct,
        currency=currency,
        source_endpoint="/uapi/overseas-price/v1/quotations/price",
        source_tr_id=tr_id,
    )


def fetch_overseas_price_detail(symbol, *, exchange="NASD", token=None, settings=None) -> dict:
    s = settings or get_settings()
    env = resolve_overseas_env(s)
    inst = resolve_us_instrument(symbol, exchange)
    tr_id = resolve_price_detail_tr_id(env)
    tok = token or issue_access_token()
    payload = request_json(
        "GET",
        f"{s.base_url}/uapi/overseas-price/v1/quotations/price-detail",
        headers=build_auth_headers(tok, tr_id=tr_id),
        params={"AUTH": "", "EXCD": inst.excd, "SYMB": inst.symbol},
        error_label="해외 상세시세 조회",
    )
    return payload.get("output") if isinstance(payload.get("output"), dict) else {}


@dataclass(frozen=True)
class OverseasOrderable:
    symbol: str
    exchange_code: str
    max_order_qty: int | None
    orderable_cash: float | None
    fx_rate: float | None
    currency: str
    source_tr_id: str
    raw: dict


def fetch_overseas_orderable(symbol, unit_price, *, exchange="NASD", token=None, settings=None):
    s = settings or get_settings()
    env = resolve_overseas_env(s)
    cfg = load_overseas_config(s)
    tr_id = resolve_psamount_tr_id(env)
    tok = token or issue_access_token()
    inst = resolve_us_instrument(symbol, exchange)
    payload = request_json(
        "GET",
        f"{s.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount",
        headers=build_auth_headers(tok, tr_id=tr_id),
        params={
            "CANO": cfg.cano,
            "ACNT_PRDT_CD": cfg.acnt_prdt_cd,
            "OVRS_EXCG_CD": inst.ovrs_excg_cd,
            "OVRS_ORD_UNPR": format_overseas_price(unit_price),
            "ITEM_CD": inst.symbol,
        },
        error_label="해외 매수가능조회",
    )
    out = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    max_qty = _num(out.get("max_ord_psbl_qty") or out.get("ovrs_max_ord_psbl_qty"))
    max_qty = int(max_qty) if max_qty is not None else None
    cash = _num(
        out.get("ovrs_ord_psbl_amt")
        or out.get("frcr_ord_psbl_amt1")
        or out.get("ord_psbl_frcr_amt")
    )
    fx = _num(out.get("exrt"))
    cur = str(out.get("tr_crcy_cd") or cfg.currency)
    return OverseasOrderable(
        inst.symbol, inst.ovrs_excg_cd, max_qty, cash, fx, cur, tr_id, out
    )


def fetch_overseas_holdings(*, exchange="NASD", token=None, settings=None):
    s = settings or get_settings()
    env = resolve_overseas_env(s)
    cfg = load_overseas_config(s)
    tr_id = resolve_balance_tr_id(env)
    tok = token or issue_access_token()
    ep = "/uapi/overseas-stock/v1/trading/inquire-balance"
    payload = request_json(
        "GET",
        f"{s.base_url}{ep}",
        headers=build_auth_headers(tok, tr_id=tr_id),
        params={
            "CANO": cfg.cano,
            "ACNT_PRDT_CD": cfg.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "TR_CRCY_CD": cfg.currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        },
        error_label="해외 잔고 조회",
    )
    holdings, _, _, _ = _normalize_holdings(
        payload, market="US", exchange_code=exchange, endpoint=ep, tr_id=tr_id
    )
    return holdings


def format_overseas_price(price):
    try:
        d = Decimal(str(price))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(f"price is not a number: {price!r}") from exc
    if not d.is_finite() or d <= 0:
        raise ValueError(f"price must be a positive finite number: {price!r}")
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s
