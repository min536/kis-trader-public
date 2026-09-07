"""Offline KR gate2 v2 rescorer (BP-6 / E5 S1).

Read-only research leaf: replays recorded runtime ``score_components`` (v1)
through the promoted v2 pipeline and diffs the v2 selection against the recorded
v1 selection. It does **not** switch the runtime scorer (operator gate) — it only
produces the comparison material for the adoption brief.

Pipeline per record: ``condition_scores_from_v1`` (adapter) ->
``weighted_gate2_score`` -> ``passes_threshold``. Input records are treated as
immutable: the rescorer never writes back into the supplied mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.gate2.adapter import condition_scores_from_v1
from app.gate2.schema import ScoreV2Artifact
from app.gate2.score_v2 import passes_threshold, weighted_gate2_score


@dataclass(frozen=True)
class RescoreOutcome:
    symbol: str
    v1_selected: bool
    v2_final_score: float
    v2_selected: bool
    agrees: bool


@dataclass(frozen=True)
class RescoreDiff:
    total: int
    agree_count: int
    v1_only: tuple[str, ...]
    v2_only: tuple[str, ...]
    outcomes: tuple[RescoreOutcome, ...]


def rescore_records(
    records: Iterable[Mapping[str, Any]],
    *,
    artifact: ScoreV2Artifact,
) -> RescoreDiff:
    """Rescore recorded candidates with ``artifact`` and diff vs recorded v1.

    Each record carries ``symbol`` (str), ``v1_selected`` (bool), and
    ``score_components`` (Mapping of recorded v1 components). Hostile records
    (non-Mapping, missing/blank symbol, non-Mapping components) are skipped
    rather than raising.
    """
    outcomes: list[RescoreOutcome] = []
    v1_only: list[str] = []
    v2_only: list[str] = []
    agree_count = 0

    for record in records:
        if not isinstance(record, Mapping):
            continue
        symbol = str(record.get("symbol", "")).strip()
        if not symbol:
            continue
        components = record.get("score_components")
        if not isinstance(components, Mapping):
            continue

        # Read-only: pass the recorded mapping straight through; the adapter and
        # scorer only read from it.
        v2_scores = condition_scores_from_v1(components, artifact.normalization_caps)
        result = weighted_gate2_score(v2_scores, artifact.weights)
        v2_selected = passes_threshold(result, artifact)
        v1_selected = bool(record.get("v1_selected", False))
        agrees = v1_selected == v2_selected
        if agrees:
            agree_count += 1
        elif v1_selected and not v2_selected:
            v1_only.append(symbol)
        else:
            v2_only.append(symbol)

        outcomes.append(
            RescoreOutcome(
                symbol=symbol,
                v1_selected=v1_selected,
                v2_final_score=result.final_score,
                v2_selected=v2_selected,
                agrees=agrees,
            )
        )

    return RescoreDiff(
        total=len(outcomes),
        agree_count=agree_count,
        v1_only=tuple(v1_only),
        v2_only=tuple(v2_only),
        outcomes=tuple(outcomes),
    )
