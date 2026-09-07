"""LLM response parser and schema validator.

``parse_response(raw, expected_tickers)`` is the only public entry point.
It accepts the verbatim text returned by the LLM, extracts the JSON object
(stripping any markdown fences), validates the schema, and returns a dict
in the exact format that ``AISignalProvider`` expects.

``ParseError`` is raised on any structural / coverage problem. The message
is human-readable and is forwarded to the LLM as a correction prompt.
"""
from __future__ import annotations

import json
import re
from typing import Any


__all__ = ["ParseError", "parse_response"]


_VALID_VETO_REASONS: frozenset[str] = frozenset({
    "overheat",
    "pullback_exhaustion",
    "weak_signal",
    "regime_risk",
    "other",
})

# Acceptable range for regime_multiplier *before* clamping.
# Slightly wider than the storage range [0.8, 1.2] to avoid hard failures
# when the LLM produces e.g. 0.75 or 1.25.
_RM_MIN_HARD = 0.5
_RM_MAX_HARD = 1.5
_RM_CLAMP_LO = 0.8
_RM_CLAMP_HI = 1.2


class ParseError(ValueError):
    """Raised when an LLM response cannot be parsed or fails validation.

    The string representation is sent back to the LLM as a correction
    prompt, so keep messages concise and actionable.
    """


# ── JSON extraction ───────────────────────────────────────────────────────


def _extract_json(raw: str) -> str:
    """Strip markdown code fences and isolate the outermost JSON object."""
    # Remove ```json … ``` or ``` … ``` wrappers.
    text = re.sub(r"```(?:json)?\s*", "", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ParseError(
            f"no JSON object found in response (response length={len(raw)})"
        )
    return text[start : end + 1]


# ── Public API ────────────────────────────────────────────────────────────


def parse_response(
    raw: str,
    expected_tickers: list[str],
) -> dict[str, Any]:
    """Parse a raw LLM response into a validated AISignalProvider day payload.

    Parameters
    ----------
    raw:
        Verbatim text returned by the LLM.
    expected_tickers:
        Every ticker the LLM was asked to score.  All must be present in
        the parsed response.

    Returns
    -------
    dict
        ``{"regime_multiplier": float, "tickers": {ticker: {...}}}``
        ready to write as a ``YYYY-MM-DD.json`` cache file.

    Raises
    ------
    ParseError
        On any JSON decode failure, missing key, type error, out-of-range
        value, coverage gap, or duplicate rank.
    """
    json_str = _extract_json(raw)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ParseError(f"JSON decode failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ParseError("top-level value must be a JSON object")

    # ── regime_multiplier ──────────────────────────────────────────────────
    if "regime_multiplier" not in data:
        raise ParseError("missing required key: regime_multiplier")
    try:
        rm = float(data["regime_multiplier"])
    except (TypeError, ValueError) as exc:
        raise ParseError(
            f"regime_multiplier is not numeric: {data['regime_multiplier']!r}"
        ) from exc
    if not (_RM_MIN_HARD <= rm <= _RM_MAX_HARD):
        raise ParseError(
            f"regime_multiplier {rm} outside acceptable range "
            f"[{_RM_MIN_HARD}, {_RM_MAX_HARD}]"
        )
    rm = round(max(_RM_CLAMP_LO, min(_RM_CLAMP_HI, rm)), 4)

    # ── tickers ────────────────────────────────────────────────────────────
    if "tickers" not in data:
        raise ParseError("missing required key: tickers")
    tickers_raw = data["tickers"]
    if not isinstance(tickers_raw, dict):
        raise ParseError("tickers must be a JSON object")

    missing = [t for t in expected_tickers if t not in tickers_raw]
    if missing:
        raise ParseError(
            f"{len(missing)} ticker(s) absent from response: "
            f"{missing[:5]}{'…' if len(missing) > 5 else ''}"
        )

    tickers_out: dict[str, dict[str, Any]] = {}
    ranks_seen: set[int] = set()

    for ticker in expected_tickers:
        row = tickers_raw[ticker]
        if not isinstance(row, dict):
            raise ParseError(
                f"ticker {ticker!r}: payload must be a JSON object, "
                f"got {type(row).__name__}"
            )

        # delta ─────────────────────────────────────────────────────────────
        try:
            delta = float(row.get("delta", 0.0))
        except (TypeError, ValueError) as exc:
            raise ParseError(f"ticker {ticker!r}: delta not numeric") from exc
        delta = round(max(-5.0, min(5.0, delta)), 4)

        # veto ──────────────────────────────────────────────────────────────
        raw_veto = row.get("veto", False)
        if isinstance(raw_veto, bool):
            veto = raw_veto
        elif isinstance(raw_veto, int):
            veto = bool(raw_veto)
        elif isinstance(raw_veto, str):
            veto = raw_veto.lower() in ("true", "1", "yes")
        else:
            raise ParseError(
                f"ticker {ticker!r}: veto must be bool, "
                f"got {type(raw_veto).__name__}"
            )

        # veto_confidence ───────────────────────────────────────────────────
        try:
            vc = float(row.get("veto_confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ParseError(
                f"ticker {ticker!r}: veto_confidence not numeric"
            ) from exc
        vc = round(max(0.0, min(1.0, vc)), 4)

        # veto_reason ───────────────────────────────────────────────────────
        vr: str | None = row.get("veto_reason")
        if vr is not None:
            if not isinstance(vr, str):
                vr = str(vr)
            # Normalise "null" string to None.
            if vr.lower() in ("null", "none", ""):
                vr = None
            elif vr not in _VALID_VETO_REASONS:
                vr = "other"  # coerce rather than reject — keep going

        # Consistency: veto=True must have reason + non-zero confidence.
        if veto and vr is None:
            vr = "other"
        if veto and vc == 0.0:
            vc = 0.5  # sensible floor when LLM forgets

        # rank ──────────────────────────────────────────────────────────────
        raw_rank = row.get("rank")
        if raw_rank is None:
            raise ParseError(f"ticker {ticker!r}: missing rank")
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError) as exc:
            raise ParseError(
                f"ticker {ticker!r}: rank not an integer"
            ) from exc
        if rank < 1:
            raise ParseError(
                f"ticker {ticker!r}: rank must be ≥ 1, got {rank}"
            )
        if rank in ranks_seen:
            raise ParseError(f"duplicate rank {rank} (ticker {ticker!r})")
        ranks_seen.add(rank)

        tickers_out[ticker] = {
            "delta": delta,
            "veto": veto,
            "veto_confidence": vc,
            "veto_reason": vr,
            "rank": rank,
        }

    # Normalise to 1..N preserving relative order (handles non-contiguous ranks).
    _normalise_ranks(tickers_out)

    return {"regime_multiplier": rm, "tickers": tickers_out}


def _normalise_ranks(tickers: dict[str, dict[str, Any]]) -> None:
    """Renumber ranks 1..N preserving relative order in-place."""
    items = sorted(tickers.items(), key=lambda kv: kv[1]["rank"])
    for new_rank, (ticker, _) in enumerate(items, start=1):
        tickers[ticker]["rank"] = new_rank
