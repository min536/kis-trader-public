"""Pure Slack text/payload builders (no HTTP transport).

Relocated verbatim from app.notifications.slack (R3-S2 reporting/notifications
slimming). app.notifications.slack keeps legacy bindings so existing import
sites and patch targets remain valid.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from app.notifications.sanitize import sanitize_text
from app.scanner.symbol_names import get_symbol_name

try:
    from app.core.symbol_tags import SymbolTagRegistry, load_symbol_tags as _load_symbol_tags
    _TAGS_AVAILABLE = True
except Exception:
    _TAGS_AVAILABLE = False

_tag_registry_cache: "SymbolTagRegistry | None" = None


def _get_tag_registry() -> "SymbolTagRegistry | None":
    global _tag_registry_cache
    if not _TAGS_AVAILABLE:
        return None
    if _tag_registry_cache is None:
        try:
            _tag_registry_cache = _load_symbol_tags()
        except Exception:
            return None
    return _tag_registry_cache


_ORDER_EVENT_LABELS = {
    "order_submitted": ("📤", "제출"),
    "order_accepted": ("✅", "접수"),
    "order_rejected": ("❌", "실패"),
}
_ORDER_SIDE_LABELS = {
    "BUY": "매수",
    "SELL": "매도",
}


def _truncate(value: str, *, limit: int = 180) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_krw(value: object, env: Mapping[str, str]) -> str | None:
    if value is None:
        return None
    try:
        amount = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        text = sanitize_text(value, env).strip()
        return text or None
    return f"{amount:,}원"


def _format_order_reason(value: object, env: Mapping[str, str]) -> str | None:
    text = _truncate(sanitize_text(value, env).strip(), limit=180)
    if not text:
        return None
    match = re.search(r"\(([^()]{3,32})\)\s*$", text)
    if not match:
        return text
    code = sanitize_text(match.group(1), env).strip()
    prefix = text[: match.start()].rstrip()
    return f"{prefix} `({code})`" if prefix else f"`({code})`"


def _format_order_quantity(value: object, env: Mapping[str, str]) -> str:
    text = sanitize_text(value, env).strip()
    if not text:
        return "-"
    if text.endswith("주"):
        return text
    return f"{text}주"


def _format_symbol_with_name(symbol: object, env: Mapping[str, str]) -> str:
    safe_symbol = sanitize_text(symbol or "-", env).strip() or "-"
    symbol_name = get_symbol_name(safe_symbol)
    if not symbol_name:
        registry = _get_tag_registry()
        if registry is not None:
            tag_name = registry.get_name(safe_symbol)
            if tag_name:
                symbol_name = tag_name
    if not symbol_name:
        return safe_symbol
    return f"{safe_symbol} {sanitize_text(symbol_name, env).strip()}"


def _compute_order_total(
    quantity: object,
    price: object,
) -> str | None:
    try:
        qty_int = int(str(quantity).replace(",", "").replace("주", "").strip())
    except (TypeError, ValueError):
        return None
    try:
        price_int = int(str(price).replace(",", "").replace("원", "").strip())
    except (TypeError, ValueError):
        return None
    if qty_int <= 0 or price_int <= 0:
        return None
    total = qty_int * price_int
    return f"{total:,}원"


_RATE_LIMIT_SOURCE_LABELS = {
    "balance": "잔고 조회",
    "buy_scan": "매수 스캔",
    "sell_watch": "매도 감시",
    "buy_order": "매수 주문",
    "sell_order": "매도 주문",
}


def _format_rate_limit_source(source: object, env: Mapping[str, str]) -> str:
    source_text = sanitize_text(source or "", env).strip()
    if not source_text:
        return "unknown"
    label = _RATE_LIMIT_SOURCE_LABELS.get(source_text)
    if not label:
        return source_text
    return f"`{source_text}` {label}"


def _build_rate_limit_alert_text(
    *,
    message: str,
    details: Mapping[str, object] | None,
    env: Mapping[str, str],
) -> str:
    detail_map = details or {}
    source = _format_rate_limit_source(
        detail_map.get("source") or detail_map.get("rate_limit_source"),
        env,
    )
    parts = [f"⚠️ *KIS rate-limit* | {source}"]

    metrics: list[str] = []
    hits = detail_map.get("hits") or detail_map.get("rate_limit_hits")
    backoff = (
        detail_map.get("backoff_seconds")
        or detail_map.get("backoff_applied_seconds")
        or detail_map.get("backoff_remaining_seconds")
    )
    if backoff is not None:
        metrics.append(f"Backoff: `{sanitize_text(backoff, env)}s`")
    if hits is not None:
        metrics.append(f"Hits: `{sanitize_text(hits, env)}`")
    if metrics:
        parts.append(" · ".join(metrics))

    action = detail_map.get("action") or detail_map.get("next_action")
    action_label = _format_rate_limit_action(action, env)
    if action_label:
        parts.append(f"Action: {action_label}")
    return "\n".join(parts)


_RATE_LIMIT_ACTION_LABELS = {
    "skip_cycle": "이번 사이클 스킵",
    "defer_buy_scan": "매수 스캔 보류",
    "defer_sell_watch": "매도 감시 보류",
    "defer_order": "주문 보류",
}


def _format_rate_limit_action(action: object, env: Mapping[str, str]) -> str:
    action_text = sanitize_text(action or "", env).strip()
    if not action_text:
        return ""
    return _RATE_LIMIT_ACTION_LABELS.get(action_text, action_text)


_POSTRUN_STATUS_COLORS = {
    "complete": "#36a64f",
    "failed": "#E53935",
}
_POSTRUN_DEFAULT_COLOR = "#daa038"


def _build_postrun_alert_payload(
    *,
    channel: str,
    message: str,
    details: Mapping[str, object] | None,
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Render an EOD postrun event as a compact, colored, glanceable alert.

    The pre-formatted ``message`` (from ``format_eod_report_summary``) already
    carries the essentials. We keep only the status header + the meaningful
    body lines, drop the long absolute ``Report:`` path and the redundant
    key=value detail dump, and wrap the body in a green/red attachment so an
    operator can read the outcome at a glance.
    """
    detail_map = details or {}
    status = str(detail_map.get("status") or "").strip().lower()
    color = _POSTRUN_STATUS_COLORS.get(status, _POSTRUN_DEFAULT_COLOR)

    raw_lines = [line for line in str(message or "").splitlines() if line.strip()]
    header = sanitize_text(raw_lines[0], env).strip() if raw_lines else "EOD postrun"

    body_lines: list[str] = []
    for line in raw_lines[1:]:
        if line.strip().lower().startswith("report:"):
            continue
        body_lines.append(sanitize_text(line, env).rstrip())

    if status == "failed":
        report = str(detail_map.get("report") or "").strip()
        if report:
            body_lines.append(f"Report: {os.path.basename(report)}")

    payload: dict[str, Any] = {
        "channel": channel,
        "text": header,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if body_lines:
        payload["attachments"] = [
            {
                "color": color,
                "text": "\n".join(body_lines),
                "mrkdwn_in": ["text"],
            }
        ]
    return payload


_BOILERPLATE_REASONS = frozenset({
    "주문 API 호출 직전",
    "주문 API 호출 직전입니다.",
    "매도 주문 API 호출 직전입니다.",
    "매수 주문 API 호출 직전입니다.",
    "모의투자 매수주문이 완료 되었습니다.",
    "모의투자 매도주문이 완료 되었습니다.",
    "API 접수 완료",
})


def format_symbol_tag_summary(symbol: str) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        registry = _get_tag_registry()
        if registry is None:
            return result
        tags = registry.get_tags(symbol)
        if not tags:
            return result
    except Exception:
        return result

    def _clean(t: str) -> str:
        if ":" not in t:
            return t
        ns, val = t.split(":", 1)
        if ns == "asset" and val == "etf": return "ETF"
        if ns == "market": return val.upper()
        if ns == "sector" and val == "semiconductor": return "반도체"
        if ns == "theme" and val == "ai": return "AI"
        if ns == "theme" and val == "hbm": return "HBM"
        if ns == "risk" and val == "cycle_sensitive": return "경기민감"
        return val.replace("_", " ")

    asset = [_clean(t) for t in tags if t.startswith("asset:") and t == "asset:etf"]
    market = [_clean(t) for t in tags if t.startswith("market:")]
    sector = [_clean(t) for t in tags if t.startswith("sector:")]
    universe = [_clean(t) for t in tags if t.startswith("universe:")]
    theme = [_clean(t) for t in tags if t.startswith("theme:")][:2]
    liq = [_clean(t) for t in tags if t.startswith("liq:")]
    vol = [_clean(t) for t in tags if t.startswith("vol:")]

    if "AI" in theme and "HBM" in theme:
        theme.remove("AI")
        theme.remove("HBM")
        theme.insert(0, "AI/HBM")

    classification = asset + market + sector + theme + universe + liq + vol
    classification = [c for c in classification if c]
    if classification:
        result["태그"] = " · ".join(classification)

    risk = [_clean(t) for t in tags if t.startswith("risk:")][:2]
    if risk:
        result["주의"] = " · ".join(risk)

    return result


def _infer_order_side(event_type: str, details: Mapping[str, object] | None, env: Mapping[str, str]) -> str:
    detail_map = details or {}
    explicit_side = sanitize_text(detail_map.get("side") or "", env).strip().upper()
    if explicit_side:
        return explicit_side

    action = sanitize_text(detail_map.get("action") or "", env).strip().lower()
    event = str(event_type or "").strip().lower()

    for val in (action, event):
        if not val:
            continue
        if val.startswith("sell_order_"):
            return "SELL"
        if val.startswith("order_"):
            return "BUY"
    return ""


def _build_order_alert_text(
    *,
    event_type: str,
    symbol: str | None,
    details: Mapping[str, object] | None,
    env: Mapping[str, str],
) -> str | None:
    event_label = _ORDER_EVENT_LABELS.get(event_type)
    if event_label is None:
        return None

    emoji, status_label = event_label
    detail_map = details or {}
    side = _infer_order_side(event_type, details, env)
    side_label = _ORDER_SIDE_LABELS.get(side, side or "주문")
    safe_symbol = _format_symbol_with_name(symbol or detail_map.get("symbol"), env)
    raw_quantity = detail_map.get("quantity", detail_map.get("qty", "-"))
    quantity = _format_order_quantity(raw_quantity, env)
    raw_price = (
        detail_map.get("submitted_price_krw")
        or detail_map.get("order_price_krw")
        or detail_map.get("price_krw")
    )
    price = _format_krw(raw_price, env)
    order_total = _compute_order_total(raw_quantity, raw_price)

    reason = _format_order_reason(detail_map.get("reason"), env)
    reason_label = "사유" if event_type == "order_rejected" else "메모"

    is_boilerplate = False
    if reason and reason.strip() in _BOILERPLATE_REASONS:
        is_boilerplate = True
    if not reason:
        is_boilerplate = True

    header = f"{emoji} {side_label} {status_label} | {safe_symbol}"

    price_str = f" × {price}" if price else ""
    total_str = f" = {order_total}" if order_total else ""

    body_lines = []
    body_lines.append(f"{quantity}{price_str}{total_str} | 상태: {status_label}")

    symbol_code = sanitize_text(symbol or detail_map.get("symbol") or "", env).strip()
    if symbol_code:
        try:
            tag_summary = format_symbol_tag_summary(symbol_code)
        except Exception:
            tag_summary = {}
        for label, value in tag_summary.items():
            body_lines.append(f"{label}: {value}")

    if reason and not is_boilerplate:
        reason_one_line = reason.replace("\n", " ").strip()
        body_lines.append(f"{reason_label}: {reason_one_line}")

    return header + "\n" + "\n".join(body_lines)
