"""CLI: autotuner_suggest -- Phase 5 MCP-assisted candidate suggestion.

Reads an external screening result (from the open-trading-api backtester/MCP,
operator-supplied JSON) + a baseline values map, and emits a curated `plan.json`
of whitelist-valid Tier A/B suggestions. The plan then feeds the human-gated
`python -m app.tools.autotuner_propose --plan plan.json` tick. SUGGESTS only:
no broker calls, no runtime application, no live write.

Usage:
    python -m app.tools.autotuner_suggest \\
        --screening screening.json --baseline baseline.json \\
        --date 20260605 --out plan.json
"""

from __future__ import annotations


def main(argv=None) -> int:
    import json
    from pathlib import Path

    from app.autotuner.candidate_suggest import suggest_candidates

    argv = list(argv or [])

    def _opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default

    screening = json.loads(Path(_opt("--screening", "")).read_text(encoding="utf-8"))
    baseline = json.loads(Path(_opt("--baseline", "")).read_text(encoding="utf-8"))
    plan = suggest_candidates(
        screening,
        baseline_values=baseline,
        date=_opt("--date", ""),
        mode=_opt("--mode", "shadow"),
    )
    out = _opt("--out", "plan.json")
    Path(out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"suggested {len(plan)} candidate(s) -> {out}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
