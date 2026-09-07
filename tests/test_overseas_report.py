from types import SimpleNamespace

from app.overseas_stock.models import OverseasQuote
from app.overseas_runtime.report import build_overseas_report


def _settings():
    return SimpleNamespace(
        base_url="https://openapivts.koreainvestment.com:29443",
        cano="12345678",
    )


def test_build_report_happy_path_two_quotes():
    captured = []

    def fake_fetch(sym, *, exchange=None, token=None, settings=None):
        captured.append(sym)
        return OverseasQuote(
            symbol=sym,
            market="US",
            last_price=100.0,
            currency="USD",
            exchange_code="NAS",
        )

    report = build_overseas_report(
        ["AAPL", "NVDA"],
        now_iso="2026-06-13T00:00:00Z",
        quote_fetcher=fake_fetch,
        settings=_settings(),
    )

    assert len(report.quotes) == 2
    assert report.environment == "mock"
    assert report.generated_at == "2026-06-13T00:00:00Z"
    assert report.account_masked.endswith("5678")
    assert report.account_masked == "****5678"
    assert report.errors == ()


def test_build_report_one_symbol_raises_goes_to_errors():
    def fake_fetch(sym, *, exchange=None, token=None, settings=None):
        if sym == "BAD":
            raise RuntimeError("boom")
        return OverseasQuote(
            symbol=sym,
            market="US",
            last_price=100.0,
            currency="USD",
            exchange_code="NAS",
        )

    report = build_overseas_report(
        ["AAPL", "BAD", "NVDA"],
        now_iso="2026-06-13T00:00:00Z",
        quote_fetcher=fake_fetch,
        settings=_settings(),
    )

    assert {q["symbol"] for q in report.quotes} == {"AAPL", "NVDA"}
    assert len(report.errors) == 1
    assert report.errors[0]["symbol"] == "BAD"
    assert report.errors[0]["error"] == "boom"
