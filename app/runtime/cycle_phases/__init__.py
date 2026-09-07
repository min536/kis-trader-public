"""Phase-scoped extraction targets for ``app.main.run_cycle``.

See ``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (Stage B). The first
inhabitant is :class:`~app.runtime.cycle_phases.context.CycleContext`, the
cycle-scoped accumulator context.
"""

from app.runtime.cycle_phases.context import CycleContext

__all__ = ["CycleContext"]
