"""Tests for app.reporting.lane_observation (BP-2 S1-S3).

Read-only reporting leaf over cycle-snapshot lane telemetry. The reader must
NEVER raise on hostile input; aggregation must match hand-computed fixtures;
the formatter must carry honesty labels in the OUTPUT BODY (not just docs).
Real-schema grounding: docs/lane_observation_plan_20260704.md §S0.
"""

from __future__ import annotations

import math

import pytest

from app.reporting.lane_observation import (
    LaneObservation,
    LaneSessionDigest,
    aggregate_lane_observations,
    format_lane_digest_console,
    format_lane_digest_json,
    read_lane_observation,
)


# ── S1: reader never-raises + degrade-safe ────────────────────────────────
HOSTILE_INPUTS = [
    None,
    "not-a-mapping",
    123,
    [],
    {},
    {"lane_scheduler_enabled": True, "order_gate_queue_depth": "not-int"},
    {"quote_age_max_ms": float("inf")},
    {"quote_age_avg_ms": float("nan")},
    {"order_gate_last_decision": 12345},          # non-str
    {"cycle_budget_exceeded": "yes"},             # truthy non-bool
    {"lane_scheduler_enabled": None},
]


@pytest.mark.parametrize("bad", HOSTILE_INPUTS)
def test_reader_never_raises(bad) -> None:
    obs = read_lane_observation(bad)
    assert isinstance(obs, LaneObservation)


def test_reader_inf_nan_quote_age_becomes_none() -> None:
    obs = read_lane_observation({"quote_age_max_ms": float("inf"),
                                 "quote_age_avg_ms": float("nan")})
    assert obs.quote_age_max_ms is None
    assert obs.quote_age_avg_ms is None


def test_reader_bad_int_degrades_to_zero() -> None:
    obs = read_lane_observation({"order_gate_queue_depth": "x",
                                 "order_gate_processed_count": None})
    assert obs.order_gate_queue_depth == 0
    assert obs.order_gate_processed_count == 0


def test_reader_flag_off_when_missing_or_none() -> None:
    assert read_lane_observation({}).flag_on is False
    assert read_lane_observation({"lane_scheduler_enabled": None}).flag_on is False
    assert read_lane_observation({"lane_scheduler_enabled": True}).flag_on is True


def test_reader_reads_flag_on_fields() -> None:
    obs = read_lane_observation({
        "lane_scheduler_enabled": True,
        "order_gate_last_decision": "blocked_detached_handler",
        "order_gate_queue_depth": 3,
        "order_gate_processed_count": 2,
        "quote_age_max_ms": 120.4,
        "cycle_budget_exceeded": True,
        "buy_scan_exception": "BoomError",
        "sell_lane_running": True,
        "buy_lane_running": True,
    })
    assert obs.flag_on is True
    assert obs.blocked_detached is True
    assert obs.order_gate_queue_depth == 3
    assert obs.quote_age_max_ms == pytest.approx(120.4)
    assert obs.cycle_budget_exceeded is True
    assert obs.overlap_observed is True


# ── S2: aggregation matches hand-computed fixture ─────────────────────────
def _records():
    # 2 flag-off (legacy) + 4 flag-on cycles.
    return [
        {"lane_scheduler_enabled": None},                                   # off
        {"foo": "bar"},                                                     # off (no flag)
        {"lane_scheduler_enabled": True, "order_gate_last_decision": "blocked_detached_handler",
         "order_gate_queue_depth": 1, "order_gate_processed_count": 0,
         "quote_age_max_ms": 100.0, "quote_age_avg_ms": 80.0,
         "sell_lane_running": True, "buy_lane_running": True},
        {"lane_scheduler_enabled": True, "order_gate_last_decision": "expired",
         "order_gate_queue_depth": 2, "order_gate_processed_count": 1,
         "quote_age_max_ms": 200.0, "quote_age_avg_ms": 120.0,
         "buy_scan_exception": "Boom"},
        {"lane_scheduler_enabled": True, "order_gate_last_decision": "submitted",
         "order_gate_queue_depth": 0, "order_gate_processed_count": 1,
         "cycle_budget_exceeded": True},
        {"lane_scheduler_enabled": True, "order_gate_last_decision": "blocked_detached_handler",
         "order_gate_queue_depth": 5, "order_gate_processed_count": 0,
         "sell_lane_running": True, "buy_lane_running": True},
    ]


def test_aggregate_hand_computed() -> None:
    d = aggregate_lane_observations(_records())
    assert isinstance(d, LaneSessionDigest)
    assert d.total_cycles == 6
    assert d.flag_off_cycles == 2
    assert d.flag_on_cycles == 4
    assert d.blocked_detached_cycles == 2           # two "blocked_detached_handler"
    assert d.expired_cycles == 1
    assert d.buy_scan_exception_cycles == 1
    assert d.budget_exceeded_cycles == 1
    assert d.overlap_cycles == 2                    # two cycles both lanes running
    assert d.max_queue_depth == 5
    assert d.total_processed == 2
    assert d.quote_age_max_ms == pytest.approx(200.0)
    assert d.quote_age_avg_ms == pytest.approx(100.0)   # mean of 80,120
    assert d.decision_counts["blocked_detached_handler"] == 2


def test_aggregate_empty() -> None:
    d = aggregate_lane_observations([])
    assert d.total_cycles == 0
    assert d.flag_on_cycles == 0
    assert d.quote_age_max_ms is None


def test_aggregate_never_raises_on_hostile_list() -> None:
    d = aggregate_lane_observations([None, "x", 5, {"lane_scheduler_enabled": True}])
    assert d.total_cycles == 4
    assert d.flag_on_cycles == 1


# ── S3: honesty labels in the OUTPUT BODY ─────────────────────────────────
def test_console_has_honesty_labels() -> None:
    d = aggregate_lane_observations(_records())
    text = "\n".join(format_lane_digest_console(d))
    assert "4" in text and "세션" in text or "cycle" in text.lower()
    # flag-off exclusion must be visible
    assert "flag-off" in text.lower() or "레거시" in text
    # derived nature of blocked_detached must be visible
    assert "파생" in text or "derived" in text.lower()


def test_console_marks_unmeasured_quote_age() -> None:
    d = aggregate_lane_observations([{"lane_scheduler_enabled": True}])
    text = "\n".join(format_lane_digest_console(d))
    assert "N/A" in text or "측정 불가" in text


def test_json_roundtrips_and_is_finite() -> None:
    import json
    d = aggregate_lane_observations(_records())
    payload = format_lane_digest_json(d)
    s = json.dumps(payload, allow_nan=False)   # must not contain inf/nan
    assert json.loads(s)["flag_on_cycles"] == 4
