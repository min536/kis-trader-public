"""M3 — shadow_watch initial-capital parameterization.

docs/backtester_redesign_20260706.md §M3 / competition_reset_design §RR-4: the
backtest capital base must match the (possibly reset) competition seed instead
of a hardcoded 100M.
"""

from __future__ import annotations

import json
import sys

from app.tools import shadow_watch


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a) -> bool:
        return False


def _capture_payload(monkeypatch) -> dict:
    captured: dict = {}

    def fake_urlopen(req, timeout: float = 0.0):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"data": {}})

    monkeypatch.setattr(shadow_watch.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_run_bt_api_uses_injected_initial_capital(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    ok, _ = shadow_watch._run_bt_api(
        "yaml",
        "2026-01-01",
        "2026-01-31",
        "http://localhost:8002",
        initial_capital=300_000_000,
    )

    assert ok is True
    assert captured["payload"]["initial_capital"] == 300_000_000


def test_run_bt_api_defaults_initial_capital_to_prior_literal(monkeypatch) -> None:
    captured = _capture_payload(monkeypatch)

    shadow_watch._run_bt_api(
        "yaml", "2026-01-01", "2026-01-31", "http://localhost:8002"
    )

    assert captured["payload"]["initial_capital"] == 100_000_000


def test_main_threads_initial_capital_flag(monkeypatch) -> None:
    captured: dict = {}

    monkeypatch.setattr(
        shadow_watch, "run_shadow_watch", lambda **kw: captured.update(kw) or []
    )
    monkeypatch.setattr(shadow_watch, "_print_report", lambda results: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["shadow_watch", "--initial-capital", "300000000", "--skip-run"],
    )

    shadow_watch.main()

    assert captured["initial_capital"] == 300_000_000
