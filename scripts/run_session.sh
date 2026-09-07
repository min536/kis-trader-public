#!/usr/bin/env bash
# run_session.sh — Start kis-trader for one regular session (09:00–15:30 KST)
#
# Usage:
#   ./scripts/run_session.sh            # normal: runs until 15:30 KST then stops
#   ./scripts/run_session.sh --dry-run  # prints what would run, doesn't start
#
# The script:
#   1. Acquires a single-instance fcntl lock (prevents duplicate runs)
#   2. Starts "python3 -m app.main" in the background
#   3. Waits 10s and confirms the process is still alive (startup health check)
#   4. Waits until 15:30:00 KST (wall-clock)
#   5. Sends SIGTERM → waits up to 15s → SIGKILL if still alive
#   6. Logs start/stop events to logs/session_scheduler.log

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR_OVERRIDE:-$(dirname "$SCRIPT_DIR")}"
PYTHON="${PYTHON_OVERRIDE:-${PROJECT_DIR}/.venv/bin/python3}"
SESSION_ENV_FILE="${SESSION_ENV_FILE_OVERRIDE:-${PROJECT_DIR}/config/regular_session.env}"
LOG_DIR="${LOG_DIR_OVERRIDE:-${PROJECT_DIR}/logs}"
SESSION_LOG="${SESSION_LOG_OVERRIDE:-${LOG_DIR}/session_scheduler.log}"
PID_FILE="${PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader.pid}"
LOCK_FILE="${LOCK_FILE_OVERRIDE:-${LOG_DIR}/kis_trader.session.lock}"
LOCK_HELPER="${LOCK_HELPER_OVERRIDE:-${PROJECT_DIR}/scripts/session_lock.py}"
LOCK_READY_FILE="${LOCK_READY_FILE_OVERRIDE:-${LOG_DIR}/.kis_trader_session_lock.ready}"
LOCK_HELPER_PID_FILE="${LOCK_HELPER_PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_session_lock.pid}"
LIVE_SNAPSHOT_REFRESH_PID_FILE="${LIVE_SNAPSHOT_REFRESH_PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_live_snapshot.pid}"
LIVE_SNAPSHOT_REFRESH_HEARTBEAT_FILE="${LIVE_SNAPSHOT_REFRESH_HEARTBEAT_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_live_snapshot.heartbeat}"
APP_STDOUT_LOG_LINK="${APP_STDOUT_LOG_LINK_OVERRIDE:-${LOG_DIR}/app_stdout.log}"
APP_STDERR_LOG_LINK="${APP_STDERR_LOG_LINK_OVERRIDE:-${LOG_DIR}/app_stderr.log}"
WAKE_GUARD_PID_FILE="${WAKE_GUARD_PID_FILE_OVERRIDE:-${LOG_DIR}/kis_trader_wake_guard.pid}"
WAKE_GUARD_ENABLED="${WAKE_GUARD_ENABLED_OVERRIDE:-true}"
CAFFEINATE_BIN="${CAFFEINATE_BIN_OVERRIDE:-$(command -v caffeinate 2>/dev/null || true)}"
SLACK_BOT_SUPERVISOR_ENABLED="${SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE:-true}"
SLACK_BOT_SUPERVISOR_SCRIPT="${SLACK_BOT_SUPERVISOR_SCRIPT_OVERRIDE:-${SCRIPT_DIR}/run_slack_bot.sh}"
SLACK_BOT_SUPERVISOR_PID_FILE="${SLACK_BOT_SUPERVISOR_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot_supervisor.pid}"
SLACK_BOT_PID_FILE="${SLACK_BOT_PID_FILE_OVERRIDE:-${LOG_DIR}/slack_bot.pid}"

# ---------------------------------------------------------------------------
# Stop time: 15:30:00 KST
# ---------------------------------------------------------------------------
STOP_HOUR="${STOP_HOUR_OVERRIDE:-15}"
STOP_MIN="${STOP_MIN_OVERRIDE:-30}"
STOP_SEC="${STOP_SEC_OVERRIDE:-0}"

# Startup health-check window (seconds to observe after fork)
STARTUP_WATCH_SECS="${STARTUP_WATCH_SECS_OVERRIDE:-10}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    local msg="$1"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S %Z')
    echo "[${ts}] ${msg}" | tee -a "${SESSION_LOG}"
}

