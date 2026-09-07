from dataclasses import dataclass, field

from app.auth.settings import get_settings
from app.overseas_stock.runtime_env import resolve_overseas_env
from app.overseas_stock.market_data import fetch_overseas_quote


def _mask_cano(cano):
    text = str(cano)
    if len(text) <= 4:
        return text
    return "*" * (len(text) - 4) + text[-4:]


@dataclass(frozen=True)
class OverseasReport:
    generated_at: str
    environment: str
    account_masked: str
    quotes: tuple
    holdings: tuple
    errors: tuple


def build_overseas_report(
    symbols,
    *,
    exchange=None,
    token=None,
    settings=None,
    now_iso,
    quote_fetcher=None,
) -> OverseasReport:
    s = settings or get_settings()
    env = resolve_overseas_env(s)
    fetch = quote_fetcher or fetch_overseas_quote
    quotes = []
    errors = []
    for sym in symbols:
        try:
            q = fetch(sym, exchange=exchange, token=token, settings=s)
            quotes.append(
                {
                    "symbol": sym,
                    "last_price": q.last_price,
                    "currency": q.currency,
                    "exchange_code": q.exchange_code,
                }
            )
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return OverseasReport(
        generated_at=now_iso,
        environment=env,
        account_masked=_mask_cano(s.cano),
        quotes=tuple(quotes),
        holdings=tuple(),
        errors=tuple(errors),
    )
