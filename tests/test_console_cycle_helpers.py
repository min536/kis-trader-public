"""Behavior-lock tests for the cycle console helpers relocated from app.main.

These pin the observable print output of the three cycle/buy summary helpers
that Stage A slice A1 moves into app.reporting.console. app.main re-binds them
under their historical underscore names, but the source of truth is now here.
"""

from __future__ import annotations

from unittest import mock


def test_print_cycle_conclusion_outputs_header_and_fields(capsys) -> None:
    from app.reporting.console import _print_cycle_conclusion

    _print_cycle_conclusion(
        side="BUY",
        display_name="삼성전자",
        reason="스코어 최상위",
        planned_qty=3,
    )

    out = capsys.readouterr().out
    assert "=== 이번 사이클 결론 ===" in out
    assert "우선 실행 대상: BUY" in out
    assert "선택 종목: 삼성전자" in out
    assert "사유: 스코어 최상위" in out
    assert "예정 수량:" in out


def test_print_buy_pre_gating_summary_outputs_markers_and_logs(capsys) -> None:
    from app.reporting import console

    pre_gating = {
        "scan_allowed": True,
        "requested_symbols": ("005930", "000660"),
        "allowed_symbols": ("005930",),
        "rejected": [{"symbol": "000660", "reason_code": "untradable_today"}],
        "reason_counts": {"untradable_today": 1},
    }

    with mock.patch.object(console, "_log_engine_event") as log_event:
        console._print_buy_pre_gating_summary(
            pre_gating=pre_gating,
            cycle_id="cycle-1",
            market_open=True,
            market_session="regular",
        )

    out = capsys.readouterr().out
    assert "BUY 사전 게이트" in out
    assert "requested=2 | allowed=1 | early_rejected=1" in out
    assert "untradable_today=1" in out
    log_event.assert_called_once()


def test_print_buy_runtime_filter_summary_delegates_with_log_engine_event() -> None:
    from app.reporting import console

    before = ("before-sentinel",)
    after = ("after-sentinel",)

    with mock.patch.object(console._rs, "print_buy_runtime_filter_summary") as printer:
        console._print_buy_runtime_filter_summary(
            before_results=before,
            after_results=after,
            cycle_id="cycle-9",
            market_open=True,
            market_session="regular",
        )

    printer.assert_called_once_with(
        before_results=before,
        after_results=after,
        cycle_id="cycle-9",
        market_open=True,
        market_session="regular",
        log_engine_event=console._log_engine_event,
    )


def test_emit_status_prints_level_prefixed_message(capsys) -> None:
    from app.reporting.console import emit_status

    emit_status("WARN", "budget low")

    out = capsys.readouterr().out
    assert out == "[WARN] budget low\n"
