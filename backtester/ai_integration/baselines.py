"""Synthetic AI providers for validation, upper-bound, and noise tests.

All three baselines are drop-in substitutes for `AISignalProvider` — they
reuse the parent's mode gating, fallback handling, threshold logic, and
coverage accounting, but populate the day-cache programmatically instead
of reading JSON files.

* `PerfectAIProvider`  — **LOOK-AHEAD.** Converts known T+5 returns into
  deltas / ranks / vetoes. For upper-bound experiments ONLY — must never
  be used against live or out-of-sample data.
* `RandomAIProvider`   — Deterministic noise. Useful as a lower bound.
* `RuleProxyAIProvider` — Hand-written RSI/momentum rule. Validates the
  integration pipeline end-to-end with a transparent signal source.
"""
from __future__ import annotations

import hashlib
import logging
import random
from datetime import date
from typing import Iterable, Mapping

from backtester.ai_integration.provider import (
    AISignalProvider,
    Fallback,
    Mode,
    _DaySignals,
    _coerce_date_key,
)

logger = logging.getLogger(__name__)


__all__ = [
    "PerfectAIProvider",
    "RandomAIProvider",
    "RuleProxyAIProvider",
]


# ── PerfectAIProvider ─────────────────────────────────────────────────────


class PerfectAIProvider(AISignalProvider):
    """LOOK-AHEAD baseline — never use against unseen data.

    Given a per-day mapping of forward returns (``future_returns_cache``
    is the backtest's T+5 OHLCV-derived return in percent), synthesizes:

    * ``delta``     = future_return_pct (winners get boosted directly)
    * ``rank``      = 1 for the highest-return ticker of the day, etc.
    * ``veto``      = True iff future_return_pct < ``veto_cutoff_pct``;
                      ``veto_confidence`` is forced to 1.0 so the veto
                      threshold on the base provider still applies.

    The constructor emits a hard warning so look-ahead usage can never
    be silent.
    """

    LOOK_AHEAD: bool = True

    def __init__(
        self,
        future_returns_cache: Mapping[str | date, Mapping[str, float]],
        *,
        mode: Mode = "combined",
        veto_threshold: float = 0.75,
        delta_scale: float = 1.0,
        fallback: Fallback = "passthrough",
        veto_cutoff_pct: float = -2.0,
        regime_multiplier: float = 1.0,
    ) -> None:
        super().__init__(
            cache_dir="",
            mode=mode,
            veto_threshold=veto_threshold,
            delta_scale=delta_scale,
            fallback=fallback,
        )
        logger.warning(
            "PerfectAIProvider instantiated — this is a LOOK-AHEAD baseline. "
            "Use only for upper-bound experiments, never for live or OOS runs."
        )
        self._veto_cutoff_pct = float(veto_cutoff_pct)
        self._populate(future_returns_cache, regime_multiplier)

    def _populate(
        self,
        future_returns_cache: Mapping[str | date, Mapping[str, float]],
        regime_multiplier: float,
    ) -> None:
        for raw_day, ticker_returns in future_returns_cache.items():
            day_key = _coerce_date_key(raw_day)
            # stable rank order: highest future return = rank 1
            sorted_pairs = sorted(
                ticker_returns.items(),
                key=lambda kv: (-float(kv[1]), kv[0]),
            )
            rank_by_ticker = {
                ticker: idx + 1 for idx, (ticker, _) in enumerate(sorted_pairs)
            }
            tickers_dict: dict[str, dict[str, object]] = {}
            for ticker, raw_ret in ticker_returns.items():
                try:
                    ret = float(raw_ret)
                except (TypeError, ValueError):
                    continue
                is_loser = ret < self._veto_cutoff_pct
                tickers_dict[str(ticker)] = {
                    "delta": ret,
                    "veto": bool(is_loser),
                    "veto_confidence": 1.0 if is_loser else 0.0,
                    "veto_reason": "future_loss" if is_loser else None,
                    "rank": rank_by_ticker[ticker],
                }
            self._days[day_key] = _DaySignals(
                regime_multiplier=float(regime_multiplier),
                tickers=tickers_dict,
            )


# ── RandomAIProvider ──────────────────────────────────────────────────────


