"""Characterization tests for reconciliation helpers in app.main."""
from __future__ import annotations

import unittest

from app import main as main_module
from app.core import reconciliation
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot


def _pos(symbol: str, qty: int, *, has_position: bool = True) -> PortfolioPosition:
    return PortfolioPosition(
        symbol=symbol, name=symbol, holding_qty=qty,
        average_cost=10_000, current_price=10_000,
        market_value=qty * 10_000, gross_pnl=0, gross_pnl_pct=0.0,
        has_position=has_position,
    )


def _snap(*positions: PortfolioPosition) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=positions, cash_total=100_000_000,
        cash_orderable=100_000_000, cash_next_day=100_000_000,
        total_evaluation_amount=100_000_000,
    )


class BuildPositionsQtyMapTests(unittest.TestCase):
    def test_none_snapshot_returns_empty(self) -> None:
        result = reconciliation.build_positions_qty_map(None)
        self.assertEqual(result, {})

    def test_maps_symbol_to_int_qty(self) -> None:
        result = reconciliation.build_positions_qty_map(_snap(_pos("005930", 10), _pos("000660", 5)))
        self.assertEqual(result, {"005930": 10, "000660": 5})

    def test_blank_symbol_excluded(self) -> None:
        result = reconciliation.build_positions_qty_map(_snap(_pos("005930", 1), _pos("", 3)))
        self.assertIn("005930", result)
        self.assertNotIn("", result)

    def test_has_position_false_excluded(self) -> None:
        snap = _snap(_pos("005930", 10, has_position=True), _pos("000660", 5, has_position=False))
        result = reconciliation.build_positions_qty_map(snap)
        self.assertIn("005930", result)
        self.assertNotIn("000660", result)

    def test_qty_is_int(self) -> None:
        result = reconciliation.build_positions_qty_map(_snap(_pos("005930", 7)))
        self.assertIsInstance(result["005930"], int)


class BuildReconciliationReportFirstSyncTests(unittest.TestCase):
    def test_no_baseline_returns_first_sync_summary(self) -> None:
        report = reconciliation.build_reconciliation_report(
            state={}, portfolio_snapshot=_snap(_pos("005930", 10))
        )
        self.assertIn("첫 broker sync", report["summary"])
        self.assertEqual(report["events"], [])

    def test_return_keys_present(self) -> None:
        report = reconciliation.build_reconciliation_report(
            state={}, portfolio_snapshot=_snap(_pos("005930", 5))
        )
        for key in ("expected_positions_by_symbol", "actual_positions_by_symbol",
                    "pending_sell_intents_by_symbol", "events", "summary", "event_count"):
            self.assertIn(key, report)

    def test_actual_positions_populated_from_snapshot(self) -> None:
        report = reconciliation.build_reconciliation_report(
            state={}, portfolio_snapshot=_snap(_pos("005930", 8))
        )
        self.assertEqual(report["actual_positions_by_symbol"], {"005930": 8})


class BuildReconciliationReportAlignedTests(unittest.TestCase):
    def test_matching_positions_no_events(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 10))
        )
        self.assertEqual(report["events"], [])
        self.assertIn("drift", report["summary"])


class BuildReconciliationReportDriftTests(unittest.TestCase):
    def test_unexpected_missing_position(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap()
        )
        self.assertEqual(report["event_count"], 1)
        event = report["events"][0]
        self.assertEqual(event["type"], "unexpected_missing_position")
        self.assertEqual(event["symbol"], "005930")
        self.assertEqual(event["expected_qty"], 10)
        self.assertEqual(event["actual_qty"], 0)

    def test_unexpected_new_position(self) -> None:
        # Baseline must be non-empty for drift detection to run.
        # A new symbol (000660) appears in actual but not in baseline.
        state = {"broker_last_synced_positions_by_symbol": {"005930": 5}}
        report = reconciliation.build_reconciliation_report(
            state=state,
            portfolio_snapshot=_snap(_pos("005930", 5), _pos("000660", 3)),
        )
        types = [e["type"] for e in report["events"]]
        self.assertIn("unexpected_new_position", types)
        evt = next(e for e in report["events"] if e["type"] == "unexpected_new_position")
        self.assertEqual(evt["symbol"], "000660")

    def test_unexpected_quantity_difference(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 6))
        )
        self.assertEqual(report["event_count"], 1)
        event = report["events"][0]
        self.assertEqual(event["type"], "unexpected_quantity_difference")
        self.assertEqual(event["expected_qty"], 10)
        self.assertEqual(event["actual_qty"], 6)

    def test_drift_suppressed_by_local_order_activity(self) -> None:
        from datetime import datetime, timezone, timedelta
        t1 = datetime(2026, 5, 20, 10, 5, tzinfo=timezone(timedelta(hours=9)))
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "recent_orders": [
                {"symbol": "005930", "action": "sell_order_succeeded", "timestamp": t1.isoformat()}
            ],
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap()
        )
        self.assertEqual(report["events"], [])

    def test_old_order_before_last_sync_does_not_suppress_drift(self) -> None:
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        t0 = datetime(2026, 5, 20, 10, 0, tzinfo=KST)
        t1 = datetime(2026, 5, 20, 10, 5, tzinfo=KST)
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "broker_last_synced_at": t1.isoformat(),
            "recent_orders": [
                {"symbol": "005930", "action": "sell_order_succeeded", "timestamp": t0.isoformat()}
            ],
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap()
        )
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["events"][0]["type"], "unexpected_missing_position")

    def test_sell_intent_exceeds_actual_qty_event(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 10, "submitted_at": None}},
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 5))
        )
        intent_events = [e for e in report["events"] if e["type"] == "sell_intent_exceeds_actual_qty"]
        self.assertEqual(len(intent_events), 1)
        self.assertEqual(intent_events[0]["expected_qty"], 10)
        self.assertEqual(intent_events[0]["actual_qty"], 5)

    def test_sell_intent_within_actual_qty_no_event(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 5, "submitted_at": None}},
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 10))
        )
        intent_events = [e for e in report["events"] if e["type"] == "sell_intent_exceeds_actual_qty"]
        self.assertEqual(len(intent_events), 0)

    def test_pending_intent_as_plain_int_still_detected(self) -> None:
        # Legacy format: pending_sell_intents entry is a plain int, not a dict
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 3},
            "pending_sell_intents_by_symbol": {"005930": 10},
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 3))
        )
        intent_events = [e for e in report["events"] if e["type"] == "sell_intent_exceeds_actual_qty"]
        self.assertEqual(len(intent_events), 1)


