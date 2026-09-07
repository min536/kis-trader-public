"""Offline deterministic cache generator for AISignalProvider.

Reads a JSONL file written by :class:`CandidateFeatureExporter`, groups
records by date, and produces one JSON cache file per date in the format
that :class:`AISignalProvider` expects.

Output format per day (``<output_dir>/YYYY-MM-DD.json``)::

    {
      "regime_multiplier": 0.93,
      "tickers": {
        "005930": {
          "delta": 1.8,
          "veto": false,
          "veto_confidence": 0.05,
          "veto_reason": null,
          "rank": 1
        }
      }
    }

Heuristic
---------
Signal values are derived from the exact feature keys that
``calculate_selection_score`` emits, so the heuristic is grounded in
real feature semantics — not synthetic noise.

Delta (score adjustment, clamped to [-5, +5])::

    delta =
        (momentum_quality_score - 0.30) * 4.0   # reward strong momentum
      + trend_quality_score * 2.0               # reward trend alignment
      + range_recovery_bonus * 2.5              # reward intraday recovery
      - overheat_penalty * 5.0                  # penalise hot stocks
      - pullback_exhaustion_penalty * 4.0       # penalise exhausted pullbacks

Veto (any of)::

    overheat_penalty > 0.25              → "overheat"
    pullback_exhaustion_penalty > 0.20   → "pullback_exhaustion"
    base_score < 1.0 and momentum_quality_score < 0.10  → "weak_signal"

Regime multiplier (per day, [0.8, 1.2])::

    day_quality = mean delta of non-vetoed candidates
    regime = clamp(1.0 + day_quality * 0.10, 0.8, 1.2)

This is **not** intended to produce alpha — it is a transparent bridge
that exercises the full cache path until a real model batch job exists.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

__all__ = [
    "build_cache_from_jsonl",
    "build_day_payload",
    "compute_signals",
    "validate_cache_dir",
]


# ── signal heuristic ──────────────────────────────────────────────────────

_DELTA_CLAMP = 5.0
_VETO_OVERHEAT = 0.25
_VETO_EXHAUSTION = 0.20
_VETO_WEAK_SCORE = 1.0
_VETO_WEAK_MOMENTUM = 0.10


def _get(features: dict[str, float], key: str, default: float = 0.0) -> float:
    v = features.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_signals(record: dict[str, Any]) -> dict[str, Any]:
    """Compute delta / veto / rank-score from one JSONL record.

    Returns a dict with keys:
    - ``delta``         (float)
    - ``veto``          (bool)
    - ``veto_confidence`` (float, [0, 1])
    - ``veto_reason``   (str | None)
    - ``_rank_score``   (float, higher = preferred; used for ranking, not persisted)
    """
    f = record.get("features") or {}
    base_score = float(record.get("base_score", 0.0))

    momentum = _get(f, "momentum_quality_score")
    trend = _get(f, "trend_quality_score")
    recovery = _get(f, "range_recovery_bonus")
    overheat = _get(f, "overheat_penalty")
    exhaustion = _get(f, "pullback_exhaustion_penalty")

    raw_delta = (
        (momentum - 0.30) * 4.0
        + trend * 2.0
        + recovery * 2.5
        - overheat * 5.0
        - exhaustion * 4.0
    )
    delta = max(-_DELTA_CLAMP, min(_DELTA_CLAMP, raw_delta))
    delta = round(delta, 4)

    # Veto logic — ordered from most severe to least.
    veto = False
    veto_confidence = 0.0
    veto_reason: str | None = None

    if overheat > _VETO_OVERHEAT:
        veto = True
        veto_confidence = min(1.0, round(overheat / 0.80, 3))
        veto_reason = "overheat"
    elif exhaustion > _VETO_EXHAUSTION:
        veto = True
        veto_confidence = min(1.0, round(exhaustion / 0.70, 3))
        veto_reason = "pullback_exhaustion"
    elif base_score < _VETO_WEAK_SCORE and momentum < _VETO_WEAK_MOMENTUM:
        veto = True
        veto_confidence = round(1.0 - max(base_score / _VETO_WEAK_SCORE, 0.0), 3)
        veto_reason = "weak_signal"
    else:
        veto_confidence = round(max(0.0, -delta / (_DELTA_CLAMP * 2)), 3)

    rank_score = base_score + delta  # higher = better rank (rank 1)

    return {
        "delta": delta,
        "veto": veto,
        "veto_confidence": veto_confidence,
        "veto_reason": veto_reason,
        "_rank_score": rank_score,
    }


# ── day payload builder ───────────────────────────────────────────────────


def build_day_payload(day_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce one cache payload for a single trading day.

    ``day_records`` is a list of JSONL records all sharing the same date.
    Tickers that appear more than once (shouldn't happen in practice) are
    de-duplicated by keeping the highest-scoring record.
    """
    # De-duplicate by ticker (keep highest base_score if repeated).
    by_ticker: dict[str, dict[str, Any]] = {}
    for rec in day_records:
        ticker = str(rec.get("ticker", ""))
        if not ticker:
            continue
        existing = by_ticker.get(ticker)
        if existing is None or float(rec.get("base_score", 0.0)) > float(
            existing.get("base_score", 0.0)
        ):
            by_ticker[ticker] = rec

    if not by_ticker:
        return {"regime_multiplier": 1.0, "tickers": {}}

    # Compute signals per ticker.
    signals: dict[str, dict[str, Any]] = {
        t: compute_signals(r) for t, r in by_ticker.items()
    }

    # Assign ranks (1 = best) by descending rank_score; break ties by ticker.
    ranked = sorted(
        signals.items(),
        key=lambda kv: (-kv[1]["_rank_score"], kv[0]),
    )
    rank_by_ticker = {ticker: idx + 1 for idx, (ticker, _) in enumerate(ranked)}

    # Regime multiplier from non-vetoed deltas.
    non_vetoed_deltas = [
        s["delta"]
        for s in signals.values()
        if not s["veto"]
    ]
    if non_vetoed_deltas:
        mean_delta = sum(non_vetoed_deltas) / len(non_vetoed_deltas)
    else:
        mean_delta = 0.0
    regime = max(0.8, min(1.2, round(1.0 + mean_delta * 0.10, 4)))

    tickers_out: dict[str, dict[str, Any]] = {}
    for ticker, sig in signals.items():
        tickers_out[ticker] = {
            "delta": sig["delta"],
            "veto": sig["veto"],
            "veto_confidence": sig["veto_confidence"],
            "veto_reason": sig["veto_reason"],
            "rank": rank_by_ticker[ticker],
        }

    return {
        "regime_multiplier": regime,
        "tickers": tickers_out,
    }


