"""App-key identity binding for the KIS OAuth token cache (Design A, R-1).

The env-scoped token cache (``kis_auth_{env}.json``) is keyed only by env, so a
token minted under a *previous* app key stays a cache hit after credential
rotation (a new mock-competition account) until its freshness window lapses.
Every API call then leaves with a stale-account token behind a new-key header
and is rejected wholesale — and there is no self-healing path, so the whole
session stays dead until the cached token expires.

These pure helpers bind a cache payload to the app key that minted it via a
truncated SHA-256 fingerprint (the raw key is never written to disk, mirroring
``app.auth.account_scope.get_account_signature``). ``app/auth/token.py`` stamps
the fingerprint on save and rejects a payload whose fingerprint is absent
(legacy cache) or mismatched (rotated key) on load, so rotation invalidates the
cache automatically.

No I/O, no network, no raw secret on disk — safe to unit-test directly.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

APP_KEY_FINGERPRINT_FIELD = "app_key_fingerprint"


def app_key_cache_fingerprint(app_key: str) -> str:
    """Return a short, non-reversible fingerprint of an app key.

    Same construction as ``account_scope.get_account_signature`` — a truncated
    SHA-256 hex digest. The raw key is never persisted; only this fingerprint is
    stored in the cache payload.
    """
    digest = hashlib.sha256(str(app_key or "").encode("utf-8")).hexdigest()
    return digest[:16]


def stamp_app_key_fingerprint(
    payload: Mapping[str, Any], app_key: str
) -> dict[str, Any]:
    """Return a copy of ``payload`` with the current app-key fingerprint set.

    Does not mutate the input mapping.
    """
    stamped = dict(payload)
    stamped[APP_KEY_FINGERPRINT_FIELD] = app_key_cache_fingerprint(app_key)
    return stamped


def cache_payload_matches_app_key(payload: Any, app_key: str) -> bool:
    """True only when ``payload`` was stamped by exactly ``app_key``.

    Fail-closed: a non-mapping payload, a missing/blank/non-string fingerprint
    field (legacy cache written before this guard), or a fingerprint that does
    not match the current key (rotated credentials) all return False, so the
    caller re-issues instead of serving a stale token.
    """
    if not isinstance(payload, Mapping):
        return False
    stored = payload.get(APP_KEY_FINGERPRINT_FIELD)
    if not isinstance(stored, str) or not stored:
        return False
    return stored == app_key_cache_fingerprint(app_key)
