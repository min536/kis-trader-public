import json
from pathlib import Path
from typing import Any, Iterable

from app.auth.account_scope import get_partitioned_log_path
from app.core.time_utils import get_korean_now


def _partition_suffix_from_ts(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    return get_korean_now().strftime("%Y%m%d")


def _jsonl_path(prefix: str, *, ts: object = None) -> Path:
    suffix = _partition_suffix_from_ts(ts)
    return get_partitioned_log_path(prefix, suffix=suffix)


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> bool:
    materialized = [row for row in rows if isinstance(row, dict)]
    if not materialized:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in materialized:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
        return True
    except OSError:
        return False


def _with_candidate_outcome_aliases(row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    if "stage" not in enriched and "stage_reached" in enriched:
        enriched["stage"] = enriched.get("stage_reached")
    if "stage_reached" not in enriched and "stage" in enriched:
        enriched["stage_reached"] = enriched.get("stage")
    if "outcome" not in enriched and "selection_outcome" in enriched:
        enriched["outcome"] = enriched.get("selection_outcome")
    if "selection_outcome" not in enriched and "outcome" in enriched:
        enriched["selection_outcome"] = enriched.get("outcome")
    return enriched


def append_candidate_outcomes(
    rows: Iterable[dict[str, Any]],
    *,
    ts: object = None,
) -> bool:
    return _append_jsonl(
        _jsonl_path("candidate_outcomes", ts=ts),
        (_with_candidate_outcome_aliases(row) for row in rows),
    )


def append_cycle_stats(
    row: dict[str, Any],
    *,
    ts: object = None,
) -> bool:
    if not isinstance(row, dict):
        return False
    return _append_jsonl(
        _jsonl_path("cycle_stats", ts=ts),
        [row],
    )
