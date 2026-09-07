"""Tests for app.tools.lane_digest (BP-2 S3 CLI glue). Read-only, bounded."""

from __future__ import annotations

import json

from app.tools.lane_digest import render_lane_digest


RECORDS = [
    {"lane_scheduler_enabled": None},
    {"lane_scheduler_enabled": True, "order_gate_last_decision": "blocked_detached_handler",
     "order_gate_queue_depth": 2, "sell_lane_running": True, "buy_lane_running": True},
    {"lane_scheduler_enabled": True, "order_gate_last_decision": "submitted",
     "quote_age_max_ms": 150.0, "quote_age_avg_ms": 90.0},
]


def test_render_console_contains_labels() -> None:
    out = render_lane_digest(RECORDS, as_json=False)
    assert "flag-on 2" in out
    assert "레거시" in out or "flag-off" in out.lower()
    assert "파생" in out


def test_render_json_is_finite_and_parses() -> None:
    out = render_lane_digest(RECORDS, as_json=True)
    payload = json.loads(out)               # valid JSON
    assert payload["flag_on_cycles"] == 2
    assert payload["flag_off_cycles"] == 1
    assert payload["blocked_detached_cycles"] == 1


def test_render_never_raises_on_hostile_records() -> None:
    out = render_lane_digest([None, "x", 5], as_json=False)
    assert isinstance(out, str) and out
