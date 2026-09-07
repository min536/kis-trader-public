import json
import math
import os
import sys
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.auth.account_scope import (
    get_account_scope_context,
    get_order_log_path,
    get_order_log_read_paths,
)
from app.core.file_read_limits import LocalReadLimitError, check_file_size, iter_lines_bounded
from app.core.time_utils import KOREA_TZ, get_korean_now
from app.scanner.symbol_names import get_symbol_name

# ── File-read cache ────────────────────────────────────────────────────────
# The order log is append-only within a cycle.  Caching parsed lines by
# (file_path, mtime, size) avoids re-reading and re-parsing the same file
# dozens of times per buy-scan cycle (once per candidate × 2 metrics = O(n)).
_LOG_CACHE: dict[str, Any] = {}  # key → {"mtime": float, "size": int, "lines": list}

# The strict order-log reader must tolerate lines larger than the generic 1MB
# per-line cap so that already-appended oversize records (produced before the
# W0/W1 line-size fixes) remain readable — otherwise a single bloated line
# fail-closed-blocks every order for the rest of the day. This only raises the
# per-line ceiling; genuinely runaway lines (> this) still raise
# order_log_too_large, and the 250MB whole-file cap (check_file_size) is
# unchanged. See docs/order_log_line_limit_design_20260707.md §R1.
ORDER_LOG_READER_LINE_MAX_BYTES = 8_000_000

# Writer-side invariant: never append a line the strict reader can't read. A line
# over this soft cap has its raw_response shrunk to the fields real consumers read
# (see _ORDER_LOG_RAW_RESPONSE_KEEP_KEYS) plus a truncation marker. This is the
# last line of defence — W0 (compact selection_details) keeps normal lines far
# under it, so tripping this signals a NEW bulk payload leaked into raw_response.
# 256KB « the 8MB reader cap, so any written line is always re-readable.
ORDER_LOG_LINE_SOFT_MAX_BYTES = 256_000

# F2-1 (E5): a stable per-process marker so order-log records from the same
# process share an identity. pid alone is reused across restarts; combined with
# process_started_at (and the already-stamped account_signature), a cross-write
# incident is diagnosable from the records alone — which process, which scope.
_PROCESS_STARTED_AT = get_korean_now().isoformat()

# raw_response keys the performance/slippage reporters actually consume — these
# are small and must survive truncation (app/reporting/performance*.py,
# fill_slippage.py). Everything else (notably selection_details) is droppable.
_ORDER_LOG_RAW_RESPONSE_KEEP_KEYS = (
    "position_sizing",
    "order_plan",
    "sell_plan",
    "sell_strategy_details",
    "reference_price_krw",
    "fill_price_krw",
    "notional_krw",
    "sell_test_mode",
    "reason_code",
)


def _shrink_oversize_raw_response(raw_response: Any, *, original_bytes: int) -> dict[str, Any]:
    """Keep consumer-critical raw_response fields, drop bulk, stamp a marker."""
    kept: dict[str, Any] = {}
    dropped: dict[str, int] = {}
    if isinstance(raw_response, Mapping):
        for key, value in raw_response.items():
            if key in _ORDER_LOG_RAW_RESPONSE_KEEP_KEYS:
                kept[key] = value
            else:
                try:
                    dropped[str(key)] = len(
                        json.dumps(value, ensure_ascii=False, default=str)
                    )
                except (TypeError, ValueError):
                    dropped[str(key)] = -1
    kept["truncated"] = True
    kept["original_bytes"] = original_bytes
    kept["dropped_keys"] = dropped
    return kept