class SyncReconciliationStateTests(unittest.TestCase):
    from datetime import datetime, timezone, timedelta
    _T1 = datetime(2026, 5, 20, 10, 5, tzinfo=timezone(timedelta(hours=9)))

    def _patched_now(self):
        return __import__("unittest.mock", fromlist=["patch"]).patch(
            "app.core.reconciliation.get_korean_now", return_value=self._T1
        )

    def test_updates_broker_last_synced_positions(self) -> None:
        state: dict = {}
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap(_pos("005930", 10)))
        self.assertEqual(state["broker_last_synced_positions_by_symbol"], {"005930": 10})

    def test_writes_broker_last_synced_at(self) -> None:
        state: dict = {}
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap())
        self.assertEqual(state["broker_last_synced_at"], self._T1.isoformat())

    def test_writes_last_reconciliation_summary(self) -> None:
        state: dict = {}
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap())
        self.assertIsInstance(state.get("last_reconciliation_summary"), str)

    def test_writes_last_reconciliation_events_list(self) -> None:
        state: dict = {}
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap())
        self.assertIsInstance(state.get("last_reconciliation_events"), list)

    def test_pending_intent_removed_when_position_gone(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 3, "submitted_at": None}},
        }
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap())
        self.assertNotIn("005930", state["pending_sell_intents_by_symbol"])

    def test_pending_intent_reduced_when_position_partially_sold(self) -> None:
        # previous=10, current=6 → reduction=4; pending was 8 → becomes 4
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 8, "submitted_at": None}},
        }
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap(_pos("005930", 6)))
        self.assertEqual(state["pending_sell_intents_by_symbol"]["005930"]["qty"], 4)

    def test_pending_intent_capped_at_current_qty(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 20, "submitted_at": None}},
        }
        with self._patched_now():
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap(_pos("005930", 5)))
        self.assertLessEqual(state["pending_sell_intents_by_symbol"]["005930"]["qty"], 5)

    def test_record_symbol_exit_called_for_unexpected_missing(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        with self._patched_now(), unittest.mock.patch("app.core.reconciliation.record_symbol_exit") as mock_exit:
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap())
        mock_exit.assert_called_once()
        kwargs = mock_exit.call_args.kwargs
        self.assertEqual(kwargs["symbol"], "005930")
        self.assertEqual(kwargs["exit_reason"], "manual_or_reconciled")
        self.assertTrue(kwargs["was_full_close"])

    def test_record_symbol_exit_called_for_quantity_difference(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        with self._patched_now(), unittest.mock.patch("app.core.reconciliation.record_symbol_exit") as mock_exit:
            reconciliation.sync_reconciliation_state(state=state, portfolio_snapshot=_snap(_pos("005930", 4)))
        mock_exit.assert_called_once()
        kwargs = mock_exit.call_args.kwargs
        self.assertEqual(kwargs["qty"], 6)  # 10 - 4
        self.assertFalse(kwargs["was_full_close"])

    def test_record_symbol_exit_not_called_for_unexpected_new_position(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 5}}
        with self._patched_now(), unittest.mock.patch("app.core.reconciliation.record_symbol_exit") as mock_exit:
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap(_pos("005930", 5), _pos("000660", 3))
            )
        mock_exit.assert_not_called()


class PrintReconciliationReportTests(unittest.TestCase):
    def _capture(self, report: dict) -> str:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            reconciliation.print_reconciliation_report(report)
        return buf.getvalue()

    def test_prints_header_and_summary(self) -> None:
        output = self._capture({"summary": "no drift", "events": []})
        self.assertIn("상태 정합성", output)
        self.assertIn("no drift", output)

    def test_prints_event_symbol_and_type(self) -> None:
        report = {
            "summary": "reconciliation event 1건 감지",
            "events": [
                {"symbol": "005930", "type": "unexpected_missing_position",
                 "expected_qty": 10, "actual_qty": 0, "reason": "gone"},
            ],
        }
        output = self._capture(report)
        self.assertIn("005930", output)
        self.assertIn("unexpected_missing_position", output)

    def test_truncation_marker_when_more_than_five_events(self) -> None:
        events = [
            {"symbol": f"{i:06d}", "type": "unexpected_missing_position",
             "expected_qty": 1, "actual_qty": 0, "reason": "-"}
            for i in range(7)
        ]
        output = self._capture({"summary": "7건 감지", "events": events})
        self.assertIn("외", output)  # "... 외 N건" truncation marker

    def test_ends_with_blank_line(self) -> None:
        output = self._capture({"summary": "ok", "events": []})
        self.assertTrue(output.endswith("\n\n"))

    def test_main_exposes_core_function(self) -> None:
        self.assertIs(
            main_module.print_reconciliation_report,
            reconciliation.print_reconciliation_report,
        )


if __name__ == "__main__":
    unittest.main()
