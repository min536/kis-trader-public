"""Overseas (US stock) market snapshot dataclass and builder.

Parses the KIS overseas price-detail (HHDFS76200200) ``output`` dict
into float USD prices for the gate2 scoring pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass


class OverseasSnapshotError(ValueError):
    """Raised when required price fields are missing or non-positive."""


@dataclass(frozen=True)
class OverseasMarketSnapshot:
    symbol: str
    current_price: float
    open_price: float
    high_price: float
    low_price: float
    prev_close: float
    prev_day_change_pct: float
    currency: str = "USD"
    as_of: str | None = None


def _num(v):
    """Parse a numeric string value — strip, remove commas, float().

    Returns None on failure/blank/None.
    Mirrors the ``_num`` helper in ``app/overseas_stock/market_data.py``.
    """
    if v is None:
        return None
    text = str(v).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first_num(output: dict, *keys):
    """Return the first non-None numeric value from ``output`` for the given keys."""
    for k in keys:
        val = _num(output.get(k))
        if val is not None:
            return val
    return None


def _first_str(output: dict, *keys):
    """Return the first non-empty string value from ``output`` for the given keys."""
    for k in keys:
        val = output.get(k)
        if val is not None:
            text = str(val).strip()
            if text:
                return text
    return None


def build_overseas_market_snapshot(output: dict) -> OverseasMarketSnapshot:
    """Build an OverseasMarketSnapshot from a KIS price-detail ``output`` dict.

    Uses tolerant multi-key fallbacks: first non-None wins per field.

    Raises:
        OverseasSnapshotError: if current_price, open_price, low_price, or
            prev_close is None or not > 0.
    """
    # symbol: first of symb, rsym, pdno
    raw_sym = _first_str(output, "symb", "rsym", "pdno")
    symbol = raw_sym.upper() if raw_sym else ""

    # prices with multi-key fallbacks
    current_price = _first_num(output, "last", "prpr", "ovrs_nmix_prpr")
    open_price = _first_num(output, "open", "oprc")
    high_price = _first_num(output, "high", "hgpr")
    if high_price is None:
        high_price = 0.0
    low_price = _first_num(output, "low", "lwpr")
    prev_close = _first_num(output, "base", "prdy_clpr", "sdpr")
    prev_day_change_pct = _first_num(output, "rate", "ovrs_prdy_ctrt", "prdy_ctrt")
    if prev_day_change_pct is None:
        prev_day_change_pct = 0.0

    # currency: first of curr, tr_crcy_cd
    raw_curr = _first_str(output, "curr", "tr_crcy_cd")
    currency = raw_curr if raw_curr else "USD"

    # as_of: first of xymd, date
    as_of = _first_str(output, "xymd", "date")

    # Validation: required fields must be present and > 0
    required = {
        "current_price": current_price,
        "open_price": open_price,
        "low_price": low_price,
        "prev_close": prev_close,
    }
    for name, val in required.items():
        if val is None or not (val > 0):
            raise OverseasSnapshotError(
                f"Required field '{name}' is missing or non-positive: {val!r}"
            )

    return OverseasMarketSnapshot(
        symbol=symbol,
        current_price=current_price,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        prev_close=prev_close,
        prev_day_change_pct=prev_day_change_pct,
        currency=currency,
        as_of=as_of,
    )
