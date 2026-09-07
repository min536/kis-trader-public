from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from app.auth.env_parsing import (
    is_valid_hhmm_window,
    parse_bool,
    parse_float,
    parse_int,
    parse_symbol_list,
    split_symbol_items,
    strip_quotes,
)
from app.auth.settings_fields import (
    build_adaptive_degraded_fields,
    build_buy_rule_fields,
    build_cost_model_fields,
    build_order_discipline_fields,
    build_pnl_brake_regime_fields,
    build_risk_limit_fields,
    build_scan_cadence_fields,
    build_sell_rule_fields,
    build_session_runtime_fields,
    build_targeting_fields,
)
from app.auth.runtime_validation import (
    ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME,
    INITIAL_ENV,
    LOADED_ENV_FILE_VALUES,
    RUNTIME_RECOMMENDED_DEFAULTS,
    RUNTIME_VALIDATED_PARAMETERS,
    SESSION_ENV_FILE_ENV_NAME,
    STARTUP_SANITY_BYPASS_ENV_NAMES,
    STRICT_RUNTIME_OVERRIDE_ENV_NAMES,
    build_runtime_parameter_validation_report,
    build_startup_sanity_report,
    detect_runtime_parameter_source,
    get_session_env_file_path,
    get_session_env_file_values,
    is_dotenv_loading_disabled,
    is_regular_session_runtime_profile_enforced,
    is_startup_sanity_bypass_enabled,
    is_strict_runtime_override_validation_enabled,
    load_env_file,
    read_env_file_values,
)


def _resolve_project_root() -> Path:
    """Keep this checkout isolated from any parent operator configuration."""
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _resolve_project_root()
TOKEN_CACHE_FILE = PROJECT_ROOT / ".token_cache.json"
_SUPPORTED_KIS_ENVS = {"mock", "live"}
_CREDENTIAL_CACHE_DIR_ENV = "KIS_CREDENTIAL_CACHE_DIR"
_DEFAULT_CREDENTIAL_CACHE_DIR = Path.home() / ".cache" / "kis-trader"
_KIS_BASE_URL_HOSTS: dict[str, frozenset[str]] = {
    "mock": frozenset({"openapivts.koreainvestment.com"}),
    "live": frozenset({"openapi.koreainvestment.com"}),
}
# Canonical read-only live host. Used as the base-URL fallback for the live
# BUY quote lane when no live base URL env var is supplied, so an unset
# KIS_BASE_LIVE_URL no longer hard-fails the quote lane (live key + secret stay
# required). scripts/live_snapshot.py imports this so both paths cannot drift.
LIVE_BASE_URL_DEFAULT = "https://openapi.koreainvestment.com:9443"
# Values moved verbatim to app/auth/runtime_validation.py (R2 settings slimming);
# the legacy underscore names stay bound for in-module call sites and patch seams.
# NOTE: _LOADED_ENV_FILE_VALUES/_INITIAL_ENV must stay the SAME objects as the
# runtime_validation module state so load_env_file() mutations remain visible.
_INITIAL_ENV = INITIAL_ENV
_LOADED_ENV_FILE_VALUES = LOADED_ENV_FILE_VALUES
_RUNTIME_VALIDATED_PARAMETERS = RUNTIME_VALIDATED_PARAMETERS
_RUNTIME_RECOMMENDED_DEFAULTS = RUNTIME_RECOMMENDED_DEFAULTS
_STRICT_RUNTIME_OVERRIDE_ENV_NAMES = STRICT_RUNTIME_OVERRIDE_ENV_NAMES
_STARTUP_SANITY_BYPASS_ENV_NAMES = STARTUP_SANITY_BYPASS_ENV_NAMES
_SESSION_ENV_FILE_ENV_NAME = SESSION_ENV_FILE_ENV_NAME
_ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME = ENFORCE_REGULAR_SESSION_RUNTIME_ENV_NAME
SLACK_NOTIFY_ORDER_SUBMITTED_ENV = "SLACK_NOTIFY_ORDER_SUBMITTED"


def get_token_cache_path(env: str) -> Path:
    normalized_env = str(env).strip().lower()
    cache_dir = Path(os.getenv(_CREDENTIAL_CACHE_DIR_ENV, "") or _DEFAULT_CREDENTIAL_CACHE_DIR)
    cache_name = "kis_auth_live.json" if normalized_env == "live" else "kis_auth_mock.json"
    return cache_dir.expanduser() / cache_name


