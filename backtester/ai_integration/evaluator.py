"""Offline evaluation of AI signal quality against a baseline backtest.

`AIEvaluator` consumes:

* ``baseline_result`` — a `BacktestResult` run with ``ai_provider=None``
* ``ai_result``       — a `BacktestResult` run with the AI provider active
* ``ai_provider``     — the provider used for the AI run (for coverage
                         and veto introspection)
* ``forward_returns`` — ground-truth forward returns indexed by
                         ``date_iso → ticker → pct_return``

and computes six metrics:

* ``sharpe_delta``          — annualized Sharpe(AI) − Sharpe(baseline)
* ``veto_precision``        — share of AI vetoes that were true losers
* ``veto_recall``           — share of true losers the AI vetoed
* ``ndcg_at_3``             — rank quality against ideal ranking by return
* ``score_separation_ks``   — KS distance between AI-delta distributions
                              of forward-winners vs forward-losers
* ``signal_coverage_rate``  — provider's observed hit/lookup ratio

The module depends only on the standard library.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Iterable, Mapping

from backtester.ai_integration.provider import (
    AISignalProvider,
    _coerce_date_key,
)

if TYPE_CHECKING:
    from backtester.engine_backtest.runner import BacktestResult


__all__ = ["AIEvaluator", "AIEvaluation", "sharpe_ratio"]


# Modes in which each signal type is actually applied during the backtest run.
# A metric that reads signal X should only be computed when mode ∈ _X_MODES;
# otherwise the signal was never applied and the metric is meaningless.
_VETO_MODES: frozenset[str] = frozenset({"veto", "combined"})
_RANK_MODES: frozenset[str] = frozenset({"rerank", "combined"})
_DELTA_MODES: frozenset[str] = frozenset({"score_delta", "combined"})


# ── helpers ───────────────────────────────────────────────────────────────


def _equity_to_returns(equity_curve: Iterable[tuple[date, int]]) -> list[float]:
    values = [int(v) for _, v in equity_curve]
    returns: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev <= 0:
            continue
        returns.append((values[i] - prev) / prev)
    return returns


def sharpe_ratio(
    equity_curve: Iterable[tuple[date, int]],
    *,
    trading_days_per_year: int = 252,
) -> float:
    """Annualized Sharpe ratio from a portfolio-value equity curve.

    Returns 0.0 when there are fewer than 2 return observations or the
    return series is degenerate (zero variance).
    """
    returns = _equity_to_returns(equity_curve)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return mean / std * math.sqrt(trading_days_per_year)


def _ks_statistic(a: list[float], b: list[float]) -> float:
    """Two-sample Kolmogorov–Smirnov D-statistic.

    Implemented from scratch to avoid a scipy dependency. Returns a
    value in ``[0, 1]``; higher = more separated distributions.
    """
    if not a or not b:
        return 0.0
    a_sorted = sorted(a)
    b_sorted = sorted(b)
    na, nb = len(a_sorted), len(b_sorted)
    i = j = 0
    ca = cb = 0.0
    d_max = 0.0
    # walk the merged sorted axis, updating each ECDF at its step points
    while i < na and j < nb:
        ai, bj = a_sorted[i], b_sorted[j]
        if ai <= bj:
            i += 1
            ca = i / na
            # consume duplicates so we only evaluate diff at the right point
            while i < na and a_sorted[i] == ai:
                i += 1
                ca = i / na
        if bj <= ai:
            j += 1
            cb = j / nb
            while j < nb and b_sorted[j] == bj:
                j += 1
                cb = j / nb
        d_max = max(d_max, abs(ca - cb))
    # tail
    while i < na:
        i += 1
        ca = i / na
        d_max = max(d_max, abs(ca - cb))
    while j < nb:
        j += 1
        cb = j / nb
        d_max = max(d_max, abs(ca - cb))
    return d_max


def _dcg(relevances: list[float]) -> float:
    # Standard DCG with log2(i+2) discount (positions are 1-indexed).
    return sum(
        rel / math.log2(idx + 2) for idx, rel in enumerate(relevances)
    )


def _ndcg_at_k(
    predicted_order: list[str],
    relevance_map: Mapping[str, float],
    k: int,
) -> float | None:
    """Return NDCG@k, or None if IDCG is 0 (no positive relevance)."""
    if not predicted_order or k <= 0:
        return None
    top_k = predicted_order[:k]
    pred_rels = [max(relevance_map.get(t, 0.0), 0.0) for t in top_k]
    ideal = sorted(
        (max(relevance_map.get(t, 0.0), 0.0) for t in predicted_order),
        reverse=True,
    )[:k]
    idcg = _dcg(ideal)
    if idcg == 0.0:
        return None
    return _dcg(pred_rels) / idcg


# ── public result ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AIEvaluation:
    sharpe_baseline: float
    sharpe_ai: float
    sharpe_delta: float
    veto_precision: float | None
    veto_recall: float | None
    veto_true_positives: int
    veto_false_positives: int
    veto_false_negatives: int
    ndcg_at_3: float | None
    score_separation_ks: float | None
    signal_coverage_rate: float
    n_days_evaluated: int
    active_mode: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "sharpe_baseline": self.sharpe_baseline,
            "sharpe_ai": self.sharpe_ai,
            "sharpe_delta": self.sharpe_delta,
            "veto_precision": self.veto_precision,
            "veto_recall": self.veto_recall,
            "veto_true_positives": self.veto_true_positives,
            "veto_false_positives": self.veto_false_positives,
            "veto_false_negatives": self.veto_false_negatives,
            "ndcg_at_3": self.ndcg_at_3,
            "score_separation_ks": self.score_separation_ks,
            "signal_coverage_rate": self.signal_coverage_rate,
            "n_days_evaluated": self.n_days_evaluated,
            "active_mode": self.active_mode,
        }


# ── evaluator ─────────────────────────────────────────────────────────────


class AIEvaluator:
    """Compute Step 5 metrics comparing baseline vs AI-augmented runs."""

    def __init__(
        self,
        *,
        baseline_result: "BacktestResult",
        ai_result: "BacktestResult",
        ai_provider: AISignalProvider,
        forward_returns: Mapping[str | date, Mapping[str, float]],
        loser_threshold_pct: float = 0.0,
        ndcg_k: int = 3,
        trading_days_per_year: int = 252,
    ) -> None:
        self._baseline = baseline_result
        self._ai = ai_result
        self._provider = ai_provider
        self._loser_thresh = float(loser_threshold_pct)
        self._ndcg_k = int(ndcg_k)
        self._trading_days = int(trading_days_per_year)
        # normalize keys to date_iso strings
        self._forward: dict[str, dict[str, float]] = {}
        for raw_day, ticker_returns in forward_returns.items():
            day_key = _coerce_date_key(raw_day)
            normed: dict[str, float] = {}
            for ticker, ret in ticker_returns.items():
                try:
                    normed[str(ticker)] = float(ret)
                except (TypeError, ValueError):
                    continue
            self._forward[day_key] = normed

    # ── metric pieces ─────────────────────────────────────────────────────
    def _compute_sharpes(self) -> tuple[float, float]:
        sb = sharpe_ratio(
            self._baseline.equity_curve,
            trading_days_per_year=self._trading_days,
        )
        sa = sharpe_ratio(
            self._ai.equity_curve,
            trading_days_per_year=self._trading_days,
        )
        return sb, sa

    def _compute_veto_stats(
        self,
    ) -> tuple[float | None, float | None, int, int, int]:
        # Veto signal is only applied during backtest runs whose mode includes
        # veto. For other modes (score_delta, rerank, regime) the veto fields
        # exist in the cache but were never consulted; computing precision/
        # recall from them would be misleading.
        if self._provider.mode not in _VETO_MODES:
            return None, None, 0, 0, 0
        tp = fp = fn = 0
        threshold = self._provider.veto_threshold
        for day_key, day_signals in self._provider._days.items():
            day_returns = self._forward.get(day_key, {})
            for ticker, row in day_signals.tickers.items():
                forward_ret = day_returns.get(ticker)
                is_vetoed = (
                    bool(row.get("veto", False))
                    and float(row.get("veto_confidence", 0.0)) >= threshold
                )
                # We can only judge a veto's correctness if we have ground truth.
                if forward_ret is None:
                    continue
                is_loser = forward_ret < self._loser_thresh
                if is_vetoed and is_loser:
                    tp += 1
                elif is_vetoed and not is_loser:
                    fp += 1
                elif (not is_vetoed) and is_loser:
                    fn += 1
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        return precision, recall, tp, fp, fn

    def _compute_ndcg(self) -> tuple[float | None, int]:
        # Rank signal is only applied during rerank / combined runs.
        # For other modes the rank fields exist in the cache but tickers were
        # never reordered; NDCG computed from them would not reflect the actual
        # selection order that drove the backtest P&L.
        if self._provider.mode not in _RANK_MODES:
            return None, 0
        per_day_scores: list[float] = []
        for day_key, day_signals in self._provider._days.items():
            day_returns = self._forward.get(day_key)
            if not day_returns:
                continue
            # Predicted order: ascending rank, then ticker for stability.
            # Tickers without rank fall to the back (huge sentinel).
            ranked_pairs = []
            for ticker, row in day_signals.tickers.items():
                if ticker not in day_returns:
                    continue
                raw_rank = row.get("rank")
                try:
                    rank = int(raw_rank) if raw_rank is not None else 10**9
                except (TypeError, ValueError):
                    rank = 10**9
                ranked_pairs.append((rank, ticker))
            if not ranked_pairs:
                continue
            ranked_pairs.sort()
            predicted = [t for _, t in ranked_pairs]
            rel_map = {t: day_returns[t] for _, t in ranked_pairs}
            score = _ndcg_at_k(predicted, rel_map, self._ndcg_k)
            if score is not None:
                per_day_scores.append(score)
        if not per_day_scores:
            return None, 0
        return sum(per_day_scores) / len(per_day_scores), len(per_day_scores)

    def _compute_score_separation(self) -> float | None:
        # Delta signal is only applied during score_delta / combined runs.
        # For other modes (veto, rerank, regime) the delta fields exist in the
        # cache but were never added to selection scores; the KS statistic
        # would measure signal quality that had no effect on the backtest.
        if self._provider.mode not in _DELTA_MODES:
            return None
        winner_deltas: list[float] = []
        loser_deltas: list[float] = []
        for day_key, day_signals in self._provider._days.items():
            day_returns = self._forward.get(day_key)
            if not day_returns:
                continue
            for ticker, row in day_signals.tickers.items():
                ret = day_returns.get(ticker)
                if ret is None:
                    continue
                try:
                    delta = float(row.get("delta", 0.0))
                except (TypeError, ValueError):
                    continue
                if ret >= self._loser_thresh:
                    winner_deltas.append(delta)
                else:
                    loser_deltas.append(delta)
        if not winner_deltas or not loser_deltas:
            return None
        return _ks_statistic(winner_deltas, loser_deltas)

    # ── public API ────────────────────────────────────────────────────────
    def evaluate(self) -> AIEvaluation:
        sb, sa = self._compute_sharpes()
        precision, recall, tp, fp, fn = self._compute_veto_stats()
        ndcg, n_days = self._compute_ndcg()
        ks = self._compute_score_separation()
        coverage = self._provider.coverage().get("signal_coverage_rate", 0.0)
        return AIEvaluation(
            sharpe_baseline=sb,
            sharpe_ai=sa,
            sharpe_delta=sa - sb,
            veto_precision=precision,
            veto_recall=recall,
            veto_true_positives=tp,
            veto_false_positives=fp,
            veto_false_negatives=fn,
            ndcg_at_3=ndcg,
            score_separation_ks=ks,
            signal_coverage_rate=float(coverage),
            n_days_evaluated=n_days,
            active_mode=self._provider.mode,
        )
