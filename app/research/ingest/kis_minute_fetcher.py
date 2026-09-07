"""KIS domestic stock minute-bar downloader.

The downloader writes the raw CSV layout consumed by
``minute_csv_to_parquet.convert_minute_csv_dir``:

``raw_dir/date=YYYY-MM-DD/symbol=XXXXXX_YYYYMMDD.csv``.
"""

from __future__ import annotations

import csv
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from io import BytesIO
from pathlib import Path
from typing import Iterable, Sequence


_DAILY_MINUTE_CHART_URL = (
    "{base_url}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
)
_KOSPI_MASTER_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
_START_TIME = "090000"
_END_TIME = "153000"
_MAX_PAGES_PER_SYMBOL_DAY = 8
_AUTH_REFRESH_SECONDS = 20 * 60 * 60
_ETF_NAME_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "KBSTAR",
    "SOL",
    "HANARO",
    "KOSEF",
    "ARIRANG",
    "PLUS",
    "TIMEFOLIO",
    "FOCUS",
    "WOORI",
    "마이티",
    "히어로즈",
)
_KOSPI_MASTER_PART2_WIDTHS = [
    2,
    1,
    4,
    4,
    4,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
]
_KOSPI_MASTER_ETP_INDEX = 12
_KOSPI_MASTER_GROUP_INDEX = 0
_KOSPI_MASTER_PREV_VOLUME_INDEX = 47
_KOSPI_MASTER_PART2_LENGTH = sum(_KOSPI_MASTER_PART2_WIDTHS)


@dataclass(frozen=True)
class MinuteFetchSummary:
    raw_dir: str
    manifest_path: str
    start_date: str
    end_date: str
    symbol_count: int
    saved_files: int
    skipped_files: int
    no_data: int
    failed: int
    rows: int
    api_calls: int


@dataclass(frozen=True)
class KRXMasterETF:
    code: str
    name: str
    prev_volume: int


class KISMinuteFetchError(RuntimeError):
    """Raised when a minute-bar request cannot be completed."""


class _RetryableKISError(RuntimeError):
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