# Bodies moved verbatim to app/auth/env_parsing.py (R2 settings slimming);
# the legacy underscore names stay bound for in-module call sites.
_strip_quotes = strip_quotes
_parse_bool = parse_bool


def get_slack_notify_order_submitted(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return _parse_bool(
        SLACK_NOTIFY_ORDER_SUBMITTED_ENV,
        str(source.get(SLACK_NOTIFY_ORDER_SUBMITTED_ENV, "false")),
    )


_parse_float = parse_float
_parse_int = parse_int
_split_symbol_items = split_symbol_items


def _get_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_non_empty(names: tuple[str, ...]) -> str:
    for name in names:
        value = _get_env(name)
        if value:
            return value
    return ""


def _classify_kis_base_url_host(hostname: str | None) -> str | None:
    host = str(hostname or "").strip().lower()
    if not host:
        return None
    for env, hosts in _KIS_BASE_URL_HOSTS.items():
        if host in hosts:
            return env
    return None


def classify_kis_base_url_env(base_url: str) -> str | None:
    parsed = urlparse(str(base_url or "").strip())
    return _classify_kis_base_url_host(parsed.hostname)


def validate_kis_base_url(base_url: str, *, expected_env: str | None = None) -> str:
    text = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("KIS base URL must be an https URL with a host.")
    if parsed.username or parsed.password:
        raise ValueError("KIS base URL must not include credentials.")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("KIS base URL must not include a path, query, or fragment.")

    actual_env = _classify_kis_base_url_host(parsed.hostname)
    if actual_env is None:
        allowed_hosts = ", ".join(
            sorted(host for hosts in _KIS_BASE_URL_HOSTS.values() for host in hosts)
        )
        raise ValueError(f"KIS base URL host is not allowed. Allowed hosts: {allowed_hosts}")

    normalized_expected = str(expected_env or "").strip().lower()
    if normalized_expected and normalized_expected not in _SUPPORTED_KIS_ENVS:
        raise ValueError("KIS environment must be mock or live.")
    if normalized_expected and actual_env != normalized_expected:
        raise ValueError(
            "KIS base URL host does not match the selected KIS environment."
        )

    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _infer_kis_env_from_base_url(base_url: str) -> str | None:
    return classify_kis_base_url_env(base_url)


def _resolve_kis_env() -> str | None:
    explicit_env = _get_env("KIS_ENV").lower()
    if explicit_env:
        if explicit_env not in _SUPPORTED_KIS_ENVS:
            raise ValueError("환경변수 KIS_ENV 는 mock 또는 live 여야 합니다.")
        return explicit_env

    generic_base_url = _get_env("KIS_BASE_URL")
    inferred_from_base_url = _infer_kis_env_from_base_url(generic_base_url)
    if inferred_from_base_url is not None:
        return inferred_from_base_url

    has_mock_scoped_value = any(
        _get_env(name)
        for name in (
            "KIS_APP_MOCK_KEY",
            "KIS_APP_MOCK_SECRET",
            "KIS_BASE_MOCK_URL",
            "KIS_MOCK_BASE_URL",
            "KIS_CANO_MOCK",
            "KIS_MOCK_CANO",
            "KIS_ACNT_PRDT_CD_MOCK",
            "KIS_MOCK_ACNT_PRDT_CD",
        )
    )
    has_live_scoped_value = any(
        _get_env(name)
        for name in (
            "KIS_APP_LIVE_KEY",
            "KIS_APP_live_KEY",
            "KIS_APP_LIVE_SECRET",
            "KIS_APP_live_SECRET",
            "KIS_BASE_LIVE_URL",
            "KIS_BASE_live_URL",
            "KIS_LIVE_BASE_URL",
            "KIS_CANO_LIVE",
            "KIS_CANO_live",
            "KIS_LIVE_CANO",
            "KIS_ACNT_PRDT_CD_LIVE",
            "KIS_ACNT_PRDT_CD_live",
            "KIS_LIVE_ACNT_PRDT_CD",
        )
    )

    if has_mock_scoped_value and not has_live_scoped_value:
        return "mock"
    if has_live_scoped_value and not has_mock_scoped_value:
        return "live"
    return None


def _resolve_scoped_kis_value(
    *,
    runtime_env: str | None,
    generic_names: tuple[str, ...],
    mock_names: tuple[str, ...] = (),
    live_names: tuple[str, ...] = (),
) -> str:
    if runtime_env == "mock":
        return _first_non_empty(mock_names + generic_names)
    if runtime_env == "live":
        return _first_non_empty(live_names + generic_names)
    return _first_non_empty(generic_names + mock_names + live_names)


_parse_symbol_list = parse_symbol_list
_is_valid_hhmm_window = is_valid_hhmm_window


# Bodies moved verbatim to app/auth/runtime_validation.py (R2 settings slimming);
# the legacy underscore names stay bound for in-module call sites and patch seams.
_load_env_file = load_env_file
_read_env_file_values = read_env_file_values
_get_session_env_file_path = get_session_env_file_path
_get_session_env_file_values = get_session_env_file_values
_detect_runtime_parameter_source = detect_runtime_parameter_source
_is_strict_runtime_override_validation_enabled = (
    is_strict_runtime_override_validation_enabled
)
_is_startup_sanity_bypass_enabled = is_startup_sanity_bypass_enabled
_is_regular_session_runtime_profile_enforced = (
    is_regular_session_runtime_profile_enforced
)


# build_runtime_parameter_validation_report / build_startup_sanity_report moved
# verbatim to app/auth/runtime_validation.py (R2 settings slimming) and are
# re-exported via the import block above for app.main and test imports.

if not is_dotenv_loading_disabled():
    _load_env_file(PROJECT_ROOT / ".env")


_GENERIC_APP_KEY_NAMES = ("KIS_APP_KEY",)
_MOCK_APP_KEY_NAMES = ("KIS_APP_MOCK_KEY", "KIS_MOCK_APP_KEY")
_LIVE_APP_KEY_NAMES = ("KIS_APP_LIVE_KEY", "KIS_APP_live_KEY", "KIS_LIVE_APP_KEY")
_GENERIC_APP_SECRET_NAMES = ("KIS_APP_SECRET",)
_MOCK_APP_SECRET_NAMES = ("KIS_APP_MOCK_SECRET", "KIS_MOCK_APP_SECRET")
_LIVE_APP_SECRET_NAMES = (
    "KIS_APP_LIVE_SECRET",
    "KIS_APP_live_SECRET",
    "KIS_LIVE_APP_SECRET",
)
_GENERIC_BASE_URL_NAMES = ("KIS_BASE_URL",)
_MOCK_BASE_URL_NAMES = ("KIS_BASE_MOCK_URL", "KIS_MOCK_BASE_URL")
_LIVE_BASE_URL_NAMES = (
    "KIS_BASE_LIVE_URL",
    "KIS_BASE_live_URL",
    "KIS_LIVE_BASE_URL",
)
_GENERIC_CANO_NAMES = ("KIS_CANO",)
_MOCK_CANO_NAMES = ("KIS_CANO_MOCK", "KIS_MOCK_CANO")
_LIVE_CANO_NAMES = ("KIS_CANO_LIVE", "KIS_CANO_live", "KIS_LIVE_CANO")
_GENERIC_ACNT_PRDT_CD_NAMES = ("KIS_ACNT_PRDT_CD",)
_MOCK_ACNT_PRDT_CD_NAMES = ("KIS_ACNT_PRDT_CD_MOCK", "KIS_MOCK_ACNT_PRDT_CD")
_LIVE_ACNT_PRDT_CD_NAMES = (
    "KIS_ACNT_PRDT_CD_LIVE",
    "KIS_ACNT_PRDT_CD_live",
    "KIS_LIVE_ACNT_PRDT_CD",
)


@dataclass(frozen=True)
class KisCredentialProfile:
    env: str
    base_url: str
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)


