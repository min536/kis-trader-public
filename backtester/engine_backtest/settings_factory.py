"""Build a Settings object for backtesting, with optional overrides.

Usage
-----
from backtester.engine_backtest.settings_factory import make_settings, make_settings_from_yaml

# Use current .env defaults
settings = make_settings()

# Override specific params
settings = make_settings(
    buy_max_budget_per_trade_krw=500_000,
    sell_stop_loss_pct=-3.0,
    buy_rule_required_pass_count=2,
)

# Load from a .kis.yaml strategy file
settings, meta = make_settings_from_yaml("backtester/strategies/my_strategy.kis.yaml")
print(meta["name"])  # strategy name from YAML metadata

YAML → Settings field mapping
------------------------------
risk.stop_loss.percent        → sell_stop_loss_pct  (negated: 3.0 → -3.0)
risk.take_profit.percent      → sell_take_profit_pct
risk.trailing_stop.percent    → sell_trailing_stop_pct
risk.stop_loss.enabled        → sell_stop_loss_enabled (if field exists)
risk.take_profit.enabled      → sell_take_profit_enabled (if field exists)
risk.trailing_stop.enabled    → sell_trailing_stop_enabled (if field exists)
settings_overrides.*          → direct Settings field override (any field)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.auth.settings import get_settings, Settings


def make_settings(**overrides: Any) -> Settings:
    """Load Settings from env, then replace fields with overrides.

    Only the field names present in Settings are valid keys.
    """
    base = get_settings()
    if not overrides:
        return base

    valid_fields = {f.name for f in base.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(overrides) - valid_fields
    if unknown:
        raise ValueError(f"Unknown settings fields: {unknown}")

    # dataclass is frozen — rebuild with updated values
    current = {f: getattr(base, f) for f in valid_fields}
    current.update(overrides)
    return Settings(**current)


def make_settings_from_yaml(
    path: str | Path,
) -> tuple[Settings, dict[str, Any]]:
    """Load a .kis.yaml strategy file and return (Settings, metadata).

    Supported YAML sections
    -----------------------
    metadata:
        name: ...
        description: ...
        tags: [...]
    risk:
        stop_loss:
            enabled: true
            percent: 3.0    # → sell_stop_loss_pct = -3.0
        take_profit:
            enabled: true
            percent: 8.0    # → sell_take_profit_pct = 8.0
        trailing_stop:
            enabled: true
            percent: 2.5    # → sell_trailing_stop_pct = 2.5
    settings_overrides:     # arbitrary Settings field overrides
        buy_min_score: 3.5
        buy_rule_required_pass_count: 2

    Returns
    -------
    settings : Settings
        Fully merged Settings object (env base + yaml overrides).
    meta : dict
        Parsed metadata + resolved overrides for logging/reporting.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        raise ImportError(
            "PyYAML is required for YAML strategy loading. "
            "Install it with: pip install pyyaml"
        )

    content = Path(path).read_text(encoding="utf-8")
    doc: dict[str, Any] = yaml.safe_load(content) or {}

    overrides: dict[str, Any] = {}

    # ── risk section ───────────────────────────────────────────────────────
    risk = doc.get("risk") or {}

    stop_loss = risk.get("stop_loss") or {}
    if "percent" in stop_loss:
        pct = float(stop_loss["percent"])
        overrides["sell_stop_loss_pct"] = -abs(pct)   # always negative
    if "enabled" in stop_loss:
        _try_set(overrides, "sell_stop_loss_enabled", bool(stop_loss["enabled"]))

    take_profit = risk.get("take_profit") or {}
    if "percent" in take_profit:
        overrides["sell_take_profit_pct"] = float(take_profit["percent"])
    if "enabled" in take_profit:
        _try_set(overrides, "sell_take_profit_enabled", bool(take_profit["enabled"]))

    trailing_stop = risk.get("trailing_stop") or {}
    if "percent" in trailing_stop:
        overrides["sell_trailing_stop_pct"] = float(trailing_stop["percent"])
    if "enabled" in trailing_stop:
        _try_set(overrides, "sell_trailing_stop_enabled", bool(trailing_stop["enabled"]))

    # ── settings_overrides section ─────────────────────────────────────────
    raw_overrides = doc.get("settings_overrides") or {}
    overrides.update({str(k): v for k, v in raw_overrides.items()})

    settings = make_settings(**overrides)

    # ── ai_integration section (optional, opt-in) ──────────────────────────
    from backtester.ai_integration.config import AIIntegrationConfig

    ai_config = AIIntegrationConfig.from_mapping(doc.get("ai_integration"))

    # ── metadata for reporting ─────────────────────────────────────────────
    metadata_raw = doc.get("metadata") or {}
    meta: dict[str, Any] = {
        "name": metadata_raw.get("name", Path(path).stem),
        "description": metadata_raw.get("description", ""),
        "tags": list(metadata_raw.get("tags") or []),
        "source_path": str(path),
        "applied_overrides": dict(overrides),
        "ai_integration": ai_config.to_dict(),
        "ai_integration_config": ai_config,
    }

    return settings, meta


def make_settings_from_env_override(**env_vars: str) -> Settings:
    """Temporarily set env vars, build Settings, then restore.

    Useful for quick parameter sweeps without touching .env.
    """
    old_values: dict[str, str | None] = {}
    for key, val in env_vars.items():
        old_values[key] = os.environ.get(key)
        os.environ[key] = val
    try:
        return get_settings()
    finally:
        for key, old_val in old_values.items():
            if old_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_val


# ── internal helpers ───────────────────────────────────────────────────────

def _try_set(overrides: dict, field: str, value: Any) -> None:
    """Add field→value to overrides only if the field exists in Settings."""
    try:
        base = get_settings()
        valid_fields = {f.name for f in base.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        if field in valid_fields:
            overrides[field] = value
    except Exception:
        pass
