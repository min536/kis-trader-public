from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def healthy_session_summary(
    *,
    session_id: str = "mock_20260609_stage2",
    api_call_count: int = 720,
    rate_limit_hits: int = 0,
    runtime_errors: int = 0,
    broker_errors: int = 0,
    order_rejections: int = 0,
    buy_scan_cycles: int = 36,
    sell_check_cycles: int = 170,
    skipped_buy_scan_cadence: int = 0,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "metrics": {
            "api_call_count": api_call_count,
            "rate_limit_hits": rate_limit_hits,
            "runtime_errors": runtime_errors,
            "broker_errors": broker_errors,
            "order_rejections": order_rejections,
            "buy_scan_cycles": buy_scan_cycles,
            "sell_check_cycles": sell_check_cycles,
            "skipped_buy_scan_cadence": skipped_buy_scan_cadence,
        },
    }


def w1_holdout_session_summaries() -> dict[str, list[dict[str, Any]]]:
    return {
        "train": [
            healthy_session_summary(
                session_id="mock_20260609_stage2",
                api_call_count=814,
                buy_scan_cycles=41,
                sell_check_cycles=168,
            ),
            healthy_session_summary(
                session_id="mock_20260610_stage2",
                api_call_count=718,
                buy_scan_cycles=39,
                sell_check_cycles=174,
            ),
        ],
        "holdout": [
            healthy_session_summary(
                session_id="mock_20260611_holdout",
                api_call_count=760,
                buy_scan_cycles=40,
                sell_check_cycles=171,
            ),
            healthy_session_summary(
                session_id="mock_20260612_holdout",
                api_call_count=744,
                buy_scan_cycles=38,
                sell_check_cycles=169,
            ),
        ],
    }


def write_session_summary_files(
    root: Path,
    summaries: list[dict[str, Any]],
) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for idx, summary in enumerate(summaries, start=1):
        session_id = str(summary.get("session_id") or f"session_{idx}")
        path = root / f"session_{idx:03d}_{session_id}.json"
        path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths.append(path)
    return paths
