from __future__ import annotations

import json
from pathlib import Path

from app.tools.rate_limit_audit import build_rate_limit_audit, format_rate_limit_audit


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_rate_limit_audit_detects_balance_sell_watch_drain_pattern(tmp_path: Path) -> None:
    root = tmp_path
    account = "mock_acct_test"
    _write_jsonl(
        root / "logs" / f"cycle_stats_{account}_20260607.jsonl",
        [{"ts": "2026-06-07T09:00:00+09:00", "session": "REGULAR"}],
    )
    _write_jsonl(
        root / "logs" / f"cycle_stats_{account}_20260608.jsonl",
        [{"ts": "2026-06-08T09:00:00+09:00", "session": "REGULAR"}],
    )
    _write_jsonl(
        root / "data" / f"cycle_snapshots_{account}.jsonl",
        [
            {
                "timestamp": "2026-06-07T09:00:00+09:00",
                "market_session": {"session": "REGULAR"},
                "rate_limit_triggered": True,
                "rate_limit_source": "buy_scan",
                "backoff_applied_seconds": 60,
            },
            {
                "timestamp": "2026-06-08T09:00:00+09:00",
                "market_session": {"session": "REGULAR"},
                "rate_limit_triggered": True,
                "rate_limit_source": "balance",
                "backoff_applied_seconds": 60,
            },
            {
                "timestamp": "2026-06-08T09:01:00+09:00",
                "market_session": {"session": "REGULAR"},
                "rate_limit_source": None,
                "sell_watch_backoff_drain_ms": 24000.0,
                "scheduler_state": {"decision": "API_BACKOFF_WAIT"},
            },
        ],
    )

    audit = build_rate_limit_audit(root=root, days=2)

    assert audit["totals"]["rate_limit_hits"] == 2
    assert audit["totals"]["balance_sell_watch_drain_count"] == 1
    assert audit["assessment"] == "balance_sell_watch_drain_detected"
    day = audit["days"][-1]
    assert day["date"] == "20260608"
    assert day["rate_limit_sources"] == {"balance": 1}
    assert day["warnings"] == (
        "WARNING: rate-limit balance->sell_watch drain detected "
        "(date=20260608 count=1)."
    ,)


def test_rate_limit_audit_selects_recent_unique_dates_across_accounts(tmp_path: Path) -> None:
    for date, account in [
        ("20260605", "mock_old"),
        ("20260608", "mock_new"),
        ("20260609", "mock_new"),
    ]:
        _write_jsonl(
            tmp_path / "logs" / f"cycle_stats_{account}_{date}.jsonl",
            [{"ts": f"{date[:4]}-{date[4:6]}-{date[6:]}T09:00:00+09:00"}],
        )

    audit = build_rate_limit_audit(root=tmp_path, days=2, session="ALL")

    assert [day["date"] for day in audit["days"]] == ["20260608", "20260609"]


def test_rate_limit_audit_terminal_format_includes_actionable_warning(tmp_path: Path) -> None:
    account = "mock_acct_test"
    _write_jsonl(
        tmp_path / "logs" / f"cycle_stats_{account}_20260608.jsonl",
        [{"ts": "2026-06-08T09:00:00+09:00", "session": "REGULAR"}],
    )
    _write_jsonl(
        tmp_path / "data" / f"cycle_snapshots_{account}.jsonl",
        [
            {
                "timestamp": "2026-06-08T09:00:00+09:00",
                "market_session": {"session": "REGULAR"},
                "rate_limit_source": "balance",
                "rate_limit_triggered": True,
            },
            {
                "timestamp": "2026-06-08T09:01:00+09:00",
                "market_session": {"session": "REGULAR"},
                "sell_watch_backoff_drain_ms": 60000.0,
            },
        ],
    )

    text = format_rate_limit_audit(build_rate_limit_audit(root=tmp_path, days=1))

    assert "rate_limit_audit" in text
    assert "balance" in text
    assert "WARNING: rate-limit balance->sell_watch drain detected" in text
