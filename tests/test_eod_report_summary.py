from __future__ import annotations

import json
from pathlib import Path

from app.tools.eod_report_summary import (
    EodReportSummary,
    build_eod_report_summary,
    format_eod_report_summary,
    main,
)


def test_format_eod_report_summary_appends_analytics_lines() -> None:
    summary = EodReportSummary(
        report_path="reports/eod_20260613.txt", market_date="20260613", account=None,
        exit_code=0, duration_sec=12, status="complete", failed_step=None, first_error=None,
        warning_count=0, rate_limit_warnings=(), best_first_target=None, overall_read=None,
        generated_files=(),
    )
    text = format_eod_report_summary(
        summary,
        analytics_lines=["📊 Portfolio analytics (realized, 3 trades)", "  005930: net=+25,000"],
    )
    assert "Portfolio analytics" in text
    assert "005930" in text


def test_build_eod_report_summary_extracts_success_fields(tmp_path: Path) -> None:
    report = tmp_path / "eod_20260603.txt"
    report.write_text(
        "\n".join(
            [
                "EOD wrapper",
                "===========",
                "compare_ml_label_viability",
                "  Recommendation",
                "  best_first_target : none",
                "  overall_read      : evidence is weak across labels",
                "",
                "Key generated files:",
                "  - logs/signal_dataset_mock_12345678_01_20260603.csv",
                "  - logs/ml/ml_label_viability_report.json",
                "",
                "EOD wrapper result",
                "==================",
                "Exit code   : 0",
                "Duration sec: 35",
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = build_eod_report_summary(report)

    assert summary.status == "complete"
    assert summary.market_date == "20260603"
    assert summary.exit_code == 0
    assert summary.duration_sec == 35
    assert summary.account is None
    assert summary.best_first_target == "none"
    assert summary.overall_read == "evidence is weak across labels"
    assert summary.generated_files == (
        "logs/signal_dataset_mock_12345678_01_20260603.csv",
        "logs/ml/ml_label_viability_report.json",
    )

    message = format_eod_report_summary(summary)
    assert message.splitlines()[0] == "✅ *EOD OK* | `20260603` | `35s`"
    assert "ML: `none` / evidence is weak across labels" in message
    assert "generated=" not in message
    assert f"Report: {report}" in message


def test_eod_report_summary_extracts_rate_limit_warning_detail(tmp_path: Path) -> None:
    report = tmp_path / "eod_20260609.txt"
    report.write_text(
        "\n".join(
            [
                "EOD verification context",
                "Account : mock_acct_test",
                "Date    : 20260609",
                "",
                "7. Rate-limit audit",
                "WARNING: rate-limit balance->sell_watch drain detected (date=20260609 count=3).",
                "",
                "EOD wrapper result",
                "==================",
                "Exit code   : 0",
                "Duration sec: 22",
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = build_eod_report_summary(report)

    assert summary.account == "mock_acct_test"
    assert summary.warning_count == 1
    assert summary.rate_limit_warnings == (
        "WARNING: rate-limit balance->sell_watch drain detected (date=20260609 count=3).",
    )
    message = format_eod_report_summary(summary)
    assert "warnings=1" not in message
    assert "⚠️ Rate-limit: balance->sell_watch drain detected" in message


def test_build_eod_report_summary_extracts_failed_step(tmp_path: Path) -> None:
    report = tmp_path / "eod_20260608.txt"
    report.write_text(
        "\n".join(
            [
                "Command: eod_health_check",
                "No candidate_outcomes rows found for account=x date=20260608.",
                "",
                "ERROR: 1. EOD health check failed.",
                "",
                "EOD wrapper result",
                "==================",
                "Exit code   : 1",
                "Duration sec: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = build_eod_report_summary(report)

    assert summary.status == "failed"
    assert summary.failed_step == "1. EOD health check"
    assert summary.first_error == "ERROR: 1. EOD health check failed."
    message = format_eod_report_summary(summary)
    assert message.splitlines()[0] == "❌ *EOD FAILED* | `20260608` | `0s`"
    assert "Failed: 1. EOD health check" in message


def test_eod_report_summary_cli_json(tmp_path: Path, capsys) -> None:
    report = tmp_path / "eod_20260603.txt"
    report.write_text(
        "\n".join(
            [
                "EOD wrapper result",
                "==================",
                "Exit code   : 0",
                "Duration sec: 12",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main([str(report), "--json"]) == 0
    output = capsys.readouterr().out

    payload = json.loads(output)
    assert payload["status"] == "complete"
    assert payload["market_date"] == "20260603"
    assert payload["exit_code"] == 0
