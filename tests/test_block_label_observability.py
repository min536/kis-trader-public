"""F5 — 차단 라벨 관측성 (E2).

설계: docs/daily_error_triage_design_20260707.md §6.

f"BUY_BLOCKED_{session}"는 마감 직전(session=REGULAR인데 주문 불가) 차단을
BUY_BLOCKED_REGULAR로 표기해 "정규장인데 차단?"으로 오독된다. 사유 중심
BUY_BLOCKED_ORDER_WINDOW로 정정하고, sell도 session_not_regular→order_window_closed로
정정한다. 퍼널 통계 버킷은 불변("other")이어야 한다(회귀 핀).
"""

from pathlib import Path

from app.dashboard.components import action_label
from app.scanner.scan_funnel import normalize_buy_funnel_reason

_BUY_FLOW = Path("app/execution/buy_flow.py")
_REGIME_PHASE = Path("app/runtime/cycle_phases/regime_phase.py")
_RUNTIME_ADAPTERS = Path("app/pipeline/runtime_adapters.py")


def test_buy_flow_uses_reason_based_block_label():
    source = _BUY_FLOW.read_text(encoding="utf-8")
    # 세션명을 action에 노출하던 f-string 제거
    assert "BUY_BLOCKED_{session_status.session}" not in source
    assert "BUY_BLOCKED_{order_session_status.session}" not in source
    # 사유 중심 라벨 사용
    assert '"BUY_BLOCKED_ORDER_WINDOW"' in source


def test_dashboard_renders_order_window_block_label():
    label = action_label("BUY_BLOCKED_ORDER_WINDOW")
    # raw 라벨이 아니라 사람이 읽는 한국어 표시로 매핑
    assert label != "BUY_BLOCKED_ORDER_WINDOW"
    assert "매수" in label


def test_funnel_bucket_stable_across_relabel():
    # 라벨 변경이 퍼널 통계 버킷을 옮기지 않는다 (둘 다 "other")
    assert normalize_buy_funnel_reason("BUY_BLOCKED_REGULAR") == "other"
    assert normalize_buy_funnel_reason("BUY_BLOCKED_ORDER_WINDOW") == "other"


def test_sell_skip_reason_is_order_window_not_session_name():
    regime = _REGIME_PHASE.read_text(encoding="utf-8")
    adapters = _RUNTIME_ADAPTERS.read_text(encoding="utf-8")
    # 마감버퍼 sell skip을 session_not_regular로 표기하던 모순 제거
    assert "session_not_regular" not in regime
    assert '"order_window_closed"' in regime
    # lane 파이프라인 경로도 동일 사유로 정렬 (lane-vs-legacy 등가)
    assert "session_not_regular" not in adapters
    assert '"order_window_closed"' in adapters
