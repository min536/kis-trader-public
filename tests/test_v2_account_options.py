from __future__ import annotations

from app.dashboard.account_discovery import AccountPresence
from app.dashboard import v2_account_options


def test_build_v2_account_options_summarizes_discovered_accounts(monkeypatch) -> None:
    presences = (
        AccountPresence("mock_acct_a", "mock", ("orders", "runtime_state")),
        AccountPresence("live_acct_b", "live", ("performance_summary",)),
    )

    def fake_discover_accounts(*, data_dir=None, logs_dir=None):
        return presences

    def fake_load_dashboard_data(*, signature=None, data_dir=None, logs_dir=None):
        return {
            "account_view": {
                "masked_account_display": f"{signature[:4]}***",
            },
            "orders": [],
            "cycles": [],
            "positions": [],
            "performance_summary": {
                "account_summary": {
                    "total_equity_krw": 123_000 if signature == "mock_acct_a" else 456_000
                }
            },
        }

    def fake_build_top_summary(data):
        summary = data.get("performance_summary", {}).get("account_summary", {})
        return {"total_equity_krw": summary.get("total_equity_krw", 0)}

    monkeypatch.setattr(v2_account_options, "discover_accounts", fake_discover_accounts)
    monkeypatch.setattr(v2_account_options, "load_dashboard_data", fake_load_dashboard_data)
    monkeypatch.setattr(v2_account_options, "build_top_summary", fake_build_top_summary)

    options = v2_account_options.build_v2_account_options(
        active_signature="live_acct_b"
    )

    assert [option["id"] for option in options] == ["mock_acct_a", "live_acct_b"]
    assert options[0]["label"] == "mock***"
    assert options[0]["equity"] == 123_000
    assert options[1]["status"] == "active"
    assert options[1]["env"] == "LIVE"


def test_build_v2_account_options_includes_active_without_artifacts(monkeypatch) -> None:
    monkeypatch.setattr(v2_account_options, "discover_accounts", lambda **_: ())

    options = v2_account_options.build_v2_account_options(
        active_signature="mock_acct_current",
        active_masked_display="5019***23-01",
    )

    assert options == [
        {
            "id": "mock_acct_current",
            "label": "5019***23-01",
            "env": "MOCK",
            "status": "active",
            "equity": 0,
            "sources": [],
            "snapshot_health": "no local artifacts",
        }
    ]


def test_build_v2_account_options_prefers_active_masked_display(monkeypatch) -> None:
    presences = (
        AccountPresence("mock_acct_current", "mock", ("orders",)),
    )

    monkeypatch.setattr(v2_account_options, "discover_accounts", lambda **_: presences)
    monkeypatch.setattr(
        v2_account_options,
        "load_dashboard_data",
        lambda *, signature=None, data_dir=None, logs_dir=None: {
            "account_view": {},
            "orders": [],
            "cycles": [],
            "positions": [],
            "performance_summary": {},
        },
    )
    monkeypatch.setattr(
        v2_account_options,
        "build_top_summary",
        lambda data: {"total_equity_krw": 0},
    )

    options = v2_account_options.build_v2_account_options(
        active_signature="mock_acct_current",
        active_masked_display="5019***23-01",
    )

    assert options[0]["id"] == "mock_acct_current"
    assert options[0]["label"] == "5019***23-01"
    assert options[0]["status"] == "active"
