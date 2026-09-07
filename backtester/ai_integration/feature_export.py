"""Candidate feature export + look-ahead audit primitives.

`CandidateFeatureExporter` writes one JSONL record per scored candidate
observed during a backtest's buy pass. Records contain the exact feature
values that fed `calculate_selection_score` — no recomputation.

`FeatureAuditMixin` (+ `LookAheadBiasError`) enforces that every feature's
`max_timestamp` precedes the decision timestamp. Since the live/backtest
engine currently derives features from the same-day snapshot and the
decision also lands on that day, equality is permitted by default and
`<` is used only when `strict_less_than=True` is requested explicitly.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import IO, Any, Iterable, Mapping

logger = logging.getLogger(__name__)


class LookAheadBiasError(RuntimeError):
    """Raised when a feature's max timestamp is at/after the decision time.

    Hard failure — this represents a correctness bug in the feature
    pipeline and should never be caught silently during backtests.
    """


# ── Feature audit ─────────────────────────────────────────────────────────

_TimestampLike = datetime | date | str


def _coerce_timestamp(value: _TimestampLike) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        # accept YYYY-MM-DD or full ISO
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {value!r}") from exc
    raise TypeError(f"unsupported timestamp type: {type(value).__name__}")


class FeatureAuditMixin:
    """Audit helper: verify feature timestamps never leak forward.

    Usage:
        auditor = FeatureAuditMixin()
        auditor.audit_features(
            decision_timestamp=trading_date,
            feature_timestamps={"pullback_pct": snapshot_date, ...},
            strict_less_than=False,
        )
    """

    def audit_features(
        self,
        *,
        decision_timestamp: _TimestampLike,
        feature_timestamps: Mapping[str, _TimestampLike],
        strict_less_than: bool = False,
    ) -> None:
        decision_ts = _coerce_timestamp(decision_timestamp)
        for name, raw_ts in feature_timestamps.items():
            feat_ts = _coerce_timestamp(raw_ts)
            if strict_less_than:
                leaked = feat_ts >= decision_ts
                bound = "<"
            else:
                leaked = feat_ts > decision_ts
                bound = "<="
            if leaked:
                raise LookAheadBiasError(
                    f"feature {name!r} timestamp {feat_ts.isoformat()} "
                    f"violates {bound} decision {decision_ts.isoformat()}"
                )


# ── Candidate export ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateFeatureRecord:
    """One exported candidate row. Kept flat to keep JSONL simple."""

    date: str            # YYYY-MM-DD (decision date)
    ticker: str
    base_score: float    # exact output from calculate_selection_score
    features: dict[str, float]  # full score_components dict — same reference
    decision: str        # "approved" | "rejected"
    decision_reason: str | None
    rule_gate_passed: bool
    score_gate_passed: bool
    candidate_rank: int | None
    executed: bool

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "ticker": self.ticker,
            "base_score": self.base_score,
            "features": dict(self.features),
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "rule_gate_passed": self.rule_gate_passed,
            "score_gate_passed": self.score_gate_passed,
            "candidate_rank": self.candidate_rank,
            "executed": self.executed,
        }


class CandidateFeatureExporter(FeatureAuditMixin):
    """Append-only JSONL writer for candidate feature snapshots.

    The exporter is intentionally passive — the backtester calls `record(...)`
    after base-score computation; no state is mutated elsewhere. When the
    exporter is not supplied to `run_backtest`, the backtester is bit-for-bit
    unchanged.

    Parameters
    ----------
    output_path:
        JSONL file path. Parent directories are created on first write.
    audit:
        If True, run the look-ahead audit on every record.
    strict_less_than:
        If True, require feature_ts < decision_ts strictly (else <=).
        Defaults to False because features currently derive from the
        same-day snapshot.
    """

    def __init__(
        self,
        output_path: str,
        *,
        audit: bool = True,
        strict_less_than: bool = False,
    ) -> None:
        self._output_path = output_path
        self._audit_enabled = bool(audit)
        self._strict = bool(strict_less_than)
        self._fh: IO[str] | None = None
        self._lock = threading.Lock()
        self._count = 0

    # lifecycle ────────────────────────────────────────────────────────────
    def _ensure_open(self) -> IO[str]:
        if self._fh is None:
            parent = os.path.dirname(self._output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._fh = open(self._output_path, "a", encoding="utf-8")
        return self._fh

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                finally:
                    self._fh.close()
                self._fh = None

    def __enter__(self) -> "CandidateFeatureExporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def record_count(self) -> int:
        return self._count

    # recording ────────────────────────────────────────────────────────────
    def record(
        self,
        *,
        trading_date: date,
        ticker: str,
        base_score: float,
        score_components: Mapping[str, float],
        decision: str,
        decision_reason: str | None = None,
        rule_gate_passed: bool = True,
        score_gate_passed: bool = True,
        candidate_rank: int | None = None,
        executed: bool = False,
        feature_timestamps: Mapping[str, _TimestampLike] | None = None,
    ) -> None:
        if decision not in ("approved", "rejected"):
            raise ValueError(
                f"decision must be 'approved' or 'rejected', got {decision!r}"
            )
        if self._audit_enabled and feature_timestamps:
            self.audit_features(
                decision_timestamp=trading_date,
                feature_timestamps=feature_timestamps,
                strict_less_than=self._strict,
            )

        record = CandidateFeatureRecord(
            date=trading_date.isoformat(),
            ticker=ticker,
            base_score=float(base_score),
            features={str(k): float(v) for k, v in score_components.items()},
            decision=decision,
            decision_reason=decision_reason,
            rule_gate_passed=bool(rule_gate_passed),
            score_gate_passed=bool(score_gate_passed),
            candidate_rank=(None if candidate_rank is None else int(candidate_rank)),
            executed=bool(executed),
        )
        line = json.dumps(record.to_json_obj(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            fh = self._ensure_open()
            fh.write(line)
            fh.write("\n")
            self._count += 1

    def record_many(self, records: Iterable[Mapping[str, Any]]) -> None:
        for kwargs in records:
            self.record(**kwargs)