class OrderLogReadError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _read_log_lines_cached(log_path, *, strict: bool = False) -> list[dict[str, Any]]:
    """Return parsed JSON records from the log file, using an mtime+size cache."""
    try:
        stat = os.stat(log_path)
        mtime = stat.st_mtime
        size = stat.st_size
        check_file_size(log_path)
    except OSError as exc:
        if strict:
            raise OrderLogReadError(
                "order_log_stat_failed",
                "주문 로그 상태를 확인할 수 없어 주문 제출을 차단합니다.",
            ) from exc
        return []
    except LocalReadLimitError as exc:
        if strict:
            raise OrderLogReadError(
                "order_log_too_large",
                "주문 로그가 읽기 제한을 초과해 주문 제출을 차단합니다.",
            ) from exc
        return []

    cache_key = str(log_path)
    cached = _LOG_CACHE.get(cache_key)
    if cached and cached["mtime"] == mtime and cached["size"] == size:
        if strict and int(cached.get("parse_error_count", 0) or 0) > 0:
            raise OrderLogReadError(
                "order_log_malformed",
                "주문 로그에 파싱 오류가 있어 주문 제출을 차단합니다.",
            )
        return cached["lines"]

    records: list[dict[str, Any]] = []
    parse_error_count = 0
    try:
        for _lineno, raw_line in iter_lines_bounded(
            log_path, max_line_bytes=ORDER_LOG_READER_LINE_MAX_BYTES
        ):
            line = raw_line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parse_error_count += 1
                if strict:
                    raise OrderLogReadError(
                        "order_log_malformed",
                        "주문 로그에 파싱 오류가 있어 주문 제출을 차단합니다.",
                    ) from exc
                continue
    except LocalReadLimitError as exc:
        if strict:
            raise OrderLogReadError(
                "order_log_too_large",
                "주문 로그가 읽기 제한을 초과해 주문 제출을 차단합니다.",
            ) from exc
        return []
    except UnicodeDecodeError as exc:
        if strict:
            raise OrderLogReadError(
                "order_log_read_failed",
                "주문 로그를 읽을 수 없어 주문 제출을 차단합니다.",
            ) from exc
        return []
    except OSError as exc:
        print(f"[warn] 주문 로그 읽기 실패: {exc}", file=sys.stderr)
        if strict:
            raise OrderLogReadError(
                "order_log_read_failed",
                "주문 로그를 읽을 수 없어 주문 제출을 차단합니다.",
            ) from exc
        return []

    _LOG_CACHE[cache_key] = {
        "mtime": mtime,
        "size": size,
        "lines": records,
        "parse_error_count": parse_error_count,
    }
    return records


def _is_same_target_date(timestamp: str, target_date: date) -> bool:
    if not timestamp:
        return False

    try:
        record_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return False

    if record_time.tzinfo is not None:
        record_date = record_time.astimezone(KOREA_TZ).date()
    else:
        record_date = record_time.date()
    return record_date == target_date


