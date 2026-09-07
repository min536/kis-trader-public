import time
from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

KOREA_TZ = ZoneInfo("Asia/Seoul")


def get_korean_now() -> datetime:
    return datetime.now(KOREA_TZ)


def _timestamp_to_datetime(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=KOREA_TZ)
    return value.astimezone(KOREA_TZ)


def is_korean_market_open(now: datetime | None = None) -> bool:
    current = now.astimezone(KOREA_TZ) if now else get_korean_now()

    if current.weekday() >= 5:
        return False

    current_hhmm = current.hour * 100 + current.minute
    return 900 <= current_hhmm <= 1530


def is_snapshot_fresh(
    snapshot: Mapping[str, object],
    *,
    max_age_seconds: int | None,
) -> bool:
    """Return True if the snapshot's observed_at timestamp is within max_age_seconds.

    Passing max_age_seconds=None means no age limit (always fresh).
    """
    if max_age_seconds is None:
        return True
    observed_at = str(snapshot.get("observed_at") or "").strip()
    if not observed_at:
        return False
    try:
        observed_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        age_seconds = time.time() - observed_dt.timestamp()
    except (TypeError, ValueError, OSError):
        return False
    return age_seconds <= max(0, int(max_age_seconds))
