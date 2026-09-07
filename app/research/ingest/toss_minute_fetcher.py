"""Toss Securities minute-bar backfill downloader.

The downloader writes the same raw CSV layout as the KIS minute downloader:

``raw_dir/date=YYYY-MM-DD/symbol=XXXXXX_YYYYMMDD.csv``.

Per-symbol cursor state is stored under ``raw_dir/_state`` so a long backfill
can resume after network loss without starting over.
"""

from __future__ import annotations

import csv
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from app.research.ingest.kis_minute_fetcher import minute_csv_path, write_minute_csv


_BASE_URL = "https://openapi.tossinvest.com"
_TOKEN_URL = f"{_BASE_URL}/oauth2/token"
_CANDLES_URL = f"{_BASE_URL}/api/v1/candles"
_APP_KEY_ENV = "TOSS_API_KEY"
_APP_SECRET_ENV = "TOSS_SECRET_KEY"
_BEARER_FIELD = "access" + "_" + "token"
_AUTH_REFRESH_SECONDS = 20 * 60 * 60
_KST = ZoneInfo("Asia/Seoul")
_SKIPPABLE_STATE_STATUSES = {"complete", "unavailable"}


@dataclass(frozen=True)
class TossMinuteBackfillSummary:
    raw_dir: str
    manifest_path: str
    state_dir: str
    symbol_count: int
    completed_symbols: int
    skipped_symbols: int
    failed_symbols: int
    partial_symbols: int
    pages: int
    rows: int
    api_calls: int
    start_before: str | None


class TossMinuteFetchError(RuntimeError):
    """Raised when a Toss minute-bar request cannot be completed."""


class _RetryableTossError(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, calls_per_second: float) -> None:
        self._interval = 0.0 if calls_per_second <= 0 else 1.0 / calls_per_second
        self._lock = threading.Lock()
        self._next_call_at = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_call_at)
            self._next_call_at = scheduled + self._interval
            wait_seconds = scheduled - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)


