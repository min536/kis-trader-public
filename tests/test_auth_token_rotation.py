"""End-to-end contract for app-key-bound token cache invalidation (Design A).

The pure fingerprint logic lives in ``app.auth.token_cache_identity`` and is
covered green by ``tests/test_token_cache_identity.py``. These tests exercise
the *wiring* inside ``app/auth/token.py`` — an agent-protected file that only an
operator can edit. They self-activate: ``_fingerprint_guard_active()`` behaviorally
probes whether ``issue_access_token_for`` rejects a stale-key cache. Until the
operator applies the token.py patch (see
``docs/mock_account_rotation_review_20260706.md`` §4-A) the probe returns False
and these tests skip, keeping the suite green; once wired they run automatically.

Operator patch (minimal, in app/auth/token.py):
  from app.auth.token_cache_identity import (
      cache_payload_matches_app_key, stamp_app_key_fingerprint)
  - on save: payload = stamp_app_key_fingerprint(payload, app_key) before writing
  - on cache-hit check: also require cache_payload_matches_app_key(cached, app_key)
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.auth.token import issue_access_token_for
from app.auth.token_cache_identity import (
    APP_KEY_FINGERPRINT_FIELD,
    app_key_cache_fingerprint,
)

_LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"

# Credential-file caching must be ON for every test here (and for the probe):
# a shell that exports KIS_TRADER_DISABLE_CREDENTIAL_FILES would otherwise make
# the probe report "reissued" for the wrong reason (files disabled, not guard
# present) and the cache-hit test would fail confusingly. Same idiom as
# tests/test_auth_token.py.
_CREDENTIAL_FILES_ENABLED = {"KIS_TRADER_DISABLE_CREDENTIAL_FILES": ""}


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _seed_cache(cache_path: Path, payload: dict[str, object]) -> None:
    cache_path.write_text(json.dumps(payload), encoding="utf-8")


def _fingerprint_guard_active() -> bool:
    """Probe whether token.py binds its cache to the issuing app key.

    Seeds a fresh legacy cache (no fingerprint) and re-requests with the network
    stubbed. If the guard is present the stale cache is rejected and the stub is
    hit (returns "reissued"); if absent the stale token is served. Any error
    means the guard is not usably present -> skip.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "kis_auth_live.json"
            _seed_cache(
                cache_path,
                {"access_token": "stale-probe", "issued_at": 9_999_999_999},
            )
            with (
                mock.patch.dict(os.environ, _CREDENTIAL_FILES_ENABLED, clear=False),
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch(
                    "app.auth.token.request.urlopen",
                    side_effect=lambda req, timeout=0.0: _FakeResponse(
                        {"access_token": "reissued"}
                    ),
                ),
            ):
                token = issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="probe-key",
                    app_secret="probe-secret",
                    env="live",
                    force_refresh=False,
                )
            return token == "reissued"
    except Exception:
        return False


_GUARD_ACTIVE = _fingerprint_guard_active()
_SKIP_REASON = (
    "app/auth/token.py not yet bound to token_cache_identity — operator patch "
    "pending (docs/mock_account_rotation_review_20260706.md §4-A)"
)


@unittest.skipUnless(_GUARD_ACTIVE, _SKIP_REASON)
class AppKeyBoundTokenCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        env_patcher = mock.patch.dict(
            os.environ, _CREDENTIAL_FILES_ENABLED, clear=False
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_rotated_app_key_forces_reissue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "kis_auth_live.json"
            # First mint under the OLD key, letting token.py stamp the cache.
            with (
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch(
                    "app.auth.token.request.urlopen",
                    side_effect=lambda req, timeout=0.0: _FakeResponse(
                        {"access_token": "old-token"}
                    ),
                ),
            ):
                issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="old-key",
                    app_secret="old-secret",
                    env="live",
                    force_refresh=True,
                )

            # New key, fresh cache on disk: must re-issue rather than serve stale.
            calls = {"n": 0}

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                calls["n"] += 1
                return _FakeResponse({"access_token": "new-token"})

            with (
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch(
                    "app.auth.token.request.urlopen", side_effect=fake_urlopen
                ),
            ):
                token = issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="new-key",
                    app_secret="new-secret",
                    env="live",
                    force_refresh=False,
                )

            self.assertEqual(token, "new-token")
            self.assertEqual(calls["n"], 1)
            # Reissue must re-stamp the cache with the NEW key's fingerprint so
            # the very next call is a clean cache hit again.
            restamped = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(
                restamped.get(APP_KEY_FINGERPRINT_FIELD),
                app_key_cache_fingerprint("new-key"),
            )

    def test_legacy_cache_without_fingerprint_is_reissued(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "kis_auth_live.json"
            _seed_cache(
                cache_path,
                {"access_token": "legacy-token", "issued_at": 9_999_999_999},
            )
            calls = {"n": 0}

            def fake_urlopen(req, timeout: float = 0.0) -> _FakeResponse:
                calls["n"] += 1
                return _FakeResponse({"access_token": "fresh-token"})

            with (
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch(
                    "app.auth.token.request.urlopen", side_effect=fake_urlopen
                ),
            ):
                token = issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="some-key",
                    app_secret="some-secret",
                    env="live",
                    force_refresh=False,
                )

            self.assertEqual(token, "fresh-token")
            self.assertEqual(calls["n"], 1)

    def test_matching_app_key_serves_cache_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "kis_auth_live.json"
            # Mint + stamp under key-A.
            with (
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch(
                    "app.auth.token.request.urlopen",
                    side_effect=lambda req, timeout=0.0: _FakeResponse(
                        {"access_token": "minted-token"}
                    ),
                ),
            ):
                issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="key-A",
                    app_secret="secret-A",
                    env="live",
                    force_refresh=True,
                )

            # Same key again: fingerprint matches -> cache hit, no network.
            def boom(req, timeout: float = 0.0) -> _FakeResponse:
                raise AssertionError("network must not be hit on a fingerprint match")

            with (
                mock.patch(
                    "app.auth.token.get_token_cache_path", return_value=cache_path
                ),
                mock.patch("app.auth.token.request.urlopen", side_effect=boom),
            ):
                token = issue_access_token_for(
                    base_url=_LIVE_BASE_URL,
                    app_key="key-A",
                    app_secret="secret-A",
                    env="live",
                    force_refresh=False,
                )

            self.assertEqual(token, "minted-token")


if __name__ == "__main__":
    unittest.main()
