#!/usr/bin/env bash
# run_slack_bot.sh — Run the read-only Slack Socket Mode bot under a supervisor
#
# The bot has previously died silently (process killed by sleep/reboot) and was
# never restarted, leaving Slack `@project-signalor` commands unanswered. This
# wrapper keeps the bot alive by:
#   1. Acquiring a single-instance lock (no duplicate bots fighting for events)
#   2. Running `python -m app.notifications.slack_bot` in a restart loop
#   3. Backing off after rapid failures so we don't hammer Slack on outages
#   4. Writing pid + logs to logs/slack_bot.{pid,log,debug.log}
#
# Usage:
#   ./scripts/run_slack_bot.sh             # run forever in foreground
#   ./scripts/run_slack_bot.sh --once      # run a single attempt without restart
#
# Stop with `./scripts/stop_slack_bot.sh` or by sending SIGTERM to the
# supervisor pid stored in logs/slack_bot_supervisor.pid.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR_OVERRIDE:-$(dirname "$SCRIPT_DIR")}"
PYTHON="${PYTHON_OVERRIDE:-${PROJECT_DIR}/.venv/bin/python3}"
LOG_DIR="${LOG_DIR_OVERRIDE:-${PROJECT_DIR}/logs}"
BOT_LOG="${BOT_LOG_OVERRIDE:-${LOG_DIR}/slack_bot.log}"
BOT_DEBUG_LOG="${BOT_DEBUG_LOG_OVERRIDE:-${LOG_DIR}/slack_bot_debug.log}"
BOT_PID_FILE="${BOT_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot.pid}"
SUPERVISOR_PID_FILE="${SUPERVISOR_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot_supervisor.pid}"

MIN_BACKOFF_SECS="${MIN_BACKOFF_SECS_OVERRIDE:-5}"
MAX_BACKOFF_SECS="${MAX_BACKOFF_SECS_OVERRIDE:-300}"
RAPID_FAILURE_THRESHOLD_SECS="${RAPID_FAILURE_THRESHOLD_SECS_OVERRIDE:-60}"

ONESHOT="false"
if [[ "${1:-}" == "--once" ]]; then
    ONESHOT="true"
fi

mkdir -p "${LOG_DIR}"

log_supervisor() {
    local msg="$1"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S %Z')
    printf '[%s] supervisor: %s\n' "${ts}" "${msg}" | tee -a "${BOT_DEBUG_LOG}"
}

ensure_no_other_supervisor() {
    if [[ ! -f "${SUPERVISOR_PID_FILE}" ]]; then
        return
    fi
    local existing
    existing=$(cat "${SUPERVISOR_PID_FILE}" 2>/dev/null || true)
    if [[ -n "${existing}" ]] && kill -0 "${existing}" 2>/dev/null; then
        local cmd
        cmd=$(ps -p "${existing}" -o command= 2>/dev/null || true)
        if [[ "${cmd}" == *"run_slack_bot.sh"* ]]; then
            echo "Slack bot supervisor already running (pid=${existing})." >&2
            exit 1
        fi
    fi
    rm -f "${SUPERVISOR_PID_FILE}"
}

cleanup() {
    local child_pid="${1:-}"
    if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" 2>/dev/null; then
        log_supervisor "shutting down bot pid=${child_pid}"
        kill -TERM "${child_pid}" 2>/dev/null || true
        local waited=0
        while kill -0 "${child_pid}" 2>/dev/null && (( waited < 10 )); do
            sleep 1
            waited=$(( waited + 1 ))
        done
        if kill -0 "${child_pid}" 2>/dev/null; then
            kill -KILL "${child_pid}" 2>/dev/null || true
        fi
    fi
    rm -f "${BOT_PID_FILE}" "${SUPERVISOR_PID_FILE}"
}

CHILD_PID=""
on_signal() {
    log_supervisor "received signal, exiting"
    cleanup "${CHILD_PID}"
    exit 0
}
trap on_signal INT TERM

ensure_no_other_supervisor
echo $$ > "${SUPERVISOR_PID_FILE}"
log_supervisor "started (oneshot=${ONESHOT}, python=${PYTHON})"

backoff="${MIN_BACKOFF_SECS}"
while true; do
    start_epoch=$(date +%s)
    "${PYTHON}" -m app.notifications.slack_bot \
        >> "${BOT_LOG}" 2>&1 &
    CHILD_PID=$!
    echo "${CHILD_PID}" > "${BOT_PID_FILE}"
    log_supervisor "bot started pid=${CHILD_PID}"

    set +e
    wait "${CHILD_PID}"
    exit_code=$?
    set -e
    end_epoch=$(date +%s)
    duration=$(( end_epoch - start_epoch ))
    log_supervisor "bot exited pid=${CHILD_PID} code=${exit_code} duration=${duration}s"
    rm -f "${BOT_PID_FILE}"
    CHILD_PID=""

    if [[ "${ONESHOT}" == "true" ]]; then
        rm -f "${SUPERVISOR_PID_FILE}"
        exit "${exit_code}"
    fi

    if (( duration < RAPID_FAILURE_THRESHOLD_SECS )); then
        backoff=$(( backoff * 2 ))
        if (( backoff > MAX_BACKOFF_SECS )); then
            backoff="${MAX_BACKOFF_SECS}"
        fi
    else
        backoff="${MIN_BACKOFF_SECS}"
    fi
    log_supervisor "restarting in ${backoff}s"
    sleep "${backoff}"
done
