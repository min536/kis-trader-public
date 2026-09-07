"""Per-account env generator for multi-account separation (C-4 Phase 1 ergonomics).

Emits ``KIS_LIVE_SNAPSHOT_DIR`` / ``KIS_LIVE_SNAPSHOT_LOG_DIR`` /
``KIS_CREDENTIAL_CACHE_DIR`` values derived from a per-account label, so an
operator can set up parallel mock sessions without hand-picking collision-free
paths. Distinctness is guaranteed only for labels that stay distinct after
slugification — labels differing solely in punctuation (``A!`` vs ``A``) collapse
to the same slug, so the CLI rejects such sets (``detect_slug_collisions``).
Shell output is escaped with ``shlex`` and the result is verifiable with
``app.tools.multi_account_preflight``.

Why this and not runtime auto-scope: the live-snapshot *writer* lives in an
operator script (``scripts/live_snapshot.py``) and ``SNAPSHOT_PATH`` is a module
constant, so auto-deriving the path on the read side alone would point reads at
``data/{signature}/`` while writes still land in bare ``data/`` — a silent split.
Generating explicit env values keeps the existing, correct env mechanism in the
loop. True zero-config runtime auto-scope stays deferred (see
``docs/multi_account_parallelization_plan.md`` §4.B).
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Iterable

_MANAGED_ENV_VARS = (
    "KIS_LIVE_SNAPSHOT_DIR",
    "KIS_LIVE_SNAPSHOT_LOG_DIR",
    "KIS_CREDENTIAL_CACHE_DIR",
)


def slugify_label(label: str) -> str:
    """Reduce a label to a filesystem-safe lowercase slug."""
    text = (label or "").strip()
    slug = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_").lower()
    return slug or "account"


def recommend_account_env(label: str, *, base_dir: str = "data/accounts") -> dict[str, str]:
    """Return distinct per-account isolation env values for ``label``.

    Each account gets ``<base_dir>/<slug>/{data,logs,cache}`` so the three
    collision-prone dirs never overlap between two labels with distinct slugs.
    """
    base = (base_dir or "").rstrip("/")
    if not base.strip():
        raise ValueError("base_dir must not be empty (would root paths at '/')")
    root = f"{base}/{slugify_label(label)}"
    return {
        "KIS_LIVE_SNAPSHOT_DIR": f"{root}/data",
        "KIS_LIVE_SNAPSHOT_LOG_DIR": f"{root}/logs",
        "KIS_CREDENTIAL_CACHE_DIR": f"{root}/cache",
    }


def detect_slug_collisions(labels: Iterable[str]) -> dict[str, list[str]]:
    """Map each slug shared by >1 label to the labels that collapse onto it."""
    grouped: dict[str, list[str]] = {}
    for label in labels:
        grouped.setdefault(slugify_label(label), []).append(label)
    return {slug: group for slug, group in grouped.items() if len(group) > 1}


def render_export_block(label: str, *, base_dir: str = "data/accounts") -> str:
    """Render a ``# label`` header plus ``export KEY=value`` lines for one account.

    The output is meant to be ``source``-d, so values are ``shlex.quote``-d and
    the comment label is flattened to a single line — a label/base_dir carrying
    newlines, quotes or ``$()`` cannot inject commands.
    """
    env = recommend_account_env(label, base_dir=base_dir)
    safe_label = " ".join(str(label).splitlines()).strip()
    lines = [f"# account: {safe_label}"]
    lines.extend(f"export {key}={shlex.quote(env[key])}" for key in _MANAGED_ENV_VARS)
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.multi_account_env",
        description=(
            "계좌 label별로 충돌 없는 KIS_LIVE_SNAPSHOT_DIR/"
            "KIS_LIVE_SNAPSHOT_LOG_DIR/KIS_CREDENTIAL_CACHE_DIR 값을 생성합니다."
        ),
    )
    parser.add_argument(
        "--label",
        action="append",
        required=True,
        dest="labels",
        help="계좌 label (반복 지정 가능: --label A --label B)",
    )
    parser.add_argument(
        "--base-dir",
        default="data/accounts",
        help="계좌별 디렉토리 루트 (기본: data/accounts)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    collisions = detect_slug_collisions(args.labels)
    if collisions:
        for slug, group in sorted(collisions.items()):
            print(
                f"error: labels {group} all map to slug {slug!r} -> shared dirs; "
                f"use labels distinct after slugification",
                file=sys.stderr,
            )
        return 1
    if args.as_json:
        payload = {
            label: recommend_account_env(label, base_dir=args.base_dir)
            for label in args.labels
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        blocks = [
            render_export_block(label, base_dir=args.base_dir) for label in args.labels
        ]
        print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
