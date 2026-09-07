"""CLI: autotuner_status -- read-only operator view of the runtime-override gate.

Explains WHY the autotuner runtime override is or isn't being applied (flag off,
wrong env, no approved bundle, etc.) without applying anything. Read-only; no
broker calls. Pairs with app.autotuner.diagnostics.
"""

from __future__ import annotations


def main(argv=None) -> int:
    import json
    from datetime import datetime, timezone

    from app.autotuner.diagnostics import (
        diagnose_high_risk_overrides,
        diagnose_live_shadow_runtime_overrides,
        diagnose_runtime_overrides,
    )

    argv = list(argv or [])

    def _opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default

    if "--live-shadow" in argv:
        diag = diagnose_live_shadow_runtime_overrides(
            project_root=_opt("--project-root", "."),
            now=datetime.now(timezone.utc),
            env=_opt("--env", "live"),
        )
        print("# Autotuner live-shadow status (Tier A, read-only)")
        print(f"- shadow_only  : {diag['shadow_only']}")
        print(f"- env          : {diag['env']}")
        print(f"- runtime_apply: {diag['runtime_activation']}")
        print(f"- reason       : {diag['reason']}")
        print(f"- would_apply  : {json.dumps(diag['would_apply'])}")
        return 0

    if "--high-risk" in argv:
        diag = diagnose_high_risk_overrides(
            project_root=_opt("--project-root", "."),
            now=datetime.now(timezone.utc),
            env=_opt("--env", "mock"),
            enabled_high_risk="--enabled-high-risk" in argv,
        )
        print("# Autotuner runtime-override status (Tier B high-risk)")
        print(f"- flag_enabled : {diag['flag_enabled']}")
        print(f"- env          : {diag['env']}")
        print(f"- reason       : {diag['reason']}")
        print(f"- would_apply  : {json.dumps(diag['would_apply'])}")
        return 0

    diag = diagnose_runtime_overrides(
        project_root=_opt("--project-root", "."),
        now=datetime.now(timezone.utc),
        env=_opt("--env", "mock"),
        enabled="--enabled" in argv,
    )
    print("# Autotuner runtime-override status (Tier A)")
    print(f"- flag_enabled : {diag['flag_enabled']}")
    print(f"- env          : {diag['env']}")
    print(f"- reason       : {diag['reason']}")
    print(f"- would_apply  : {json.dumps(diag['would_apply'])}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