session_log_date() {
    if [[ -n "${SESSION_DATE_OVERRIDE:-}" ]]; then
        echo "${SESSION_DATE_OVERRIDE}"
        return
    fi
    TZ=Asia/Seoul date '+%Y%m%d'
}

# Seconds until HH:MM:SS today (or 0 if already past)
seconds_until() {
    local h=$1 m=$2 s=$3
    local now_epoch target_epoch today
    now_epoch=$(date +%s)
    today=$(date '+%Y-%m-%d')
    target_epoch=$(date -j -f '%Y-%m-%d %H:%M:%S' \
        "${today} $(printf '%02d:%02d:%02d' "$h" "$m" "$s")" '+%s' 2>/dev/null \
        || date -d "${today} $(printf '%02d:%02d:%02d' "$h" "$m" "$s")" '+%s')
    local diff=$(( target_epoch - now_epoch ))
    echo $(( diff < 0 ? 0 : diff ))
}

graceful_stop() {
    local pid=$1
    if ! kill -0 "$pid" 2>/dev/null; then
        log "Process ${pid} already exited (may have crashed mid-session)."
        return
    fi
    log "Sending SIGTERM to pid=${pid} ..."
    kill -TERM "$pid" 2>/dev/null || true

    local waited=0
    while kill -0 "$pid" 2>/dev/null && (( waited < 15 )); do
        sleep 1
        waited=$(( waited + 1 ))
    done

    if kill -0 "$pid" 2>/dev/null; then
        log "Process ${pid} still alive after ${waited}s — sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
    else
        log "Process ${pid} exited cleanly after ${waited}s."
    fi
}

load_session_env() {
    if [[ ! -f "${SESSION_ENV_FILE}" ]]; then
        log "No session override file found at ${SESSION_ENV_FILE}; using .env/default settings."
        return
    fi

    set -a
    # shellcheck disable=SC1090
    source "${SESSION_ENV_FILE}"
    set +a

    export KIS_SESSION_ENV_FILE="${SESSION_ENV_FILE}"
    export KIS_ENFORCE_REGULAR_SESSION_RUNTIME="true"

    log "Loaded session override file: ${SESSION_ENV_FILE}"
    local session_override_names=(
        "BUY_SCAN_INTERVAL_SECONDS"
        "SELL_CHECK_INTERVAL_SECONDS"
        "SCAN_SYMBOLS_MAX_PER_CYCLE"
        "BUY_SCAN_SHALLOW_TOP_K"
        "BUY_SCAN_DEEP_EVAL_LIMIT"
        "LIVE_SNAPSHOT_TTL_SECONDS"
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS"
        "API_SOFT_MAX_REQUESTS_PER_SECOND"
        "API_SOFT_MAX_QUOTES_PER_TICK"
        "API_MIN_INTER_REQUEST_SECONDS"
        "API_BUY_SCAN_MIN_REQUEST_RESERVE"
        "API_BUY_SCAN_MIN_QUOTE_RESERVE"
        "BUY_SCAN_QUOTE_KIS_ENV"
        "BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND"
        "BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS"
        "BUY_MIN_SCORE"
        "BUY_MAX_BUDGET_PER_TRADE_KRW"
        "BUY_RULE_REQUIRED_PASS_COUNT"
    )
    local override_name
    for override_name in "${session_override_names[@]}"; do
        if [[ -n "${!override_name:-}" ]]; then
            log "  session override ${override_name}=${!override_name}"
        fi
    done
    log "  regular-session runtime enforcement=enabled"
}

recommended_runtime_value() {
    local name="$1"
    case "${name}" in
        BUY_SCAN_INTERVAL_SECONDS) echo "60" ;;
        SELL_CHECK_INTERVAL_SECONDS) echo "30" ;;
        SCAN_SYMBOLS_MAX_PER_CYCLE) echo "200" ;;
        BUY_SCAN_SHALLOW_TOP_K) echo "200" ;;
        BUY_SCAN_DEEP_EVAL_LIMIT) echo "200" ;;
        LIVE_SNAPSHOT_TTL_SECONDS) echo "420" ;;
        LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS) echo "180" ;;
        API_SOFT_MAX_REQUESTS_PER_SECOND) echo "4" ;;
        API_SOFT_MAX_QUOTES_PER_TICK) echo "20" ;;
        API_MIN_INTER_REQUEST_SECONDS) echo "1.10" ;;
        API_BUY_SCAN_MIN_REQUEST_RESERVE) echo "3" ;;
        API_BUY_SCAN_MIN_QUOTE_RESERVE) echo "4" ;;
        *) echo "" ;;
    esac
}

