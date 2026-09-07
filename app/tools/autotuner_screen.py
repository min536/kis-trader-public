"""CLI: autotuner_screen -- Phase A backtest->screening aggregator.

Globs a directory of ``run_proposal_backtest`` eval JSONs (each self-describing:
it carries the proposal ``changes`` + its own ``verdict``) and emits a
``screening.json`` of ``{parameter, to_value, verdict, backtest_eval}`` entries.
That screening then feeds the human-gated ``autotuner_suggest`` tick:

    python -m app.tools.autotuner_screen --evals-dir research/evaluations --out screening.json
    python -m app.tools.autotuner_suggest --screening screening.json --baseline baseline.json \
        --date 20260607 --out plan.json

AGGREGATES only: it reads bounded eval files, never runs the backtester, never
applies anything. The verdict is the eval's own (source of truth, not set here).
"""

from __future__ import annotations


def main(argv=None) -> int:
    import json
    from pathlib import Path

    from app.autotuner.screening import evals_to_screening

    argv = list(argv or [])

    def _opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default

    evals_dir_raw = _opt("--evals-dir", "")
    if not evals_dir_raw:
        # Required: never silently scan the current working directory.
        print("error: --evals-dir is required (a directory of run_proposal_backtest eval JSONs)")
        return 2
    evals_dir = Path(evals_dir_raw)
    if not evals_dir.is_dir():
        print(f"error: --evals-dir is not a directory: {evals_dir}")
        return 2
    eval_paths = sorted(evals_dir.glob("*.json"))
    screening = evals_to_screening(eval_paths)

    out = _opt("--out", "screening.json")
    Path(out).write_text(
        json.dumps(screening, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"screened {len(eval_paths)} eval(s) -> {len(screening)} entry(ies) -> {out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
