from unittest import mock

from app.tools import overseas_order as cli


def test_main_refuses_without_confirm(capsys):
    with mock.patch.object(cli, "place_overseas_limit_order") as place:
        rc = cli.main(
            ["--symbol", "AAPL", "--qty", "1", "--price", "145.00", "--side", "buy"]
        )
    out = capsys.readouterr().out
    assert rc == 2
    assert place.call_count == 0
    assert "confirm" in out.lower()


def test_main_submits_with_confirm(capsys):
    record = {"market": "overseas", "side": "buy", "symbol": "AAPL"}
    with mock.patch.object(
        cli, "place_overseas_limit_order", return_value=record
    ) as place:
        rc = cli.main(
            ["--symbol", "AAPL", "--exchange", "NASD", "--qty", "2",
             "--price", "145.50", "--side", "buy", "--confirm"]
        )
    assert rc == 0
    place.assert_called_once_with(
        symbol="AAPL", exchange="NASD", qty=2, unit_price="145.50", side="buy"
    )
    out = capsys.readouterr().out
    assert "AAPL" in out
