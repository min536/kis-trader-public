#!/usr/bin/env bash
# eod_wrapper.sh - capture the deterministic EOD pipeline into reports/.
#
# Usage:
#   ./scripts/eod_wrapper.sh <ACCOUNT> [DATE]
#   ACCOUNT=<ACCOUNT> ./scripts/eod_wrapper.sh
#   ACCOUNT=<ACCOUNT> DATE=<YYYYMMDD> ./scripts/eod_wrapper.sh
#
# This wrapper is launchd-friendly: it runs check_eod.sh, preserves its exit
# code, writes a single report file, appends a best-effort orchestrator
# postrun_audit (|| true — never affects the preserved exit code), and sends a
# best-effort operator Slack notification through the already-gated SlackNotifier.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR_OVERRIDE:-$(dirname "${SCRIPT_DIR}")}"
DEFAULT_PYTHON="${PROJECT_DIR}/.venv/bin/python3"
if [[ -x "${DEFAULT_PYTHON}" ]]; then
    PYTHON_DEFAULT_RESOLVED="${DEFAULT_PYTHON}"
else
    PYTHON_DEFAULT_RESOLVED="python3"
fi
PYTHON="${PYTHON_OVERRIDE:-${PYTHON_DEFAULT_RESOLVED}}"
CHECK_EOD_SCRIPT="${CHECK_EOD_SCRIPT_OVERRIDE:-${SCRIPT_DIR}/check_eod.sh}"
REPORTS_DIR="${REPORTS_DIR_OVERRIDE:-${PROJECT_DIR}/reports}"
NOTIFY="${EOD_WRAPPER_NOTIFY:-true}"
POSTRUN_AUDIT="${EOD_WRAPPER_POSTRUN_AUDIT:-true}"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/eod_wrapper.sh <ACCOUNT> [DATE]
  ACCOUNT=<ACCOUNT> ./scripts/eod_wrapper.sh
  ACCOUNT=<ACCOUNT> DATE=<YYYYMMDD> ./scripts/eod_wrapper.sh

Examples:
  ./scripts/eod_wrapper.sh mock_12345678_01
  ./scripts/eod_wrapper.sh mock_12345678_01 20260409
  ACCOUNT=mock_12345678_01 DATE=20260409 ./scripts/eod_wrapper.sh
EOF
}

resolve_latest_date() {
    "${PYTHON}" -c 'from app.tools.eod_health_check import detect_latest_market_date; import sys; print(detect_latest_market_date(sys.argv[1]))' "$1"
}

notify_postrun() {
    local event_type="$1"
    local exit_code="$2"
    local report_path="$3"
    local market_date="$4"
    local duration_sec="$5"

    if [[ "${NOTIFY}" =~ ^(0|false|False|no|NO)$ ]]; then
        printf 'Slack notification: skipped reason=disabled_by_wrapper\n'
        return 0
    fi

    "${PYTHON}" - "$event_type" "$exit_code" "$report_path" "$market_date" "$duration_sec" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from app.notifications.slack import SlackNotifier
from app.tools.eod_report_summary import (
    build_eod_report_summary,
    format_eod_report_summary,
)

event_type, exit_code, report_path, market_date, duration_sec = sys.argv[1:6]
path = Path(report_path)

try:
    summary = build_eod_report_summary(path)
    message = format_eod_report_summary(summary, market_date=market_date)
    details = summary.to_details()
except OSError:
    status_text = "complete" if event_type == "postrun.complete" else "failed"
    message = "\n".join(
        [
            f"EOD postrun {status_text}",
            f"date={market_date}",
            f"exit_code={exit_code}",
            f"duration_sec={duration_sec}",
            f"report={path}",
        ]
    )
    details = {}

details.update(
    {
        "market_date": market_date,
        "exit_code": exit_code,
        "duration_sec": duration_sec,
        "report": str(path),
    }
)

result = SlackNotifier().notify(
    event_type,
    message,
    details=details,
)
reason = f" reason={result.reason}" if result.reason else ""
channel = f" channel_env={result.channel_env_var}" if result.channel_env_var else ""
print(f"Slack notification: {result.status}{reason}{channel}")
PY
}

ACCOUNT="${1:-${ACCOUNT:-}}"
DATE_INPUT="${2:-${DATE:-}}"

