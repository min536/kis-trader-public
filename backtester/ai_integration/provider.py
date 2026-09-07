"""AI signal provider — read-only cache of offline-computed signals.

The provider is a pure lookup layer: it loads a directory of per-day
JSON files ONCE at construction time, and exposes four query methods
the backtester uses at four injection points.

Design properties:

* **No I/O in the hot loop.** All files are read in `__init__`.
* **Deterministic.** Same cache + same inputs ⇒ same outputs.
* **Mode-gated.** `mode` controls which signals are active; getters
  for inactive signals return neutral values regardless of cache
  contents. `mode="disabled"` is the strictest form and additionally
  skips cache loading entirely, so constructing a disabled provider
  is free.
* **Fallback-aware.** When a signal is missing for a given
  (date, ticker), the configured fallback policy decides whether to
  return neutral, warn, or raise.
* **Observable.** `coverage()` reports hit / miss counts and
  `signal_coverage_rate` for reporting and tests.

Cache format (per day file, e.g. ``ai_signals/2024-03-15.json``)::

    {
      "regime_multiplier": 0.85,
      "tickers": {
        "005930": {"delta": 3.2, "veto": false, "veto_confidence": 0.1, "rank": 2},
        "000660": {"delta": -1.5, "veto": true, "veto_confidence": 0.82,
                   "veto_reason": "sector_weakness"}
      }
    }
"""
from __future__ import annotations

import glob
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence, TypeVar

logger = logging.getLogger(__name__)


Mode = Literal["disabled", "score_delta", "veto", "rerank", "regime", "combined"]
Fallback = Literal["passthrough", "reject", "warn"]

_VALID_MODES: tuple[str, ...] = (
    "disabled",
    "score_delta",
    "veto",
    "rerank",
    "regime",
    "combined",
)
_VALID_FALLBACKS: tuple[str, ...] = ("passthrough", "reject", "warn")


class MissingAISignalError(RuntimeError):
    """Raised under fallback='reject' when a signal lookup misses."""


# ── internal data types ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _DaySignals:
    regime_multiplier: float
    tickers: dict[str, dict[str, Any]]


@dataclass
class _CoverageCounters:
    ticker_lookups: int = 0
    ticker_hits: int = 0
    regime_lookups: int = 0
    regime_hits: int = 0


T = TypeVar("T")


