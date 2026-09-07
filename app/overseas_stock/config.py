import os
from dataclasses import dataclass

from app.auth.settings import get_settings, classify_kis_base_url_env
from app.overseas_stock.exchanges import normalize_trading_exchange


@dataclass(frozen=True)
class OverseasConfig:
    scan_symbols: tuple[str, ...]
    scan_symbols_source: str
    default_exchange: str
    currency: str
    acnt_prdt_cd: str
    cano: str
    base_url: str


def _split_symbols(text):
    return tuple(
        token.strip().upper()
        for token in text.replace(",", " ").split()
        if token.strip()
    )


def load_overseas_config(settings=None) -> OverseasConfig:
    s = settings or get_settings()
    raw = os.getenv("OVERSEAS_SCAN_SYMBOLS", "").strip()
    scan_symbols = _split_symbols(raw)
    scan_symbols_source = "OVERSEAS_SCAN_SYMBOLS" if raw else ""
    default_exchange = normalize_trading_exchange(os.getenv("KIS_OVERSEAS_EXCHANGE"))
    currency = (os.getenv("KIS_OVERSEAS_CURRENCY", "USD").strip() or "USD")
    env = classify_kis_base_url_env(s.base_url)
    scoped = (
        os.getenv(f"KIS_OVRS_ACNT_PRDT_CD_{env.upper()}")
        if env in ("mock", "live")
        else None
    )
    acnt_prdt_cd = (
        scoped or os.getenv("KIS_OVRS_ACNT_PRDT_CD") or s.acnt_prdt_cd or ""
    ).strip()
    return OverseasConfig(
        scan_symbols,
        scan_symbols_source,
        default_exchange,
        currency,
        acnt_prdt_cd,
        s.cano,
        s.base_url,
    )
