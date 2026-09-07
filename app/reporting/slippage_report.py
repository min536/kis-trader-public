from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlippageSideSummary:
    count: int
    mean_bps: float | None = None
    p50_bps: float | None = None
    p75_bps: float | None = None
    max_bps: float | None = None
    missing_reference_count: int = 0


@dataclass(frozen=True)
class SlippageReportSummary:
    report_date: str
    sample_count: int
    buy: SlippageSideSummary
    sell: SlippageSideSummary
    policy_buy_bps: float
    policy_sell_bps: float


def format_slippage_report(summary: SlippageReportSummary) -> str:
    lines = [
        f"Slippage report {summary.report_date}",
        (
            f"samples={int(summary.sample_count)} "
            f"policy=BUY {_format_bps(summary.policy_buy_bps)} / "
            f"SELL {_format_bps(summary.policy_sell_bps)}"
        ),
        _format_side_line("BUY ", summary.buy),
        _format_side_line("SELL", summary.sell),
        f"read={_format_read(summary)}",
    ]
    return "\n".join(lines)


def _format_side_line(label: str, side: SlippageSideSummary) -> str:
    return (
        f"{label} n={int(side.count)} "
        f"mean={_format_optional_bps(side.mean_bps)} "
        f"p50={_format_optional_bps(side.p50_bps)} "
        f"p75={_format_optional_bps(side.p75_bps)} "
        f"max={_format_optional_bps(side.max_bps)} "
        f"missing_ref={int(side.missing_reference_count)}"
    )


def _format_read(summary: SlippageReportSummary) -> str:
    if summary.buy.count <= 0 and summary.sell.count <= 0:
        return "insufficient slippage samples"
    buy_read = _side_policy_read("BUY", summary.buy, summary.policy_buy_bps)
    sell_read = _side_policy_read("SELL", summary.sell, summary.policy_sell_bps)
    return f"{buy_read}; {sell_read}"


def _side_policy_read(
    label: str,
    side: SlippageSideSummary,
    policy_bps: float,
) -> str:
    if side.count <= 0 or side.p75_bps is None:
        return f"{label} has insufficient samples"
    if side.p75_bps > policy_bps:
        return f"{label} p75 is above policy"
    return f"{label} p75 is within policy"


def _format_optional_bps(value: float | None) -> str:
    if value is None:
        return "n/a"
    return _format_bps(value)


def _format_bps(value: float) -> str:
    return f"{float(value):.2f}bps"
