"""R-a tests: additive event classification + manual-trade suspects ledger.

Design: docs/manual_trade_reconciliation_design_20260704.md §2.
Additive-only — existing event keys/type strings are byte-untouched (asserted
here and in the unmodified tests/test_reconciliation_helpers.py gate file).
"""
from __future__ import annotations

import unittest
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


class EventClassificationTests(unittest.TestCase):
    def test_new_position_event_gets_classification_and_detected_at(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 5}}
        report = reconciliation.build_reconciliation_report(
            state=state,
            portfolio_snapshot=_snap(_pos("005930", 5), _pos("000660", 3)),
        )
        evt = next(e for e in report["events"] if e["type"] == "unexpected_new_position")
        self.assertEqual(evt["classification"], "suspected_manual_buy")
        self.assertIsInstance(evt["detected_at"], str)
        self.assertTrue(evt["detected_at"])

    def test_qty_decrease_classifies_partial_sell(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 6))
        )
        evt = report["events"][0]
        self.assertEqual(evt["type"], "unexpected_quantity_difference")
        self.assertEqual(evt["classification"], "suspected_manual_partial_sell")

    def test_qty_increase_classifies_manual_buy(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 6}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 10))
        )
        evt = report["events"][0]
        self.assertEqual(evt["type"], "unexpected_quantity_difference")
        self.assertEqual(evt["classification"], "suspected_manual_buy")

    def test_sell_intent_exceeds_classifies_unexplained(self) -> None:
        state = {
            "broker_last_synced_positions_by_symbol": {"005930": 5},
            "pending_sell_intents_by_symbol": {"005930": {"qty": 10, "submitted_at": None}},
        }
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap(_pos("005930", 5))
        )
        evt = next(e for e in report["events"] if e["type"] == "sell_intent_exceeds_actual_qty")
        self.assertEqual(evt["classification"], "unexplained")


class SuspectsLedgerTests(unittest.TestCase):
    from datetime import datetime, timezone, timedelta
    _T1 = datetime(2026, 5, 20, 10, 5, tzinfo=timezone(timedelta(hours=9)))

    def _patched_now(self):
        return mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=self._T1
        )

    def test_suspect_upserted_on_manual_sell(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        with self._patched_now():
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        suspects = state["manual_trade_suspects_by_symbol"]
        self.assertIn("005930", suspects)
        entry = suspects["005930"]
        self.assertEqual(entry["classification"], "suspected_manual_sell")
        self.assertEqual(entry["last_event_type"], "unexpected_missing_position")
        self.assertEqual(entry["expected_qty"], 10)
        self.assertEqual(entry["actual_qty"], 0)
        self.assertEqual(entry["occurrences"], 1)
        self.assertEqual(entry["first_detected_at"], self._T1.isoformat())
        self.assertEqual(entry["last_detected_at"], self._T1.isoformat())
        self.assertNotIn("resolved_at", entry)

    def test_occurrences_increment_on_repeat_detection(self) -> None:
        from datetime import timedelta
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        with mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=self._T1
        ):
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        # Baseline was overwritten to {} after first sync; re-seed drift so the
        # same symbol re-triggers, and advance the clock.
        state["broker_last_synced_positions_by_symbol"] = {"005930": 10}
        t2 = self._T1 + timedelta(minutes=5)
        with mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=t2
        ):
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        entry = state["manual_trade_suspects_by_symbol"]["005930"]
        self.assertEqual(entry["occurrences"], 2)
        self.assertEqual(entry["first_detected_at"], self._T1.isoformat())
        self.assertEqual(entry["last_detected_at"], t2.isoformat())

    def test_resolved_at_recorded_when_drift_clears(self) -> None:
        from datetime import timedelta
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        with mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=self._T1
        ):
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        # Next sync: position matches baseline -> no drift event -> resolved.
        state["broker_last_synced_positions_by_symbol"] = {"005930": 5}
        t2 = self._T1 + timedelta(minutes=5)
        with mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=t2
        ):
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap(_pos("005930", 5))
            )
        entry = state["manual_trade_suspects_by_symbol"]["005930"]
        self.assertEqual(entry["resolved_at"], t2.isoformat())
        # Entry retained, occurrences unchanged.
        self.assertEqual(entry["occurrences"], 1)

    def test_ledger_capped_at_30_fifo_by_first_detected(self) -> None:
        from datetime import timedelta
        state: dict = {"manual_trade_suspects_by_symbol": {}}
        suspects = state["manual_trade_suspects_by_symbol"]
        # Pre-seed 30 resolved entries with ascending first_detected_at.
        for i in range(30):
            ts = (self._T1 + timedelta(minutes=i)).isoformat()
            suspects[f"OLD{i:03d}"] = {
                "first_detected_at": ts,
                "last_detected_at": ts,
                "last_event_type": "unexpected_missing_position",
                "classification": "suspected_manual_sell",
                "expected_qty": 1,
                "actual_qty": 0,
                "occurrences": 1,
                "resolved_at": ts,
            }
        oldest_symbol = "OLD000"
        # 31st distinct symbol drift -> must drop the oldest.
        state["broker_last_synced_positions_by_symbol"] = {"999999": 10}
        t_new = self._T1 + timedelta(hours=1)
        with mock.patch(
            "app.core.reconciliation.get_korean_now", return_value=t_new
        ):
            reconciliation.sync_reconciliation_state(
                state=state, portfolio_snapshot=_snap()
            )
        self.assertLessEqual(len(state["manual_trade_suspects_by_symbol"]), 30)
        self.assertNotIn(oldest_symbol, state["manual_trade_suspects_by_symbol"])
        self.assertIn("999999", state["manual_trade_suspects_by_symbol"])


class EventKeyInvarianceTests(unittest.TestCase):
    def test_existing_event_keys_and_type_unchanged(self) -> None:
        state = {"broker_last_synced_positions_by_symbol": {"005930": 10}}
        report = reconciliation.build_reconciliation_report(
            state=state, portfolio_snapshot=_snap()
        )
        event = report["events"][0]
        # Existing keys/type string must remain byte-identical (additive only).
        self.assertEqual(event["type"], "unexpected_missing_position")
        for key in ("type", "symbol", "expected_qty", "actual_qty", "reason"):
            self.assertIn(key, event)
        # Additive fields present but do not displace the originals.
        self.assertEqual(
            set(event) - {"type", "symbol", "expected_qty", "actual_qty", "reason"},
            {"classification", "detected_at"},
        )


if __name__ == "__main__":
    unittest.main()
