from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_EXIT_CODE_RE = re.compile(r"^Exit code\s*:\s*(?P<value>-?\d+)\s*$")
_DURATION_RE = re.compile(r"^Duration sec\s*:\s*(?P<value>\d+)\s*$")
_FAILED_STEP_RE = re.compile(r"^ERROR:\s*(?P<value>.+?)\s+failed\.\s*$")
_BEST_TARGET_RE = re.compile(r"^\s*best_first_target\s*:\s*(?P<value>.+?)\s*$")
_OVERALL_READ_RE = re.compile(r"^\s*overall_read\s*:\s*(?P<value>.+?)\s*$")
_REPORT_DATE_RE = re.compile(r"eod_(?P<date>\d{8})\.txt$")
_ACCOUNT_RE = re.compile(r"^Account\s*:\s*(?P<value>.+?)\s*$")
_RATE_LIMIT_WARNING_RE = re.compile(r"^WARNING:\s*rate[- ]limit\s*(?P<value>.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class EodReportSummary:
    report_path: str
    market_date: str | None
    account: str | None
    exit_code: int | None
    duration_sec: int | None
    status: str
    failed_step: str | None
    first_error: str | None
    warning_count: int
    rate_limit_warnings: tuple[str, ...]
    best_first_target: str | None
    overall_read: str | None
    generated_files: tuple[str, ...]

    def to_details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "report": self.report_path,
            "status": self.status,
            "warning_count": self.warning_count,
        }
        if self.market_date:
            details["market_date"] = self.market_date
        if self.account:
            details["account"] = self.account
        if self.exit_code is not None:
            details["exit_code"] = self.exit_code
        if self.duration_sec is not None:
            details["duration_sec"] = self.duration_sec
        if self.failed_step:
            details["failed_step"] = self.failed_step
        if self.first_error:
            details["first_error"] = self.first_error
        if self.best_first_target:
            details["best_first_target"] = self.best_first_target
        if self.overall_read:
            details["overall_read"] = self.overall_read
        if self.rate_limit_warnings:
            details["rate_limit_warnings"] = " | ".join(self.rate_limit_warnings)
        if self.generated_files:
            details["generated_files"] = ", ".join(self.generated_files[:4])
        return details


def _clean_value(value: str) -> str:
    return " ".join(value.strip().split())


def _infer_report_date(path: Path) -> str | None:
    match = _REPORT_DATE_RE.search(path.name)
    return match.group("date") if match else None


def _status_from_exit_code(exit_code: int | None) -> str:
    if exit_code == 0:
        return "complete"
    if exit_code is None:
        return "unknown"
    return "failed"


def _format_rate_limit_warning(value: str) -> str:
    match = _RATE_LIMIT_WARNING_RE.match(value)
    if match:
        return _clean_value(match.group("value"))
    return _clean_value(value)


def build_eod_report_summary(report_path: str | Path) -> EodReportSummary:
    path = Path(report_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    account: str | None = None
    exit_code: int | None = None
    duration_sec: int | None = None
    failed_step: str | None = None
    first_error: str | None = None
    best_first_target: str | None = None
    overall_read: str | None = None
    generated_files: list[str] = []
    rate_limit_warnings: list[str] = []
    warning_count = 0
    in_generated_files = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        account_match = _ACCOUNT_RE.match(stripped)
        if account_match:
            account = _clean_value(account_match.group("value"))
            in_generated_files = False
            continue

        exit_match = _EXIT_CODE_RE.match(stripped)
        if exit_match:
            exit_code = int(exit_match.group("value"))
            in_generated_files = False
            continue

        duration_match = _DURATION_RE.match(stripped)
        if duration_match:
            duration_sec = int(duration_match.group("value"))
            in_generated_files = False
            continue

        if stripped.startswith("WARNING:"):
            warning_count += 1
            if _RATE_LIMIT_WARNING_RE.match(stripped):
                rate_limit_warnings.append(stripped)

        if stripped.startswith("ERROR:") and first_error is None:
            first_error = _clean_value(stripped)

        failed_match = _FAILED_STEP_RE.match(stripped)
        if failed_match and failed_step is None:
            failed_step = _clean_value(failed_match.group("value"))

        best_match = _BEST_TARGET_RE.match(line)
        if best_match:
            best_first_target = _clean_value(best_match.group("value"))
            in_generated_files = False
            continue

        overall_match = _OVERALL_READ_RE.match(line)
        if overall_match:
            overall_read = _clean_value(overall_match.group("value"))
            in_generated_files = False
            continue

        if stripped == "Key generated files:":
            in_generated_files = True
            continue

        if in_generated_files:
            if stripped.startswith("- "):
                generated_files.append(_clean_value(stripped[2:]))
                continue
            if stripped:
                in_generated_files = False

    return EodReportSummary(
        report_path=str(path),
        market_date=_infer_report_date(path),
        account=account,
        exit_code=exit_code,
        duration_sec=duration_sec,
        status=_status_from_exit_code(exit_code),
        failed_step=failed_step,
        first_error=first_error,
        warning_count=warning_count,
        rate_limit_warnings=tuple(rate_limit_warnings),
        best_first_target=best_first_target,
        overall_read=overall_read,
        generated_files=tuple(generated_files),
    )


def format_eod_report_summary(
    summary: EodReportSummary,
    *,
    market_date: str | None = None,
    analytics_lines: Sequence[str] | None = None,
) -> str:
    date_text = market_date or summary.market_date or "unknown"
    if summary.status == "complete":
        status_text = "✅ *EOD OK*"
    elif summary.status == "failed":
        status_text = "❌ *EOD FAILED*"
    else:
        status_text = f"⚠️ *EOD {summary.status.upper()}*"
    duration_text = (
        f"{summary.duration_sec}s" if summary.duration_sec is not None else "unknown"
    )

    lines = [
        f"{status_text} | `{date_text}` | `{duration_text}`",
    ]

    if summary.failed_step:
        lines.append(f"Failed: {summary.failed_step}")
    elif summary.first_error:
        lines.append(f"Error: {summary.first_error}")

    if summary.rate_limit_warnings:
        lines.append(
            "⚠️ Rate-limit: "
            f"{_format_rate_limit_warning(summary.rate_limit_warnings[0])}"
        )

    if summary.best_first_target or summary.overall_read:
        best = summary.best_first_target or "-"
        read = summary.overall_read or "-"
        lines.append(f"ML: `{best}` / {read}")

    lines.append(f"Report: {summary.report_path}")
    if analytics_lines:
        lines.extend(analytics_lines)
    return "\n".join(lines)


def _load_analytics_lines():
    """Best-effort 'realized to date' portfolio analytics lines for the EOD report.

    Reads the (bounded) order log via the W7 loader; any failure degrades to
    None so the EOD summary still prints.
    """
    try:
        from app.auth.settings import get_settings
        from app.reporting.portfolio_analytics_report import (
            load_portfolio_analytics,
            render_portfolio_analytics_lines,
        )
        return render_portfolio_analytics_lines(load_portfolio_analytics(get_settings()))
    except Exception:
        return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize a captured EOD wrapper report.",
    )
    parser.add_argument("report", help="Path to reports/eod_YYYYMMDD.txt")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON instead of Slack-friendly text.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    summary = build_eod_report_summary(args.report)
    if args.json:
        print(json.dumps(summary.to_details(), ensure_ascii=False, sort_keys=True))
    else:
        print(format_eod_report_summary(summary, analytics_lines=_load_analytics_lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
