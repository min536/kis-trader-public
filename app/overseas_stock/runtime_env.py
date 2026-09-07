from app.auth.settings import get_settings, classify_kis_base_url_env, _resolve_kis_env


class OverseasEnvError(RuntimeError):
    pass


class OverseasLiveBlockedError(RuntimeError):
    pass


def resolve_overseas_env(settings=None):
    s = settings or get_settings()
    env = classify_kis_base_url_env(s.base_url)
    if env not in ("mock", "live"):
        raise OverseasEnvError(
            f"cannot determine overseas env from base_url={s.base_url!r}"
        )
    return env


def require_mock_env(settings=None):
    env = resolve_overseas_env(settings)
    if env != "mock":
        raise OverseasLiveBlockedError(
            f"overseas order blocked: env={env} (mock-only)"
        )
    # R1: cross-validate KIS_ENV to catch injected-settings attack
    kis_env = _resolve_kis_env()
    if kis_env is not None and kis_env != "mock":
        raise OverseasLiveBlockedError(
            f"overseas order blocked: KIS_ENV resolves to {kis_env!r} (mock-only)"
        )
    return "mock"
