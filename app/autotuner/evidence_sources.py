"""Evidence-source bridge for autotuner proposals (post-Phase-4 Step 3).

Bridges the pre-existing research pipeline's real outputs into the autotuner's
evidence entries WITHOUT reimplementing generation/backtest (see
docs/live_autotuner_pipeline_reconciliation.md): a ``run_proposal_backtest``
evaluation dict becomes a ``backtest`` proxy-evidence entry, and a session-summary
dict becomes a ``live_log`` evidence entry. Read-only / proposal-generation only;
no broker calls, no runtime application. Thin file readers are size-bounded so a
huge/corrupt artifact cannot be slurped wholesale.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.autotuner.evidence import backtest_evidence, live_log_evidence

# Evidence artifacts (backtest evals, session summaries) are small JSON files.
# Cap the read so a runaway/corrupt artifact cannot be slurped wholesale on the
# proposal-generation path (the runtime hot path has its own 64 KiB cap).
_MAX_EVIDENCE_BYTES = 1024 * 1024


def _read_bounded_json(path: Path):
    if path.stat().st_size > _MAX_EVIDENCE_BYTES:
        raise ValueError(
            f"evidence artifact exceeds {_MAX_EVIDENCE_BYTES} bytes (refusing to read): {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_live_log_evidence(summary_path, *, source_id=None, generated_at=None):
    path = Path(summary_path)
    summary = _read_bounded_json(path)
    if not isinstance(summary, dict):
        raise ValueError(f"live-log summary is not an object: {path}")
    sid = source_id or f"ll_{summary.get('session_date') or path.stem}"
    gen = generated_at or datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    return live_log_summary_to_evidence(
        summary, source_id=sid, source_path=str(path), generated_at=gen
    )


def live_log_summary_to_evidence(summary, *, source_id, source_path, generated_at):
    """Map a session-summary dict into a ``live_log`` evidence entry."""
    data = summary if isinstance(summary, dict) else {}
    session_date = data.get("session_date", "unknown")
    text = (
        f"live session {session_date}: "
        f"cycles={data.get('cycles')}, buys={data.get('buys')}, sells={data.get('sells')}"
    )
    return live_log_evidence(
        source_id=source_id,
        source_path=source_path,
        generated_at=generated_at,
        summary=text,
    )


def load_backtest_evidence(eval_path, *, source_id=None, generated_at=None):
    """Read a run_proposal_backtest evaluation JSON and shape a backtest evidence."""
    path = Path(eval_path)
    result = _read_bounded_json(path)
    if not isinstance(result, dict):
        raise ValueError(f"backtest evaluation is not an object: {path}")
    if source_id is None:
        run_id = result.get("candidate_run_id") or result.get("proposal_id") or path.stem
        source_id = f"bt_{run_id}"
    if generated_at is None:
        generated_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    return backtest_result_to_evidence(
        result,
        source_id=source_id,
        source_path=str(path),
        generated_at=generated_at,
    )


def backtest_result_to_evidence(eval_result, *, source_id, source_path, generated_at):
    """Map a run_proposal_backtest evaluation dict into a ``backtest`` evidence entry."""
    result = eval_result if isinstance(eval_result, dict) else {}
    # Accept both the per-evaluation shape (top-level verdict/deltas) and the real
    # aggregate file run_proposal_backtest writes ({evaluations:[...],
    # overall_verdict}): fall back to overall_verdict + the first evaluation's deltas.
    first_eval = {}
    evaluations = result.get("evaluations")
    if isinstance(evaluations, list) and evaluations and isinstance(evaluations[0], dict):
        first_eval = evaluations[0]
    verdict = str(
        result.get("verdict")
        or result.get("overall_verdict")
        or first_eval.get("verdict")
        or "unknown"
    )
    deltas = result.get("deltas") or first_eval.get("deltas") or {}
    summary = f"backtest verdict={verdict}; sharpe delta={deltas.get('sharpe')}"
    confidence = "medium" if verdict == "pass" else "low"
    # E1: carry the eval's provenance (source/repo_head/data_window) so the proxy
    # origin is auditable on the evidence entry itself.
    provenance = result.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else None
    return backtest_evidence(
        source_id=source_id,
        source_path=source_path,
        generated_at=generated_at,
        summary=summary,
        confidence=confidence,
        provenance=provenance,
    )
