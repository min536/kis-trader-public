"""Safe read-only adapter for the sibling open-trading-api repository."""

from app.integrations.open_trading_api.adapter import (
    OPEN_TRADING_API_ROOT_ENV,
    ArtifactResult,
    CommandResult,
    RootResolution,
    append_audit_record,
    build_command_audit_record,
    is_loopback_url,
    read_csv_artifact,
    read_json_artifact,
    resolve_backtester_root,
    resolve_open_trading_api_root,
    run_allowed_open_trading_api_command,
)

__all__ = [
    "OPEN_TRADING_API_ROOT_ENV",
    "ArtifactResult",
    "CommandResult",
    "RootResolution",
    "is_loopback_url",
    "read_csv_artifact",
    "read_json_artifact",
    "resolve_backtester_root",
    "resolve_open_trading_api_root",
    "run_allowed_open_trading_api_command",
]

