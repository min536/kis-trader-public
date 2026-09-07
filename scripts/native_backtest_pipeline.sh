#!/usr/bin/env bash
# native_backtest_pipeline.sh
#
# The Slack backtest pipeline: runs the in-repo native minute-replay backtester
# (app.tools.run_native_backtest) over the collected KIS/Toss parquet store —
# NO external :8002 sidecar, no uv/npm, no HTTP server. This is the ONLY script
# app/notifications/backtest_control.py::launch_backtest_pipeline executes (the
# legacy open-trading-api sidecar wrapper was retired 2026-07-10,
# docs/todo_20260710.md §D).
#
# docs/slack_backtest_native_pipeline_design_20260707.md §4.2.
#
# Contract (subset used by the Slack launcher):
#   --account <ACCOUNT>   accepted+ignored (backward compat) — no longer used
#   --date <YYYYMMDD>     backtest window end (default: latest available)
#   --days N              replay window length in days (overrides the
#                         NATIVE_BACKTEST_MAX_DAYS env default)
#   --skip-run            freshness probe + plan only, no replay
#   --dry-run             echo the plan and exit (no python invocation)
#   --stop-after          no-op (there is no server to stop)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_OVERRIDE:-$PROJECT_ROOT/.venv/bin/python}"
MANIFEST="${NATIVE_BACKTEST_MANIFEST:-_workspace/gate2/paths.json}"
SYMBOLS_FILE="${NATIVE_BACKTEST_SYMBOLS_FILE:-}"
REPORTS_DIR="${REPORTS_DIR_OVERRIDE:-$PROJECT_ROOT/reports/native_backtest}"
MAX_DAYS="${NATIVE_BACKTEST_MAX_DAYS:-60}"
INITIAL_CAPITAL="${NATIVE_BACKTEST_INITIAL_CAPITAL:-100000000}"

ACCOUNT=""
DATE_ARG=""
SKIP_RUN=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account) ACCOUNT="$2"; shift 2 ;;
    --date) DATE_ARG="$2"; shift 2 ;;
    --days) MAX_DAYS="$2"; shift 2 ;;
    --skip-run) SKIP_RUN="1"; shift ;;
    --dry-run) DRY_RUN="1"; shift ;;
    --stop-after) shift ;;                 # no server to stop — accept + ignore
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

TIMESTAMP="$(TZ=Asia/Seoul date '+%Y%m%d_%H%M%S')"
OUT_PATH="${REPORTS_DIR}/native_backtest_${TIMESTAMP}.json"

CMD=("${PYTHON_BIN}" -m app.tools.run_native_backtest
     --manifest "${MANIFEST}"
     --max-days "${MAX_DAYS}"
     --initial-capital "${INITIAL_CAPITAL}"
     --out "${OUT_PATH}")
[[ -n "${SYMBOLS_FILE}" ]] && CMD+=(--symbols-file "${SYMBOLS_FILE}")
[[ -n "${DATE_ARG}" ]] && CMD+=(--end "${DATE_ARG}")
[[ -n "${SKIP_RUN}" ]] && CMD+=(--plan-only)

echo "native backtest pipeline"
echo "  manifest:  ${MANIFEST}"
echo "  window-end:${DATE_ARG:-<latest>}"
echo "  max-days:  ${MAX_DAYS}"
echo "  skip-run:  ${SKIP_RUN:-0}"
echo "  out:       ${OUT_PATH}"
echo "  command:   ${CMD[*]}"

if [[ -n "${DRY_RUN}" ]]; then
  echo "DRY-RUN: not executing"
  exit 0
fi

mkdir -p "${REPORTS_DIR}"
exec "${CMD[@]}"
