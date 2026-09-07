from __future__ import annotations

from app.core.order_log import build_price_capture


def test_build_price_capture_coerces_both_prices_to_float() -> None:
    capture = build_price_capture(reference_price_krw=70000, quote_at_submit=70010)

    assert capture == {"reference_price_krw": 70000.0, "quote_at_submit": 70010.0}


def test_build_price_capture_omits_unparseable_value_without_raising() -> None:
    capture = build_price_capture(reference_price_krw="not-a-number", quote_at_submit=70010)

    assert capture == {"quote_at_submit": 70010.0}


def test_build_price_capture_omits_non_finite_values() -> None:
    capture = build_price_capture(
        reference_price_krw=float("nan"), quote_at_submit=float("inf")
    )

    assert capture == {}


def test_build_price_capture_rejects_bool_disguised_as_price() -> None:
    capture = build_price_capture(reference_price_krw=True, quote_at_submit=False)

    assert capture == {}


def test_build_price_capture_omits_overflowing_value_without_raising() -> None:
    # float(10**400) raises OverflowError, not TypeError/ValueError; the helper
    # must stay total so its "never raises" contract holds even if the call-site
    # try/except were ever removed.
    capture = build_price_capture(reference_price_krw=10**400, quote_at_submit=5)

    assert capture == {"quote_at_submit": 5.0}
