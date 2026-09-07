"""F2-1 — order-log writer identity stamp (E5 재발 진단).

설계: docs/daily_error_triage_design_20260707.md §3 (F2-1).

order-log 레코드는 이미 pid(=writer_pid)·account_signature(=resolved_signature)를
싣는다. 누락된 forensic 필드는 process_started_at — PID 재사용/동시 프로세스 판별용.
크로스라이트 재발 시 "어느 프로세스/어느 해석 시점"을 레코드만으로 판별.
"""

import json

from app.auth.account_scope import get_order_log_path
from app.core import order_log
from app.core.order_log import log_order_event
from app.reporting.runtime_snapshots import log_engine_event


def _read_records() -> list[dict]:
    path = get_order_log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_log_order_event_stamps_process_identity():
    log_order_event(
        symbol="005930",
        qty=1,
        order_type="market_buy",
        confirm_buy="Y",
        market_open=True,
        action="test_event",
        result="skipped",
        reason="unit",
        raw_response={"ok": True},
    )
    records = _read_records()
    assert records, "record not written"
    rec = records[-1]
    assert isinstance(rec.get("process_started_at"), str) and rec["process_started_at"].strip()
    assert isinstance(rec.get("pid"), int)
    assert rec.get("account_signature"), "resolved_signature (account_signature) missing"


def test_process_started_at_stable_within_process():
    for i in range(2):
        log_order_event(
            symbol="005930",
            qty=1,
            order_type="market_buy",
            confirm_buy="Y",
            market_open=True,
            action="test_event",
            result="skipped",
            reason=f"unit{i}",
            raw_response={},
        )
    records = _read_records()
    stamps = {r["process_started_at"] for r in records if "process_started_at" in r}
    assert len(stamps) == 1, f"process_started_at should be stable, got {stamps}"
    # module-level constant matches the record
    assert order_log._PROCESS_STARTED_AT in stamps


def test_log_engine_event_forwards_error_type():
    log_engine_event(
        action="cycle_error",
        reason="ValueError: boom",
        cycle_id="c1",
        market_open=False,
        market_session=None,
        error_type="ValueError",
    )
    rec = _read_records()[-1]
    raw = rec.get("raw_response") or {}
    assert raw.get("error_type") == "ValueError"
    assert raw.get("engine_event") is True


def test_log_engine_event_without_error_type_is_backward_compatible():
    log_engine_event(
        action="hold",
        reason="idle",
        cycle_id="c2",
        market_open=False,
        market_session="CLOSED",
    )
    rec = _read_records()[-1]
    raw = rec.get("raw_response") or {}
    assert "error_type" not in raw