def _coerce_date_key(value: date | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        # accept YYYY-MM-DD or ISO datetime
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return value
    raise TypeError(f"unsupported date type: {type(value).__name__}")


def _default_ticker_of(candidate: Any) -> str:
    """Best-effort ticker extraction for heterogenous candidate shapes."""
    for attr in ("symbol", "ticker"):
        if hasattr(candidate, attr):
            return str(getattr(candidate, attr))
    if isinstance(candidate, Mapping):
        for key in ("symbol", "ticker"):
            if key in candidate:
                return str(candidate[key])
    if isinstance(candidate, tuple) and len(candidate) >= 2:
        # runner uses (score, symbol, snapshot, buy_result)
        if isinstance(candidate[1], str):
            return candidate[1]
    raise ValueError(
        "cannot infer ticker from candidate; "
        "pass ticker_of= to get_reranking(...)"
    )


# ── AISignalProvider ──────────────────────────────────────────────────────


class AISignalProvider:
    def __init__(
        self,
        cache_dir: str,
        *,
        mode: Mode = "disabled",
        veto_threshold: float = 0.75,
        delta_scale: float = 1.0,
        fallback: Fallback = "passthrough",
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(
                f"mode must be one of {_VALID_MODES}, got {mode!r}"
            )
        if fallback not in _VALID_FALLBACKS:
            raise ValueError(
                f"fallback must be one of {_VALID_FALLBACKS}, got {fallback!r}"
            )

        self._cache_dir = cache_dir
        self._mode: Mode = mode
        self._veto_threshold = float(veto_threshold)
        self._delta_scale = float(delta_scale)
        self._fallback: Fallback = fallback

        self._days: dict[str, _DaySignals] = {}
        self._coverage = _CoverageCounters()
        self._warned_keys: set[tuple[str, str]] = set()
        self._warn_lock = threading.Lock()

        if mode != "disabled":
            self._load_cache()

    # configuration accessors ──────────────────────────────────────────────
    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def veto_threshold(self) -> float:
        return self._veto_threshold

    @property
    def delta_scale(self) -> float:
        return self._delta_scale

    @property
    def fallback(self) -> Fallback:
        return self._fallback

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    @property
    def loaded_day_count(self) -> int:
        return len(self._days)

    # ── cache loading ─────────────────────────────────────────────────────
    def _load_cache(self) -> None:
        if not os.path.isdir(self._cache_dir):
            logger.debug(
                "ai_signal cache dir %s does not exist; no signals loaded",
                self._cache_dir,
            )
            return
        pattern = os.path.join(self._cache_dir, "*.json")
        for path in sorted(glob.glob(pattern)):
            day_key = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "ai_signal cache file %s unreadable: %s", path, exc
                )
                continue
            if not isinstance(payload, Mapping):
                logger.warning(
                    "ai_signal cache file %s has non-object root; skipped",
                    path,
                )
                continue
            raw_regime = payload.get("regime_multiplier", 1.0)
            try:
                regime = float(raw_regime)
            except (TypeError, ValueError):
                logger.warning(
                    "ai_signal %s has non-numeric regime_multiplier %r; using 1.0",
                    day_key,
                    raw_regime,
                )
                regime = 1.0
            raw_tickers = payload.get("tickers", {}) or {}
            tickers: dict[str, dict[str, Any]] = {}
            if isinstance(raw_tickers, Mapping):
                for ticker, row in raw_tickers.items():
                    if isinstance(row, Mapping):
                        tickers[str(ticker)] = dict(row)
            self._days[day_key] = _DaySignals(
                regime_multiplier=regime, tickers=tickers
            )

    # ── fallback handling ─────────────────────────────────────────────────
    def _handle_miss(
        self,
        *,
        day_key: str,
        ticker: str | None,
        neutral: T,
    ) -> T:
        if self._fallback == "reject":
            raise MissingAISignalError(
                f"no AI signal cached for date={day_key} ticker={ticker!r}"
            )
        if self._fallback == "warn":
            key = (day_key, ticker or "<regime>")
            # warn at most once per (date, ticker) per provider instance
            with self._warn_lock:
                if key not in self._warned_keys:
                    self._warned_keys.add(key)
                    logger.warning(
                        "ai_signal miss: date=%s ticker=%s (fallback=warn)",
                        day_key,
                        ticker,
                    )
        return neutral

    def _ticker_row(self, day_key: str, ticker: str) -> dict[str, Any] | None:
        day = self._days.get(day_key)
        if day is None:
            return None
        return day.tickers.get(ticker)

    # ── query API ─────────────────────────────────────────────────────────
    def get_score_delta(self, when: date | str, ticker: str) -> float:
        """Additive score adjustment. Neutral value: ``0.0``."""
        if self._mode not in ("score_delta", "combined"):
            return 0.0
        day_key = _coerce_date_key(when)
        self._coverage.ticker_lookups += 1
        row = self._ticker_row(day_key, ticker)
        if row is None:
            return self._handle_miss(
                day_key=day_key, ticker=ticker, neutral=0.0
            )
        self._coverage.ticker_hits += 1
        raw = row.get("delta", 0.0)
        try:
            delta = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return delta * self._delta_scale

    def get_veto(self, when: date | str, ticker: str) -> str | None:
        """Return a veto reason string, or ``None`` if not vetoed.

        A candidate is vetoed when the cache entry carries ``veto=True``
        AND ``veto_confidence >= veto_threshold``.
        """
        if self._mode not in ("veto", "combined"):
            return None
        day_key = _coerce_date_key(when)
        self._coverage.ticker_lookups += 1
        row = self._ticker_row(day_key, ticker)
        if row is None:
            return self._handle_miss(
                day_key=day_key, ticker=ticker, neutral=None
            )
        self._coverage.ticker_hits += 1
        if not bool(row.get("veto", False)):
            return None
        try:
            confidence = float(row.get("veto_confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self._veto_threshold:
            return None
        reason = row.get("veto_reason")
        return str(reason) if isinstance(reason, str) and reason else "ai_veto"

    def get_regime_multiplier(self, when: date | str) -> float:
        """Multiplicative regime adjustment. Neutral value: ``1.0``."""
        if self._mode not in ("regime", "combined"):
            return 1.0
        day_key = _coerce_date_key(when)
        self._coverage.regime_lookups += 1
        day = self._days.get(day_key)
        if day is None:
            return self._handle_miss(
                day_key=day_key, ticker=None, neutral=1.0
            )
        self._coverage.regime_hits += 1
        return float(day.regime_multiplier)

    def get_reranking(
        self,
        when: date | str,
        candidates: Sequence[T],
        *,
        ticker_of: Callable[[T], str] | None = None,
    ) -> list[T]:
        """Return ``candidates`` reordered by AI rank (ascending).

        Candidates with no cached rank fall to the end in their original
        order, preserving the caller's default tie-breaking.
        When mode does not include rerank, the input order is returned
        unchanged (a new list, so callers can mutate freely).
        """
        items = list(candidates)
        if self._mode not in ("rerank", "combined"):
            return items
        if not items:
            return items
        day_key = _coerce_date_key(when)
        extractor = ticker_of or _default_ticker_of
        day = self._days.get(day_key)
        if day is None:
            # treat as full miss — one lookup per candidate for coverage
            for _ in items:
                self._coverage.ticker_lookups += 1
            return self._handle_miss(
                day_key=day_key, ticker=None, neutral=items
            )

        ranked: list[tuple[int, int, T]] = []  # (rank, original_idx, item)
        for idx, item in enumerate(items):
            ticker = extractor(item)
            self._coverage.ticker_lookups += 1
            row = day.tickers.get(ticker)
            if row is None:
                # missing rank → push to the back, stable
                ranked.append((_LARGE_RANK + idx, idx, item))
                # fallback policy affects logging but cannot raise here
                # without breaking a partial hit — so we treat this as
                # per-row miss with the usual policy (silent passthrough
                # / warn / reject).
                if self._fallback == "reject":
                    raise MissingAISignalError(
                        f"no AI rank for date={day_key} ticker={ticker!r}"
                    )
                if self._fallback == "warn":
                    key = (day_key, ticker)
                    with self._warn_lock:
                        if key not in self._warned_keys:
                            self._warned_keys.add(key)
                            logger.warning(
                                "ai_signal rank miss: date=%s ticker=%s",
                                day_key,
                                ticker,
                            )
                continue
            self._coverage.ticker_hits += 1
            raw_rank = row.get("rank")
            try:
                rank_value = int(raw_rank) if raw_rank is not None else _LARGE_RANK + idx
            except (TypeError, ValueError):
                rank_value = _LARGE_RANK + idx
            ranked.append((rank_value, idx, item))

        ranked.sort(key=lambda triple: (triple[0], triple[1]))
        return [item for _rank, _idx, item in ranked]

    # ── metrics ───────────────────────────────────────────────────────────
    def coverage(self) -> dict[str, float | int]:
        """Report coverage metrics.

        ``signal_coverage_rate`` is computed across all ticker-level
        lookups (score_delta / veto / rerank). Regime lookups are tracked
        separately because there is exactly one per day.
        """
        ticker_lookups = self._coverage.ticker_lookups
        ticker_hits = self._coverage.ticker_hits
        regime_lookups = self._coverage.regime_lookups
        regime_hits = self._coverage.regime_hits
        rate = (ticker_hits / ticker_lookups) if ticker_lookups else 0.0
        return {
            "ticker_lookups": ticker_lookups,
            "ticker_hits": ticker_hits,
            "ticker_misses": ticker_lookups - ticker_hits,
            "regime_lookups": regime_lookups,
            "regime_hits": regime_hits,
            "regime_misses": regime_lookups - regime_hits,
            "signal_coverage_rate": round(rate, 6),
        }

    def reset_coverage(self) -> None:
        self._coverage = _CoverageCounters()
        self._warned_keys.clear()


# rank sentinel: any value large enough to push un-ranked items to the back
_LARGE_RANK = 10**9
