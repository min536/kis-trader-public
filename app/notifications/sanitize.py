"""Secret/account redaction helpers shared by notification senders.

Relocated verbatim from app.notifications.slack (R3-S1 reporting/notifications
slimming). app.notifications.slack keeps legacy bindings so existing import
sites and patch targets remain valid.
"""

from __future__ import annotations

import os
import re
from typing import Mapping

# Values mirror the channel/token env-var names defined in
# app.notifications.slack (kept as literals here so the sanitize module does
# not import the slack transport module — slack imports sanitize, not the
# other way around).
_SENSITIVE_ENV_NAMES = (
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNEL_PROJECT_OPERATOR",
    "SLACK_CHANNEL_PROJECT_ORDERS",
    "SLACK_CHANNEL_PROJECT_BOTTLENECKS",
    "SLACK_CHANNEL_PROJECT_ACTIVATOR",
    "SLACK_CHANNEL_PROJECT_BACK_TESTER",
    "SLACK_CHANNEL_PROJECT_SUMMARY",
    "SLACK_CHANNEL_PROJECT_PATH_FINDER",
    "SLACK_CHANNEL_PROJECT_SPECTATOR",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_CANO",
    "KIS_ACCOUNT_NO",
    "KIS_ACCOUNT_NUMBER",
    "APP_KEY",
    "APP_SECRET",
    "TOKEN",
)
_TOKEN_RE = re.compile(r"xox[a-z]-[A-Za-z0-9-]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b(token|app[_ -]?key|app[_ -]?secret|secret)\b(\s*[:=]\s*)[A-Za-z0-9._~+/=-]{4,}"
)
_LABELED_ACCOUNT_RE = re.compile(
    r"(?i)\b(account(?:[_ -]?(?:no|number))?|acct|cano)\b(\s*[:=]\s*)[0-9-]{6,}"
)
_KOREAN_ACCOUNT_RE = re.compile(r"(계좌(?:번호)?\s*[:=]?\s*)[0-9-]{6,}")


def _sensitive_env_values(env: Mapping[str, str]) -> list[str]:
    values: list[str] = []
    for name in _SENSITIVE_ENV_NAMES:
        value = str(env.get(name) or "")
        if len(value) >= 4:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def sanitize_text(value: object, env: Mapping[str, str] | None = None) -> str:
    text = "" if value is None else str(value)
    env_values = _sensitive_env_values(env or os.environ)
    for secret_value in env_values:
        text = text.replace(secret_value, "[REDACTED]")
    text = _TOKEN_RE.sub("[REDACTED]", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _LABELED_SECRET_RE.sub(r"\1\2[REDACTED]", text)
    text = _LABELED_ACCOUNT_RE.sub(r"\1\2[REDACTED]", text)
    text = _KOREAN_ACCOUNT_RE.sub(r"\1[REDACTED]", text)
    return text


_SENSITIVE_KEY_PATTERNS = (
    "token",
    "secret",
    "appkey",
    "app_key",
    "appsecret",
    "app_secret",
    "account",
    "accountno",
    "account_no",
    "accountnumber",
    "account_number",
    "acct",
    "cano",
    "acnt",
    "계좌",
)


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[\s_.-]+", "", str(key or "").lower())
    return any(pattern.replace("_", "") in normalized for pattern in _SENSITIVE_KEY_PATTERNS)


def _sanitize_details(
    details: Mapping[str, object] | None,
    env: Mapping[str, str],
) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in (details or {}).items():
        key_text = sanitize_text(key, env)
        if _is_sensitive_key(key):
            sanitized[key_text] = "[REDACTED]"
        else:
            sanitized[key_text] = sanitize_text(value, env)
    return sanitized