def fetch_minute_csvs(
    *,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    raw_dir: str | Path,
    settings,
    market_div_code: str = "J",
    start_time: str = _START_TIME,
    end_time: str = _END_TIME,
    include_past_data: str = "Y",
    include_fake_tick: str = "",
    delay_seconds: float = 0.6,
    mode: str = "best-effort",
    max_retries: int = 3,
    retry_backoff_seconds: float = 2.0,
    overwrite: bool = False,
    max_pages_per_symbol_day: int = _MAX_PAGES_PER_SYMBOL_DAY,
    workers: int = 1,
    rate_limit_per_second: float = 0.0,
) -> MinuteFetchSummary:
    """Download daily minute bars for ``symbols`` into partitioned raw CSVs."""

    if start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date")
    if workers <= 0:
        raise ValueError("workers must be positive")

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"strict", "best-effort", "best_effort"}:
        raise ValueError("mode must be 'strict' or 'best-effort'")
    continue_on_error = normalized_mode in {"best-effort", "best_effort"}

    deduped_symbols = tuple(_dedupe_symbols(symbols))
    raw_path = Path(raw_dir)
    raw_path.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_path / "fetch_manifest.jsonl"

    bearer = _get_bearer(settings)
    auth_started_at = time.monotonic()
    saved_files = 0
    skipped_files = 0
    no_data = 0
    failed = 0
    total_rows = 0
    total_calls = 0
    rate_limiter = _RateLimiter(rate_limit_per_second)

    pending_jobs: list[tuple[date, str, Path]] = []

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for target_day in _iter_weekdays(start_date, end_date):
            for symbol in deduped_symbols:
                csv_path = minute_csv_path(raw_path, target_day, symbol)
                if csv_path.exists() and not overwrite:
                    skipped_files += 1
                    _write_manifest(
                        manifest,
                        {
                            "status": "skipped",
                            "date": target_day.isoformat(),
                            "symbol": symbol,
                            "path": str(csv_path),
                        },
                    )
                    continue

                pending_jobs.append((target_day, symbol, csv_path))

        if workers == 1:
            for target_day, symbol, csv_path in pending_jobs:
                if time.monotonic() - auth_started_at >= _AUTH_REFRESH_SECONDS:
                    bearer = _get_bearer(settings)
                    auth_started_at = time.monotonic()

                try:
                    record = _fetch_symbol_day_to_record(
                        symbol=symbol,
                        target_date=target_day,
                        csv_path=csv_path,
                        settings=settings,
                        bearer=bearer,
                        market_div_code=market_div_code,
                        start_time=start_time,
                        end_time=end_time,
                        include_past_data=include_past_data,
                        include_fake_tick=include_fake_tick,
                        delay_seconds=delay_seconds,
                        max_retries=max_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                        max_pages=max_pages_per_symbol_day,
                        rate_limiter=rate_limiter,
                    )
                except KISMinuteFetchError as exc:
                    record = {
                        "status": "failed",
                        "date": target_day.isoformat(),
                        "symbol": symbol,
                        "error": str(exc),
                    }
                    failed, saved_files, skipped_files, no_data, total_rows, total_calls = (
                        _write_counted_manifest(
                            manifest,
                            record,
                            failed=failed,
                            saved_files=saved_files,
                            skipped_files=skipped_files,
                            no_data=no_data,
                            total_rows=total_rows,
                            total_calls=total_calls,
                        )
                    )
                    if not continue_on_error:
                        raise
                    continue

                failed, saved_files, skipped_files, no_data, total_rows, total_calls = (
                    _write_counted_manifest(
                        manifest,
                        record,
                        failed=failed,
                        saved_files=saved_files,
                        skipped_files=skipped_files,
                        no_data=no_data,
                        total_rows=total_rows,
                        total_calls=total_calls,
                    )
                )
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _fetch_symbol_day_to_record,
                        symbol=symbol,
                        target_date=target_day,
                        csv_path=csv_path,
                        settings=settings,
                        bearer=bearer,
                        market_div_code=market_div_code,
                        start_time=start_time,
                        end_time=end_time,
                        include_past_data=include_past_data,
                        include_fake_tick=include_fake_tick,
                        delay_seconds=delay_seconds,
                        max_retries=max_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                        max_pages=max_pages_per_symbol_day,
                        rate_limiter=rate_limiter,
                    ): (target_day, symbol)
                    for target_day, symbol, csv_path in pending_jobs
                }
                for future in as_completed(futures):
                    target_day, symbol = futures[future]
                    try:
                        record = future.result()
                    except KISMinuteFetchError as exc:
                        record = {
                            "status": "failed",
                            "date": target_day.isoformat(),
                            "symbol": symbol,
                            "error": str(exc),
                        }
                        failed, saved_files, skipped_files, no_data, total_rows, total_calls = (
                            _write_counted_manifest(
                                manifest,
                                record,
                                failed=failed,
                                saved_files=saved_files,
                                skipped_files=skipped_files,
                                no_data=no_data,
                                total_rows=total_rows,
                                total_calls=total_calls,
                            )
                        )
                        if not continue_on_error:
                            raise
                        continue

                    failed, saved_files, skipped_files, no_data, total_rows, total_calls = (
                        _write_counted_manifest(
                            manifest,
                            record,
                            failed=failed,
                            saved_files=saved_files,
                            skipped_files=skipped_files,
                            no_data=no_data,
                            total_rows=total_rows,
                            total_calls=total_calls,
                        )
                    )

    return MinuteFetchSummary(
        raw_dir=str(raw_path),
        manifest_path=str(manifest_path),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        symbol_count=len(deduped_symbols),
        saved_files=saved_files,
        skipped_files=skipped_files,
        no_data=no_data,
        failed=failed,
        rows=total_rows,
        api_calls=total_calls,
    )


