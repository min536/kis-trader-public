#!/usr/bin/env bash
# stop_slack_bot.sh — Stop the Slack Socket Mode bot supervisor and bot.
#
# Sends SIGTERM to the supervisor (which then stops the child bot cleanly).
# Falls back to killing the bot directly if no supervisor is recorded.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR_OVERRIDE:-$(dirname "$SCRIPT_DIR")}"
LOG_DIR="${LOG_DIR_OVERRIDE:-${PROJECT_DIR}/logs}"
BOT_PID_FILE="${BOT_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot.pid}"
SUPERVISOR_PID_FILE="${SUPERVISOR_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot_supervisor.pid}"
STOP_TIMEOUT_SECS="${STOP_TIMEOUT_SECS_OVERRIDE:-15}"

stop_pid() {
    local pid="$1"
    local label="$2"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        return 1
    fi
    echo "Sending SIGTERM to ${label} pid=${pid}"
    kill -TERM "${pid}" 2>/dev/null || true
    local waited=0
    while kill -0 "${pid}" 2>/dev/null && (( waited < STOP_TIMEOUT_SECS )); do
        sleep 1
        waited=$(( waited + 1 ))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        echo "${label} pid=${pid} still alive after ${waited}s — sending SIGKILL"
        kill -KILL "${pid}" 2>/dev/null || true
    else
        echo "${label} pid=${pid} exited after ${waited}s"
    fi
    return 0
}

handled="false"

if [[ -f "${SUPERVISOR_PID_FILE}" ]]; then
    SUPERVISOR_PID=$(cat "${SUPERVISOR_PID_FILE}" 2>/dev/null || true)
    if stop_pid "${SUPERVISOR_PID}" "supervisor"; then
        handled="true"
    fi
    rm -f "${SUPERVISOR_PID_FILE}"
fi

if [[ -f "${BOT_PID_FILE}" ]]; then
    BOT_PID=$(cat "${BOT_PID_FILE}" 2>/dev/null || true)
    if stop_pid "${BOT_PID}" "bot"; then
        handled="true"
    fi
    rm -f "${BOT_PID_FILE}"
fi

if [[ "${handled}" == "false" ]]; then
    echo "No live Slack bot supervisor or bot found."
fi
