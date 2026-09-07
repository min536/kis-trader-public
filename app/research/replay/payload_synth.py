"""Slice F1 — synthesize KIS quote payloads from minute observations.

Turns a :class:`MinuteObservation` into the exact ``output`` shape that the
runtime parser ``app.market_data.schema.build_market_snapshot`` reads, so a
replay can drive the real scanner without live quote calls.

Research module: imports only the runtime pure-calculation types it mirrors
(``MinuteObservation``). No settings/broker imports.
"""

from __future__ import annotations

from app.research.replay.minute_provider import MinuteObservation


def synth_quote_payload(
    obs: MinuteObservation, *, prev_close: float | None
) -> dict | None:
    """Build a KIS current-price payload for ``obs``.

    Returns ``None`` (symbol excluded from the scan) when ``prev_close`` is
    ``None`` or ``0`` — there is no valid prior-day baseline to compute the
    change ratio against. Otherwise returns exactly the ``rt_cd``/``output``
    envelope the runtime parser reads, with prices int-truncated and
    ``prdy_ctrt`` as the percent change to 2 decimals.
    """
    if not prev_close:
        return None

    return {
        "rt_cd": "0",
        "output": {
            "stck_shrn_iscd": obs.symbol,
            "stck_prpr": str(int(obs.close)),
            "stck_oprc": str(int(obs.day_open)),
            "stck_hgpr": str(int(obs.day_high)),
            "stck_lwpr": str(int(obs.day_low)),
            "prdy_ctrt": f"{(obs.close / prev_close - 1) * 100:.2f}",
        },
    }