# ── grouping + writer ─────────────────────────────────────────────────────


def build_cache_from_jsonl(
    features_path: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    limit_days: int | None = None,
) -> list[str]:
    """Read JSONL, group by date, write one JSON file per date.

    Parameters
    ----------
    features_path:
        Path to the ``candidate_features.jsonl`` written by
        :class:`CandidateFeatureExporter`.
    output_dir:
        Directory to write ``YYYY-MM-DD.json`` files.
        Created if it does not exist.
    overwrite:
        If False (default) and the output file for a given date already
        exists, skip that date and report it as skipped.
    limit_days:
        If set, stop after writing this many date files (useful for
        quick iteration during development).

    Returns
    -------
    list[str]
        Sorted list of YYYY-MM-DD ISO strings for which files were
        written or already existed.
    """
    features_path = Path(features_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_parse = 0

    with open(features_path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                skipped_parse += 1
                continue
            date_key = str(rec.get("date", "")).strip()
            if not date_key:
                skipped_parse += 1
                continue
            by_date[date_key].append(rec)

    if skipped_parse:
        import warnings
        warnings.warn(
            f"cache_builder: skipped {skipped_parse} malformed/dateless records",
            stacklevel=2,
        )

    written: list[str] = []
    skipped_exist: list[str] = []

    for date_key in sorted(by_date):
        if limit_days is not None and len(written) >= limit_days:
            break

        out_path = output_dir / f"{date_key}.json"
        if out_path.exists() and not overwrite:
            skipped_exist.append(date_key)
            continue

        payload = build_day_payload(by_date[date_key])
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        written.append(date_key)

    if skipped_exist:
        import warnings
        warnings.warn(
            f"cache_builder: skipped {len(skipped_exist)} dates (file exists, "
            "pass overwrite=True to replace)",
            stacklevel=2,
        )

    return sorted(written + skipped_exist)


# ── validation ────────────────────────────────────────────────────────────


def validate_cache_dir(output_dir: str | Path) -> list[dict[str, Any]]:
    """Check every ``YYYY-MM-DD.json`` file in ``output_dir``.

    Returns a list of issue dicts (empty = all OK). Each dict has:
    - ``file``    — filename
    - ``issue``   — human-readable problem description

    Checks:
    - Valid JSON
    - Top-level keys: ``regime_multiplier`` (float, [0,2]) + ``tickers`` (dict)
    - Each ticker payload has required keys with correct types
    - Ranks are unique within a day when all present
    """
    output_dir = Path(output_dir)
    issues: list[dict[str, Any]] = []

    for p in sorted(output_dir.glob("????-??-??.json")):
        fname = p.name
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append({"file": fname, "issue": f"invalid JSON: {exc}"})
            continue

        rm = doc.get("regime_multiplier")
        if not isinstance(rm, (int, float)) or not math.isfinite(float(rm)):
            issues.append(
                {"file": fname, "issue": f"regime_multiplier invalid: {rm!r}"}
            )
        elif not (0.0 <= float(rm) <= 2.0):
            issues.append(
                {"file": fname, "issue": f"regime_multiplier {rm} outside [0,2]"}
            )

        tickers = doc.get("tickers")
        if not isinstance(tickers, dict):
            issues.append({"file": fname, "issue": "tickers must be a dict"})
            continue

        ranks_seen: set[int] = set()
        for ticker, payload in tickers.items():
            if not isinstance(payload, dict):
                issues.append(
                    {"file": fname, "issue": f"ticker {ticker!r}: payload not a dict"}
                )
                continue

            # Check delta.
            delta = payload.get("delta")
            if delta is not None and not isinstance(delta, (int, float)):
                issues.append(
                    {"file": fname, "issue": f"ticker {ticker!r}: delta type {type(delta).__name__}"}
                )

            # Check veto.
            veto = payload.get("veto")
            if veto is not None and not isinstance(veto, bool):
                issues.append(
                    {"file": fname, "issue": f"ticker {ticker!r}: veto must be bool, got {type(veto).__name__}"}
                )

            # Check veto_confidence.
            vc = payload.get("veto_confidence")
            if vc is not None:
                try:
                    vc_f = float(vc)
                    if not (0.0 <= vc_f <= 1.0):
                        issues.append(
                            {"file": fname, "issue": f"ticker {ticker!r}: veto_confidence {vc_f} outside [0,1]"}
                        )
                except (TypeError, ValueError):
                    issues.append(
                        {"file": fname, "issue": f"ticker {ticker!r}: veto_confidence not numeric"}
                    )

            # Check rank uniqueness.
            rank = payload.get("rank")
            if rank is not None:
                try:
                    rank_int = int(rank)
                    if rank_int in ranks_seen:
                        issues.append(
                            {"file": fname, "issue": f"duplicate rank {rank_int}"}
                        )
                    ranks_seen.add(rank_int)
                except (TypeError, ValueError):
                    issues.append(
                        {"file": fname, "issue": f"ticker {ticker!r}: rank not integer"}
                    )

    return issues


# ── CLI ───────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.cache_builder",
        description=(
            "Generate AISignalProvider cache files from exported feature JSONL."
        ),
    )
    parser.add_argument(
        "--features",
        required=True,
        help="Path to candidate_features.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for YYYY-MM-DD.json output files",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files",
    )
    parser.add_argument(
        "--limit-days",
        type=int,
        default=None,
        help="Stop after writing N date files (development shortcut)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="After building, validate all files in --output-dir",
    )

    args = parser.parse_args(argv)

    print(f"Reading features from: {args.features}")
    print(f"Writing cache files to: {args.output_dir}")
    if args.limit_days:
        print(f"Limit: {args.limit_days} days")

    written = build_cache_from_jsonl(
        features_path=args.features,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        limit_days=args.limit_days,
    )
    print(f"Done: {len(written)} date files in {args.output_dir}")

    if written:
        print(f"  Date range: {written[0]} → {written[-1]}")

    if args.validate:
        issues = validate_cache_dir(args.output_dir)
        if issues:
            print(f"\nValidation: {len(issues)} issue(s) found:")
            for iss in issues:
                print(f"  [{iss['file']}] {iss['issue']}")
        else:
            print(f"\nValidation: all {len(written)} files OK")


if __name__ == "__main__":  # pragma: no cover
    _main()