def _iter_today_records_by_actions(
    *,
    actions: tuple[str, ...],
    today: date | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    target_date = today or get_korean_now().date()
    primary_path = get_order_log_path()
    read_paths = [primary_path]
    try:
        candidates = list(get_order_log_read_paths())
        if primary_path in candidates:
            read_paths = candidates
    except Exception:
        pass
    order_log_files = [path for path in read_paths if path.exists()]
    if not order_log_files:
        return []

    action_set = set(actions)
    records: list[dict[str, Any]] = []
    for order_log_file in order_log_files:
        records.extend(
            record
            for record in _read_log_lines_cached(order_log_file, strict=strict)
            if str(record.get("action", "")).strip() in action_set
            and _is_same_target_date(str(record.get("timestamp", "")).strip(), target_date)
        )
    return records


def _extract_notional_krw(record: dict[str, Any]) -> int:
    raw_response = record.get("raw_response")
    if not isinstance(raw_response, dict):
        return 0

    direct_value = raw_response.get("notional_krw")
    if direct_value is not None:
        try:
            return int(float(str(direct_value).strip()))
        except ValueError:
            return 0

    order_plan = raw_response.get("order_plan")
    if isinstance(order_plan, dict):
        plan_value = order_plan.get("notional_krw")
        if plan_value is not None:
            try:
                return int(float(str(plan_value).strip()))
            except ValueError:
                return 0

    sell_plan = raw_response.get("sell_plan")
    if isinstance(sell_plan, dict):
        plan_value = sell_plan.get("notional_krw")
        if plan_value is not None:
            try:
                return int(float(str(plan_value).strip()))
            except ValueError:
                return 0

    return 0


def summarize_order_reason(raw_response: Any, default: str) -> str:
    if isinstance(raw_response, dict):
        msg1 = str(raw_response.get("msg1", "")).strip()
        message = str(raw_response.get("message", "")).strip()
        msg_cd = str(raw_response.get("msg_cd", "")).strip()

        if msg1 and msg_cd:
            return f"{msg1} ({msg_cd})"
        if msg1:
            return msg1
        if message and msg_cd:
            return f"{message} ({msg_cd})"
        if message:
            return message
        if msg_cd:
            return msg_cd

    if isinstance(raw_response, str):
        text = raw_response.strip()
        if text:
            return text

    return default


# Fatal account-level KIS rejection codes. A rejection carrying one of these
# means the *account itself* cannot place orders (e.g. 40910000 "모의투자 주문이
# 불가한 계좌입니다") — retrying the same order will never succeed until the
# operator fixes the account. Kept as a module-level frozenset for extension.
_FATAL_ACCOUNT_REJECTION_CODES: frozenset[str] = frozenset({"40910000"})


def classify_order_rejection(raw: Any) -> str:
    """Classify an order rejection into one of ``{"fatal_account", "other"}``.

    Total by design (mirrors ``_coerce_price``): must NEVER raise so the order
    failure path can never crash on classification. Handles, in order:

    * a top-level mapping ``{"msg_cd": "40910000"}``
    * a nested ``{"order_response": {"msg_cd": ...}}`` / ``{"raw_response": ...}``
      / ``{"data": ...}`` exception-data dict
    * a REASON STRING with a trailing ``"(40910000)"`` — the primary production
      path (``summarize_order_reason`` renders e.g. "모의투자 주문이 불가한
      계좌입니다. (40910000)")
    * ``None`` / unknown codes / unexpected shapes → ``"other"``.
    """

    try:
        code = _extract_rejection_code(raw)
    except Exception:
        return "other"
    if code and code in _FATAL_ACCOUNT_REJECTION_CODES:
        return "fatal_account"
    return "other"


def _extract_rejection_code(raw: Any) -> str | None:
    """Best-effort extraction of the KIS ``msg_cd`` from an order rejection.

    Never raises; returns ``None`` when no code can be located.
    """

    if raw is None:
        return None
    if isinstance(raw, Mapping):
        direct = str(raw.get("msg_cd", "") or "").strip()
        if direct:
            return direct
        for nested_key in ("order_response", "raw_response", "response", "data"):
            nested = raw.get(nested_key)
            if isinstance(nested, Mapping):
                nested_code = str(nested.get("msg_cd", "") or "").strip()
                if nested_code:
                    return nested_code
        return None
    if isinstance(raw, str):
        return _extract_trailing_paren_code(raw)
    return None


def _extract_trailing_paren_code(text: str) -> str | None:
    """Pull a trailing ``(NNNN)`` code out of a summarized reason string.

    e.g. "모의투자 주문이 불가한 계좌입니다. (40910000)" -> "40910000".
    Returns ``None`` when no trailing parenthesized token exists.
    """

    stripped = text.strip()
    if not stripped.endswith(")"):
        return None
    open_idx = stripped.rfind("(")
    if open_idx < 0:
        return None
    inner = stripped[open_idx + 1 : -1].strip()
    return inner or None


def _coerce_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except Exception:
        # Total by design: float() can raise OverflowError (huge int) or an
        # arbitrary error from a custom __float__; capture must never raise.
        return None
    if not math.isfinite(result):
        return None
    return result


def build_price_capture(
    *,
    reference_price_krw: Any = None,
    quote_at_submit: Any = None,
) -> dict[str, float]:
    """Build the additive W2 price-capture block for an order record.

    Pure and total: each price is coerced to a finite ``float`` and included
    only when valid; an unparseable, non-finite, ``None``, or ``bool`` value is
    silently omitted (the slippage consumer counts a missing reference). This
    never raises so that price capture can never affect the order path — the
    submit/succeed call sites still wrap the value extraction in ``try/except``
    as a second layer of isolation.
    """
    capture: dict[str, float] = {}
    reference = _coerce_price(reference_price_krw)
    if reference is not None:
        capture["reference_price_krw"] = reference
    quote = _coerce_price(quote_at_submit)
    if quote is not None:
        capture["quote_at_submit"] = quote
    return capture


def log_order_event(
    *,
    symbol: str,
    qty: int,
    order_type: str,
    confirm_buy: str,
    market_open: bool,
    action: str,
    result: str,
    reason: str,
    raw_response: Any,
    environment: str = "mock",
    cycle_id: str | None = None,
) -> bool:
    record = {
        "timestamp": get_korean_now().isoformat(),
        "cycle_id": cycle_id,
        "symbol": symbol,
        "symbol_name": get_symbol_name(symbol),
        "qty": qty,
        "order_type": order_type,
        "confirm_buy": confirm_buy,
        "market_open": market_open,
        "action": action,
        "result": result,
        "reason": reason,
        "environment": environment,
        **get_account_scope_context(),
        "pid": os.getpid(),
        "process_started_at": _PROCESS_STARTED_AT,
        "raw_response": raw_response,
    }

    try:
        serialized = json.dumps(record, ensure_ascii=False, default=str)
        line_bytes = len(serialized.encode("utf-8"))
        if line_bytes > ORDER_LOG_LINE_SOFT_MAX_BYTES:
            record["raw_response"] = _shrink_oversize_raw_response(
                raw_response, original_bytes=line_bytes
            )
            serialized = json.dumps(record, ensure_ascii=False, default=str)
            print(
                f"[warn] 주문 로그 라인이 {line_bytes} bytes로 상한을 초과해 "
                f"raw_response를 요약했습니다. (action={action})",
                file=sys.stderr,
            )
        log_path = get_order_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(serialized)
            log_file.write("\n")

        return True
    except OSError as exc:
        print(f"[warn] 주문 로그 저장 실패: {exc}", file=sys.stderr)
        return False


def count_today_order_submissions(today: date | None = None, *, strict: bool = False) -> int:
    return len(
        _iter_today_records_by_actions(
            actions=("order_submitted",),
            today=today,
            strict=strict,
        )
    )


def sum_today_order_submission_notional_krw(
    today: date | None = None,
    *,
    strict: bool = False,
) -> int:
    return sum(
        _extract_notional_krw(record)
        for record in _iter_today_records_by_actions(
            actions=("order_submitted",),
            today=today,
            strict=strict,
        )
    )


def count_today_buy_order_submissions(
    today: date | None = None,
    *,
    strict: bool = False,
) -> int:
    return len(
        _iter_today_records_by_actions(
            actions=("order_submitted",),
            today=today,
            strict=strict,
        )
    )


def sum_today_buy_order_submission_notional_krw(
    today: date | None = None,
    *,
    strict: bool = False,
) -> int:
    return sum_today_order_submission_notional_krw(today=today, strict=strict)


def count_today_sell_order_submissions(
    today: date | None = None,
    *,
    strict: bool = False,
) -> int:
    return len(
        _iter_today_records_by_actions(
            actions=("sell_order_submitted",),
            today=today,
            strict=strict,
        )
    )


def sum_today_sell_order_submission_notional_krw(
    today: date | None = None,
    *,
    strict: bool = False,
) -> int:
    return sum(
        _extract_notional_krw(record)
        for record in _iter_today_records_by_actions(
            actions=("sell_order_submitted",),
            today=today,
            strict=strict,
        )
    )
