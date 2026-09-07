"""F3 — Slack 알림 파일 아웃박스 (E3 내구성).

설계: docs/daily_error_triage_design_20260707.md §4.

전송 실패한 주문/체결/가드 이벤트를 logs/slack_outbox.jsonl에 적재하고 다음 notify
시점에 재전송(최대 3회·TTL 1h·성공 시 제거). 순수·never-raises.
"""

import json

from app.notifications import slack_outbox
from app.notifications.slack_outbox import (
    OUTBOX_MAX_ATTEMPTS,
    OUTBOX_TTL_SECONDS,
    enqueue_failed_notification,
    load_pending,
    resend_pending,
    slack_outbox_path,
)


def _payload(text="hi"):
    return {"channel": "C1", "text": text}


def test_outbox_path_under_state_root(tmp_path, monkeypatch):
    # conftest redirect already sets KIS_STATE_ROOT=tmp; path must live under it.
    from app.auth.account_scope import state_root

    assert slack_outbox_path() == state_root() / "logs" / "slack_outbox.jsonl"


def test_enqueue_then_load_pending():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)
    pending = load_pending(now_epoch=1001.0)
    assert len(pending) == 1
    assert pending[0]["event_type"] == "order_accepted"
    assert pending[0]["payload"] == _payload()
    assert pending[0]["attempts"] == 0


def test_resend_success_removes_entry():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)
    sent_payloads = []
    result = resend_pending(
        sender=lambda p: sent_payloads.append(p) or True,
        now_epoch=1001.0,
    )
    assert result["sent"] == 1
    assert sent_payloads == [_payload()]
    assert load_pending(now_epoch=1002.0) == []


def test_resend_failure_increments_attempts_and_keeps():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)
    result = resend_pending(sender=lambda p: False, now_epoch=1001.0)
    assert result["failed"] == 1
    pending = load_pending(now_epoch=1002.0)
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1


def test_attempts_exhaustion_drops_entry():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)
    now = 1000.0
    for _ in range(OUTBOX_MAX_ATTEMPTS):
        now += 1
        resend_pending(sender=lambda p: False, now_epoch=now)
    # after MAX_ATTEMPTS failures the entry is dropped
    assert load_pending(now_epoch=now + 1) == []


def test_ttl_expiry_drops_entry():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)
    expired = 1000.0 + OUTBOX_TTL_SECONDS + 1
    assert load_pending(now_epoch=expired) == []
    # a resend past TTL drops rather than sends
    called = []
    result = resend_pending(sender=lambda p: called.append(p) or True, now_epoch=expired)
    assert called == []
    assert result["dropped"] >= 1


def test_sender_exception_is_treated_as_failure():
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1000.0)

    def boom(_p):
        raise RuntimeError("network down")

    result = resend_pending(sender=boom, now_epoch=1001.0)
    assert result["failed"] == 1
    assert load_pending(now_epoch=1002.0)[0]["attempts"] == 1


def test_corrupt_outbox_never_raises():
    path = slack_outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n{partial\n", encoding="utf-8")
    # must not raise; corrupt lines skipped
    assert load_pending(now_epoch=1.0) == []
    enqueue_failed_notification(event_type="order_accepted", payload=_payload(), now_epoch=1.0)
    assert len(load_pending(now_epoch=2.0)) == 1
