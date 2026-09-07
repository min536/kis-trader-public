import types
import unittest.mock

import pytest

from app.overseas_stock.runtime_env import (
    OverseasEnvError,
    OverseasLiveBlockedError,
    require_mock_env,
    resolve_overseas_env,
)


def test_resolve_overseas_env_mock_url():
    settings = types.SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443"
    )
    assert resolve_overseas_env(settings) == "mock"


def test_resolve_overseas_env_live_url():
    settings = types.SimpleNamespace(
        base_url="https://openapi.koreainvestment.com:9443"
    )
    assert resolve_overseas_env(settings) == "live"


def test_resolve_overseas_env_unknown_url_raises():
    settings = types.SimpleNamespace(base_url="https://example.test")
    with pytest.raises(OverseasEnvError):
        resolve_overseas_env(settings)


def test_require_mock_env_mock_url():
    settings = types.SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443"
    )
    assert require_mock_env(settings) == "mock"


def test_require_mock_env_live_url_blocked():
    settings = types.SimpleNamespace(
        base_url="https://openapi.koreainvestment.com:9443"
    )
    with pytest.raises(OverseasLiveBlockedError):
        require_mock_env(settings)


def test_require_mock_env_unknown_url_raises_env_error():
    settings = types.SimpleNamespace(base_url="https://example.test")
    with pytest.raises(OverseasEnvError):
        require_mock_env(settings)


def test_require_mock_env_blocked_when_kis_env_is_live():
    # R1: mock base_url but KIS_ENV=live → cross-check must catch this
    settings = types.SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443"
    )
    import app.overseas_stock.runtime_env as _renv
    with unittest.mock.patch.object(_renv, "_resolve_kis_env", return_value="live"):
        with pytest.raises(OverseasLiveBlockedError):
            require_mock_env(settings)
