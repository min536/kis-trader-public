from dataclasses import dataclass

US_TRADING_EXCHANGES = ("NASD", "NYSE", "AMEX")
EXCD_BY_TRADING = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
_TRADING_ALIASES = {"NAS": "NASD"}


@dataclass(frozen=True)
class OverseasInstrument:
    symbol: str
    ovrs_excg_cd: str
    excd: str
    currency: str = "USD"


def normalize_trading_exchange(value, *, default="NASD"):
    if value is None:
        return default
    code = value.strip().upper()
    if not code:
        return default
    code = _TRADING_ALIASES.get(code, code)
    if code not in US_TRADING_EXCHANGES:
        raise ValueError(f"unknown US trading exchange: {value!r}")
    return code


def resolve_us_instrument(symbol, exchange=None, *, currency="USD"):
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("symbol must not be empty")
    ovrs = normalize_trading_exchange(exchange)
    excd = EXCD_BY_TRADING[ovrs]
    return OverseasInstrument(sym, ovrs, excd, currency)


def parse_universe_entry(entry):
    text = entry.strip()
    symbol = text
    exchange = None
    positions = [text.index(sep) for sep in ("@", ":") if sep in text]
    if positions:
        idx = min(positions)
        symbol = text[:idx]
        exchange = text[idx + 1 :]
    return resolve_us_instrument(symbol, exchange)