runtime_value_from_session_file() {
    local name="$1"
    if [[ ! -f "${SESSION_ENV_FILE}" ]]; then
        return
    fi
    grep -E "^${name}=" "${SESSION_ENV_FILE}" | tail -n 1 | cut -d '=' -f 2- || true
}

print_runtime_control_preview() {
    echo "  RUNTIME_CONTROL_SUMMARY:"
    echo "    parameter | effective | source category | recommended | status"
    local names=(
        "BUY_SCAN_INTERVAL_SECONDS"
        "SELL_CHECK_INTERVAL_SECONDS"
        "SCAN_SYMBOLS_MAX_PER_CYCLE"
        "BUY_SCAN_SHALLOW_TOP_K"
        "BUY_SCAN_DEEP_EVAL_LIMIT"
        "LIVE_SNAPSHOT_TTL_SECONDS"
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS"
        "API_SOFT_MAX_REQUESTS_PER_SECOND"
        "API_SOFT_MAX_QUOTES_PER_TICK"
        "API_MIN_INTER_REQUEST_SECONDS"
        "API_BUY_SCAN_MIN_REQUEST_RESERVE"
        "API_BUY_SCAN_MIN_QUOTE_RESERVE"
    )
    local name recommended effective source status
    local mismatch_count=0
    for name in "${names[@]}"; do
        recommended=$(recommended_runtime_value "${name}")
        effective=$(runtime_value_from_session_file "${name}")
        source="default"
        if [[ -n "${effective}" ]]; then
            source="session override"
        else
            effective="${recommended}"
        fi
        status="OK"
        if [[ "${effective}" != "${recommended}" ]]; then
            status="MISMATCH"
            mismatch_count=$(( mismatch_count + 1 ))
        fi
        echo "    ${name} | ${effective} | ${source} | ${recommended} | ${status}"
    done
    if (( mismatch_count > 0 )); then
        echo "  [warn] regular-session runtime-control mismatches detected | count=${mismatch_count}"
    else
        echo "  [info] regular-session runtime-control values match recommended session profile."
    fi
}

live_snapshot_refresh_interval_seconds() {
    echo "${LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS_OVERRIDE:-${LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS:-180}}"
}

live_snapshot_ttl_seconds() {
    echo "${LIVE_SNAPSHOT_TTL_SECONDS_OVERRIDE:-${LIVE_SNAPSHOT_TTL_SECONDS:-420}}"
}

live_snapshot_refresh_top_n() {
    echo "${LIVE_SNAPSHOT_TOP_N_OVERRIDE:-30}"
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
    local command guard_name
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    guard_name="$(basename "${CAFFEINATE_BIN:-caffeinate}")"
    [[ "${command}" == *"${guard_name}"* || "${command}" == *"caffeinate"* ]]
}

write_live_snapshot_refresh_heartbeat() {
    local status="$1"
    local heartbeat_tmp="${LIVE_SNAPSHOT_REFRESH_HEARTBEAT_FILE}.tmp"
    {
        echo "timestamp=$(date '+%Y-%m-%dT%H:%M:%S%z')"
        echo "status=${status}"
        echo "app_pid=${APP_PID:-}"
        echo "worker_pid=${LIVE_SNAPSHOT_REFRESH_PID:-}"
        echo "consecutive_failures=${LIVE_SNAPSHOT_REFRESH_CONSECUTIVE_FAILURES:-0}"
        echo "last_success_at=${LIVE_SNAPSHOT_REFRESH_LAST_SUCCESS_AT:-}"
    } > "${heartbeat_tmp}"
    mv "${heartbeat_tmp}" "${LIVE_SNAPSHOT_REFRESH_HEARTBEAT_FILE}"
}