def _resolve_scoped_only_kis_value(
    *,
    runtime_env: str,
    mock_names: tuple[str, ...],
    live_names: tuple[str, ...],
) -> str:
    if runtime_env == "mock":
        return _first_non_empty(mock_names)
    if runtime_env == "live":
        return _first_non_empty(live_names)
    return ""


def resolve_kis_credential_profile(
    kis_env: str,
    *,
    allow_generic_fallback: bool = True,
) -> KisCredentialProfile:
    normalized_env = str(kis_env or "").strip().lower()
    if normalized_env not in _SUPPORTED_KIS_ENVS:
        raise ValueError("KIS credential profile env must be mock or live.")

    if allow_generic_fallback:
        app_key = _resolve_scoped_kis_value(
            runtime_env=normalized_env,
            generic_names=_GENERIC_APP_KEY_NAMES,
            mock_names=_MOCK_APP_KEY_NAMES,
            live_names=_LIVE_APP_KEY_NAMES,
        )
        app_secret = _resolve_scoped_kis_value(
            runtime_env=normalized_env,
            generic_names=_GENERIC_APP_SECRET_NAMES,
            mock_names=_MOCK_APP_SECRET_NAMES,
            live_names=_LIVE_APP_SECRET_NAMES,
        )
        base_url = _resolve_scoped_kis_value(
            runtime_env=normalized_env,
            generic_names=_GENERIC_BASE_URL_NAMES,
            mock_names=_MOCK_BASE_URL_NAMES,
            live_names=_LIVE_BASE_URL_NAMES,
        ).rstrip("/")
    else:
        app_key = _resolve_scoped_only_kis_value(
            runtime_env=normalized_env,
            mock_names=_MOCK_APP_KEY_NAMES,
            live_names=_LIVE_APP_KEY_NAMES,
        )
        app_secret = _resolve_scoped_only_kis_value(
            runtime_env=normalized_env,
            mock_names=_MOCK_APP_SECRET_NAMES,
            live_names=_LIVE_APP_SECRET_NAMES,
        )
        base_url = _resolve_scoped_only_kis_value(
            runtime_env=normalized_env,
            mock_names=_MOCK_BASE_URL_NAMES,
            live_names=_LIVE_BASE_URL_NAMES,
        ).rstrip("/")

    # Default the live base URL to the canonical read-only live host when none
    # was supplied (applies to both fallback branches). Live-only by design:
    # mock still requires an explicit base URL, and app_key/app_secret are never
    # defaulted, so the live lane still requires explicit live credentials.
    if normalized_env == "live" and not base_url:
        base_url = LIVE_BASE_URL_DEFAULT

    missing = []
    if not app_key:
        missing.append(f"KIS_APP_{normalized_env.upper()}_KEY")
    if not app_secret:
        missing.append(f"KIS_APP_{normalized_env.upper()}_SECRET")
    if not base_url:
        missing.append(f"KIS_BASE_{normalized_env.upper()}_URL")
    if missing:
        raise ValueError(
            f"KIS {normalized_env} credential profile is incomplete: {', '.join(missing)}"
        )

    return KisCredentialProfile(
        env=normalized_env,
        base_url=validate_kis_base_url(base_url, expected_env=normalized_env),
        app_key=app_key,
        app_secret=app_secret,
    )


