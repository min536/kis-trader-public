from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class BuyScanStartDecision:
    allowed: bool
    scan_id: str
    reason: str
    previous_scan_id: str | None = None
    previous_started_at: datetime | None = None


@dataclass
class BuyScanRunGuard:
    running_scan_id: str | None = None
    started_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self.running_scan_id is not None

    def try_start(self, *, scan_id: str, now: datetime) -> BuyScanStartDecision:
        if self.running_scan_id is not None:
            return BuyScanStartDecision(
                allowed=False,
                scan_id=scan_id,
                reason="previous_scan_running",
                previous_scan_id=self.running_scan_id,
                previous_started_at=self.started_at,
            )
        self.running_scan_id = scan_id
        self.started_at = now
        return BuyScanStartDecision(
            allowed=True,
            scan_id=scan_id,
            reason="started",
        )

    def finish(self, *, scan_id: str) -> None:
        if self.running_scan_id == scan_id:
            self.running_scan_id = None
            self.started_at = None
