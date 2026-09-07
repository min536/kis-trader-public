"""R-c main.py wiring test (gate C7): import + call-site kw binding.

Design: docs/manual_trade_reconciliation_design_20260704.md §4/§6.7.
main.py adds exactly one import (maybe_notify_manual_trade_suspects) and passes
``_notify_reconciliation=`` into the existing run_account_snapshot_phase call
site — no net-new top-level def/class (trading_guard).
"""
from __future__ import annotations

import inspect
import unittest

from app import main as main_module
from app.notifications import reconciliation_alerts


class MainReconciliationWiringTests(unittest.TestCase):
    def test_main_imports_notify_helper(self) -> None:
        self.assertIs(
            main_module.maybe_notify_manual_trade_suspects,
            reconciliation_alerts.maybe_notify_manual_trade_suspects,
        )

    def test_call_site_passes_notify_reconciliation_kw(self) -> None:
        source = inspect.getsource(main_module.run_cycle)
        self.assertIn("_notify_reconciliation=", source)


if __name__ == "__main__":
    unittest.main()
