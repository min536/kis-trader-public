"""Aggregate **autotuner-domain** backtest evals into a candidate-suggest screening.

``evals_to_screening`` reads eval JSONs in the aggregate shape
``{evaluations: [{changes: [...], verdict}], ...}`` and emits the screening shape
``suggest_candidates`` consumes: ``{parameter, to_value, verdict, backtest_eval}``
(+ E1 provenance). The verdict is the eval's own — source of truth, not
operator-set — so a downstream suggestion can never claim a pass the backtest did
not produce. Read-only / offline: bounded reads, never runs the backtester, never
applies anything; ``suggest_candidates`` still drops any non-pass / non-whitelisted
/ out-of-bounds move and the human gate is unchanged.

**Domain boundary (important).** This screener consumes only the *autotuner
operational-cadence* domain, where each change is ``{parameter, to_value}`` (e.g.
``buy_scan_shallow_top_k``). It is **disjoint** from the strategy/research domain
that ``app/tools/run_proposal_backtest.py`` evaluates — those evals carry
``{family, param_id, new_value}`` (RSI etc.) and intentionally produce **no**
screening entries (no ``parameter`` key to read). See
``docs/live_autotuner_pipeline_reconciliation.md``. The producer of
autotuner-domain evals is ``app.tools.autotuner_operational_backtest``, not the
research backtester; force-mapping RSI moves onto operational params would be
meaningless and is deliberately not done.
"""

from __future__ import annotations

import json
from pathlib import Path

_MAX_EVAL_BYTES = 1024 * 1024


def evals_to_screening(eval_paths):
    screening: list[dict] = []
    for raw in eval_paths:
        path = Path(raw)
        eval_obj = _read_bounded_eval(path)
        if not isinstance(eval_obj, dict):
            continue  # unreadable/oversized/malformed eval -> skip (never raise)
        # run_proposal_backtest writes a top-level `evaluations` array; each
        # evaluation carries its own `changes` + `verdict`. Flatten one screening
        # entry per change, tagged with that evaluation's verdict.
        # E1 provenance: run-wide source/repo_head/generated_at from the top-level
        # `provenance` block, strategy_family/data_window per evaluation.
        provenance = eval_obj.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        evaluations = eval_obj.get("evaluations")
        if not isinstance(evaluations, list):
            continue
        for evaluation in evaluations:
            if not isinstance(evaluation, dict):
                continue
            verdict = evaluation.get("verdict")
            # W1 walk-forward: a pass the holdout window contradicts is downgraded to
            # the non-pass state the producer flagged (e.g. ``inconclusive``). This is
            # conservative-only — it can shrink the suggestion set, never promote a
            # non-pass to pass. Evals without reconciliation are untouched.
            reconciliation = evaluation.get("verdict_reconciliation")
            if isinstance(reconciliation, dict) and verdict == "pass":
                downgrade_to = reconciliation.get("downgrade_to")
                if downgrade_to:
                    verdict = downgrade_to
            changes = evaluation.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                parameter = change.get("parameter")
                if not isinstance(parameter, str):
                    continue
                screening.append(
                    {
                        "parameter": parameter,
                        "to_value": change.get("to_value"),
                        "verdict": verdict,
                        "backtest_eval": str(path),
                        "repo_head": provenance.get("repo_head"),
                        "generated_at": provenance.get("generated_at"),
                        "strategy_family": evaluation.get("strategy_family"),
                        "data_window": evaluation.get("data_window"),
                    }
                )
    return screening


def _read_bounded_eval(path: Path):
    try:
        if path.stat().st_size > _MAX_EVAL_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
