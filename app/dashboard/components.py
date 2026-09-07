from datetime import datetime
from html import escape
from typing import Any

from app.dashboard.formatters import format_timestamp
from app.dashboard.formatters import format_krw, format_signed_krw, format_signed_pct
from app.core.time_utils import KOREA_TZ, get_korean_now


def render_section_header(st, title: str, caption: str | None = None) -> None:
    caption_html = f"<p>{escape(caption)}</p>" if caption else ""
    st.markdown(
        f"""
        <div class="kd-section">
          <h3>{escape(title)}</h3>
          {caption_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _tone_class(tone: str) -> str:
    return {
        "positive": "kd-badge-positive",
        "warning": "kd-badge-warning",
        "danger": "kd-badge-danger",
        "info": "kd-badge-info",
    }.get(tone, "kd-badge-neutral")


def badge_html(text: str, tone: str = "neutral") -> str:
    return f'<span class="kd-badge {_tone_class(tone)}">{escape(text)}</span>'


def market_session_badge(session: str | None) -> str:
    normalized = (session or "").upper()
    label = {
        "REGULAR": "정규장",
        "PREMARKET": "장전",
        "AFTER_MARKET": "장후",
        "CLOSED": "휴장",
    }.get(normalized, normalized or "UNKNOWN")
    tone = {
        "REGULAR": "positive",
        "PREMARKET": "warning",
        "AFTER_MARKET": "info",
        "CLOSED": "danger",
    }.get(normalized, "neutral")
    return badge_html(label, tone)


def action_label(action: str | None) -> str:
    normalized = (action or "").upper()
    if not normalized:
        return "상태 없음"
    mapping = [
        ("BUY_ORDER_SUCCEEDED", "매수 주문 성공"),
        ("BUY_ORDER_SUBMITTED", "매수 주문 제출"),
        ("SELL_ORDER_SUCCEEDED", "매도 주문 성공"),
        ("SELL_ORDER_SUBMITTED", "매도 주문 제출"),
        ("BUY_BLOCKED_AFTER_MARKET", "장후 매수 차단"),
        ("SELL_BLOCKED_AFTER_MARKET", "장후 매도 차단"),
        ("BUY_BLOCKED_PREMARKET", "장전 매수 차단"),
        ("SELL_BLOCKED_PREMARKET", "장전 매도 차단"),
        ("BUY_BLOCKED_ORDER_WINDOW", "주문 가능시간 외 매수 차단"),
        ("BUY_BLOCKED_RISK_GUARD", "매수 리스크 가드 차단"),
        ("BLOCKED_BUY_CASH_INSUFFICIENT", "현금 부족으로 매수 차단"),
        ("BLOCKED_BUY_EXPOSURE_LIMITED", "노출 한도로 매수 차단"),
        ("BLOCKED_BUY_TRADE_BUDGET_LIMITED", "1회 예산 한도로 매수 차단"),
        ("BLOCKED_BUY_QTY_OR_PRICE_LIMITED", "수량/가격 제약으로 매수 차단"),
        ("BLOCKED_BUY_EXPECTED_COST_TOO_HIGH", "예상 비용 과다로 매수 보류"),
        ("BLOCKED_BUY_NET_EDGE_TOO_LOW", "비용 반영 순우위 부족"),
        ("BUY_BLOCKED_EXPECTED_COST_TOO_HIGH", "예상 비용 과다로 매수 보류"),
        ("BUY_BLOCKED_NET_EDGE_TOO_LOW", "비용 반영 순우위 부족"),
        ("SELL_BLOCKED_RISK_GUARD", "매도 리스크 가드 차단"),
        ("BUY_BLOCKED_DAILY_PNL_PAUSE", "일중 손실로 매수 일시중단"),
        ("BUY_BLOCKED_DAILY_PNL_HARD_STOP", "일중 손실로 매수 중단"),
        ("BLOCKED_DAILY_PNL_PAUSE", "일중 손실로 매수 일시중단"),
        ("BLOCKED_DAILY_PNL_HARD_STOP", "일중 손실로 매수 중단"),
        ("BLOCKED_REBUY_COOLDOWN", "재매수 cooldown 차단"),
        ("BLOCKED_SAME_SYMBOL_DAILY_LIMIT", "동일 종목 당일 한도 차단"),
        ("BLOCKED_BUY_REENTRY_COOLDOWN", "재진입 cooldown 차단"),
        ("BLOCKED_BUY_SAME_SYMBOL_DAILY_LIMIT", "동일 종목 진입 한도 차단"),
        ("SKIPPED_BUY_SCAN_CADENCE", "매수 스캔 주기 대기"),
        ("SKIPPED_BUY_SCAN_BUDGET_LIMITED", "API 예산으로 매수 스캔 연기"),
        ("REBALANCE_CONSIDERED", "리밸런싱 검토"),
        ("REBALANCE_SKIPPED_SCORE_DELTA", "score delta 부족으로 리밸런싱 미검토"),
        ("REBALANCE_SKIPPED_PROFIT_BUFFER", "profit buffer 부족으로 리밸런싱 미검토"),
        ("REBALANCE_SKIPPED_LIMIT", "리밸런싱 제한으로 미검토"),
        ("REBALANCE_SKIPPED_NO_CANDIDATE", "리밸런싱 후보 없음"),
        ("REBALANCE_DEFERRED_SELL_WATCH_INCOMPLETE", "sell_watch 미완료로 리밸런싱 다음 주기 보류"),
        ("REBALANCE_SELL_PREVIEW", "리밸런싱 매도 미리보기"),
        ("WAITING_PREMARKET_OPEN", "장전 대기"),
        ("WAITING_AFTER_MARKET", "장후 대기"),
        ("WAITING_CLOSED", "휴장 대기"),
        ("BUY_BLOCKED_DAILY_NOTIONAL_LIMIT", "매수 금액 한도 차단"),
        ("SELL_BLOCKED_DAILY_NOTIONAL_LIMIT", "매도 금액 한도 차단"),
        ("BUY_BLOCKED_DAILY_ORDER_LIMIT", "매수 횟수 한도 차단"),
        ("SELL_BLOCKED_DAILY_ORDER_LIMIT", "매도 횟수 한도 차단"),
        ("BLOCKED_AFTER_MARKET", "장후 차단"),
        ("BLOCKED_PREMARKET", "장전 차단"),
        ("BLOCKED_HOLIDAY_OR_CLOSED", "휴장 차단"),
        ("BUY_SKIPPED_COOLDOWN", "매수 cooldown 건너뜀"),
        ("SELL_SKIPPED_COOLDOWN", "매도 cooldown 건너뜀"),
        ("HOLD_NO_SIGNAL", "관망"),
        ("CYCLE_ERROR", "사이클 오류"),
    ]
    for raw, label in mapping:
        if normalized == raw:
            return label
    if "BUY" in normalized and "BLOCKED" not in normalized and "SKIPPED" not in normalized:
        return "매수 진행"
    if "SELL" in normalized and "BLOCKED" not in normalized and "SKIPPED" not in normalized:
        return "매도 진행"
    if "BLOCKED" in normalized:
        return "차단"
    if "SKIPPED" in normalized:
        return "건너뜀"
    if "HOLD" in normalized:
        return "관망"
    return normalized.replace("_", " ")


def order_permission_badge(session: str | None) -> str:
    normalized = (session or "").upper()
    return badge_html("주문 가능" if normalized == "REGULAR" else "주문 불가", "positive" if normalized == "REGULAR" else "danger")


def action_badge(action: str | None) -> str:
    normalized = (action or "").upper()
    if "SELL" in normalized and "BLOCKED" not in normalized and "SKIPPED" not in normalized:
        return badge_html(action_label(action), "warning")
    if "BUY" in normalized and "BLOCKED" not in normalized and "SKIPPED" not in normalized:
        return badge_html(action_label(action), "info")
    if "HOLD" in normalized:
        return badge_html(action_label(action), "neutral")
    if "BLOCKED" in normalized or "SKIPPED" in normalized or "ERROR" in normalized:
        return badge_html(action_label(action), "danger")
    return badge_html(action_label(action), "neutral")


def result_badge(result: str | None, action: str | None = None) -> str:
    normalized = (result or "").lower()
    action_text = (action or "").lower()
    if normalized == "success":
        return badge_html("SUCCESS", "positive")
    if normalized == "failed":
        return badge_html("FAILED", "danger")
    if normalized == "skipped":
        if "blocked" in action_text:
            return badge_html("BLOCKED", "danger")
        if "preview" in action_text:
            return badge_html("PREVIEW", "info")
        return badge_html("SKIPPED", "warning")
    return badge_html((result or "UNKNOWN").upper(), "neutral")


def pnl_badge(pnl_pct: float | None) -> str:
    if pnl_pct is None:
        return badge_html("FLAT", "neutral")
    if pnl_pct > 0:
        return badge_html("WIN", "positive")
    if pnl_pct < 0:
        return badge_html("LOSS", "danger")
    return badge_html("FLAT", "neutral")


def compact_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not text:
        return "사유 없음"
    replacements = [
        ("오늘 주문 제출 횟수 제한에 도달했습니다.", "오늘 주문 한도를 모두 사용했습니다"),
        ("오늘 총 매수 예정 금액 제한을 초과합니다.", "오늘 매수 금액 한도에 도달했습니다"),
        ("한국 정규장 시작 전이라 자동매매 주문을 보내지 않습니다.", "장 시작 전이라 주문을 보내지 않습니다"),
        ("정규장이 종료되어 자동매매 주문을 보내지 않습니다.", "정규장이 끝나 주문을 보내지 않습니다"),
        ("같은 BUY 신호가 cooldown 안에 있어 이번 사이클은 건너뜁니다.", "같은 매수 신호라 이번 사이클은 건너뜁니다"),
        ("같은 SELL 신호가 cooldown 안에 있어 이번 사이클은 건너뜁니다.", "같은 매도 신호라 이번 사이클은 건너뜁니다"),
        ("매수 전략이 통과하지 않아 주문 검토를 진행하지 않습니다.", "매수 전략이 기준에 못 미쳤습니다"),
        ("매도 전략이 통과하지 않아 주문 검토를 진행하지 않습니다.", "매도 전략이 기준에 못 미쳤습니다"),
        ("예상 거래비용", "예상 비용"),
        ("허용 기준", "기준"),
        ("비용 반영 후 기대 순우위", "비용 반영 후 순우위"),
        ("진입을 보류했습니다.", "진입을 보류했습니다"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    if len(text) > 72:
        return text[:69] + "..."
    return text


def data_freshness_badge(timestamp: str | None) -> str:
    if not timestamp:
        return badge_html("데이터 없음", "warning")
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KOREA_TZ)
        else:
            dt = dt.astimezone(KOREA_TZ)
    except ValueError:
        return badge_html("시각 파싱 실패", "warning")

    age_seconds = max(0.0, (get_korean_now() - dt).total_seconds())
    if age_seconds <= 120:
        return badge_html("최신", "positive")
    if age_seconds <= 600:
        return badge_html("조금 전", "info")
    return badge_html("지연", "warning")


def render_kpi_card(st, *, label: str, value: str, foot: str | None = None, size: str = "normal") -> None:
    val_size = "22px" if size == "normal" else "17px"
    foot_html = f'<div class="kd-card-foot">{escape(foot)}</div>' if foot else ""
    st.markdown(
        f"""
        <div class="kd-card">
          <div class="kd-card-label">{escape(label)}</div>
          <div class="kd-card-value" style="font-size:{val_size};">{escape(value)}</div>
          {foot_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_header(
    st,
    *,
    market_session: str,
    latest_updated_at: str | None,
    recent_action: str | None,
    environment_label: str | None,
    snapshot_health: str | None,
    auto_refresh: bool,
    refresh_seconds: int,
) -> None:
    environment_badge = badge_html(f"{(environment_label or 'UNKNOWN').upper()}", "info")
    refresh_label = f"{refresh_seconds}초 자동 새로고침" if auto_refresh else "수동 새로고침"
    st.markdown(
        f"""
        <div class="kd-header">
          <div class="kd-header-top">
            <div class="kd-header-copy">
              <div class="kd-eyebrow">KIS-TRADER</div>
              <div class="kd-title">운영 브리핑 콘솔</div>
              <div class="kd-subtitle">상태를 먼저 읽고, 이유를 이해하고, 필요한 디테일로 내려갑니다.</div>
            </div>
            <div class="kd-header-status">
              <div class="kd-header-status-label">마지막 업데이트</div>
              <div class="kd-header-status-value">{format_timestamp(latest_updated_at) or '—'}</div>
              <div class="kd-header-status-foot">{escape(refresh_label)}</div>
            </div>
          </div>
          <div class="kd-header-meta">
            {market_session_badge(market_session)}
            {order_permission_badge(market_session)}
            {action_badge(recent_action)}
            {environment_badge}
            {data_freshness_badge(latest_updated_at)}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_glance_box(st, items: list[tuple[str, str]]) -> None:
    inner = "".join(
        f"""
        <div class="kd-glance-item">
          <div class="kd-glance-key">{escape(key)}</div>
          <div class="kd-glance-value">{value}</div>
        </div>
        """
        for key, value in items
    )
    st.markdown(
        f'<div class="kd-glance">{inner}</div>',
        unsafe_allow_html=True,
    )


def render_alert_card(st, *, title: str, body: str, tone: str = "neutral") -> None:
    badge = badge_html("주의", tone) if tone != "neutral" else badge_html("정보", "neutral")
    st.markdown(
        f"""
        <div class="kd-card">
          <div class="kd-card-label">{badge}</div>
          <div style="font-size:18px;font-weight:800;color:#0f172a;line-height:1.35;">{escape(title)}</div>
          <div class="kd-card-foot" style="margin-top:8px;">{escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state(st, message: str) -> None:
    st.markdown(
        f'<div class="kd-empty">{escape(message)}</div>',
        unsafe_allow_html=True,
    )


def render_data_quality_hint(st, message: str, *, tone: str = "warning") -> None:
    st.markdown(
        f"""
        <div class="kd-data-hint kd-data-hint-{_tone_class(tone)}">
          {escape(message)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_insight_card(st, *, title: str, body: str, tone: str = "neutral") -> None:
    badge = badge_html(title, tone)
    st.markdown(
        f"""
        <div class="kd-card">
          <div class="kd-card-label">{badge}</div>
          <div style="font-size:15px;font-weight:700;color:#191f28;line-height:1.55;">{escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_interpretation_line(st, message: str) -> None:
    st.markdown(
        f"""
        <div class="kd-interpretation">
          {escape(message)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_concentration_rows(st, rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_state(st, "집중도 데이터를 만들 수 없습니다.")
        return
    for index, row in enumerate(rows):
        label = str(row.get("label") or "종목명 미확인")
        symbol = str(row.get("symbol") or "-")
        value = float(row.get("value", 0.0) or 0.0)
        market_value = row.get("market_value_krw")
        net_pnl_krw = row.get("net_pnl_krw")
        net_pnl_pct = row.get("net_pnl_pct")
        pnl_text = "수익 정보 없음"
        if net_pnl_krw is not None or net_pnl_pct is not None:
            pnl_text = f"{format_signed_krw(net_pnl_krw)} · {format_signed_pct(net_pnl_pct)}"
        st.markdown(
            f"""
            <div class="kd-concentration-row">
              <div class="kd-concentration-meta">
                <div class="kd-concentration-head">
                  <div class="kd-concentration-title">{escape(label)} <span class="kd-concentration-symbol">{escape(symbol)}</span></div>
                  <div class="kd-concentration-weight kd-num">{value:.2f}%</div>
                </div>
                <div class="kd-concentration-sub">
                  <span class="kd-concentration-stat"><span class="kd-concentration-stat-label">평가금액</span><span class="kd-num">{escape(format_krw(market_value))}</span></span>
                  <span class="kd-concentration-dot">·</span>
                  <span class="kd-concentration-stat"><span class="kd-concentration-stat-label">손익</span><span class="kd-num">{escape(pnl_text)}</span></span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if index < len(rows) - 1:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


def render_focus_position_cards(st, rows: list[dict[str, Any]]) -> None:
    if not rows:
        render_empty_state(st, "집중도 상위 데이터를 만들 수 없습니다.")
        return
    for index, row in enumerate(rows):
        symbol_name = str(row.get("symbol_name") or "종목명 미확인")
        symbol = str(row.get("symbol") or "-")
        weight_text = format_signed_pct(row.get("weight_pct")).replace("+", "")
        market_value_text = format_krw(row.get("market_value_krw"))
        pnl_text = format_signed_krw(row.get("net_pnl_krw"))
        pnl_pct_text = format_signed_pct(row.get("net_pnl_pct"))
        qty = row.get("holding_qty")
        qty_text = f"{int(qty)}주" if qty is not None else "-"
        st.markdown(
            f"""
            <div class="kd-focus-card">
              <div class="kd-focus-top">
                <div>
                  <div class="kd-focus-title">{escape(symbol_name)}</div>
                  <div class="kd-focus-subtitle">{escape(symbol)} · {escape(qty_text)}</div>
                </div>
                <div class="kd-focus-weight kd-num">{escape(weight_text)}</div>
              </div>
              <div class="kd-focus-grid">
                <div class="kd-focus-metric">
                  <div class="kd-focus-label">평가금액</div>
                  <div class="kd-focus-value kd-num">{escape(market_value_text)}</div>
                </div>
                <div class="kd-focus-metric">
                  <div class="kd-focus-label">평가손익</div>
                  <div class="kd-focus-value kd-num">{escape(pnl_text)}</div>
                </div>
                <div class="kd-focus-metric">
                  <div class="kd-focus-label">수익률</div>
                  <div class="kd-focus-value kd-num">{escape(pnl_pct_text)}</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if index < len(rows) - 1:
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)


def render_list_rows(st, rows: list[dict[str, str]]) -> None:
    if not rows:
        render_empty_state(st, "데이터 없음")
        return
    for index, row in enumerate(rows):
        box = st.container(border=False)
        left_col, right_col = box.columns([4.5, 1.5], vertical_alignment="top")
        with left_col:
            if row.get("title_html") is not None:
                st.markdown(row["title_html"], unsafe_allow_html=True)
            else:
                st.markdown(f"**{row.get('title', '')}**")
            if row.get("subtitle_html") is not None:
                st.markdown(
                    f'<div class="kd-list-subtitle">{row["subtitle_html"]}</div>',
                    unsafe_allow_html=True,
                )
            elif row.get("subtitle"):
                st.caption(str(row.get("subtitle", "")))
        with right_col:
            st.markdown(
                f"<div class='kd-num' style='text-align:right;font-weight:800;color:#191f28;'>{escape(str(row.get('value', '')))}</div>",
                unsafe_allow_html=True,
            )
            if row.get("meta"):
                st.markdown(
                    f"<div class='kd-num' style='text-align:right;color:#8b95a1;font-size:12px;margin-top:4px;'>{escape(str(row.get('meta', '')))}</div>",
                    unsafe_allow_html=True,
                )
        if index < len(rows) - 1:
            st.divider()


def render_cycle_summary_panel(
    st,
    *,
    final_action: str,
    final_reason: str,
    market_session: str,
    selected_buy_candidate: str | None,
    selected_sell_candidate: str | None,
) -> None:
    st.markdown(
        f"""
        <div class="kd-glance">
          <div class="kd-glance-title">이번 cycle 핵심 요약</div>
          <div class="kd-glance-item">
            <div class="kd-glance-key">세션 / 액션</div>
            <div class="kd-glance-value">{market_session_badge(market_session)} {action_badge(final_action)}</div>
          </div>
          <div class="kd-glance-item">
            <div class="kd-glance-key">선택된 BUY 후보</div>
            <div class="kd-glance-value">{escape(selected_buy_candidate or '-')}</div>
          </div>
          <div class="kd-glance-item">
            <div class="kd-glance-key">선택된 SELL 후보</div>
            <div class="kd-glance-value">{escape(selected_sell_candidate or '-')}</div>
          </div>
          <div class="kd-glance-item">
            <div class="kd-glance-key">최종 사유</div>
            <div class="kd-glance-value">{escape(compact_reason(final_reason))}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
