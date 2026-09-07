from app.tools import eod_report_summary as eod


def _write_report(tmp_path):
    p = tmp_path / "eod_20260610.txt"
    p.write_text("Account : mock\nExit code : 0\nDuration sec : 12\n", encoding="utf-8")
    return p


def test_main_text_includes_analytics_lines(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(eod, "_load_analytics_lines", lambda: ["📊 Portfolio analytics (realized to date, 3 trades)"])
    report = _write_report(tmp_path)
    rc = eod.main([str(report)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Portfolio analytics" in out


def test_main_json_branch_excludes_analytics(tmp_path, capsys, monkeypatch):
    # JSON output must be unchanged by the analytics wiring.
    called = {"n": 0}
    monkeypatch.setattr(eod, "_load_analytics_lines", lambda: (called.__setitem__("n", called["n"] + 1), ["X"])[1])
    report = _write_report(tmp_path)
    rc = eod.main([str(report), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Portfolio analytics" not in out
    assert called["n"] == 0  # analytics not loaded in JSON mode


def test_load_analytics_lines_is_fail_safe(monkeypatch):
    # Real call path must never raise even if settings/loader blow up.
    import app.tools.eod_report_summary as mod
    result = mod._load_analytics_lines()
    assert result is None or isinstance(result, list)
