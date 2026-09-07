"""Filename-based account discovery for the multi-account dashboard (C-3 foundation).

Enumerates the ``account_signature`` values that already have state on disk by
scanning **filenames only** under ``data/`` and ``logs/`` — never reading file
contents (respecting the large-file / data-read policy). This is the discovery
layer the C-3 multi-account overview builds on; the loader, payload aggregation
and v2 multi-account UI remain gated on a second real account actually running in
parallel (see ``docs/multi_account_parallelization_plan.md`` §11 Phase 2–4).

Account-scoped artifacts come in two filename shapes (both from
``app/auth/account_scope.py``):

  - regular:     ``{prefix}_{signature}{ext}``                 e.g. ``orders_mock_acct_ab.jsonl``
  - partitioned: ``{prefix}_{signature}_{YYYYMMDD}.jsonl``     e.g. ``cycle_stats_mock_acct_ab_20260622.jsonl``
    (``get_partitioned_log_path``; the trailing 8-digit date must be stripped)

Signatures look like ``mock_acct_<16hex>`` (current) or ``mock_<cano>_<prdt>``
(legacy), both of which start with the environment token. The legacy 2-digit
``_<prdt>`` tail never collides with the 8-digit date suffix, so suffix
stripping is unambiguous.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.auth.settings import PROJECT_ROOT

DATA_DEFAULT_DIR = PROJECT_ROOT / "data"
LOGS_DEFAULT_DIR = PROJECT_ROOT / "logs"

# (root, prefix, extension, partitioned) for each account-scoped artifact family.
# Partitioned logs carry a trailing "_YYYYMMDD" date that is stripped to recover
# the signature (see _PARTITION_DATE_SUFFIX).
_SIGNATURE_SOURCES: tuple[tuple[str, str, str, bool], ...] = (
    ("data", "runtime_state_", ".json", False),
    ("data", "cycle_snapshots_", ".jsonl", False),
    ("data", "performance_snapshots_", ".jsonl", False),
    ("logs", "orders_", ".jsonl", False),
    ("logs", "performance_summary_", ".jsonl", False),
    ("logs", "cycle_stats_", ".jsonl", True),
    ("logs", "candidate_outcomes_", ".jsonl", True),
    ("logs", "backtest_signals_", ".jsonl", True),
)

_PARTITION_DATE_SUFFIX = re.compile(r"_\d{8}$")

# Incident/rotation backups (runtime_state_<sig>_rotated_<YYYYMMDD>_<HHMMSS>.json)
# share the artifact prefixes but are snapshots of an existing signature, not an
# account of their own — surfacing them pollutes the account selector.
_ROTATED_BACKUP_SUFFIX = re.compile(r"_rotated_\d{8}_\d{6}$")


@dataclass(frozen=True)
class AccountPresence:
    """Which account signatures have on-disk state, and from which artifacts."""

    signature: str
    environment: str
    sources: tuple[str, ...]


def _environment_of(signature: str) -> str:
    token = signature.split("_", 1)[0].strip()
    return token or "unknown"


def _extract_signature(
    name: str, prefix: str, extension: str, *, partitioned: bool = False
) -> str | None:
    if not (name.startswith(prefix) and name.endswith(extension)):
        return None
    signature = name[len(prefix) : len(name) - len(extension)]
    if _ROTATED_BACKUP_SUFFIX.search(signature):
        return None
    if partitioned:
        signature = _PARTITION_DATE_SUFFIX.sub("", signature)
    return signature.strip() or None


def discover_accounts(
    *,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> tuple[AccountPresence, ...]:
    """Discover account signatures present on disk, with their artifact sources."""
    data_root = data_dir if data_dir is not None else DATA_DEFAULT_DIR
    logs_root = logs_dir if logs_dir is not None else LOGS_DEFAULT_DIR

    sources_by_signature: dict[str, set[str]] = {}
    for root_key, prefix, extension, partitioned in _SIGNATURE_SOURCES:
        base = data_root if root_key == "data" else logs_root
        if not base.exists():
            continue
        for path in base.glob(f"{prefix}*{extension}"):
            signature = _extract_signature(
                path.name, prefix, extension, partitioned=partitioned
            )
            if signature is None:
                continue
            source_key = f"{prefix.rstrip('_')}"
            sources_by_signature.setdefault(signature, set()).add(source_key)

    return tuple(
        AccountPresence(
            signature=signature,
            environment=_environment_of(signature),
            sources=tuple(sorted(sources_by_signature[signature])),
        )
        for signature in sorted(sources_by_signature)
    )


def discover_account_signatures(
    *,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> tuple[str, ...]:
    """Just the sorted, de-duplicated account signatures present on disk."""
    return tuple(
        account.signature
        for account in discover_accounts(data_dir=data_dir, logs_dir=logs_dir)
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.dashboard.account_discovery",
        description="디스크에 상태가 존재하는 계좌 signature 목록 (filename-only, read-only).",
    )
    parser.add_argument("--data-dir", default=None, help="data 디렉토리 (기본: repo data/)")
    parser.add_argument("--logs-dir", default=None, help="logs 디렉토리 (기본: repo logs/)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    accounts = discover_accounts(
        data_dir=Path(args.data_dir) if args.data_dir else None,
        logs_dir=Path(args.logs_dir) if args.logs_dir else None,
    )
    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "signature": account.signature,
                        "environment": account.environment,
                        "sources": list(account.sources),
                    }
                    for account in accounts
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not accounts:
            print("(no account state found)")
        for account in accounts:
            print(
                f"{account.signature}  env={account.environment}  "
                f"sources={','.join(account.sources)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