def fetch_symbol_day_minute_rows(
    *,
    symbol: str,
    target_date: date,
    settings,
    bearer: str,
    market_div_code: str = "J",
    start_time: str = _START_TIME,
    end_time: str = _END_TIME,
    include_past_data: str = "Y",
    include_fake_tick: str = "",
    delay_seconds: float = 0.6,
    max_retries: int = 3,
    retry_backoff_seconds: float = 2.0,
    max_pages: int = _MAX_PAGES_PER_SYMBOL_DAY,
    rate_limiter: _RateLimiter | None = None,
) -> tuple[list[dict[str, int | str]], int]:
    """Fetch and normalize one symbol/day of minute bars."""

    cursor = end_time
    seen_cursors: set[str] = set()
    rows_by_datetime: dict[str, dict[str, int | str]] = {}
    api_calls = 0

    for _ in range(max_pages):
        if cursor in seen_cursors:
            break
        seen_cursors.add(cursor)

        data = _fetch_minute_page(
            symbol=symbol,
            target_date=target_date,
            input_hour=cursor,
            settings=settings,
            bearer=bearer,
            market_div_code=market_div_code,
            include_past_data=include_past_data,
            include_fake_tick=include_fake_tick,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            rate_limiter=rate_limiter,
        )
        api_calls += 1
        page_rows = parse_minute_output(data.get("output2", []), symbol=symbol)
        page_rows = [
            row
            for row in page_rows
            if row["date"] == target_date.isoformat()
            and start_time <= str(row["time"]).replace(":", "") <= end_time
        ]
        if not page_rows:
            break

        for row in page_rows:
            rows_by_datetime[str(row["datetime"])] = row

        earliest = min(str(row["time"]).replace(":", "") for row in page_rows)
        if earliest <= start_time:
            break
        next_cursor = previous_minute_cursor(earliest)
        if next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(delay_seconds)

    rows = [rows_by_datetime[key] for key in sorted(rows_by_datetime)]
    return rows, api_calls


def _fetch_symbol_day_to_record(
    *,
    symbol: str,
    target_date: date,
    csv_path: Path,
    settings,
    bearer: str,
    market_div_code: str,
    start_time: str,
    end_time: str,
    include_past_data: str,
    include_fake_tick: str,
    delay_seconds: float,
    max_retries: int,
    retry_backoff_seconds: float,
    max_pages: int,
    rate_limiter: _RateLimiter,
) -> dict:
    rows, calls = fetch_symbol_day_minute_rows(
        symbol=symbol,
        target_date=target_date,
        settings=settings,
        bearer=bearer,
        market_div_code=market_div_code,
        start_time=start_time,
        end_time=end_time,
        include_past_data=include_past_data,
        include_fake_tick=include_fake_tick,
        delay_seconds=delay_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        max_pages=max_pages,
        rate_limiter=rate_limiter,
    )
    if not rows:
        return {
            "status": "no_data",
            "date": target_date.isoformat(),
            "symbol": symbol,
            "api_calls": calls,
        }
    write_minute_csv(csv_path, rows)
    return {
        "status": "saved",
        "date": target_date.isoformat(),
        "symbol": symbol,
        "rows": len(rows),
        "api_calls": calls,
        "path": str(csv_path),
    }


def parse_minute_output(
    output: object,
    *,
    symbol: str,
) -> list[dict[str, int | str]]:
    """Normalize KIS ``output2`` minute rows to raw CSV dictionaries."""

    if not isinstance(output, list):
        return []

    rows: list[dict[str, int | str]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        try:
            row_date = parse_kis_date(str(item.get("stck_bsop_date", "")))
            row_time = parse_kis_time(str(item.get("stck_cntg_hour", "")))
            close_price = _parse_int(item.get("stck_prpr"))
            open_price = _parse_int(item.get("stck_oprc"))
            high_price = _parse_int(item.get("stck_hgpr"))
            low_price = _parse_int(item.get("stck_lwpr"))
            volume = _parse_int(item.get("cntg_vol"))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "datetime": f"{row_date} {row_time}",
                "date": row_date,
                "time": row_time,
                "symbol": symbol,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        )
    return rows