# D5: when no account is supplied, resolve the currently active signature so a
# mock-account rotation does not silently stop EOD output for the new account
# (docs/dashboard_account_routing_design_20260707.md §D5). An explicit arg wins.
if [[ -z "${ACCOUNT}" ]]; then
    # Run from PROJECT_DIR: `-m app.tools...` needs the repo on sys.path, and
    # launchd invokes this wrapper with an arbitrary cwd.
    ACCOUNT="$(cd "${PROJECT_DIR}" && "${PYTHON}" -m app.tools.resolve_active_account 2>/dev/null || true)"
    if [[ -n "${ACCOUNT}" ]]; then
        printf 'EOD wrapper: no account arg — resolved active signature %s\n' "${ACCOUNT}" >&2
    fi
fi

if [[ $# -gt 2 || -z "${ACCOUNT}" ]]; then
    usage >&2
    exit 1
fi

if [[ -n "${DATE_INPUT}" && ! "${DATE_INPUT}" =~ ^[0-9]{8}$ ]]; then
    printf 'ERROR: DATE must be YYYYMMDD, got %s\n' "${DATE_INPUT}" >&2
    exit 1
fi

cd "${PROJECT_DIR}"
mkdir -p "${REPORTS_DIR}"

RESOLVED_DATE="${DATE_INPUT}"
if [[ -z "${RESOLVED_DATE}" ]]; then
    if ! RESOLVED_DATE="$(resolve_latest_date "${ACCOUNT}")"; then
        RESOLVED_DATE="$(TZ=Asia/Seoul date '+%Y%m%d')"
        REPORT_PATH="${REPORTS_DIR}/eod_${RESOLVED_DATE}.txt"
        {
            printf 'EOD wrapper failed before check_eod.sh\n'
            printf 'Account: %s\n' "${ACCOUNT}"
            printf 'Date   : unresolved (fallback report date %s)\n' "${RESOLVED_DATE}"
            printf 'Reason : failed to resolve latest market date\n'
        } > "${REPORT_PATH}"
        notify_output="$(notify_postrun "postrun.failed" "1" "${REPORT_PATH}" "${RESOLVED_DATE}" "0" 2>&1 || true)"
        printf '\n%s\n' "${notify_output}" >> "${REPORT_PATH}"
        printf '%s\n' "${notify_output}"
        printf 'EOD report: %s\n' "${REPORT_PATH}" >&2
        exit 1
    fi
fi

REPORT_PATH="${REPORTS_DIR}/eod_${RESOLVED_DATE}.txt"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S %Z')"
SECONDS=0

set +e
{
    printf 'EOD wrapper\n'
    printf '===========\n'
    printf 'Started : %s\n' "${STARTED_AT}"
    printf 'Project : %s\n' "${PROJECT_DIR}"
    printf 'Account : %s\n' "${ACCOUNT}"
    printf 'Date    : %s\n' "${RESOLVED_DATE}"
    printf 'Command : %q %q %q\n\n' "${CHECK_EOD_SCRIPT}" "${ACCOUNT}" "${RESOLVED_DATE}"
    "${CHECK_EOD_SCRIPT}" "${ACCOUNT}" "${RESOLVED_DATE}"
} > "${REPORT_PATH}" 2>&1
exit_code=$?
set -e

DURATION_SEC="${SECONDS}"
if [[ "${exit_code}" -eq 0 ]]; then
    EVENT_TYPE="postrun.complete"
else
    EVENT_TYPE="postrun.failed"
fi

{
    printf '\nEOD wrapper result\n'
    printf '==================\n'
    printf 'Exit code   : %s\n' "${exit_code}"
    printf 'Duration sec: %s\n' "${DURATION_SEC}"
} >> "${REPORT_PATH}"

# Best-effort orchestrator post-run audit, appended to the report. Wrapped in
# `|| true` and never touches ${exit_code}, so an audit failure can never
# escalate the EOD outcome (contract). Disable with EOD_WRAPPER_POSTRUN_AUDIT=0.
if [[ ! "${POSTRUN_AUDIT}" =~ ^(0|false|False|no|NO)$ ]]; then
    audit_output="$("${PYTHON}" -m app.tools.orchestrate postrun_audit --date "${RESOLVED_DATE}" --account "${ACCOUNT}" 2>&1 || true)"
    {
        printf '\nPostrun audit\n'
        printf '=============\n'
        printf '%s\n' "${audit_output}"
    } >> "${REPORT_PATH}"
fi

notify_output="$(notify_postrun "${EVENT_TYPE}" "${exit_code}" "${REPORT_PATH}" "${RESOLVED_DATE}" "${DURATION_SEC}" 2>&1 || true)"
{
    printf '%s\n' "${notify_output}"
} >> "${REPORT_PATH}"

printf 'EOD report: %s\n' "${REPORT_PATH}"
printf '%s\n' "${notify_output}"
exit "${exit_code}"
