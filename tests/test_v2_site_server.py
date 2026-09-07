from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_ROOT / "workspace/claude-design/kis-trader-v2/server.py"


def _load_server_module(monkeypatch) -> ModuleType:
    monkeypatch.setenv("KIS_TRADER_ROOT", str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "kis_trader_v2_server_under_test",
        SERVER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_server_module_without_root_env(monkeypatch) -> ModuleType:
    monkeypatch.delenv("KIS_TRADER_ROOT", raising=False)
    spec = importlib.util.spec_from_file_location(
        "kis_trader_v2_server_default_root_under_test",
        SERVER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_repo_root_resolves_to_repository_when_env_absent(monkeypatch) -> None:
    """Without KIS_TRADER_ROOT the server must find this repo, not /workspace.

    The old default ('/workspace', a container path) made every `app.*` import
    fail on a local run: /api/kt-data answered 500 and the frontend silently
    stayed on mockData — the stale-account symptom observed on 2026-07-06.
    """
    module = _load_server_module_without_root_env(monkeypatch)

    assert module.REPO_ROOT == PROJECT_ROOT
    from app.dashboard import v2_account_options
    monkeypatch.setattr(v2_account_options, "discover_accounts", lambda **kwargs: [])
    monkeypatch.setattr(module, "_active_account_context", lambda: {
        "account_signature": "mock_demo",
        "masked_account_display": "DEMO",
    })
    selected, options = module._selected_signature(None)
    assert options, "the synthetic active context should produce an account option"
    assert selected in {option["id"] for option in options}


def test_env_override_still_wins_for_repo_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KIS_TRADER_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "kis_trader_v2_server_env_root_under_test",
        SERVER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.REPO_ROOT == tmp_path.resolve()


def test_server_import_does_not_load_dashboard_data(monkeypatch) -> None:
    from app.dashboard import data_loader

    def fail_load() -> dict:
        raise AssertionError("dashboard data must not be loaded at import time")

    monkeypatch.setattr(data_loader, "load_dashboard_data", fail_load)

    module = _load_server_module(monkeypatch)

    assert module.build_health_payload()["ok"] is True


def test_build_kt_data_delegates_to_stable_payload_builder(monkeypatch) -> None:
    from app.dashboard import data_loader
    from app.dashboard import v2_site_payload

    calls: list[object] = []
    dashboard_data = {"positions": []}
    expected_payload = {"NOW_ISO": "2026-07-06T00:00:00+00:00"}

    def fake_load(*, signature=None) -> dict:
        calls.append(("load", signature))
        return dashboard_data

    def fake_build(data: dict, **kwargs) -> dict:
        calls.append((data, kwargs))
        return expected_payload

    monkeypatch.setattr(data_loader, "load_dashboard_data", fake_load)
    monkeypatch.setattr(v2_site_payload, "build_v2_site_payload", fake_build)

    module = _load_server_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "_active_account_context",
        lambda: {
            "account_signature": "mock_acct_a",
            "masked_account_display": "5019***23-01",
        },
    )
    monkeypatch.setattr(
        "app.dashboard.v2_account_options.build_v2_account_options",
        lambda *, active_signature=None, active_masked_display=None: [
            {
                "id": "mock_acct_a",
                "label": active_masked_display,
                "env": "MOCK",
                "status": "active",
                "equity": 0,
            }
        ],
    )

    assert module.build_kt_data() == expected_payload
    assert calls == [
        ("load", "mock_acct_a"),
        (
            dashboard_data,
            {
                "account_signature": "mock_acct_a",
                "account_options": [
                    {
                        "id": "mock_acct_a",
                        "label": "5019***23-01",
                        "env": "MOCK",
                        "status": "active",
                        "equity": 0,
                    }
                ],
            },
        ),
    ]


def test_build_kt_data_rejects_unknown_signature(monkeypatch) -> None:
    module = _load_server_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "_active_account_context",
        lambda: {
            "account_signature": "mock_acct_a",
            "masked_account_display": "5019***23-01",
        },
    )
    monkeypatch.setattr(
        "app.dashboard.v2_account_options.build_v2_account_options",
        lambda *, active_signature=None, active_masked_display=None: [
            {
                "id": "mock_acct_a",
                "label": active_masked_display,
                "env": "MOCK",
                "status": "active",
                "equity": 0,
            }
        ],
    )

    try:
        module.build_kt_data(signature="mock_acct_missing")
    except ValueError as exc:
        assert "Unknown account signature" in str(exc)
    else:
        raise AssertionError("unknown signatures must be rejected")


def test_kt_data_or_error_maps_unknown_signature_to_400(monkeypatch) -> None:
    """C5: an unknown ?signature= must be a 400 (bad request) carrying the known
    signatures, not a 500 that the frontend reads as a server crash."""
    module = _load_server_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "_active_account_context",
        lambda: {
            "account_signature": "mock_acct_a",
            "masked_account_display": "5019***23-01",
        },
    )
    monkeypatch.setattr(
        "app.dashboard.v2_account_options.build_v2_account_options",
        lambda *, active_signature=None, active_masked_display=None: [
            {"id": "mock_acct_a", "label": "x", "env": "MOCK", "status": "active", "equity": 0}
        ],
    )

    status, payload = module.kt_data_or_error("mock_acct_missing")
    assert status == 400
    assert "known_signatures" in payload
    assert "mock_acct_a" in payload["known_signatures"]


def test_kt_data_or_error_returns_200_for_known_signature(monkeypatch) -> None:
    module = _load_server_module(monkeypatch)
    monkeypatch.setattr(module, "build_kt_data", lambda signature=None: {"NOW_ISO": "x"})

    status, payload = module.kt_data_or_error(None)
    assert status == 200
    assert payload == {"NOW_ISO": "x"}


def test_server_exposes_health_endpoint_and_host_override() -> None:
    source = SERVER_PATH.read_text(encoding="utf-8")

    assert '"/api/health"' in source
    assert 'os.environ.get("HOST", "127.0.0.1")' in source
    assert "build_v2_site_payload(" in source
    assert "_signature_from_path" in source
