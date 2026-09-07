from __future__ import annotations

from pathlib import Path
from typing import Any

from app.dashboard.account_discovery import AccountPresence, discover_accounts
from app.dashboard.data_loader import load_dashboard_data
from app.dashboard.metrics import build_top_summary


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _account_label(
    signature: str,
    data: dict[str, Any],
    *,
    active_signature: str | None = None,
    active_masked_display: str | None = None,
) -> str:
    if signature == active_signature and active_masked_display:
        return active_masked_display
    account = data.get("account_view") or {}
    masked = str(account.get("masked_account_display") or "").strip()
    if masked:
        return masked
    env = signature.split("_", 1)[0].upper() if signature else "ACCOUNT"
    suffix = signature[-8:] if len(signature) > 8 else signature
    return f"{env} · ...{suffix}"


def _option_from_presence(
    presence: AccountPresence,
    *,
    active_signature: str | None,
    active_masked_display: str | None,
) -> dict[str, Any]:
    data = load_dashboard_data(signature=presence.signature)
    summary = build_top_summary(data)
    return {
        "id": presence.signature,
        "label": _account_label(
            presence.signature,
            data,
            active_signature=active_signature,
            active_masked_display=active_masked_display,
        ),
        "env": presence.environment.upper(),
        "status": "active" if presence.signature == active_signature else "available",
        "equity": _int(summary.get("total_equity_krw")),
        "sources": list(presence.sources),
        "snapshot_health": summary.get("snapshot_health"),
    }


def build_v2_account_options(
    *,
    active_signature: str | None = None,
    active_masked_display: str | None = None,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build read-only v2 account selector options from local artifacts only."""

    options: list[dict[str, Any]] = []
    for presence in discover_accounts(data_dir=data_dir, logs_dir=logs_dir):
        try:
            options.append(
                _option_from_presence(
                    presence,
                    active_signature=active_signature,
                    active_masked_display=active_masked_display,
                )
            )
        except Exception as exc:
            options.append(
                {
                    "id": presence.signature,
                    "label": presence.signature,
                    "env": presence.environment.upper(),
                    "status": (
                        "active"
                        if presence.signature == active_signature
                        else "degraded"
                    ),
                    "equity": 0,
                    "sources": list(presence.sources),
                    "snapshot_health": f"load_error: {exc}",
                }
            )
    if active_signature and active_signature not in {option["id"] for option in options}:
        env = active_signature.split("_", 1)[0].upper()
        label = active_masked_display or f"{env} · ...{active_signature[-8:]}"
        options.insert(
            0,
            {
                "id": active_signature,
                "label": label,
                "env": env,
                "status": "active",
                "equity": 0,
                "sources": [],
                "snapshot_health": "no local artifacts",
            },
        )
    return options
