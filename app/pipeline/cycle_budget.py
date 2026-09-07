from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class CycleBudget:
    started_at: float
    hard_budget_seconds: float = 60.0
    clock: Callable[[], float] | None = None

    def _now(self) -> float:
        if self.clock is None:
            import time

            return time.perf_counter()
        return float(self.clock())

    @property
    def budget_ms(self) -> float:
        return max(0.0, float(self.hard_budget_seconds)) * 1000.0

    @property
    def deadline_at(self) -> float:
        return float(self.started_at) + max(0.0, float(self.hard_budget_seconds))

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._now() - float(self.started_at))

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_seconds * 1000.0

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, float(self.hard_budget_seconds) - self.elapsed_seconds)

    @property
    def exceeded(self) -> bool:
        return self.elapsed_seconds > float(self.hard_budget_seconds)

    def should_skip_stage(self, *, min_remaining_seconds: float) -> bool:
        return self.remaining_seconds <= max(0.0, float(min_remaining_seconds))

    def bounded_wait_seconds(self, *, desired_seconds: float, reserve_seconds: float = 2.0) -> float:
        return max(
            0.0,
            min(
                max(0.0, float(desired_seconds)),
                max(0.0, self.remaining_seconds - max(0.0, float(reserve_seconds))),
            ),
        )

    def expired(self) -> bool:
        return self.exceeded

    def checkpoint(self, stage: str) -> dict[str, object]:
        return {
            "stage": stage,
            "deadline_at": self.deadline_at,
            "remaining_seconds": self.remaining_seconds,
            "expired": self.expired(),
        }


@dataclass
class LaneBudget:
    started_at: float
    hard_budget_seconds: float
    clock: Callable[[], float]
    checkpoints: list[dict[str, object]] = field(default_factory=list)

    @property
    def deadline_at(self) -> float:
        return float(self.started_at) + max(0.0, float(self.hard_budget_seconds))

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - float(self.clock()))

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def checkpoint(self, stage: str) -> dict[str, object]:
        snapshot = {
            "stage": str(stage),
            "deadline_at": self.deadline_at,
            "remaining_seconds": self.remaining_seconds(),
            "expired": self.expired(),
        }
        self.checkpoints.append(snapshot)
        return snapshot
