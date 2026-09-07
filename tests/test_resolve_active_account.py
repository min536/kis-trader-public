"""D5 (docs/dashboard_account_routing_design_20260707.md §D5): the EOD wrapper
auto-resolves the active account signature when no explicit account is passed,
so a mock-account rotation does not silently stop EOD output for the new
account."""

from __future__ import annotations

from app.tools import resolve_active_account


def test_resolves_active_signature(monkeypatch) -> None:
    monkeypatch.setattr(
        resolve_active_account,
        "get_account_scope_context",
        lambda: {"account_signature": "mock_acct_new", "account_environment": "mock"},
    )
    assert resolve_active_account.resolve_active_account_signature() == "mock_acct_new"


def test_returns_empty_string_when_unresolvable(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(resolve_active_account, "get_account_scope_context", _boom)
    # Never raises — the wrapper falls back to its own error path on empty output.
    assert resolve_active_account.resolve_active_account_signature() == ""


def test_main_prints_signature(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        resolve_active_account,
        "get_account_scope_context",
        lambda: {"account_signature": "mock_acct_xyz"},
    )
    rc = resolve_active_account.main([])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "mock_acct_xyz"
