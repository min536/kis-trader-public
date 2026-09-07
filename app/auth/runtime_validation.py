"""Runtime parameter validation and startup sanity reporting.

Env-file value loading, runtime-parameter source detection, and the
startup validation/sanity report builders used by app.main at startup.
Bodies moved verbatim from app/auth/settings.py (R2 settings slimming).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from app.auth.env_parsing import parse_bool, strip_quotes
from app.core.kis_rate_limits import (
    KIS_MOCK_MIN_INTER_REQUEST_SECONDS,
    environment_from_base_url,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle exists only for type hints
    from app.auth.settings import Settings

INITIAL_ENV = dict(os.environ)
LOADED_ENV_FILE_VALUES: dict[str, str] = {}
SESSION_ENV_FILE_ENV_NAME = "KIS_SESSION_ENV_FILE"
STRICT_RUNTIME_OVERRIDE_ENV_NAMES: tuple[str, ...] = (
    "STRICT_RUNTIME_PARAM_OVERRIDES",
    "STRICT_RUNTIME_VALIDATION",
)
STARTUP_SANITY_BYPASS_ENV_NAMES: tuple[str, ...] = (
    "BYPASS_STARTUP_SANITY_CHECK",
    "ALLOW_UNSAFE_RUNTIME_PARAMS",
)
ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME = "KIS_ENFORCE_REGULAR_SESSION_RUNTIME"
DISABLE_DOTENV_ENV_NAME = "KIS_TRADER_DISABLE_DOTENV"
RUNTIME_VALIDATED_PARAMETERS: tuple[tuple[str, str], ...] = (
    ("BUY_SCAN_INTERVAL_SECONDS", "buy_scan_interval_seconds"),
    ("SELL_CHECK_INTERVAL_SECONDS", "sell_check_interval_seconds"),
    ("SCAN_SYMBOLS_MAX_PER_CYCLE", "scan_symbols_max_per_cycle"),
    ("BUY_SCAN_SHALLOW_TOP_K", "buy_scan_shallow_top_k"),
    ("BUY_SCAN_DEEP_EVAL_LIMIT", "buy_scan_deep_eval_limit"),
    ("BUY_SCAN_CORE_MAX", "buy_scan_core_max"),
    ("LIVE_SNAPSHOT_TTL_SECONDS", "live_snapshot_ttl_seconds"),
    (
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
        "live_snapshot_refresh_interval_seconds",
    ),
    (
        "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
        "buy_scan_quote_prefetch_deadline_seconds",
    ),
    (
        "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS",
        "buy_scan_quote_request_timeout_seconds",
    ),
    ("BUY_SCAN_QUOTE_MAX_ATTEMPTS", "buy_scan_quote_max_attempts"),
    ("BUY_SCAN_TOTAL_BUDGET_SECONDS", "buy_scan_total_budget_seconds"),
    ("API_SOFT_MAX_REQUESTS_PER_SECOND", "api_soft_max_requests_per_second"),
    ("API_SOFT_MAX_QUOTES_PER_TICK", "api_soft_max_quotes_per_tick"),
    ("API_MIN_INTER_REQUEST_SECONDS", "api_min_inter_request_seconds"),
    ("API_BUY_SCAN_MIN_REQUEST_RESERVE", "api_buy_scan_min_request_reserve"),
    ("API_BUY_SCAN_MIN_QUOTE_RESERVE", "api_buy_scan_min_quote_reserve"),
    ("SESSION_CYCLE_HARD_BUDGET_SECONDS", "session_cycle_hard_budget_seconds"),
    ("LANE_SCHEDULER_ENABLED", "lane_scheduler_enabled"),
)
RUNTIME_RECOMMENDED_DEFAULTS: dict[str, int | float] = {
    "BUY_SCAN_INTERVAL_SECONDS": 60,
    "SELL_CHECK_INTERVAL_SECONDS": 30,
    "SCAN_SYMBOLS_MAX_PER_CYCLE": 200,
    "BUY_SCAN_SHALLOW_TOP_K": 200,
    "BUY_SCAN_DEEP_EVAL_LIMIT": 200,
    "BUY_SCAN_CORE_MAX": 12,
    "LIVE_SNAPSHOT_TTL_SECONDS": 420,
    "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": 180,
    "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS": 18.0,
    "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS": 2.0,
    "BUY_SCAN_QUOTE_MAX_ATTEMPTS": 1,
    "BUY_SCAN_TOTAL_BUDGET_SECONDS": 25.0,
    "API_SOFT_MAX_REQUESTS_PER_SECOND": 4,
    "API_SOFT_MAX_QUOTES_PER_TICK": 20,
    "API_MIN_INTER_REQUEST_SECONDS": 1.1,
    "API_BUY_SCAN_MIN_REQUEST_RESERVE": 3,
    "API_BUY_SCAN_MIN_QUOTE_RESERVE": 4,
    "SESSION_CYCLE_HARD_BUDGET_SECONDS": 60.0,
    "LANE_SCHEDULER_ENABLED": 0,
}


def is_dotenv_loading_disabled() -> bool:
    return os.getenv(DISABLE_DOTENV_ENV_NAME, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def load_env_file(env_path: Path) -> None:
    if is_dotenv_loading_disabled():
        return
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = strip_quotes(value.strip())
        LOADED_ENV_FILE_VALUES[normalized_key] = normalized_value
        os.environ.setdefault(normalized_key, normalized_value)


def build_runtime_parameter_validation_report(settings: "Settings") -> dict[str, object]:
    entries: list[dict[str, object]] = []
    warnings: list[str] = []
    strict_violations: list[str] = []
    strict_mode_enabled = is_strict_runtime_override_validation_enabled()
    regular_session_runtime_profile_enforced = (
        is_regular_session_runtime_profile_enforced()
    )
    session_env_file_path = get_session_env_file_path()

    for env_name, attribute_name in RUNTIME_VALIDATED_PARAMETERS:
        effective_value = getattr(settings, attribute_name)
        recommended_value = RUNTIME_RECOMMENDED_DEFAULTS[env_name]
        source = detect_runtime_parameter_source(env_name)
        mismatched_from_recommended = effective_value != recommended_value

        entry = {
            "name": env_name,
            "attribute": attribute_name,
            "effective_value": effective_value,
            "recommended_value": recommended_value,
            "source": source,
            "matches_recommended_default": not mismatched_from_recommended,
        }
        entries.append(entry)

        if mismatched_from_recommended and source != "default":
            warning = (
                f"{env_name}={effective_value} (recommended={recommended_value}, "
                f"source={source})"
            )
            warnings.append(warning)
            if strict_mode_enabled:
                strict_violations.append(warning)

    return {
        "entries": entries,
        "warnings": warnings,
        "strict_mode_enabled": strict_mode_enabled,
        "strict_violations": strict_violations,
        "strict_blocked": bool(strict_violations),
        "regular_session_runtime_profile_enforced": regular_session_runtime_profile_enforced,
        "session_env_file_path": str(session_env_file_path) if session_env_file_path else "",
    }


def _mask_account_number(value: str) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def build_startup_sanity_report(
    settings: "Settings",
    *,
    runtime_parameter_report: dict[str, object] | None = None,
) -> dict[str, object]:
    runtime_parameter_report = (
        runtime_parameter_report
        if isinstance(runtime_parameter_report, dict)
        else build_runtime_parameter_validation_report(settings)
    )
    warnings: list[str] = []
    errors: list[str] = []
    bypass_enabled = is_startup_sanity_bypass_enabled()

    def _warn(message: str) -> None:
        warnings.append(message)

    def _error(message: str) -> None:
        errors.append(message)

    def _regular_session_mismatch_allowed(entry: object) -> bool:
        if not isinstance(entry, dict):
            return False
        if entry.get("name") != "LANE_SCHEDULER_ENABLED":
            return False
        return (
            entry.get("source") == "session config file"
            and bool(entry.get("effective_value")) is True
        )

    if int(settings.buy_scan_interval_seconds) < 60:
        _error(
            "BUY_SCAN_INTERVAL_SECONDS 가 60초 미만입니다. "
            f"(current={settings.buy_scan_interval_seconds}, required>=60)"
        )
    buy_scan_quote_env = os.getenv("BUY_SCAN_QUOTE_KIS_ENV", "").strip().lower()
    read_only_quote_lane = buy_scan_quote_env in {"mock", "live"}
    scan_symbol_limit = 250 if read_only_quote_lane else 40
    if int(settings.scan_symbols_max_per_cycle) > scan_symbol_limit:
        _error(
            f"SCAN_SYMBOLS_MAX_PER_CYCLE 가 {scan_symbol_limit}을 초과합니다. "
            f"(current={settings.scan_symbols_max_per_cycle}, required<={scan_symbol_limit})"
        )
    if int(settings.api_buy_scan_min_quote_reserve) < 2:
        _error(
            "API_BUY_SCAN_MIN_QUOTE_RESERVE 가 2 미만입니다. "
            f"(current={settings.api_buy_scan_min_quote_reserve}, required>=2)"
        )
    if int(settings.api_buy_scan_min_request_reserve) < 2:
        _error(
            "API_BUY_SCAN_MIN_REQUEST_RESERVE 가 2 미만입니다. "
            f"(current={settings.api_buy_scan_min_request_reserve}, required>=2)"
        )
    execution_env = environment_from_base_url(
        str(getattr(settings, "base_url", "") or "")
    )
    minimum_request_spacing = (
        KIS_MOCK_MIN_INTER_REQUEST_SECONDS if execution_env == "mock" else 0.8
    )
    if float(settings.api_min_inter_request_seconds) < minimum_request_spacing:
        _error(
            f"API_MIN_INTER_REQUEST_SECONDS 가 {minimum_request_spacing}초 미만입니다. "
            f"(current={settings.api_min_inter_request_seconds}, "
            f"required>={minimum_request_spacing})"
        )
    if int(settings.live_snapshot_ttl_seconds) < int(
        settings.live_snapshot_refresh_interval_seconds
    ):
        _error(
            "LIVE_SNAPSHOT_TTL_SECONDS 가 LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS 보다 작습니다. "
            f"(ttl={settings.live_snapshot_ttl_seconds}, "
            f"refresh={settings.live_snapshot_refresh_interval_seconds})"
        )

    # Credential-rotation guard (R-2): a competition account number pasted with a
    # hyphen, embedded space, or a merged product code would otherwise reach every
    # order/balance call unchecked. Only validate when present — empty CANO is
    # already rejected upstream by get_settings(), and test doubles omit it.
    cano_value = str(getattr(settings, "cano", "") or "").strip()
    if cano_value and not (len(cano_value) == 8 and cano_value.isdigit()):
        _error(
            "KIS_CANO 형식이 올바르지 않습니다. 계좌번호는 8자리 숫자여야 합니다 "
            "(하이픈·공백·상품코드 혼입 여부 확인). "
            f"(masked={_mask_account_number(cano_value)}, length={len(cano_value)})"
        )
    product_code_value = str(getattr(settings, "acnt_prdt_cd", "") or "").strip()
    if product_code_value and not (
        len(product_code_value) == 2 and product_code_value.isdigit()
    ):
        _warn(
            "KIS_ACNT_PRDT_CD 가 통상값(2자리 숫자)과 다릅니다. "
            f"(value={product_code_value})"
        )

    for item in tuple(runtime_parameter_report.get("warnings", ())):
        _warn(str(item))

    if runtime_parameter_report.get("regular_session_runtime_profile_enforced"):
        for entry in tuple(runtime_parameter_report.get("entries", ())):
            if entry.get("matches_recommended_default"):
                continue
            if _regular_session_mismatch_allowed(entry):
                continue
            _error(
                "정규 세션 런타임 제어값이 권장 세션 프로파일과 다릅니다. "
                f"({entry.get('name')}={entry.get('effective_value')}, "
                f"recommended={entry.get('recommended_value')}, "
                f"source={entry.get('source')})"
            )

    return {
        "warnings": warnings,
        "errors": errors,
        "bypass_enabled": bypass_enabled,
        "blocked": bool(errors) and not bypass_enabled,
    }


def is_strict_runtime_override_validation_enabled() -> bool:
    for env_name in STRICT_RUNTIME_OVERRIDE_ENV_NAMES:
        text = os.getenv(env_name, "").strip()
        if not text:
            continue
        return parse_bool(env_name, text)
    return False


def is_startup_sanity_bypass_enabled() -> bool:
    for env_name in STARTUP_SANITY_BYPASS_ENV_NAMES:
        text = os.getenv(env_name, "").strip()
        if not text:
            continue
        return parse_bool(env_name, text)
    return False


def is_regular_session_runtime_profile_enforced() -> bool:
    text = os.getenv(ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME, "").strip()
    if not text:
        return False
    return parse_bool(ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME, text)


def detect_runtime_parameter_source(name: str) -> str:
    current_value = os.environ.get(name)
    if current_value is None or not current_value.strip():
        return "default"

    normalized_current = current_value.strip()
    session_value = get_session_env_file_values().get(name)
    if session_value is not None and session_value.strip() == normalized_current:
        return "session config file"

    initial_value = INITIAL_ENV.get(name)
    if initial_value is not None and initial_value.strip() == normalized_current:
        return "environment override"

    config_value = LOADED_ENV_FILE_VALUES.get(name)
    if config_value is not None and config_value.strip() == normalized_current:
        return "config file"

    return "environment override"


def get_session_env_file_path() -> Path | None:
    raw_value = os.getenv(SESSION_ENV_FILE_ENV_NAME, "").strip()
    if not raw_value:
        return None
    return Path(raw_value)


def get_session_env_file_values() -> dict[str, str]:
    return read_env_file_values(get_session_env_file_path())


def read_env_file_values(env_path: Path | None) -> dict[str, str]:
    if env_path is None or not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = strip_quotes(value.strip())
    return values