start_session_wake_guard() {
    if [[ "${WAKE_GUARD_ENABLED}" != "true" ]]; then
        log "Session wake guard disabled by WAKE_GUARD_ENABLED_OVERRIDE."
        return
    fi
    if [[ -z "${CAFFEINATE_BIN}" || ! -x "${CAFFEINATE_BIN}" ]]; then
        log "Session wake guard unavailable: caffeinate command not found."
        return
    fi
    if [[ -z "${APP_PID:-}" ]] || ! kill -0 "${APP_PID}" 2>/dev/null; then
        log "Session wake guard not started because app pid is unavailable."
        return
    fi

    if [[ -f "${WAKE_GUARD_PID_FILE}" ]]; then
        local existing_pid
        existing_pid=$(cat "${WAKE_GUARD_PID_FILE}" 2>/dev/null || true)
        if process_matches_wake_guard "${existing_pid}"; then
            log "Session wake guard already running pid=${existing_pid}; duplicate start blocked."
            WAKE_GUARD_PID="${existing_pid}"
            return
        fi
        log "Removing stale session wake guard pid file."
        rm -f "${WAKE_GUARD_PID_FILE}"
    fi

    "${CAFFEINATE_BIN}" -dims -w "${APP_PID}" >> "${SESSION_LOG}" 2>&1 &
    WAKE_GUARD_PID=$!
    echo "${WAKE_GUARD_PID}" > "${WAKE_GUARD_PID_FILE}"
    log "Session wake guard started via caffeinate pid=${WAKE_GUARD_PID} app_pid=${APP_PID}"
}