@dataclass(frozen=True)
class Settings:
    app_key: str
    app_secret: str
    base_url: str
    cano: str
    acnt_prdt_cd: str
    symbol: str
    target_symbols_source: str
    target_symbols_raw: str
    target_symbols_split_items: tuple[str, ...]
    target_symbols: tuple[str, ...]
    buy_excluded_symbols: tuple[str, ...]
    qty: int
    buy_fee_bps: float
    sell_fee_bps: float
    sell_tax_bps: float
    buy_slippage_bps: float
    sell_slippage_bps: float
    expected_slippage_bps_base: float
    expected_cost_block_bps: float
    min_net_edge_bps: float
    min_net_profit_buffer_bps: float
    use_cost_aware_pnl: bool
    performance_benchmark_symbol: str
    buy_rule_enable_intraday_pullback: bool
    buy_rule_enable_rebound_from_low: bool
    buy_rule_enable_controlled_down_day: bool
    buy_rule_enable_gap_down_open: bool
    buy_rule_enable_range_recovery: bool
    buy_rule_enable_live_volume_rank: bool
    buy_rule_enable_live_volume_power_rank: bool
    buy_rule_rebound_from_low_pct: float
    buy_rule_controlled_down_day_min: float
    buy_rule_controlled_down_day_max: float
    buy_rule_gap_down_open_min_pct: float
    buy_rule_gap_down_open_max_pct: float
    buy_rule_range_recovery_min_ratio: float
    buy_rule_required_pass_count: int
    buy_rule_required_pass_count_core: int  # core bucket 전용 (기본값: buy_rule_required_pass_count)
    buy_min_passed_count: int
    buy_min_score: float
    buy_min_score_core: float  # core bucket 전용 (기본값: buy_min_score - 0.20)
    buy_max_budget_per_trade_krw: int
    buy_max_account_exposure_pct: float
    buy_max_qty_per_trade: int
    strict_sell_first: bool
    block_rebuy_symbols_bought_today: bool
    buy_block_on_blocked_preview: bool
    enable_buy_cooldown: bool
    allow_one_buy_per_symbol_per_day: bool
    rebuy_cooldown_minutes: int
    stop_loss_same_day_reentry_min_minutes: int
    same_symbol_max_buys_per_day: int
    buy_blocked_cooldown_minutes: int
    block_resell_symbols_sold_today: bool
    enable_sell_cooldown: bool
    allow_one_sell_trigger_per_symbol_per_day: bool
    sell_blocked_cooldown_minutes: int
    order_cooldown_minutes: int
    sell_enable: bool
    sell_stop_loss_pct: float
    sell_take_profit_pct: float
    sell_trailing_stop_pct: float
    sell_rule_enable_live_leadership_loss: bool
    sell_rule_enable_live_power_breakdown: bool
    enable_sell_test_scenarios: bool
    enable_sell_guard_selftest: bool
    sell_test_mode: str
    sell_exit_required_pass_count: int
    buy_enable_risk_guards: bool
    buy_daily_max_order_submissions: int
    sell_daily_max_order_submissions: int
    buy_daily_max_notional_krw: int
    sell_daily_max_notional_krw: int
    enable_rebalance_sell: bool
    enable_quality_rebalance_preview: bool
    rebalance_sell_max_submissions_per_day: int
    rebalance_min_score_delta: float
    rebalance_min_profit_buffer_bps: float
    rebalance_max_concentration_pct: float
    rebalance_min_net_edge_bps: float
    sell_check_interval_seconds: int
    buy_scan_interval_seconds: int
    scan_symbols_max_per_cycle: int
    buy_scan_profile_rotation_enabled: bool
    buy_scan_exploration_ratio: float
    buy_scan_core_fraction: float
    buy_scan_rotating_fraction: float
    buy_scan_shallow_top_k: int
    buy_scan_deep_eval_limit: int
    buy_scan_core_max: int
    buy_scan_top_k_candidates: int
    live_snapshot_ttl_seconds: int
    live_snapshot_refresh_interval_seconds: int
    buy_scan_prefetch_deadline_enabled: bool
    buy_scan_prefetch_overlap_enabled: bool
    buy_scan_quote_prefetch_deadline_seconds: float
    buy_scan_quote_request_timeout_seconds: float
    buy_scan_quote_max_attempts: int
    buy_scan_total_budget_seconds: float
    buy_scan_min_remaining_budget_seconds: float
    api_soft_max_requests_per_second: int
    api_soft_max_quotes_per_tick: int
    api_backoff_seconds_on_rate_limit: int
    api_min_inter_request_seconds: float
    api_buy_scan_min_request_reserve: int
    api_buy_scan_min_quote_reserve: int
    adaptive_midday_enabled: bool
    adaptive_midday_window: str
    adaptive_midday_buy_scan_interval_seconds: int
    adaptive_midday_sell_check_interval_seconds: int
    adaptive_midday_scan_symbols_max_per_cycle: int
    adaptive_midday_buy_scan_deep_eval_limit: int
    degraded_mode_enabled: bool
    degraded_mode_rate_limit_hits_in_10m: int
    degraded_mode_consecutive_backoff_cycles: int
    degraded_mode_duration_seconds: int
    degraded_mode_buy_scan_interval_seconds: int
    degraded_mode_sell_check_interval_seconds: int
    degraded_mode_scan_symbols_max_per_cycle: int
    degraded_mode_buy_scan_deep_eval_limit: int
    degraded_mode_sell_watch_max_holdings_per_tick: int
    enable_daily_pnl_brake: bool
    daily_pnl_warning_pct: float
    daily_pnl_buy_pause_pct: float
    daily_pnl_hard_stop_pct: float
    daily_pnl_cooldown_minutes: int
    regime_caution_drawdown_pct: float
    regime_risk_off_drawdown_pct: float
    regime_normal_multiplier: float
    regime_caution_multiplier: float
    regime_risk_off_multiplier: float
    enable_premarket_wait: bool
    run_mode: str
    run_once: bool
    run_interval_seconds: int
    confirm_buy: str
    lane_scheduler_enabled: bool
    order_gate_enabled: bool
    session_cycle_hard_budget_seconds: float
    slack_notify_order_submitted: bool


