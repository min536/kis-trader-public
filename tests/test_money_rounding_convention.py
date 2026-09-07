"""Money rounding convention boundary tests (F-20).

Characterization tests that LOCK the current KRW fee rounding convention:
``int(round(notional * bps / 10_000))`` uses Python's round-half-to-even
(banker's) rounding. If the convention ever changes (e.g. to half-up), these
boundary cases fail and flag it. Behavior is unchanged — this only pins it.
See docs/money_rounding_convention_20260705.md.
"""

from __future__ import annotations

from app.core.costs import calc_buy_fee


def test_half_to_even_rounds_down_to_even() -> None:
    # 10000 * 2.5 / 10000 = 2.5 -> banker's rounds to even -> 2.
    assert calc_buy_fee(10_000, 2.5) == 2


def test_half_to_even_rounds_up_to_even() -> None:
    # 10000 * 7.5 / 10000 = 7.5 -> banker's rounds to even -> 8.
    assert calc_buy_fee(10_000, 7.5) == 8


def test_half_at_zero_rounds_to_zero() -> None:
    # 10000 * 0.5 / 10000 = 0.5 -> banker's rounds to even -> 0.
    assert calc_buy_fee(10_000, 0.5) == 0


def test_half_at_one_and_half_rounds_to_two() -> None:
    # 10000 * 1.5 / 10000 = 1.5 -> banker's rounds to even -> 2.
    assert calc_buy_fee(10_000, 1.5) == 2


def test_negative_notional_clamps_to_zero() -> None:
    # notional is clamped to >= 0, so a negative notional yields zero fee.
    assert calc_buy_fee(-10_000, 5.0) == 0
