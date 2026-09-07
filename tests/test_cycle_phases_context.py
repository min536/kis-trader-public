"""Behavior-lock tests for the promoted run_cycle accumulator context.

Stage B-2 (``docs/main_run_cycle_slimming_plan_20260703.md`` §3) promotes the
~146 local accumulators declared at the top of ``app.main.run_cycle`` (plus the
``buy_scan_due`` parameter) onto :class:`app.runtime.cycle_phases.CycleContext`.

These pin the neutral construction defaults (so ``CycleContext()`` mirrors the
pre-promotion initial values) and the total field count, guarding against an
accidental field drop/add during the mechanical rename.
"""

from __future__ import annotations

import dataclasses


def test_cycle_context_construction_defaults() -> None:
    from app.runtime.cycle_phases import CycleContext

    ctx = CycleContext()

    # Numeric accumulators start at zero.
    assert ctx.buy_scan_requested_count == 0
    assert ctx.buy_scan_evaluated_count == 0
    assert ctx.cycle_warning_count == 0
    assert ctx.cycle_error_count == 0
    # Status/text flags mirror the original literals.
    assert ctx.buy_status_text == "not_run"
    assert ctx.sell_status_text == "not_run"
    assert ctx.cycle_environment == "mock"
    # Empty accumulator containers.
    assert ctx.timing_summary == {}
    assert ctx.buy_scan_sample_symbols == []


def test_cycle_context_mutable_defaults_are_isolated() -> None:
    from app.runtime.cycle_phases import CycleContext

    first = CycleContext()
    second = CycleContext()

    first.timing_summary["marker"] = 1
    first.buy_scan_sample_symbols.append("x")

    # Each instance owns its own mutable containers (default_factory, not shared).
    assert second.timing_summary == {}
    assert second.buy_scan_sample_symbols == []


def test_cycle_context_field_count_matches_promotion_list() -> None:
    from app.runtime.cycle_phases import CycleContext

    # 146 accumulators promoted from app.main:679-873 + the buy_scan_due
    # parameter (gate C3) + prioritized_positions (slice K gate K-1: promoted so
    # the SELL-watch-built ordering survives into the SELL-order defer block) =
    # 148 fields.
    assert len(dataclasses.fields(CycleContext)) == 148