def get_settings() -> Settings:
    runtime_env = _resolve_kis_env()
    app_key = _resolve_scoped_kis_value(
        runtime_env=runtime_env,
        generic_names=_GENERIC_APP_KEY_NAMES,
        mock_names=_MOCK_APP_KEY_NAMES,
        live_names=_LIVE_APP_KEY_NAMES,
    )
    app_secret = _resolve_scoped_kis_value(
        runtime_env=runtime_env,
        generic_names=_GENERIC_APP_SECRET_NAMES,
        mock_names=_MOCK_APP_SECRET_NAMES,
        live_names=_LIVE_APP_SECRET_NAMES,
    )
    base_url = _resolve_scoped_kis_value(
        runtime_env=runtime_env,
        generic_names=_GENERIC_BASE_URL_NAMES,
        mock_names=_MOCK_BASE_URL_NAMES,
        live_names=_LIVE_BASE_URL_NAMES,
    ).rstrip("/")
    cano = _resolve_scoped_kis_value(
        runtime_env=runtime_env,
        generic_names=_GENERIC_CANO_NAMES,
        mock_names=_MOCK_CANO_NAMES,
        live_names=_LIVE_CANO_NAMES,
    )
    acnt_prdt_cd = _resolve_scoped_kis_value(
        runtime_env=runtime_env,
        generic_names=_GENERIC_ACNT_PRDT_CD_NAMES,
        mock_names=_MOCK_ACNT_PRDT_CD_NAMES,
        live_names=_LIVE_ACNT_PRDT_CD_NAMES,
    )
    missing = []
    if not app_key:
        missing.append("KIS_APP_KEY or KIS_APP_MOCK_KEY/KIS_APP_LIVE_KEY")
    if not app_secret:
        missing.append("KIS_APP_SECRET or KIS_APP_MOCK_SECRET/KIS_APP_LIVE_SECRET")
    if not base_url:
        missing.append("KIS_BASE_URL or KIS_BASE_MOCK_URL/KIS_BASE_LIVE_URL")
    if not cano:
        missing.append("KIS_CANO or KIS_CANO_MOCK/KIS_CANO_LIVE")
    if not acnt_prdt_cd:
        missing.append("KIS_ACNT_PRDT_CD or KIS_ACNT_PRDT_CD_MOCK/KIS_ACNT_PRDT_CD_LIVE")

    if missing:
        raise ValueError(f"환경변수가 비어 있습니다: {', '.join(missing)}")

    base_url = validate_kis_base_url(base_url, expected_env=runtime_env)

    targeting = build_targeting_fields()
    cost_model = build_cost_model_fields()
    buy_rules = build_buy_rule_fields()
    order_discipline = build_order_discipline_fields()
    sell_rules = build_sell_rule_fields()
    risk_limits = build_risk_limit_fields()
    scan_cadence = build_scan_cadence_fields()
    adaptive_degraded = build_adaptive_degraded_fields()
    pnl_brake_regime = build_pnl_brake_regime_fields()
    session_runtime = build_session_runtime_fields()

    return Settings(
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        slack_notify_order_submitted=get_slack_notify_order_submitted(),
        **asdict(targeting),
        **asdict(cost_model),
        **asdict(buy_rules),
        **asdict(order_discipline),
        **asdict(sell_rules),
        **asdict(risk_limits),
        **asdict(scan_cadence),
        **asdict(adaptive_degraded),
        **asdict(pnl_brake_regime),
        **asdict(session_runtime),
    )
