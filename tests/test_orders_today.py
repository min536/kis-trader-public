"""Tests for app.notifications.orders_today + snapshot_shared (R3-S3).

orders-today 주문 수명주기 클러스터와 공유 스냅샷 프리미티브를
runtime_status_snapshot에서 verbatim 이동; 기존 모듈은 facade로 동일 객체를
재수출해야 한다 (patch/import seam 보존).
"""

from __future__ import annotations

import unittest

from app.notifications import orders_today, runtime_status_snapshot, snapshot_shared

_SHARED_NAMES = (
    "DEFAULT_SNAPSHOT_PATH",
    "DEFAULT_STALE_AFTER_SEC",
    "_SIDE_LABELS",
    "_safe_int",
    "_safe_text",
    "_safe_price",
)

_ORDERS_NAMES = (
    "DEFAULT_ORDER_LOG_MAX_LINES",
    "_ORDER_ACTION_STATUS",
    "_read_today_order_events",
    "_order_record_time",
    "_order_side_status",
    "_raw_response_mapping",
    "_nested_mapping",
    "_first_present",
    "_order_qty",
    "_order_price_and_notional",
    "_order_failure_category",
    "_order_compact_key",
    "_pair_submission",
    "_compact_order_lifecycles",
    "_format_order_time",
    "_truncate_order_reason",
    "_render_order_lifecycle_line",
    "render_orders_today_reply",
)


class OrdersTodayFacadeTests(unittest.TestCase):
    def test_runtime_status_snapshot_binds_canonical_objects(self) -> None:
        for name in _SHARED_NAMES:
            with self.subTest(f"shared:{name}"):
                self.assertIs(
                    getattr(runtime_status_snapshot, name),
                    getattr(snapshot_shared, name),
                )
        for name in _ORDERS_NAMES:
            with self.subTest(f"orders:{name}"):
                self.assertIs(
                    getattr(runtime_status_snapshot, name),
                    getattr(orders_today, name),
                )


if __name__ == "__main__":
    unittest.main()