def _stable_seed(seed: int, day_key: str) -> int:
    """Build a process-stable integer seed from (seed, day_key).

    ``random.Random(string)`` relies on ``hash()``, which is randomized
    per interpreter run when ``PYTHONHASHSEED`` is unset. We use SHA-256
    instead to keep determinism across processes and test runs.
    """
    digest = hashlib.sha256(f"{seed}:{day_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class RandomAIProvider(AISignalProvider):
    """Deterministic random signals — same ``seed`` + inputs ⇒ same output.

    Produces noise-level deltas, random vetoes at ``veto_probability``,
    and a shuffled per-day rank. Useful as a lower-bound baseline and
    for testing that downstream components do not accidentally depend
    on signal quality.
    """

    def __init__(
        self,
        dates: Iterable[str | date],
        tickers: Iterable[str],
        *,
        seed: int = 1337,
        delta_range: float = 3.0,
        veto_probability: float = 0.1,
        regime_range: tuple[float, float] = (0.8, 1.2),
        mode: Mode = "combined",
        veto_threshold: float = 0.75,
        delta_scale: float = 1.0,
        fallback: Fallback = "passthrough",
    ) -> None:
        super().__init__(
            cache_dir="",
            mode=mode,
            veto_threshold=veto_threshold,
            delta_scale=delta_scale,
            fallback=fallback,
        )
        self._seed = int(seed)
        self._delta_range = float(delta_range)
        self._veto_probability = float(veto_probability)
        self._regime_lo, self._regime_hi = (
            float(regime_range[0]),
            float(regime_range[1]),
        )
        self._populate(dates, tickers)

    def _populate(
        self, dates: Iterable[str | date], tickers: Iterable[str]
    ) -> None:
        ticker_list = list(dict.fromkeys(str(t) for t in tickers))  # preserve order, de-dup
        for raw_day in dates:
            day_key = _coerce_date_key(raw_day)
            rng = random.Random(_stable_seed(self._seed, day_key))
            ranks = list(range(1, len(ticker_list) + 1))
            rng.shuffle(ranks)
            tickers_dict: dict[str, dict[str, object]] = {}
            for idx, ticker in enumerate(ticker_list):
                is_veto = rng.random() < self._veto_probability
                tickers_dict[ticker] = {
                    "delta": rng.uniform(-self._delta_range, self._delta_range),
                    "veto": bool(is_veto),
                    "veto_confidence": rng.uniform(0.75, 1.0) if is_veto else 0.0,
                    "veto_reason": "random" if is_veto else None,
                    "rank": ranks[idx],
                }
            regime = rng.uniform(self._regime_lo, self._regime_hi)
            self._days[day_key] = _DaySignals(
                regime_multiplier=regime,
                tickers=tickers_dict,
            )


# ── RuleProxyAIProvider ───────────────────────────────────────────────────


class RuleProxyAIProvider(AISignalProvider):
    """Hand-written RSI / momentum proxy for pipeline validation only.

    Rules (per spec):
        RSI > 65                     ⇒ delta = -5
        RSI < 45 and momentum > 0    ⇒ delta = +5
        otherwise                    ⇒ delta =  0

    ``features_cache`` shape: ``date_iso → ticker → {"rsi": float, "momentum": float}``.
    This provider sets no vetoes, no ranks, and a neutral regime — the
    point is to validate the score-delta wire, not to generate alpha.
    """

    def __init__(
        self,
        features_cache: Mapping[str | date, Mapping[str, Mapping[str, float]]],
        *,
        mode: Mode = "score_delta",
        veto_threshold: float = 0.75,
        delta_scale: float = 1.0,
        fallback: Fallback = "passthrough",
        rsi_high: float = 65.0,
        rsi_low: float = 45.0,
    ) -> None:
        super().__init__(
            cache_dir="",
            mode=mode,
            veto_threshold=veto_threshold,
            delta_scale=delta_scale,
            fallback=fallback,
        )
        self._rsi_high = float(rsi_high)
        self._rsi_low = float(rsi_low)
        self._populate(features_cache)

    def _populate(
        self,
        features_cache: Mapping[str | date, Mapping[str, Mapping[str, float]]],
    ) -> None:
        for raw_day, ticker_feats in features_cache.items():
            day_key = _coerce_date_key(raw_day)
            tickers_dict: dict[str, dict[str, object]] = {}
            for ticker, feats in ticker_feats.items():
                try:
                    rsi = float(feats.get("rsi", 50.0))
                    momentum = float(feats.get("momentum", 0.0))
                except (TypeError, ValueError):
                    continue
                if rsi > self._rsi_high:
                    delta = -5.0
                elif rsi < self._rsi_low and momentum > 0.0:
                    delta = 5.0
                else:
                    delta = 0.0
                tickers_dict[str(ticker)] = {"delta": delta}
            self._days[day_key] = _DaySignals(
                regime_multiplier=1.0, tickers=tickers_dict,
            )
