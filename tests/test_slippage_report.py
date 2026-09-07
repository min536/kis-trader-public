from __future__ import annotations

from app.reporting.slippage_report import (
    SlippageReportSummary,
    SlippageSideSummary,
    format_slippage_report,
)


def test_format_slippage_report_matches_golden_text() -> None:
    summary = SlippageReportSummary(
        report_date="20260611",
        sample_count=4,
        buy=SlippageSideSummary(
            count=2,
            mean_bps=4.25,
            p50_bps=4.0,
            p75_bps=5.5,
            max_bps=7.0,
            missing_reference_count=1,
        ),
        sell=SlippageSideSummary(
            count=2,
            mean_bps=-1.25,
            p50_bps=-1.0,
            p75_bps=0.25,
            max_bps=0.5,
            missing_reference_count=0,
        ),
        policy_buy_bps=5.0,
        policy_sell_bps=6.0,
    )

    assert format_slippage_report(summary) == "\n".join(
        [
            "Slippage report 20260611",
            "samples=4 policy=BUY 5.00bps / SELL 6.00bps",
            "BUY  n=2 mean=4.25bps p50=4.00bps p75=5.50bps max=7.00bps missing_ref=1",
            "SELL n=2 mean=-1.25bps p50=-1.00bps p75=0.25bps max=0.50bps missing_ref=0",
            "read=BUY p75 is above policy; SELL p75 is within policy",
        ]
    )


def test_format_slippage_report_handles_empty_sides() -> None:
    summary = SlippageReportSummary(
        report_date="20260611",
        sample_count=0,
        buy=SlippageSideSummary(count=0),
        sell=SlippageSideSummary(count=0),
        policy_buy_bps=5.0,
        policy_sell_bps=5.0,
    )

    assert format_slippage_report(summary) == "\n".join(
        [
            "Slippage report 20260611",
            "samples=0 policy=BUY 5.00bps / SELL 5.00bps",
            "BUY  n=0 mean=n/a p50=n/a p75=n/a max=n/a missing_ref=0",
            "SELL n=0 mean=n/a p50=n/a p75=n/a max=n/a missing_ref=0",
            "read=insufficient slippage samples",
        ]
    )