stop_session_wake_guard() {
    local guard_pid="${WAKE_GUARD_PID:-}"
    if [[ -z "${guard_pid}" && -f "${WAKE_GUARD_PID_FILE}" ]]; then
        guard_pid=$(cat "${WAKE_GUARD_PID_FILE}" 2>/dev/null || true)
    fi

    if [[ -z "${guard_pid}" ]]; then
        rm -f "${WAKE_GUARD_PID_FILE}"
        return
    fi

    if process_matches_wake_guard "${guard_pid}"; then
        log "Stopping session wake guard pid=${guard_pid} ..."
        kill -TERM "${guard_pid}" 2>/dev/null || true
    else
        log "Removing stale session wake guard reference pid=${guard_pid}."
    fi
    rm -f "${WAKE_GUARD_PID_FILE}"
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

start_slack_bot_supervisor() {
    if [[ "${SLACK_BOT_SUPERVISOR_ENABLED}" != "true" ]]; then
        log "Slack bot supervisor disabled by SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE."
        return
    fi
    if [[ ! -x "${SLACK_BOT_SUPERVISOR_SCRIPT}" ]]; then
        log "Slack bot supervisor script not executable: ${SLACK_BOT_SUPERVISOR_SCRIPT}"
        return
    fi

    if [[ -f "${SLACK_BOT_SUPERVISOR_PID_FILE}" ]]; then
        local existing_pid
        existing_pid=$(cat "${SLACK_BOT_SUPERVISOR_PID_FILE}" 2>/dev/null || true)
        if process_matches_slack_bot_supervisor "${existing_pid}"; then
            log "Slack bot supervisor already running pid=${existing_pid}; reusing."
            SLACK_BOT_SUPERVISOR_PID="${existing_pid}"
            return
        fi
        log "Removing stale slack bot supervisor pid file."
        rm -f "${SLACK_BOT_SUPERVISOR_PID_FILE}"
    fi

    "${SLACK_BOT_SUPERVISOR_SCRIPT}" >> "${SESSION_LOG}" 2>&1 < /dev/null &
    SLACK_BOT_SUPERVISOR_PID=$!
    log "Slack bot supervisor started pid=${SLACK_BOT_SUPERVISOR_PID}"
}

stop_slack_bot_supervisor() {
    local supervisor_pid="${SLACK_BOT_SUPERVISOR_PID:-}"
    if [[ -z "${supervisor_pid}" && -f "${SLACK_BOT_SUPERVISOR_PID_FILE}" ]]; then
        supervisor_pid=$(cat "${SLACK_BOT_SUPERVISOR_PID_FILE}" 2>/dev/null || true)
    fi

    if [[ -z "${supervisor_pid}" ]]; then
        return
    fi

    if process_matches_slack_bot_supervisor "${supervisor_pid}"; then
        log "Stopping slack bot supervisor pid=${supervisor_pid} ..."
        kill -TERM "${supervisor_pid}" 2>/dev/null || true

        local waited=0
        while kill -0 "${supervisor_pid}" 2>/dev/null && (( waited < 12 )); do
            sleep 1
            waited=$(( waited + 1 ))
        done
        if kill -0 "${supervisor_pid}" 2>/dev/null; then
            log "Slack bot supervisor pid=${supervisor_pid} still alive after ${waited}s — sending SIGKILL."
            kill -KILL "${supervisor_pid}" 2>/dev/null || true
        else
            log "Slack bot supervisor pid=${supervisor_pid} exited after ${waited}s."
        fi
    else
        log "Removing stale slack bot supervisor reference pid=${supervisor_pid}."
    fi

    # Belt-and-braces: if the bot child outlived its supervisor, kill it too.
    if [[ -f "${SLACK_BOT_PID_FILE}" ]]; then
        local bot_pid
        bot_pid=$(cat "${SLACK_BOT_PID_FILE}" 2>/dev/null || true)
        if [[ -n "${bot_pid}" ]] && kill -0 "${bot_pid}" 2>/dev/null; then
            kill -TERM "${bot_pid}" 2>/dev/null || true
        fi
        rm -f "${SLACK_BOT_PID_FILE}"
    fi
    rm -f "${SLACK_BOT_SUPERVISOR_PID_FILE}"
}

configure_day_scoped_app_logs() {
    SESSION_LOG_DATE="$(session_log_date)"
    APP_STDOUT_LOG="${LOG_DIR}/app_stdout_${SESSION_LOG_DATE}.log"
    APP_STDERR_LOG="${LOG_DIR}/app_stderr_${SESSION_LOG_DATE}.log"
    mkdir -p "${LOG_DIR}"
    : > "${APP_STDOUT_LOG}"
    : > "${APP_STDERR_LOG}"
    ln -sfn "$(basename "${APP_STDOUT_LOG}")" "${APP_STDOUT_LOG_LINK}"
    ln -sfn "$(basename "${APP_STDERR_LOG}")" "${APP_STDERR_LOG_LINK}"
}

stop_live_snapshot_refresh_worker() {
    local worker_pid="${LIVE_SNAPSHOT_REFRESH_PID:-}"
    if [[ -z "${worker_pid}" && -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" ]]; then
        worker_pid=$(cat "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" 2>/dev/null || true)
    fi

    if [[ -z "${worker_pid}" ]]; then
        rm -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
        return
    fi

    if process_matches_live_snapshot_worker "${worker_pid}"; then
        log "Stopping live snapshot refresh worker pid=${worker_pid} ..."
        kill -TERM "${worker_pid}" 2>/dev/null || true

        local waited=0
        while kill -0 "${worker_pid}" 2>/dev/null && (( waited < 5 )); do
            sleep 1
            waited=$(( waited + 1 ))
        done

        if kill -0 "${worker_pid}" 2>/dev/null; then
            log "Live snapshot refresh worker pid=${worker_pid} still alive after ${waited}s."
        else
            log "Live snapshot refresh worker pid=${worker_pid} exited after ${waited}s."
        fi
    else
        log "Removing stale live snapshot refresh worker reference pid=${worker_pid}."
    fi

    rm -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
}

start_live_snapshot_refresh_worker() {
    local refresh_interval ttl_seconds top_n
    refresh_interval=$(live_snapshot_refresh_interval_seconds)
    ttl_seconds=$(live_snapshot_ttl_seconds)
    top_n=$(live_snapshot_refresh_top_n)

    if [[ -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" ]]; then
        local existing_pid
        existing_pid=$(cat "${LIVE_SNAPSHOT_REFRESH_PID_FILE}" 2>/dev/null || true)
        if process_matches_live_snapshot_worker "${existing_pid}"; then
            log "Live snapshot refresh worker already running pid=${existing_pid}; duplicate start blocked."
            LIVE_SNAPSHOT_REFRESH_PID="${existing_pid}"
            return
        fi
        log "Removing stale live snapshot refresh worker pid file."
        rm -f "${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
    fi

    (
        trap 'exit 0' TERM INT

        LIVE_SNAPSHOT_REFRESH_PID="${BASHPID:-$$}"
        LIVE_SNAPSHOT_REFRESH_CONSECUTIVE_FAILURES=0
        LIVE_SNAPSHOT_REFRESH_LAST_SUCCESS_AT=""
        log "Live snapshot refresh worker started | pid=${LIVE_SNAPSHOT_REFRESH_PID} | interval=${refresh_interval}s | ttl=${ttl_seconds}s | top_n=${top_n}"
        while true; do
            if "${PYTHON}" "${PROJECT_DIR}/scripts/live_snapshot.py" --top-n "${top_n}" --ttl "${ttl_seconds}" >> "${SESSION_LOG}" 2>&1; then
                LIVE_SNAPSHOT_REFRESH_CONSECUTIVE_FAILURES=0
                LIVE_SNAPSHOT_REFRESH_LAST_SUCCESS_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
                write_live_snapshot_refresh_heartbeat "success"
            else
                local refresh_exit_status=$?
                LIVE_SNAPSHOT_REFRESH_CONSECUTIVE_FAILURES=$(( LIVE_SNAPSHOT_REFRESH_CONSECUTIVE_FAILURES + 1 ))
                write_live_snapshot_refresh_heartbeat "failure(${refresh_exit_status})"
                log "[warn] Live snapshot refresh tick failed | pid=${LIVE_SNAPSHOT_REFRESH_PID} | exit=${refresh_exit_status}"
            fi

            local slept=0
            while (( slept < refresh_interval )); do
                if [[ -n "${APP_PID:-}" ]] && ! kill -0 "${APP_PID}" 2>/dev/null; then
                    log "Live snapshot refresh worker stopping because app pid=${APP_PID} is no longer alive."
                    exit 0
                fi
                sleep 1
                slept=$(( slept + 1 ))
            done
        done
    ) &
    LIVE_SNAPSHOT_REFRESH_PID=$!
    echo "${LIVE_SNAPSHOT_REFRESH_PID}" > "${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
    write_live_snapshot_refresh_heartbeat "starting"
}

# ---------------------------------------------------------------------------
# Lock management  (fcntl lock is held by a small Python helper process)
# ---------------------------------------------------------------------------
acquire_lock() {
    mkdir -p "${LOG_DIR}"
    rm -f "${LOCK_READY_FILE}"

    "${PYTHON}" "${LOCK_HELPER}" hold \
        --lock-path "${LOCK_FILE}" \
        --owner-pid "$$" \
        --command "${SCRIPT_DIR}/run_session.sh -> ${PYTHON} -m app.main" \
        --ready-file "${LOCK_READY_FILE}" \
        >> "${SESSION_LOG}" 2>&1 &
    LOCK_HELPER_PID=$!
    echo "${LOCK_HELPER_PID}" > "${LOCK_HELPER_PID_FILE}"

    local waited=0
    while (( waited < 50 )); do
        if [[ -f "${LOCK_READY_FILE}" ]]; then
            rm -f "${LOCK_READY_FILE}"
            log "Acquired session lock via ${LOCK_FILE} (helper pid=${LOCK_HELPER_PID})"
            return
        fi
        if ! kill -0 "${LOCK_HELPER_PID}" 2>/dev/null; then
            local status_summary
            status_summary=$("${PYTHON}" "${LOCK_HELPER}" status --lock-path "${LOCK_FILE}" --expected-command "run_session.sh" --summary 2>/dev/null || true)
            log "ERROR: Already running or lock acquisition blocked. ${status_summary:-lock helper exited unexpectedly.}"
            exit 1
        fi
        sleep 0.1
        waited=$(( waited + 1 ))
    done

    log "ERROR: Timed out waiting for session lock readiness."
    exit 1
}

release_lock() {
    rm -f "${LOCK_READY_FILE}"
    if [[ -f "${LOCK_HELPER_PID_FILE}" ]]; then
        local helper_pid
        helper_pid=$(cat "${LOCK_HELPER_PID_FILE}" 2>/dev/null || true)
        if [[ -n "${helper_pid}" ]] && kill -0 "${helper_pid}" 2>/dev/null; then
            kill -TERM "${helper_pid}" 2>/dev/null || true
        fi
    fi
    rm -f "${LOCK_HELPER_PID_FILE}"
}

# ---------------------------------------------------------------------------
# Cleanup trap  (only registered after we own the lock)
# ---------------------------------------------------------------------------
_on_exit() {
    stop_live_snapshot_refresh_worker
    stop_session_wake_guard
    stop_slack_bot_supervisor
    release_lock
    rm -f "${PID_FILE}"
}

# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--dry-run" ]]; then
    SESSION_LOG_DATE="$(session_log_date)"
    echo "DRY RUN — would execute:"
    echo "  PROJECT_DIR        : ${PROJECT_DIR}"
    echo "  PYTHON             : ${PYTHON}"
    echo "  COMMAND            : cd ${PROJECT_DIR} && ${PYTHON} -m app.main"
    echo "  SESSION_ENV_FILE   : ${SESSION_ENV_FILE}"
    echo "  LOCK_FILE          : ${LOCK_FILE}"
    echo "  LIVE_SNAPSHOT_PID  : ${LIVE_SNAPSHOT_REFRESH_PID_FILE}"
    echo "  LIVE_SNAPSHOT_HB   : ${LIVE_SNAPSHOT_REFRESH_HEARTBEAT_FILE}"
    echo "  WAKE_GUARD_PID     : ${WAKE_GUARD_PID_FILE}"
    echo "  WAKE_GUARD         : ${WAKE_GUARD_ENABLED} (${CAFFEINATE_BIN:-unavailable})"
    echo "  SLACK_BOT_SUP_PID  : ${SLACK_BOT_SUPERVISOR_PID_FILE}"
    echo "  SLACK_BOT          : ${SLACK_BOT_SUPERVISOR_ENABLED} (${SLACK_BOT_SUPERVISOR_SCRIPT})"
    echo "  SESSION_LOG_DATE   : ${SESSION_LOG_DATE}"
    echo "  APP_STDOUT_LOG     : ${LOG_DIR}/app_stdout_${SESSION_LOG_DATE}.log"
    echo "  APP_STDERR_LOG     : ${LOG_DIR}/app_stderr_${SESSION_LOG_DATE}.log"
    echo "  APP_STDOUT_LINK    : ${APP_STDOUT_LOG_LINK}"
    echo "  APP_STDERR_LINK    : ${APP_STDERR_LOG_LINK}"
    if [[ -f "${SESSION_ENV_FILE}" ]]; then
        echo "  SESSION_OVERRIDES  :"
        grep -E '^[A-Z0-9_]+=' "${SESSION_ENV_FILE}" || true
    else
        echo "  SESSION_OVERRIDES  : (none)"
    fi
    print_runtime_control_preview
    echo "  SNAPSHOT_REFRESH   : interval=$(live_snapshot_refresh_interval_seconds)s ttl=$(live_snapshot_ttl_seconds)s top_n=$(live_snapshot_refresh_top_n)"
    echo "  STOP AT            : $(printf '%02d:%02d:%02d' $STOP_HOUR $STOP_MIN $STOP_SEC) KST"
    echo "  STARTUP_WATCH_SECS : ${STARTUP_WATCH_SECS}s"
    secs=$(seconds_until $STOP_HOUR $STOP_MIN $STOP_SEC)
    echo "  STOP IN            : ${secs}s from now"
    exit 0
fi

# ---------------------------------------------------------------------------
# Acquire lock (before any other side-effects)
# ---------------------------------------------------------------------------
acquire_lock

# Register cleanup AFTER acquiring the lock so we don't release a lock we don't own
trap '_on_exit' EXIT

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
log "=========================================="
log "Starting regular session"
log "  project : ${PROJECT_DIR}"
log "  python  : ${PYTHON}"
log "  stop at : $(printf '%02d:%02d:%02d' $STOP_HOUR $STOP_MIN $STOP_SEC) KST"
log "  lock    : ${LOCK_FILE}"

cd "${PROJECT_DIR}"
load_session_env
configure_day_scoped_app_logs
log "  app_stdout : ${APP_STDOUT_LOG}"
log "  app_stderr : ${APP_STDERR_LOG}"
"${PYTHON}" -m app.main >> "${APP_STDOUT_LOG}" 2>> "${APP_STDERR_LOG}" &
APP_PID=$!
echo "${APP_PID}" > "${PID_FILE}"
"${PYTHON}" "${LOCK_HELPER}" update \
    --lock-path "${LOCK_FILE}" \
    --pid "$$" \
    --app-pid "${APP_PID}" \
    --status "running" \
    --command "${SCRIPT_DIR}/run_session.sh -> ${PYTHON} -m app.main" \
    >/dev/null 2>&1 || true
log "Forked app.main  pid=${APP_PID}  (watching ${STARTUP_WATCH_SECS}s for startup health)"
start_session_wake_guard
start_live_snapshot_refresh_worker
start_slack_bot_supervisor

# ---------------------------------------------------------------------------
# Startup health check — confirm the process doesn't crash-exit immediately
# ---------------------------------------------------------------------------
for (( i=1; i<=STARTUP_WATCH_SECS; i++ )); do
    sleep 1
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        # Process died; capture its exit status (non-blocking wait is safe here
        # because we're the parent shell)
        wait "${APP_PID}" 2>/dev/null || true

        FAILURE_LOG="${LOG_DIR}/session_start_failure_$(date '+%Y%m%d').log"
        {
            echo "=== STARTUP FAILURE ==="
            echo "timestamp : $(date '+%Y-%m-%d %H:%M:%S %Z')"
            echo "pid       : ${APP_PID}"
            echo "died_after: ${i}s"
            echo "stdout_log : ${APP_STDOUT_LOG}"
            echo "stderr_log : ${APP_STDERR_LOG}"
            echo ""
            echo "--- Last 50 lines of stdout log ---"
            tail -50 "${APP_STDOUT_LOG}" 2>/dev/null || echo "(no stdout captured)"
            echo ""
            echo "--- Last 50 lines of stderr log ---"
            tail -50 "${APP_STDERR_LOG}" 2>/dev/null || echo "(no stderr captured)"
        } > "${FAILURE_LOG}"

        log "ERROR: app.main (pid=${APP_PID}) exited after ${i}s — startup failed."
        log "       Failure details written to: ${FAILURE_LOG}"

        # _on_exit trap will clean up lock + PID file
        exit 2
    fi
done

log "Startup confirmed — pid=${APP_PID} alive after ${STARTUP_WATCH_SECS}s."
log "Session start success."

# ---------------------------------------------------------------------------
# Wait until stop time  (poll wall-clock every 30s so macOS system-suspend
# doesn't cause the session to run into the next day — bash sleep doesn't
# advance during CPU suspend, but the RTC wall-clock does)
# ---------------------------------------------------------------------------
log "Waiting until $(printf '%02d:%02d:%02d' $STOP_HOUR $STOP_MIN $STOP_SEC) KST (wall-clock polling) ..."
while true; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        log "app.main pid=${APP_PID} exited before scheduled stop time. Ending session wrapper early."
        break
    fi
    secs=$(seconds_until $STOP_HOUR $STOP_MIN $STOP_SEC)
    if (( secs <= 0 )); then
        break
    fi
    # Sleep at most 5s at a time so manual stop/restart can release the wrapper promptly.
    chunk=$(( secs < 5 ? secs : 5 ))
    sleep "${chunk}"
done

# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------
log "Stop time reached. Stopping pid=${APP_PID} ..."
"${PYTHON}" "${LOCK_HELPER}" update \
    --lock-path "${LOCK_FILE}" \
    --pid "$$" \
    --app-pid "${APP_PID}" \
    --status "stopping" \
    >/dev/null 2>&1 || true

# Re-read PID in case anything updated it (defensive)
if [[ -f "${PID_FILE}" ]]; then
    APP_PID=$(cat "${PID_FILE}")
fi

if kill -0 "${APP_PID}" 2>/dev/null; then
    graceful_stop "${APP_PID}"
else
    log "app.main pid=${APP_PID} already exited before stop request."
fi
# _on_exit trap will remove PID_FILE and release lock
log "Session ended."
log "=========================================="
