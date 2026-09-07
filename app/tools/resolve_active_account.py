"""D5 — print the active account signature for the EOD wrapper's no-arg path.

docs/dashboard_account_routing_design_20260707.md §D5. When ``eod_wrapper.sh``
is invoked without an explicit ACCOUNT, it calls this to recover the currently
active signature from ``Settings`` so a mock-account rotation does not silently
stop producing EOD artifacts for the new account. Never raises — on failure it
prints nothing and the wrapper falls back to its own error path.
"""

from __future__ import annotations

from app.auth.account_scope import get_account_scope_context


def resolve_active_account_signature() -> str:
    try:
        context = get_account_scope_context()
    except Exception:
        return ""
    signature = str((context or {}).get("account_signature") or "").strip()
    return signature


def main(argv: list[str] | None = None) -> int:
    signature = resolve_active_account_signature()
    if signature:
        print(signature)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
