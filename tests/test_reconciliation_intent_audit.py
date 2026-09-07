"""R-b tests: pending SELL-intent shrink audit trail.

Design: docs/manual_trade_reconciliation_design_20260704.md §3.
The existing shrink arithmetic is byte-untouched (proven by the unmodified
tests/test_reconciliation_helpers.py gate file); this suite only asserts the
SEPARATE audit list (report["intent_adjustments"] + state["last_intent_adjustments"],
capped 50 FIFO).
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

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


_T1 = datetime(2026, 5, 20, 10, 5, tzinfo=timezone(timedelta(hours=9)))


def _patched_now(now=_T1):
    return mock.patch("app.core.reconciliation.get_korean_now", return_value=now)


class IntentAdjustmentAuditTests(unittest.TestCase):
    def test_position_gone_records_adjustment(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 3, "submitted_at": None}},
        }
        with _patched_now():
            report = reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        adjustments = report["intent_adjustments"]
        self.assertEqual(len(adjustments), 1)
        adj = adjustments[0]
        self.assertEqual(adj["symbol"], "005930")
        self.assertEqual(adj["before_qty"], 3)
        self.assertEqual(adj["after_qty"], 0)
        self.assertEqual(adj["cause"], "position_gone")
        self.assertEqual(adj["detected_at"], _T1.isoformat())
        # State mirror populated too.
        self.assertEqual(state["last_intent_adjustments"], adjustments)

    def test_shrink_records_position_shrunk(self) -> None:
        # previous=10, current=6 -> reduction 4; pending 8 -> 4.
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 8, "submitted_at": None}},
        }
        with _patched_now():
            report = reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap(_pos("005930", 6))
            )
        shrink = [a for a in report["intent_adjustments"] if a["cause"] == "position_shrunk"]
        self.assertEqual(len(shrink), 1)
        self.assertEqual(shrink[0]["before_qty"], 8)
        self.assertEqual(shrink[0]["after_qty"], 4)

    def test_no_change_records_empty_adjustments(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 10},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 5, "submitted_at": None}},
        }
        with _patched_now():
            report = reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap(_pos("005930", 10))
            )
        self.assertEqual(report["intent_adjustments"], [])
        # No adjustments -> state mirror is not grown (absent or untouched).
        self.assertEqual(state.get("last_intent_adjustments", []), [])

    def test_last_intent_adjustments_capped_at_50_fifo(self) -> None:
        # Pre-seed 50 old adjustments, then force one new -> oldest drops.
        state: dict = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 3, "submitted_at": None}},
            "last_intent_adjustments": [
                {
                    "symbol": f"OLD{i:03d}",
                    "before_qty": 1,
                    "after_qty": 0,
                    "cause": "position_gone",
                    "detected_at": (_T1 + timedelta(seconds=i)).isoformat(),
                }
                for i in range(50)
            ],
        }
        with _patched_now():
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        stored = state["last_intent_adjustments"]
        self.assertLessEqual(len(stored), 50)
        symbols = [a["symbol"] for a in stored]
        self.assertNotIn("OLD000", symbols)  # oldest dropped
        self.assertIn("005930", symbols)  # newest kept


class PrintIntentAdjustmentsTests(unittest.TestCase):
    def _capture(self, report: dict) -> str:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            reconciliation.print_reconciliation_report(report)
        return buf.getvalue()

    def test_prints_adjustment_line(self) -> None:
        report = {
            "summary": "ok",
            "events": [],
            "intent_adjustments": [
                {"symbol": "005930", "before_qty": 3, "after_qty": 0,
                 "cause": "position_gone", "detected_at": "t"},
            ],
        }
        out = self._capture(report)
        self.assertIn("005930", out)
        self.assertIn("position_gone", out)

    def test_no_adjustments_prints_no_adjustment_line(self) -> None:
        report = {"summary": "ok", "events": [], "intent_adjustments": []}
        out = self._capture(report)
        self.assertNotIn("position_gone", out)


if __name__ == "__main__":
    unittest.main()
