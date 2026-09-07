"""Regression tests for ``_build_deployment_invariant_equity``.

The brake equity formula must include ``cash_orderable_krw`` (KIS field
``prvs_rcdl_excc_amt``) in its max() so that intraday sell proceeds — which
KIS reflects in ``prvs_rcdl_excc_amt`` immediately while ``dnca_tot_amt`` and
``nxdy_excc_amt`` lag for several minutes — do not silently disappear from
the brake-equity baseline and trip a false HARD_STOP.

The 2026-04-23 production incident: operating equity 111.2M, brake equity
67.0M, daily pnl -40.38%, HARD_STOP_READY brake locking out all BUYs even
though the account hadn't actually lost money — the only difference between
the two equity numbers was 44.3M of orderable cash from a prior sell that
the legacy formula didn't count.
"""

from __future__ import annotations

import unittest

from app.portfolio.equity_state import (
    DEPLOYMENT_INVARIANT_EQUITY_BASIS,
    LEGACY_DEPLOYMENT_INVARIANT_EQUITY_BASIS,
    _build_deployment_invariant_equity,
    _uses_deployment_invariant_equity,
)


class BrakeEquityCashLegTests(unittest.TestCase):
    def test_includes_orderable_cash_when_total_and_next_day_lag(self) -> None:
        # The exact 14:39 / 15:25-style production state on 2026-04-23.
        equity, cash_leg = _build_deployment_invariant_equity(
            cash_total_krw=159_446,        # dnca_tot_amt — lagging
            cash_next_day_krw=48_321,      # nxdy_excc_amt — lagging
            cash_orderable_krw=44_324_501, # prvs_rcdl_excc_amt — fresh, fully reflects sell proceeds
            holdings_market_value_krw=66_895_810,
        )
        # cash_leg must pick up the orderable cash, not the stale total.
        self.assertEqual(cash_leg, 44_324_501)
        self.assertEqual(equity, 44_324_501 + 66_895_810)

    def test_picks_largest_cash_leg_when_total_is_larger(self) -> None:
        # Mirror case: settled cash already swept into dnca_tot_amt.
        equity, cash_leg = _build_deployment_invariant_equity(
            cash_total_krw=50_000_000,
            cash_next_day_krw=10_000_000,
            cash_orderable_krw=20_000_000,  # smaller — recently spent on a buy
            holdings_market_value_krw=30_000_000,
        )
        self.assertEqual(cash_leg, 50_000_000)
        self.assertEqual(equity, 50_000_000 + 30_000_000)

    def test_handles_all_zero_inputs_safely(self) -> None:
        equity, cash_leg = _build_deployment_invariant_equity(
            cash_total_krw=0,
            cash_next_day_krw=0,
            cash_orderable_krw=0,
            holdings_market_value_krw=0,
        )
        self.assertEqual((equity, cash_leg), (0, 0))

    def test_handles_none_inputs_safely(self) -> None:
        # Defensive: KIS sometimes returns missing fields.
        equity, cash_leg = _build_deployment_invariant_equity(
            cash_total_krw=None,  # type: ignore[arg-type]
            cash_next_day_krw=None,  # type: ignore[arg-type]
            cash_orderable_krw=1_000_000,
            holdings_market_value_krw=None,  # type: ignore[arg-type]
        )
        self.assertEqual((equity, cash_leg), (1_000_000, 1_000_000))

    def test_legacy_basis_string_still_recognized_as_compatible(self) -> None:
        # Yesterday's snapshots — written with the legacy basis name — must
        # still be considered valid baselines after the formula upgrade so
        # the daily PnL brake doesn't lose history at the rollover.
        legacy = {"equity_basis": LEGACY_DEPLOYMENT_INVARIANT_EQUITY_BASIS}
        current = {"equity_basis": DEPLOYMENT_INVARIANT_EQUITY_BASIS}
        unrelated = {"equity_basis": "something_else"}
        self.assertTrue(_uses_deployment_invariant_equity(legacy))
        self.assertTrue(_uses_deployment_invariant_equity(current))
        self.assertFalse(_uses_deployment_invariant_equity(unrelated))


if __name__ == "__main__":
    unittest.main()