def write_minute_csv(path: str | Path, rows: Sequence[dict[str, int | str]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["datetime", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "datetime": row["datetime"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                }
            )
    return path


def minute_csv_path(raw_dir: str | Path, target_date: date, symbol: str) -> Path:
    date_text = target_date.isoformat()
    compact = target_date.strftime("%Y%m%d")
    return Path(raw_dir) / f"date={date_text}" / f"symbol={symbol}_{compact}.csv"


def build_current_plus_etf_symbols(
    *,
    current_symbols: Sequence[str],
    target_count: int,
    tagged_etfs: Sequence[str] = (),
    master_etfs: Sequence[KRXMasterETF] = (),
) -> list[str]:
    """Return current symbols plus ETF additions until ``target_count``."""

    symbols = _dedupe_symbols(current_symbols)
    for code in _dedupe_symbols(tagged_etfs):
        if len(symbols) >= target_count:
            break
        if code not in symbols:
            symbols.append(code)
    for etf in sorted(master_etfs, key=lambda item: (-item.prev_volume, item.code)):
        if len(symbols) >= target_count:
            break
        if etf.code not in symbols:
            symbols.append(etf.code)
    if len(symbols) < target_count:
        raise ValueError(
            f"only resolved {len(symbols)} symbols; target_count={target_count}"
        )
    return symbols[:target_count]


def download_kospi_master_etfs(url: str = _KOSPI_MASTER_URL) -> list[KRXMasterETF]:
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    try:
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            for name in zf.namelist():
                return parse_kospi_master_etfs(zf.read(name))
    except zipfile.BadZipFile:
        return parse_kospi_master_etfs(payload)
    return []


def parse_kospi_master_etfs(content: bytes) -> list[KRXMasterETF]:
    """Parse KIS public KOSPI master bytes and return ETF-like ETP rows."""

    etfs: list[KRXMasterETF] = []
    for raw_line in content.splitlines():
        if len(raw_line) <= _KOSPI_MASTER_PART2_LENGTH:
            continue
        part1 = raw_line[: len(raw_line) - _KOSPI_MASTER_PART2_LENGTH]
        part2 = raw_line[-_KOSPI_MASTER_PART2_LENGTH:]
        code = part1[0:9].decode("euc-kr", errors="ignore").strip()
        if len(code) > 6:
            code = code[-6:]
        name = part1[21:].decode("euc-kr", errors="ignore").strip()
        fields = _split_fixed_width(part2, _KOSPI_MASTER_PART2_WIDTHS)
        if len(fields) <= max(_KOSPI_MASTER_ETP_INDEX, _KOSPI_MASTER_PREV_VOLUME_INDEX):
            continue
        group_code = fields[_KOSPI_MASTER_GROUP_INDEX].upper()
        etp_flag = fields[_KOSPI_MASTER_ETP_INDEX].upper()
        if group_code != "EF" and etp_flag not in {"Y", "1", "2"}:
            continue
        if not _looks_like_etf_name(name):
            continue
        etfs.append(
            KRXMasterETF(
                code=code,
                name=name,
                prev_volume=_parse_int(fields[_KOSPI_MASTER_PREV_VOLUME_INDEX]),
            )
        )
    return etfs


def parse_kis_date(raw: str) -> str:
    text = raw.strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid KIS date: {raw!r}")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def parse_kis_time(raw: str) -> str:
    text = raw.strip().zfill(6)
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"invalid KIS time: {raw!r}")
    return f"{text[:2]}:{text[2:4]}:{text[4:6]}"


def previous_minute_cursor(raw: str) -> str:
    text = raw.replace(":", "").strip().zfill(6)
    parsed = datetime.combine(
        date(2000, 1, 1),
        dt_time.fromisoformat(parse_kis_time(text)),
    )
    previous = parsed - timedelta(minutes=1)
    return previous.strftime("%H%M00")


def _fetch_minute_page(
    *,
    symbol: str,
    target_date: date,
    input_hour: str,
    settings,
    bearer: str,
    market_div_code: str,
    include_past_data: str,
    include_fake_tick: str,
    max_retries: int,
    retry_backoff_seconds: float,
    rate_limiter: _RateLimiter | None,
) -> dict:
    url = _DAILY_MINUTE_CHART_URL.format(base_url=settings.base_url)
    headers = {
        "authorization": f"Bearer {bearer}",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
        "tr_id": "FHKST03010230",
        "content-type": "application/json; charset=utf-8",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": market_div_code,
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_HOUR_1": input_hour,
        "FID_INPUT_DATE_1": target_date.strftime("%Y%m%d"),
        "FID_PW_DATA_INCU_YN": include_past_data,
        "FID_FAKE_TICK_INCU_YN": include_fake_tick,
    }
    return _request_json(
        url=url,
        headers=headers,
        params=params,
        symbol=symbol,
        target_date=target_date,
        input_hour=input_hour,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        rate_limiter=rate_limiter,
    )


def _request_json(
    *,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    symbol: str,
    target_date: date,
    input_hour: str,
    max_retries: int,
    retry_backoff_seconds: float,
    rate_limiter: _RateLimiter | None,
) -> dict:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}", headers=headers, method="GET"
    )
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()
            with urllib.request.urlopen(request, timeout=20) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            rt_cd = str(data.get("rt_cd", "0"))
            if rt_cd not in {"0", ""}:
                message = f"{data.get('msg_cd', '')} {data.get('msg1', '')}".strip()
                if _is_retryable_kis_error(message):
                    raise _RetryableKISError(message)
                raise KISMinuteFetchError(
                    f"KIS error symbol={symbol} date={target_date} hour={input_hour}: {message}"
                )
            return data
        except (urllib.error.URLError, TimeoutError, ValueError, _RetryableKISError) as exc:
            last_error = exc
            if attempt >= max_retries:
                raise KISMinuteFetchError(
                    "minute request failed "
                    f"(symbol={symbol}, date={target_date}, hour={input_hour}, "
                    f"attempts={attempt + 1}): {_format_error(exc)}"
                ) from exc
            sleep_seconds = retry_backoff_seconds * (2**attempt)
            time.sleep(sleep_seconds)
    raise KISMinuteFetchError(f"minute request failed unexpectedly: {last_error}")