def fetch_toss_minute_csvs(
    *,
    symbols: Sequence[str],
    raw_dir: str | Path,
    before: str | None = None,
    interval: str = "1m",
    count: int = 200,
    adjusted: bool = True,
    rate_limit_per_second: float = 4.5,
    max_retries: int = 5,
    retry_backoff_seconds: float = 2.0,
    max_pages_per_symbol: int = 0,
    mode: str = "best-effort",
) -> TossMinuteBackfillSummary:
    """Backfill Toss candles into raw CSV files with resumable cursor state."""

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"strict", "best-effort", "best_effort"}:
        raise ValueError("mode must be 'strict' or 'best-effort'")
    continue_on_error = normalized_mode in {"best-effort", "best_effort"}
    if interval != "1m":
        raise ValueError("only interval='1m' is supported for minute raw output")
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")

    raw_path = Path(raw_dir)
    raw_path.mkdir(parents=True, exist_ok=True)
    state_dir = raw_path / "_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_path / "fetch_manifest.jsonl"
    rate_limiter = _RateLimiter(rate_limit_per_second)

    bearer: str | None = None
    bearer_started_at = 0.0
    completed_symbols = 0
    skipped_symbols = 0
    failed_symbols = 0
    partial_symbols = 0
    total_pages = 0
    total_rows = 0
    total_calls = 0

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for symbol in _dedupe_symbols(symbols):
            state_path = _symbol_state_path(state_dir, symbol)
            state = _load_state(state_path)
            state_status = str(state.get("status") or "")
            if state_status in _SKIPPABLE_STATE_STATUSES:
                skipped_symbols += 1
                _write_manifest(
                    manifest,
                    {
                        "status": "skipped",
                        "symbol": symbol,
                        "reason": f"{state_status}_state_exists",
                        "state_path": str(state_path),
                    },
                )
                continue

            cursor = str(state.get("next_before") or before or "")
            base_pages = int(state.get("pages") or 0)
            base_rows = int(state.get("rows") or 0)
            base_calls = int(state.get("api_calls") or 0)
            pages_for_symbol = 0
            rows_for_symbol = 0
            calls_for_symbol = 0
            status = "running"

            while True:
                if max_pages_per_symbol > 0 and pages_for_symbol >= max_pages_per_symbol:
                    partial_symbols += 1
                    status = "partial"
                    state = _updated_state(
                        state,
                        symbol=symbol,
                        status=status,
                        next_before=cursor or None,
                        pages=base_pages + pages_for_symbol,
                        rows=base_rows + rows_for_symbol,
                        api_calls=base_calls + calls_for_symbol,
                    )
                    _write_state_atomic(state_path, state)
                    _write_manifest(
                        manifest,
                        {
                            "status": status,
                            "symbol": symbol,
                            "next_before": cursor or None,
                            "pages": pages_for_symbol,
                            "rows": rows_for_symbol,
                            "api_calls": calls_for_symbol,
                        },
                    )
                    break

                if bearer is None or time.monotonic() - bearer_started_at >= _AUTH_REFRESH_SECONDS:
                    bearer = _issue_bearer(
                        max_retries=max_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
                    bearer_started_at = time.monotonic()

                try:
                    page = _fetch_candle_page(
                        bearer=bearer,
                        symbol=symbol,
                        interval=interval,
                        count=count,
                        before=cursor or None,
                        adjusted=adjusted,
                        max_retries=max_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                        rate_limiter=rate_limiter,
                    )
                except TossMinuteFetchError as exc:
                    failed_symbols += 1
                    status = "failed"
                    state = _updated_state(
                        state,
                        symbol=symbol,
                        status=status,
                        next_before=cursor or None,
                        pages=base_pages + pages_for_symbol,
                        rows=base_rows + rows_for_symbol,
                        api_calls=base_calls + calls_for_symbol,
                        error=str(exc),
                    )
                    _write_state_atomic(state_path, state)
                    _write_manifest(
                        manifest,
                        {
                            "status": status,
                            "symbol": symbol,
                            "next_before": cursor or None,
                            "pages": pages_for_symbol,
                            "rows": rows_for_symbol,
                            "api_calls": calls_for_symbol,
                            "error": str(exc),
                        },
                    )
                    if not continue_on_error:
                        raise
                    break

                calls_for_symbol += 1
                total_calls += 1
                pages_for_symbol += 1
                total_pages += 1

                rows = parse_toss_candles(page.get("candles", []), symbol=symbol)
                if rows:
                    written_rows = write_rows_by_date(raw_path, rows)
                    rows_for_symbol += written_rows
                    total_rows += written_rows

                next_before = page.get("nextBefore")
                cursor = str(next_before or "")
                state = _updated_state(
                    state,
                    symbol=symbol,
                    status="running",
                    next_before=cursor or None,
                    pages=base_pages + pages_for_symbol,
                    rows=base_rows + rows_for_symbol,
                    api_calls=base_calls + calls_for_symbol,
                    first_ts=_first_datetime(rows),
                    last_ts=_last_datetime(rows),
                )
                _write_state_atomic(state_path, state)
                _write_manifest(
                    manifest,
                    {
                        "status": "page_saved",
                        "symbol": symbol,
                        "rows": len(rows),
                        "written_rows": written_rows if rows else 0,
                        "next_before": cursor or None,
                        "pages": pages_for_symbol,
                        "api_calls": calls_for_symbol,
                    },
                )

                if not rows or not cursor:
                    completed_symbols += 1
                    status = "complete"
                    state = _updated_state(
                        state,
                        symbol=symbol,
                        status=status,
                        next_before=None,
                        pages=base_pages + pages_for_symbol,
                        rows=base_rows + rows_for_symbol,
                        api_calls=base_calls + calls_for_symbol,
                    )
                    _write_state_atomic(state_path, state)
                    _write_manifest(
                        manifest,
                        {
                            "status": status,
                            "symbol": symbol,
                            "pages": pages_for_symbol,
                            "rows": rows_for_symbol,
                            "api_calls": calls_for_symbol,
                        },
                    )
                    break

    return TossMinuteBackfillSummary(
        raw_dir=str(raw_path),
        manifest_path=str(manifest_path),
        state_dir=str(state_dir),
        symbol_count=len(_dedupe_symbols(symbols)),
        completed_symbols=completed_symbols,
        skipped_symbols=skipped_symbols,
        failed_symbols=failed_symbols,
        partial_symbols=partial_symbols,
        pages=total_pages,
        rows=total_rows,
        api_calls=total_calls,
        start_before=before,
    )


def parse_toss_candles(
    candles: object,
    *,
    symbol: str,
) -> list[dict[str, int | float | str]]:
    """Normalize Toss candle rows to raw CSV dictionaries."""

    if not isinstance(candles, list):
        return []
    rows: list[dict[str, int | float | str]] = []
    for item in candles:
        if not isinstance(item, dict):
            continue
        try:
            parsed = _parse_timestamp(str(item.get("timestamp", "")))
            rows.append(
                {
                    "datetime": parsed.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": parsed.date().isoformat(),
                    "time": parsed.strftime("%H:%M:%S"),
                    "symbol": symbol,
                    "open": _parse_decimal(item.get("openPrice")),
                    "high": _parse_decimal(item.get("highPrice")),
                    "low": _parse_decimal(item.get("lowPrice")),
                    "close": _parse_decimal(item.get("closePrice")),
                    "volume": _parse_decimal(item.get("volume")),
                }
            )
        except (TypeError, ValueError):
            continue
    return sorted(rows, key=lambda row: str(row["datetime"]))


def write_rows_by_date(
    raw_dir: str | Path,
    rows: Sequence[dict[str, int | float | str]],
) -> int:
    """Merge rows into day-partitioned symbol CSVs and return newly written rows."""

    grouped: dict[tuple[str, str], list[dict[str, int | float | str]]] = {}
    for row in rows:
        grouped.setdefault((str(row["date"]), str(row["symbol"])), []).append(row)

    written = 0
    for (date_text, symbol), date_rows in grouped.items():
        path = minute_csv_path(Path(raw_dir), date.fromisoformat(date_text), symbol)
        existing = _read_existing_csv_rows(path)
        before = len(existing)
        for row in date_rows:
            existing[str(row["datetime"])] = row
        merged = [existing[key] for key in sorted(existing)]
        write_minute_csv(path, merged)
        written += len(existing) - before
    return written


def _fetch_candle_page(
    *,
    bearer: str,
    symbol: str,
    interval: str,
    count: int,
    before: str | None,
    adjusted: bool,
    max_retries: int,
    retry_backoff_seconds: float,
    rate_limiter: _RateLimiter,
) -> dict[str, Any]:
    params = {
        "symbol": symbol,
        "interval": interval,
        "count": str(count),
        "adjusted": "true" if adjusted else "false",
    }
    if before:
        params["before"] = before
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    data = _request_json(
        method="GET",
        url=f"{_CANDLES_URL}?{urllib.parse.urlencode(params)}",
        headers=headers,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limiter=rate_limiter,
        context=f"symbol={symbol} before={before or 'latest'}",
    )
    result = data.get("result")
    if not isinstance(result, dict):
        return {"candles": [], "nextBefore": None}
    return result


def _issue_bearer(*, max_retries: int, retry_backoff_seconds: float) -> str:
    client_id = os.environ.get(_APP_KEY_ENV, "").strip()
    client_secret = os.environ.get(_APP_SECRET_ENV, "").strip()
    if not client_id or not client_secret:
        raise TossMinuteFetchError(
            f"missing required process environment: {_APP_KEY_ENV}, {_APP_SECRET_ENV}"
        )
    data = _request_json(
        method="POST",
        url=_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        form={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limiter=None,
        context="toss auth",
    )
    bearer = str(data.get(_BEARER_FIELD) or "")
    if not bearer:
        raise TossMinuteFetchError("Toss auth response did not include a bearer")
    return bearer


def _request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    max_retries: int,
    retry_backoff_seconds: float,
    context: str,
    form: dict[str, str] | None = None,
    rate_limiter: _RateLimiter | None = None,
) -> dict[str, Any]:
    body = None if form is None else urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                last_error = _RetryableTossError(_format_http_error(exc))
            else:
                raise TossMinuteFetchError(f"Toss request failed ({context}): {_format_http_error(exc)}") from exc
        except (
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
            ValueError,
            _RetryableTossError,
        ) as exc:
            last_error = exc

        if attempt >= max_retries:
            raise TossMinuteFetchError(
                f"Toss request failed ({context}, attempts={attempt + 1}): "
                f"{_format_error(last_error)}"
            ) from last_error
        time.sleep(retry_backoff_seconds * (2**attempt))

    raise TossMinuteFetchError(f"Toss request failed unexpectedly: {last_error}")


def _read_existing_csv_rows(path: Path) -> dict[str, dict[str, int | float | str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, int | float | str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dt = str(row.get("datetime") or "")
            if not dt:
                continue
            rows[dt] = {
                "datetime": dt,
                "open": _parse_decimal(row.get("open")),
                "high": _parse_decimal(row.get("high")),
                "low": _parse_decimal(row.get("low")),
                "close": _parse_decimal(row.get("close")),
                "volume": _parse_decimal(row.get("volume")),
            }
    return rows


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _updated_state(
    state: dict[str, Any],
    *,
    symbol: str,
    status: str,
    next_before: str | None,
    pages: int,
    rows: int,
    api_calls: int,
    first_ts: str | None = None,
    last_ts: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    updated = dict(state)
    now = datetime.now(_KST).isoformat(timespec="seconds")
    updated.setdefault("started_at", now)
    updated.update(
        {
            "symbol": symbol,
            "status": status,
            "next_before": next_before,
            "pages": pages,
            "rows": rows,
            "api_calls": api_calls,
            "updated_at": now,
        }
    )
    if first_ts:
        current = updated.get("newest_datetime")
        updated["newest_datetime"] = max(str(current), first_ts) if current else first_ts
    if last_ts:
        current = updated.get("oldest_datetime")
        updated["oldest_datetime"] = min(str(current), last_ts) if current else last_ts
    if error:
        updated["error"] = error
    elif "error" in updated:
        updated.pop("error", None)
    if status == "complete":
        updated["finished_at"] = now
    return updated


def _write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_manifest(handle, payload: dict[str, Any]) -> None:
    payload = {"ts": datetime.now(_KST).isoformat(timespec="seconds"), **payload}
    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def _symbol_state_path(state_dir: Path, symbol: str) -> Path:
    safe = "".join(ch for ch in symbol if ch.isalnum() or ch in {"-", "."})
    return state_dir / f"{safe}.json"


def _parse_timestamp(raw: str) -> datetime:
    text = raw.strip()
    if not text:
        raise ValueError("empty timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_KST).replace(tzinfo=None)
    return parsed


def _parse_decimal(raw: object) -> int | float:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return 0
    value = float(text)
    return int(value) if value.is_integer() else value


def _first_datetime(rows: Sequence[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    return str(rows[-1]["datetime"])


def _last_datetime(rows: Sequence[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    return str(rows[0]["datetime"])


def _dedupe_symbols(symbols: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in symbols:
        symbol = str(raw).strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)
    return deduped


def _format_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read() or b""
    except Exception:
        raw = b""
    body = raw.decode("utf-8", errors="ignore").strip().replace("\n", " ")
    if len(body) > 200:
        body = body[:197] + "..."
    return f"HTTP {exc.code} {exc.reason}: {body}".strip()


def _format_error(exc: Exception | None) -> str:
    if exc is None:
        return "unknown"
    if isinstance(exc, urllib.error.URLError):
        return f"URLError: {exc.reason}"
    return str(exc)


def summary_to_dict(summary: TossMinuteBackfillSummary) -> dict[str, Any]:
    return asdict(summary)
