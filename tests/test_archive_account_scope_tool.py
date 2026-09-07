from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.tools.archive_account_scope import (
    ActiveSignatureArchiveError,
    apply_archive_plan,
    load_retired_scope_targets,
    plan_archive_for_signature,
)


SIG_OLD = "mock_acct_1111111111111111"
SIG_ACTIVE = "mock_acct_2222222222222222"


@pytest.fixture()
def roots(tmp_path: Path):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    archive_root = tmp_path / "archive" / "accounts"
    data.mkdir()
    logs.mkdir()
    return data, logs, archive_root


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _create_eight_family_files(data: Path, logs: Path, signature: str = SIG_OLD) -> tuple[Path, ...]:
    return (
        _write(data / f"runtime_state_{signature}.json", "{}"),
        _write(data / f"cycle_snapshots_{signature}.jsonl", "cycle\n"),
        _write(data / f"performance_snapshots_{signature}.jsonl", "perf\n"),
        _write(logs / f"orders_{signature}.jsonl", "order\n"),
        _write(logs / f"performance_summary_{signature}.jsonl", "summary\n"),
        _write(logs / f"cycle_stats_{signature}_20260701.jsonl", "stats\n"),
        _write(logs / f"candidate_outcomes_{signature}_20260702.jsonl", "outcomes\n"),
        _write(logs / f"backtest_signals_{signature}_20260703.jsonl", "signals\n"),
    )


def test_plan_collects_eight_families_and_dry_run_does_not_move(roots) -> None:
    data, logs, archive_root = roots
    sources = _create_eight_family_files(data, logs)

    plan = plan_archive_for_signature(
        SIG_OLD,
        active_signature=SIG_ACTIVE,
        label="mock contest 2026H2",
        data_dir=data,
        logs_dir=logs,
        archive_root=archive_root,
        now=datetime(2026, 7, 6, 12, 0, 0),
    )
    outcome = apply_archive_plan(plan, apply=False)

    assert len(plan.files) == 8
    assert "20260706_mock_contest_2026H2_mock_acct_1111111111111111" in str(plan.archive_dir)
    assert outcome.moved == ()
    assert outcome.manifest_path is None
    assert all(path.exists() for path in sources)
    assert not plan.manifest_path.exists()


def test_apply_moves_files_and_writes_manifest(roots) -> None:
    data, logs, archive_root = roots
    sources = _create_eight_family_files(data, logs)

    plan = plan_archive_for_signature(
        SIG_OLD,
        active_signature=SIG_ACTIVE,
        label="contest",
        data_dir=data,
        logs_dir=logs,
        archive_root=archive_root,
        now=datetime(2026, 7, 6, 12, 0, 0),
    )
    outcome = apply_archive_plan(plan, apply=True)

    assert len(outcome.moved) == 8
    assert all(not path.exists() for path in sources)
    assert all(item.destination.exists() for item in plan.files)
    manifest = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    assert manifest["signature"] == SIG_OLD
    assert manifest["label"] == "contest"
    assert manifest["file_count"] == 8
    assert manifest["date_range"] == {"min": "20260701", "max": "20260703"}
    assert manifest["total_bytes"] == sum(item.size_bytes for item in plan.files)


def test_active_signature_is_rejected_even_for_dry_run(roots) -> None:
    data, logs, archive_root = roots
    _create_eight_family_files(data, logs, signature=SIG_ACTIVE)

    with pytest.raises(ActiveSignatureArchiveError):
        plan_archive_for_signature(
            SIG_ACTIVE,
            active_signature=SIG_ACTIVE,
            label="active",
            data_dir=data,
            logs_dir=logs,
            archive_root=archive_root,
            now=datetime(2026, 7, 6, 12, 0, 0),
        )


def test_retired_selection_uses_history_and_excludes_active(tmp_path: Path) -> None:
    history = tmp_path / "account_scope_history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signature": SIG_OLD,
                        "label": "old contest",
                        "first_seen_at": "2026-06-01T09:00:00+09:00",
                    }
                ),
                "{not-json",
                json.dumps(
                    {
                        "signature": SIG_ACTIVE,
                        "label": "new contest",
                        "first_seen_at": "2026-07-06T09:00:00+09:00",
                        "retired_previous": SIG_OLD,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    targets = load_retired_scope_targets(
        history_file=history,
        active_signature=SIG_ACTIVE,
    )

    assert tuple(target.signature for target in targets) == (SIG_OLD,)
    assert targets[0].label == "old contest"
    assert targets[0].retired_at == "20260706"