def _get_bearer(settings) -> str:
    from app.auth.settings import classify_kis_base_url_env
    from app.auth.token import issue_access_token_for

    env = classify_kis_base_url_env(settings.base_url)
    if env is None:
        raise KISMinuteFetchError("KIS base URL host is not allowed")
    return issue_access_token_for(
        base_url=settings.base_url,
        app_key=settings.app_key,
        app_secret=settings.app_secret,
        env=env,
    )


def _iter_weekdays(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _dedupe_symbols(symbols: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in symbols:
        code = str(raw).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        deduped.append(code)
    return deduped


def _write_manifest(handle, payload: dict) -> None:
    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()


def _write_counted_manifest(
    handle,
    payload: dict,
    *,
    failed: int,
    saved_files: int,
    skipped_files: int,
    no_data: int,
    total_rows: int,
    total_calls: int,
) -> tuple[int, int, int, int, int, int]:
    _write_manifest(handle, payload)
    status = payload.get("status")
    total_calls += int(payload.get("api_calls") or 0)
    if status == "saved":
        saved_files += 1
        total_rows += int(payload.get("rows") or 0)
    elif status == "skipped":
        skipped_files += 1
    elif status == "no_data":
        no_data += 1
    elif status == "failed":
        failed += 1
    return failed, saved_files, skipped_files, no_data, total_rows, total_calls


def _split_fixed_width(raw: bytes, widths: Sequence[int]) -> list[str]:
    fields: list[str] = []
    offset = 0
    for width in widths:
        chunk = raw[offset : offset + width]
        fields.append(chunk.decode("euc-kr", errors="ignore").strip())
        offset += width
    return fields


def _looks_like_etf_name(name: str) -> bool:
    text = name.strip().upper()
    if not text or "ETN" in text or "ELW" in text:
        return False
    return any(text.startswith(prefix.upper()) for prefix in _ETF_NAME_PREFIXES)


def _parse_int(raw: object) -> int:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return 0
    return int(float(text))


def _is_retryable_kis_error(message: str) -> bool:
    text = message.upper()
    return (
        "EGW00201" in text
        or "초당 거래건수" in message
        or "RATE" in text
        or "TIMEOUT" in text
    )


def _format_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            raw = exc.read() or b""
        except Exception:
            raw = b""
        body = raw.decode("utf-8", errors="ignore").strip().replace("\n", " ")
        if len(body) > 200:
            body = body[:197] + "..."
        return f"HTTP {exc.code} {exc.reason}: {body}".strip()
    if isinstance(exc, urllib.error.URLError):
        return f"URLError: {exc.reason}"
    return str(exc)


def summary_to_dict(summary: MinuteFetchSummary) -> dict:
    return asdict(summary)
