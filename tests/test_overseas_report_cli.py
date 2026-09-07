import types
from unittest import mock

from app.tools import overseas_report as cli


def _report(quotes=(), errors=()):
    return types.SimpleNamespace(
        generated_at="2026-06-13T10:00:00+09:00",
        environment="mock",
        account_masked="****5678",
        quotes=tuple(quotes),
        holdings=(),
        errors=tuple(errors),
    )


def test_resolve_symbols_uppercases_args():
    s = types.SimpleNamespace(cano="x", base_url="y")
    assert cli._resolve_symbols(["aapl", " tsla "], s) == ("AAPL", "TSLA")


def test_main_prints_quotes(capsys):
    rep = _report(
        quotes=[{"symbol": "AAPL", "last_price": 145.5,
                 "currency": "USD", "exchange_code": "NAS"}]
    )
    with mock.patch.object(
        cli, "get_settings",
        return_value=types.SimpleNamespace(cano="12345678", base_url="x"),
    ), mock.patch.object(cli, "build_overseas_report", return_value=rep):
        rc = cli.main(["--symbols", "AAPL"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "AAPL" in out
    assert "mock" in out


def test_main_no_symbols_returns_1():
    with mock.patch.object(
        cli, "get_settings",
        return_value=types.SimpleNamespace(cano="12345678", base_url="x"),
    ), mock.patch.object(cli, "_resolve_symbols", return_value=()):
        rc = cli.main([])
    assert rc == 1
