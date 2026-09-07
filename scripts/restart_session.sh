#!/usr/bin/env bash
# restart_session.sh — Safely restart a running kis-trader session
#
# Usage:
#   ./scripts/restart_session.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR_OVERRIDE:-$(dirname "$SCRIPT_DIR")}"
LOG_DIR="${LOG_DIR_OVERRIDE:-${PROJECT_DIR}/logs}"
SESSION_LOG="${SESSION_LOG_OVERRIDE:-${LOG_DIR}/session_scheduler.log}"
PID_FILE="${PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader.pid}"
LOCK_FILE="${LOCK_FILE_OVERRIDE:-${LOG_DIR}/kis_trader.session.lock}"
LOCK_HELPER="${LOCK_HELPER_OVERRIDE:-${PROJECT_DIR}/scripts/session_lock.py}"
PYTHON="${PYTHON_OVERRIDE:-${PROJECT_DIR}/.venv/bin/python3}"
RUN_SCRIPT="${RUN_SCRIPT_OVERRIDE:-${PROJECT_DIR}/scripts/run_session.sh}"
STOP_TIMEOUT_SECS="${STOP_TIMEOUT_SECS_OVERRIDE:-20}"
LIVE_SNAPSHOT_REFRESH_PID_FILE="${LIVE_SNAPSHOT_REFRESH_PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_live_snapshot.pid}"
WAKE_GUARD_PID_FILE="${WAKE_GUARD_PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_wake_guard.pid}"
SLACK_BOT_SUPERVISOR_PID_FILE="${SLACK_BOT_SUPERVISOR_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot_supervisor.pid}"
SLACK_BOT_PID_FILE="${SLACK_BOT_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot.pid}"

log() {
    local msg="$1"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S %Z')
    echo "[${ts}] ${msg}" | tee -a "${SESSION_LOG}"
}

process_matches_live_snapshot_worker() {
    local pid="$1"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        return 1
    fi
    local command
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    [[ "${command}" == *"live_snapshot.py"* ]]
}

process_matches_wake_guard() {
    local pid="$1"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        return 1
    fi
    local command
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    [[ "${command}" == *"caffeinate"* ]]
}

stop_session_wake_guard() {
    if [[ ! -f "${WAKE_GUARD_PID_FILE}" ]]; then
        return
    fi

    local guard_pid
    guard_pid=$(cat "${WAKE_GUARD_PID_FILE}" 2>/dev/null || true)
    if process_matches_wake_guard "${guard_pid}"; then
        log "Stopping session wake guard pid=${guard_pid} before restart."
        kill -TERM "${guard_pid}" 2>/dev/null || true
    elif [[ -n "${guard_pid}" ]]; then
        log "Removing stale session wake guard reference pid=${guard_pid}"
    fi

    rm -f "${WAKE_GUARD_PID_FILE}"
}

