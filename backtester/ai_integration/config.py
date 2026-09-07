"""YAML-backed configuration for the AI integration layer.

The ``ai_integration`` block is optional in a ``.kis.yaml`` strategy file.
When absent (or when ``enabled: false``), the backtester runs byte-for-byte
identically to a build that never referenced the AI layer.

YAML shape
----------
::

    ai_integration:
        enabled: true           # master switch (default: false)
        mode: combined          # disabled | score_delta | veto | rerank | regime | combined
        cache_dir: data/ai_signals
        veto_threshold: 0.75    # sweepable
        delta_scale: 1.0        # sweepable
        fallback: passthrough   # passthrough | reject | warn

Usage
-----
>>> from backtester.ai_integration.config import AIIntegrationConfig
>>> cfg = AIIntegrationConfig.from_mapping(doc.get("ai_integration"))
>>> provider = cfg.build_provider() if cfg and cfg.is_active else None

Dotted-path sweep support
-------------------------
The CLI sweep/grid path accepts ``ai_integration.<field>=v1,v2,...``;
:meth:`AIIntegrationConfig.with_override` applies a single override
by dotted field name and validates the value.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Mapping, get_args

from backtester.ai_integration.provider import (
    AISignalProvider,
    Fallback,
    Mode,
    _VALID_FALLBACKS,
    _VALID_MODES,
)

__all__ = [
    "AIIntegrationConfig",
    "AI_DOTTED_PREFIX",
]


AI_DOTTED_PREFIX = "ai_integration."


@dataclass(frozen=True)
class AIIntegrationConfig:
    """Configuration for the optional AI signal layer.

    The defaults match \"AI off\" — constructing an instance with no
    arguments is equivalent to no ``ai_integration`` block at all.
    """

    enabled: bool = False
    mode: Mode = "disabled"
    cache_dir: str = ""
    veto_threshold: float = 0.75
    delta_scale: float = 1.0
    fallback: Fallback = "passthrough"

    # ── validation ───────────────────────────────────────────────────────
    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"ai_integration.mode must be one of {_VALID_MODES}, "
                f"got {self.mode!r}"
            )
        if self.fallback not in _VALID_FALLBACKS:
            raise ValueError(
                f"ai_integration.fallback must be one of {_VALID_FALLBACKS}, "
                f"got {self.fallback!r}"
            )
        if not (0.0 <= float(self.veto_threshold) <= 1.0):
            raise ValueError(
                "ai_integration.veto_threshold must be in [0, 1], "
                f"got {self.veto_threshold!r}"
            )
        if float(self.delta_scale) < 0.0:
            raise ValueError(
                "ai_integration.delta_scale must be >= 0, "
                f"got {self.delta_scale!r}"
            )
        if self.enabled and self.mode != "disabled" and not self.cache_dir:
            raise ValueError(
                "ai_integration.cache_dir is required when "
                "enabled=true and mode!=disabled"
            )

    # ── derived views ────────────────────────────────────────────────────
    @property
    def is_active(self) -> bool:
        """True iff the provider should actually be wired into the run."""
        return bool(self.enabled) and self.mode != "disabled"

    # ── builders ─────────────────────────────────────────────────────────
    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "AIIntegrationConfig":
        """Build a config from the ``ai_integration`` YAML block.

        ``None`` / empty mapping ⇒ AI off (defaults).
        """
        if not raw:
            return cls()
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"Unknown ai_integration fields: {sorted(unknown)}. "
                f"Valid fields: {sorted(known)}"
            )
        kwargs: dict[str, Any] = {}
        if "enabled" in raw:
            kwargs["enabled"] = bool(raw["enabled"])
        if "mode" in raw:
            kwargs["mode"] = str(raw["mode"])
        if "cache_dir" in raw:
            kwargs["cache_dir"] = str(raw["cache_dir"])
        if "veto_threshold" in raw:
            kwargs["veto_threshold"] = float(raw["veto_threshold"])
        if "delta_scale" in raw:
            kwargs["delta_scale"] = float(raw["delta_scale"])
        if "fallback" in raw:
            kwargs["fallback"] = str(raw["fallback"])
        return cls(**kwargs)

    def with_override(self, dotted_name: str, raw_value: Any) -> "AIIntegrationConfig":
        """Return a copy with a single ``ai_integration.<field>`` overridden.

        Used by the CLI sweep/grid code when a ``--param`` key starts with
        ``ai_integration.``.  Raises ``ValueError`` if the field is unknown.
        """
        if not dotted_name.startswith(AI_DOTTED_PREFIX):
            raise ValueError(
                f"Override name must start with {AI_DOTTED_PREFIX!r}, "
                f"got {dotted_name!r}"
            )
        field_name = dotted_name[len(AI_DOTTED_PREFIX):]
        valid = {f.name for f in fields(self)}
        if field_name not in valid:
            raise ValueError(
                f"Unknown ai_integration field {field_name!r}. "
                f"Valid fields: {sorted(valid)}"
            )
        # Type-coerce based on the current field's type.
        current = getattr(self, field_name)
        coerced = _coerce(field_name, raw_value, current)
        return replace(self, **{field_name: coerced})

    def build_provider(self) -> AISignalProvider | None:
        """Instantiate the provider, or return None if AI is off."""
        if not self.is_active:
            return None
        return AISignalProvider(
            cache_dir=self.cache_dir,
            mode=self.mode,
            veto_threshold=self.veto_threshold,
            delta_scale=self.delta_scale,
            fallback=self.fallback,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "cache_dir": self.cache_dir,
            "veto_threshold": self.veto_threshold,
            "delta_scale": self.delta_scale,
            "fallback": self.fallback,
        }


def _coerce(field_name: str, raw_value: Any, current: Any) -> Any:
    """Coerce a raw sweep value to the type of the corresponding field."""
    if field_name == "enabled":
        if isinstance(raw_value, bool):
            return raw_value
        s = str(raw_value).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Cannot coerce {raw_value!r} to bool for 'enabled'")
    if field_name in ("mode", "fallback", "cache_dir"):
        return str(raw_value)
    if field_name in ("veto_threshold", "delta_scale"):
        return float(raw_value)
    # Fallback: keep the current field's Python type if possible.
    return type(current)(raw_value)
