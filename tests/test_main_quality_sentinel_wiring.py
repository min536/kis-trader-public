"""W6 activation guard: run_cycle must invoke the market-data quality sentinel.

Structural test — the sentinel's behavior is covered by
``tests/test_main_runtime_cycle_sentinel.py`` and ``tests/test_quality_sentinel.py``;
this only pins that the every-cycle path actually wires the (fail-safe) call so
the observability artifact is written each cycle. Slice F
(``docs/main_run_cycle_slimming_plan_20260703.md`` §3) moved the sentinel call
with the ``finally:`` body into ``finalize_cycle``, so the wiring chain is now
pinned without a gap: run_cycle → finalize_cycle → sentinel.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from app.runtime.cycle_phases.finalize import finalize_cycle

MAIN_PY = Path(__file__).resolve().parents[1] / "app" / "main.py"


def test_run_cycle_invokes_market_data_quality_sentinel():
    src = MAIN_PY.read_text(encoding="utf-8")
    assert "run_cycle_market_data_quality_sentinel" in src  # imported

    start = src.index("def run_cycle(")
    end = src.index("\ndef main(", start)
    body = src[start:end]
    assert "finalize_cycle(" in body  # run_cycle runs finalize every cycle

    finalize_source = inspect.getsource(finalize_cycle)
    assert "_run_cycle_market_data_quality_sentinel(" in finalize_source  # called in finalize
    assert "observed_market_snapshots" in finalize_source  # fed the cycle's snapshots
