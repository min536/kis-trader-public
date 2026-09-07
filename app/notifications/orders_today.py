"""Order-lifecycle reading/compaction/rendering for the Slack orders-today reply.

Relocated verbatim from app.notifications.runtime_status_snapshot (R3-S3).
runtime_status_snapshot keeps legacy bindings so existing import sites and
patch targets remain valid.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from app.core.file_read_limits import LocalReadLimitError, iter_lines_bounded
from app.core.time_utils import KOREA_TZ, get_korean_now
from app.notifications.snapshot_shared import (
    DEFAULT_SNAPSHOT_PATH,
    DEFAULT_STALE_AFTER_SEC,
    _SIDE_LABELS,
    _safe_int,
    _safe_price,
    _safe_text,
)

DEFAULT_ORDER_LOG_MAX_LINES = 5000


def _read_today_order_events(
    *,
    log_dir: Path | None = None,
    today: datetime | None = None,
    account_signature: str | None = None,
    max_lines: int = DEFAULT_ORDER_LOG_MAX_LINES,
) -> list[dict[str, Any]]:
    resolved_account_signature = str(account_signature or "").strip() or None
    if log_dir is None:
        try:
            from app.auth.account_scope import get_account_signature, get_order_log_path
            log_path = get_order_log_path()
            resolved_account_signature = resolved_account_signature or get_account_signature()
        except Exception:
            return []
    else:
        log_path = log_dir

    if not log_path.exists():
        return []

    current = today or get_korean_now()
    if hasattr(current, "astimezone"):
        target_date = current.astimezone(KOREA_TZ).date()
    elif hasattr(current, "date"):
        target_date = current.date()
    else:
        target_date = current
    raw_lines: deque[str] = deque(maxlen=max(1, int(max_lines or 1)))
    try:
        for _lineno, raw_line in iter_lines_bounded(log_path, encoding="utf-8"):
            raw_lines.append(raw_line)
    except (LocalReadLimitError, OSError, UnicodeDecodeError):
        return []

    records: list[dict[str, Any]] = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        record_account = str(record.get("account_signature") or "").strip()
        if (
            resolved_account_signature
            and record_account
            and record_account != resolved_account_signature
        ):
            continue
        ts = str(record.get("timestamp") or "").strip()
        if not ts:
            continue
        try:
            record_time = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if record_time.tzinfo is not None:
            record_date = record_time.astimezone(KOREA_TZ).date()
        else:
            record_date = record_time.date()
        if record_date == target_date:
            records.append(record)
    return records


_ORDER_ACTION_STATUS = {
    "order_submitted": ("BUY", "submitted"),
    "order_succeeded": ("BUY", "succeeded"),
    "order_accepted": ("BUY", "succeeded"),
    "order_completed": ("BUY", "succeeded"),
    "order_failed": ("BUY", "failed"),
    "order_rejected": ("BUY", "failed"),
    "sell_order_submitted": ("SELL", "submitted"),
    "sell_order_succeeded": ("SELL", "succeeded"),
    "sell_order_accepted": ("SELL", "succeeded"),
    "sell_order_completed": ("SELL", "succeeded"),
    "sell_order_failed": ("SELL", "failed"),
    "sell_order_rejected": ("SELL", "failed"),
}


def _order_record_time(record: Mapping[str, Any]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(record.get("timestamp") or ""))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(KOREA_TZ)
    return parsed.replace(tzinfo=KOREA_TZ)


def _order_side_status(record: Mapping[str, Any]) -> tuple[str, str] | None:
    action = str(record.get("action") or "").strip()
    if action in _ORDER_ACTION_STATUS:
        return _ORDER_ACTION_STATUS[action]
    if "sell_order" in action and ("fail" in action or "reject" in action):
        return ("SELL", "failed")
    if "order" in action and ("fail" in action or "reject" in action):
        return ("BUY", "failed")
    return None


def _raw_response_mapping(record: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_response = record.get("raw_response")
    return raw_response if isinstance(raw_response, Mapping) else {}


def _nested_mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


def _first_present(*values: object) -> object | None:
    for value in values:
        if value is not None and str(value).strip() != "":
            return value
    return None


def _order_qty(record: Mapping[str, Any]) -> int:
    raw_response = _raw_response_mapping(record)
    order_plan = _nested_mapping(raw_response, "order_plan")
    sell_plan = _nested_mapping(raw_response, "sell_plan")
    buy_plan = _nested_mapping(raw_response, "buy_plan")
    return _safe_int(
        _first_present(
            record.get("qty"),
            record.get("quantity"),
            order_plan.get("qty"),
            sell_plan.get("qty"),
            buy_plan.get("qty"),
        )
    )


def _order_price_and_notional(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    raw_response = _raw_response_mapping(record)
    order_plan = _nested_mapping(raw_response, "order_plan")
    sell_plan = _nested_mapping(raw_response, "sell_plan")
    buy_plan = _nested_mapping(raw_response, "buy_plan")
    price = _safe_price(
        _first_present(
            record.get("price"),
            record.get("order_price"),
            record.get("order_price_krw"),
            record.get("submitted_price_krw"),
            record.get("price_krw"),
            order_plan.get("current_price_krw"),
            order_plan.get("price"),
            sell_plan.get("current_price_krw"),
            sell_plan.get("price"),
            buy_plan.get("current_price_krw"),
            buy_plan.get("price"),
        )
    )
    notional = _safe_price(
        _first_present(
            record.get("notional_krw"),
            record.get("order_notional_krw"),
            raw_response.get("notional_krw"),
            order_plan.get("notional_krw"),
            sell_plan.get("notional_krw"),
            buy_plan.get("notional_krw"),
        )
    )
    if notional is None and price is not None:
        qty = _order_qty(record)
        if qty > 0:
            notional = qty * price
    return price, notional


def _order_failure_category(record: Mapping[str, Any]) -> str | None:
    raw_response = _raw_response_mapping(record)
    return _safe_text(raw_response.get("failure_category"))


def _order_compact_key(record: Mapping[str, Any], side: str) -> tuple[object, ...]:
    cycle_id = _safe_text(record.get("cycle_id"))
    symbol = _safe_text(record.get("symbol")) or ""
    qty = _order_qty(record)
    if cycle_id:
        return ("cycle", cycle_id, side, symbol, qty)
    return ("fallback", side, symbol, qty)


def _pair_submission(
    pending: list[dict[str, Any]],
    terminal: Mapping[str, Any],
    side: str,
) -> dict[str, Any] | None:
    terminal_time = _order_record_time(terminal)
    terminal_key = _order_compact_key(terminal, side)
    for index in range(len(pending) - 1, -1, -1):
        candidate = pending[index]
        candidate_key = _order_compact_key(candidate, side)
        if candidate_key != terminal_key:
            continue
        candidate_time = _order_record_time(candidate)
        if terminal_time is not None and candidate_time is not None:
            delta_seconds = (terminal_time - candidate_time).total_seconds()
            if delta_seconds < 0 or delta_seconds > 600:
                continue
        return pending.pop(index)
    return None


def _compact_order_lifecycles(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_records = sorted(records, key=lambda item: str(item.get("timestamp") or ""))
    pending_submissions: list[dict[str, Any]] = []
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    counts = {
        "submitted": 0,
        "succeeded": 0,
        "failed": 0,
        "no_position_on_sell": 0,
    }

    for record in ordered_records:
        side_status = _order_side_status(record)
        if side_status is None:
            continue
        side, status = side_status
        if status == "submitted":
            counts["submitted"] += 1
            pending_submissions.append(record)
            continue

        row = dict(record)
        row["_order_side"] = side
        row["_order_status"] = status
        submitted_record = _pair_submission(pending_submissions, record, side)
        if submitted_record is not None:
            row["_submitted_at"] = submitted_record.get("timestamp")
        if status == "succeeded":
            counts["succeeded"] += 1
            succeeded.append(row)
        elif status == "failed":
            counts["failed"] += 1
            if _order_failure_category(record) == "no_position_on_sell":
                counts["no_position_on_sell"] += 1
            failed.append(row)

    submitted_only = []
    for record in pending_submissions:
        side_status = _order_side_status(record)
        if side_status is None:
            continue
        row = dict(record)
        row["_order_side"] = side_status[0]
        row["_order_status"] = "submitted"
        submitted_only.append(row)

    return {
        "counts": counts,
        "succeeded": succeeded,
        "failed": failed,
        "submitted_only": submitted_only,
    }


def _format_order_time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return "n/a"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(KOREA_TZ)
    return parsed.strftime("%H:%M")


def _truncate_order_reason(value: object, *, limit: int = 100) -> str:
    reason = str(value or "알 수 없는 오류").replace("\n", " ").strip()
    if len(reason) > limit:
        return reason[: limit - 3] + "..."
    return reason


def _render_order_lifecycle_line(record: Mapping[str, Any]) -> str:
    side = str(record.get("_order_side") or "").strip().upper()
    status = str(record.get("_order_status") or "").strip()
    side_label = _SIDE_LABELS.get(side, side or "-")
    status_label = {
        "submitted": "제출",
        "succeeded": "접수",
        "failed": "실패",
    }.get(status, status or "-")
    symbol = _safe_text(record.get("symbol")) or "-"
    name = _safe_text(record.get("symbol_name"))
    display = f"{symbol} {name}" if name else symbol
    qty = _order_qty(record)
    price, notional = _order_price_and_notional(record)
    pid = _safe_text(record.get("pid"))
    category = _order_failure_category(record)

    time_str = _format_order_time(record.get("timestamp"))
    line1 = f"*{side_label}* `{status_label}` · {time_str}"
    line2 = f"*{display}*" + (f" · {qty}주" if qty > 0 else "")

    price_parts: list[str] = []
    if price is not None:
        price_parts.append(f"`{price:,}원`")
    if notional is not None:
        price_parts.append(f"총 `{notional:,}원`")

    meta_parts: list[str] = []
    if pid:
        meta_parts.append(f"pid `{pid}`")
    if category:
        meta_parts.append(f"`{category}`")
    if record.get("_submitted_at"):
        meta_parts.append(f"submitted `{_format_order_time(record.get('_submitted_at'))}`")

    lines = [line1, line2]
    if price_parts:
        lines.append(" · ".join(price_parts))
    if meta_parts:
        lines.append(" · ".join(meta_parts))
    if status == "failed":
        lines.append(f"사유: {_truncate_order_reason(record.get('reason'))}")
    return "\n".join(lines)


def render_orders_today_reply(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    order_log_path: Path | None = None,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
    account_signature: str | None = None,
    max_log_lines: int = DEFAULT_ORDER_LOG_MAX_LINES,
) -> str | dict[str, Any]:
    filter_time = now or get_korean_now()
    records = _read_today_order_events(
        log_dir=order_log_path,
        today=filter_time,
        account_signature=account_signature,
        max_lines=max_log_lines,
    )
    if not records:
        return "*오늘 주문 처리 내역*\n\n주문 기록이 없습니다."
    compact = _compact_order_lifecycles(records)
    counts = compact["counts"]

    header_text = (
        "*오늘 주문 처리 내역*\n"
        "_로컬 주문 로그 기준 · KST · 제출은 감사 이벤트이며 기본 표시는 브로커 응답 중심_\n\n"
        f"요약: 제출 {int(counts['submitted'])}건 · "
        f"접수 {int(counts['succeeded'])}건 · "
        f"실패 {int(counts['failed'])}건 · "
        f"no_position_on_sell {int(counts['no_position_on_sell'])}건"
    )

    succeeded = list(reversed(compact["succeeded"]))[:8]
    failed = list(reversed(compact["failed"]))[:5]
    submitted_only = list(reversed(compact["submitted_only"]))[:3]

    attachments: list[dict[str, Any]] = []

    if succeeded:
        attachments.append({"color": "#888888", "text": "*접수/성공*", "mrkdwn_in": ["text"]})
        for row in succeeded:
            side = str(row.get("_order_side") or "").strip().upper()
            color = "#2eb886" if side == "BUY" else "#e01e5a"
            attachments.append({"color": color, "text": _render_order_lifecycle_line(row), "mrkdwn_in": ["text"]})

    if failed:
        attachments.append({"color": "#888888", "text": "*실패/거절*", "mrkdwn_in": ["text"]})
        for row in failed:
            side = str(row.get("_order_side") or "").strip().upper()
            color = "#2eb886" if side == "BUY" else "#e01e5a"
            attachments.append({"color": color, "text": _render_order_lifecycle_line(row), "mrkdwn_in": ["text"]})

    if submitted_only:
        attachments.append({"color": "#888888", "text": "*제출만 기록* _(브로커 응답 없음/미확인)_", "mrkdwn_in": ["text"]})
        for row in submitted_only:
            attachments.append({"color": "#fcb400", "text": _render_order_lifecycle_line(row), "mrkdwn_in": ["text"]})

    return {"text": header_text, "attachments": attachments}