stop_live_snapshot_refresh_worker() {
    if [[ ! -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" ]]; then
        return
    fi

    local worker_pid
    worker_pid=$(cat "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" 2>/dev/null || true)
    if process_matches_live_snapshot_worker "${worker_pid}"; then
        log "Stopping live snapshot refresh worker pid=${worker_pid} before restart."
        kill -TERM "${worker_pid}" 2>/dev/null || true

        local waited=0
        while kill -0 "${worker_pid}" 2>/dev/null && (( waited < 5 )); do
            sleep 1
            waited=$(( waited + 1 ))
        done
        if kill -0 "${worker_pid}" 2>/dev/null; then
            log "Live snapshot refresh worker pid=${worker_pid} is still alive after ${waited}s."
        else
            log "Live snapshot refresh worker pid=${worker_pid} exited after ${waited}s."
        fi
    elif [[ -n "${worker_pid}" ]]; then
        log "Removing stale live snapshot refresh worker reference pid=${worker_pid}"
    fi

    rm -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
}

process_matches_slack_bot_supervisor() {
    local pid="$1"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        return 1
    fi
    local command
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    [[ "${command}" == *"run_slack_bot.sh"* ]]
}

stop_slack_bot_supervisor() {
    local supervisor_pid=""
    if [[ -f "${SLACK_BOT_SUPERVISOR_PID_FILE}" ]]; then
        supervisor_pid=$(cat "${SLACK_BOT_SUPERVISOR_PID_FILE}" 2>/dev/null || true)
    fi

    if [[ -n "${supervisor_pid}" ]]; then
        if process_matches_slack_bot_supervisor "${supervisor_pid}"; then
            log "Stopping slack bot supervisor pid=${supervisor_pid} before restart."
            kill -TERM "${supervisor_pid}" 2>/dev/null || true
            local waited=0
            while kill -0 "${supervisor_pid}" 2>/dev/null && (( waited < 12 )); do
                sleep 1
                waited=$(( waited + 1 ))
            done
            if kill -0 "${supervisor_pid}" 2>/dev/null; then
                log "Slack bot supervisor pid=${supervisor_pid} still alive after ${waited}s."
                kill -KILL "${supervisor_pid}" 2>/dev/null || true
            fi
        elif [[ -n "${supervisor_pid}" ]]; then
            log "Removing stale slack bot supervisor reference pid=${supervisor_pid}"
        fi
        rm -f "${SLACK_BOT_SUPERVISOR_PID_FILE}"
    fi

    if [[ -f "${SLACK_BOT_PID_FILE}" ]]; then
        local bot_pid
        bot_pid=$(cat "${SLACK_BOT_PID_FILE}" 2>/dev/null || true)
        if [[ -n "${bot_pid}" ]] && kill -0 "${bot_pid}" 2>/dev/null; then
            kill -TERM "${bot_pid}" 2>/dev/null || true
        fi
        rm -f "${SLACK_BOT_PID_FILE}"
    fi
}

wait_for_lock_release() {
    local waited=0
    while (( waited < STOP_TIMEOUT_SECS )); do
        local lock_held
        lock_held=$("${PYTHON}" "${LOCK_HELPER}" status --lock-path "${LOCK_FILE}" --field lock_held 2>/dev/null || true)
        if [[ "${lock_held}" != "True" && "${lock_held}" != "true" ]]; then
            return 0
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done
    return 1
}

resolve_running_pid() {
    local pid=""
    if [[ -f "${PID_FILE}" ]]; then
        pid=$(cat "${PID_FILE}" 2>/dev/null || true)
    fi
    if [[ -z "${pid}" ]] && [[ -x "${PYTHON}" ]]; then
        pid=$("${PYTHON}" "${LOCK_HELPER}" status --lock-path "${LOCK_FILE}" --field app_pid 2>/dev/null || true)
    fi
    echo "${pid}"
}

is_same_program_running() {
    local pid="$1"
    if [[ -z "${pid}" ]]; then
        return 1
    fi
    local same_program
    same_program=$("${PYTHON}" "${LOCK_HELPER}" status --lock-path "${LOCK_FILE}" --expected-command "run_session.sh -> app.main" --field same_program 2>/dev/null || true)
    [[ "${same_program}" == "True" || "${same_program}" == "true" ]]
}

mkdir -p "${LOG_DIR}"
CURRENT_PID="$(resolve_running_pid)"

if [[ -z "${CURRENT_PID}" ]]; then
    log "No active session detected. Starting a new session."
    exec "${RUN_SCRIPT}"
fi

if ! kill -0 "${CURRENT_PID}" 2>/dev/null; then
    log "Stale session reference detected for pid=${CURRENT_PID}; starting a new session."
    exec "${RUN_SCRIPT}"
fi

if ! is_same_program_running "${CURRENT_PID}"; then
    log "ERROR: Restart blocked because pid=${CURRENT_PID} is alive but does not match kis-trader session command."
    exit 1
fi

log "Restart requested for pid=${CURRENT_PID}"
"${PYTHON}" "${LOCK_HELPER}" update \
    --lock-path "${LOCK_FILE}" \
    --app-pid "${CURRENT_PID}" \
    --status "restarting" \
    >/dev/null 2>&1 || true

log "Sending SIGTERM to pid=${CURRENT_PID} before restart."
kill -TERM "${CURRENT_PID}" 2>/dev/null || true
stop_live_snapshot_refresh_worker
stop_session_wake_guard
stop_slack_bot_supervisor

waited=0
while kill -0 "${CURRENT_PID}" 2>/dev/null && (( waited < STOP_TIMEOUT_SECS )); do
    sleep 1
    waited=$(( waited + 1 ))
done

if kill -0 "${CURRENT_PID}" 2>/dev/null; then
    log "ERROR: Restart blocked. pid=${CURRENT_PID} is still alive after ${waited}s."
    exit 1
fi

if ! wait_for_lock_release; then
    log "ERROR: Restart blocked. session lock is still held after app pid=${CURRENT_PID} exited."
    exit 1
fi

log "Previous session exited after ${waited}s. Session lock released; starting new session."
log "Restart success: launching new regular session."
exec "${RUN_SCRIPT}"
