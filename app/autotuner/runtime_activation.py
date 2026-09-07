"""Human-gated activation for Phase 3 runtime overrides.

This module is intentionally tiny: app.main wires into it, while all gate
decisions and fail-safe behavior stay outside the trading entrypoint.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.auth.settings import classify_kis_base_url_env

RUNTIME_OVERRIDES_FLAG = "AUTOTUNER_RUNTIME_OVERRIDES_ENABLED"
HIGH_RISK_OVERRIDES_FLAG = "AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def apply_runtime_overrides(
    runtime_rate_control: dict[str, object],
    *,
    settings,
    project_root,
    now,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    env_map = os.environ if environ is None else environ
    if not _flag_enabled(env_map.get(RUNTIME_OVERRIDES_FLAG)):
        return runtime_rate_control

    runtime_env = _runtime_env_from_settings(settings)
    if runtime_env != "mock":
        return runtime_rate_control

    try:
        overrides = _resolve_runtime_overrides(
            project_root=project_root,
            now=now,
            env=runtime_env,
            enabled=True,
        )
        if not overrides:
            return runtime_rate_control
        return _merge_safer_of(runtime_rate_control, overrides)
    except Exception:
        return runtime_rate_control


def apply_high_risk_overrides(
    regime_state, *, settings, project_root, now, environ=None
):
    if regime_state is None:
        return regime_state
    env_map = os.environ if environ is None else environ
    if not _flag_enabled(env_map.get(HIGH_RISK_OVERRIDES_FLAG)):
        return regime_state
    try:
        from app.autotuner.runtime_override import (
            merge_safer_of_settings,
            resolve_high_risk_overrides,
        )

        overrides = resolve_high_risk_overrides(
            project_root=project_root,
            now=now,
            env=_runtime_env_from_settings(settings),
            enabled_high_risk=True,
        )
        if not overrides:
            return regime_state
        effective_overrides = _scale_high_risk_overrides_to_regime(
            overrides,
            regime_state=regime_state,
            settings=settings,
        )
        current = {
            "rebuy_cooldown_minutes": regime_state.get(
                "effective_rebuy_cooldown_minutes"
            ),
            "same_symbol_max_buys_per_day": regime_state.get(
                "effective_same_symbol_max_buys_per_day"
            ),
        }
        merged = merge_safer_of_settings(current, effective_overrides)
        new_state = dict(regime_state)
        new_state["effective_rebuy_cooldown_minutes"] = merged["rebuy_cooldown_minutes"]
        new_state["effective_same_symbol_max_buys_per_day"] = merged[
            "same_symbol_max_buys_per_day"
        ]
        return new_state
    except Exception:
        # M1/W4: a reader feeding run_cycle must never raise -> leave regime as-is.
        return regime_state


def _flag_enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _runtime_env_from_settings(settings) -> str | None:
    explicit_env = str(getattr(settings, "kis_env", "") or "").strip().lower()
    if explicit_env in {"mock", "live"}:
        return explicit_env
    return classify_kis_base_url_env(str(getattr(settings, "base_url", "") or ""))


def _resolve_runtime_overrides(**kwargs):
    from app.autotuner.runtime_override import resolve_runtime_overrides

    return resolve_runtime_overrides(**kwargs)


def _scale_high_risk_overrides_to_regime(overrides, *, regime_state, settings):
    scaled = dict(overrides or {})
    override_cooldown = scaled.get("rebuy_cooldown_minutes")
    if not isinstance(override_cooldown, (int, float)):
        return scaled

    base_cooldown = getattr(settings, "rebuy_cooldown_minutes", None)
    current_effective = regime_state.get("effective_rebuy_cooldown_minutes")
    if not isinstance(base_cooldown, (int, float)) or base_cooldown <= 0:
        return scaled
    if not isinstance(current_effective, (int, float)) or current_effective <= 0:
        return scaled

    regime_multiplier = current_effective / base_cooldown
    if regime_multiplier <= 0:
        return scaled

    scaled["rebuy_cooldown_minutes"] = int(round(override_cooldown * regime_multiplier))
    return scaled


def _merge_safer_of(runtime_rate_control, overrides):
    from app.autotuner.runtime_override import merge_safer_of

    return merge_safer_of(runtime_rate_control, overrides)
