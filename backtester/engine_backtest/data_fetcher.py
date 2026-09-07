"""KIS API historical OHLCV downloader.

Fetches daily OHLCV from KIS inquire-daily-itemchartprice endpoint
and writes a CSV that BacktestDataProvider.from_csv() can load.

Usage
-----
from backtester.engine_backtest.data_fetcher import fetch_symbols_to_csv

fetch_symbols_to_csv(
    symbols=["005930", "000660"],
    start_date=date(2025, 1, 1),
    end_date=date(2026, 4, 12),
    output_path="data/prices.csv",
    settings=settings,
)
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests


# KIS daily chart endpoint
_DAILY_CHART_URL = "{base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

_MAX_ROWS_PER_CALL = 100  # KIS returns up to 100 trading days per call
_BATCH_LOOKBACK_DAYS = 180  # keep each request window bounded while covering ~100 trading days


@dataclass(frozen=True)
class FetchFailure:
    symbol: str
    message: str


class HistoricalDataFetchError(RuntimeError):
    """Raised when historical OHLCV download fails."""


def fetch_symbols_to_csv(
    *,
    symbols: list[str],
    start_date: date,
    end_date: date,
    output_path: str | Path,
    settings,
    delay_seconds: float = 0.5,
    mode: str = "strict",
    max_retries: int = 2,
    retry_backoff_seconds: float = 1.0,
) -> Path:
    """Download daily OHLCV for all symbols and write combined CSV.

    Parameters
    ----------
    symbols:
        List of KRX ticker codes.
    start_date / end_date:
        Inclusive date range.
    output_path:
        CSV output file path.
    settings:
        Engine settings (needs app_key, app_secret, base_url).
    delay_seconds:
        Sleep between API calls to respect rate limits.
    mode:
        ``strict`` aborts on first symbol failure.
        ``best-effort`` records failed symbols and continues.
    max_retries:
        Number of retries per failing batch request after the initial attempt.
    retry_backoff_seconds:
        Base sleep duration for exponential backoff between retries.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"strict", "best-effort", "best_effort"}:
        raise ValueError("mode must be 'strict' or 'best-effort'")
    continue_on_error = normalized_mode in {"best-effort", "best_effort"}

    token = _get_access_token(settings)
    rows: list[dict] = []
    failures: list[FetchFailure] = []

    for symbol in symbols:
        try:
            symbol_rows = _fetch_symbol(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                settings=settings,
                token=token,
                delay_seconds=delay_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
        except HistoricalDataFetchError as exc:
            failures.append(FetchFailure(symbol=symbol, message=str(exc)))
            print(f"  {symbol}: FAILED - {exc}")
            if not continue_on_error:
                raise
            continue
        rows.extend(symbol_rows)
        print(f"  {symbol}: {len(symbol_rows)} days fetched")

    rows.sort(key=lambda r: (r["date"], r["symbol"]))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "symbol", "open", "high", "low", "close", "volume", "prev_close"],
        )
        writer.writeheader()
        writer.writerows(rows)

    if failures:
        print("\nFetch failures:")
        for failure in failures:
            print(f"  - {failure.symbol}: {failure.message}")
        if continue_on_error:
            print(f"Completed in best-effort mode with {len(failures)} failed symbol(s).")

    print(f"Saved {len(rows)} rows → {output_path}")
    return output_path


# ── internal helpers ───────────────────────────────────────────────────────

def _get_access_token(settings) -> str:
    url = f"{settings.base_url}/oauth2/tokenP"
    resp = requests.post(
        url,
        json={
            "grant_type": "client_credentials",
            "appkey": settings.app_key,
            "appsecret": settings.app_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_symbol(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    settings,
    token: str,
    delay_seconds: float,
    max_retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    all_rows: list[dict] = []
    current_end = end_date

    while current_end >= start_date:
        batch_start = max(start_date, current_end - timedelta(days=_BATCH_LOOKBACK_DAYS))
        batch = _fetch_batch(
            symbol=symbol,
            start_date=batch_start,
            end_date=current_end,
            settings=settings,
            token=token,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if not batch:
            break

        for row in batch:
            row_date = date.fromisoformat(row["date"])
            if start_date <= row_date <= end_date:
                all_rows.append(row)

        # KIS returns newest first; find oldest date in batch
        oldest = min(date.fromisoformat(r["date"]) for r in batch)
        if oldest <= start_date:
            break
        current_end = oldest - timedelta(days=1)
        time.sleep(delay_seconds)

    return all_rows


def _fetch_batch(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    settings,
    token: str,
    max_retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    url = _DAILY_CHART_URL.format(base_url=settings.base_url)
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
        "tr_id": "FHKST03010100",
        "content-type": "application/json; charset=utf-8",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                detail = _format_request_exception(exc)
                raise HistoricalDataFetchError(
                    "batch request failed "
                    f"(symbol={symbol}, start={params['FID_INPUT_DATE_1']}, end={params['FID_INPUT_DATE_2']}, "
                    f"attempts={attempt + 1}): {detail}"
                ) from exc
            sleep_seconds = retry_backoff_seconds * (2 ** attempt)
            print(
                f"    retrying {symbol} batch {params['FID_INPUT_DATE_1']}..{params['FID_INPUT_DATE_2']} "
                f"after error: {_format_request_exception(exc)} "
                f"(retry {attempt + 1}/{max_retries}, sleep={sleep_seconds:.1f}s)"
            )
            time.sleep(sleep_seconds)
    else:
        raise HistoricalDataFetchError(
            f"batch request failed unexpectedly for {symbol}: {last_error}"
        )

    output = data.get("output2", [])
    if not isinstance(output, list):
        return []

    rows = []
    prev_close = 0
    for item in reversed(output):  # oldest first
        try:
            d = _parse_date(str(item.get("stck_bsop_date", "")))
            close_p = int(item.get("stck_clpr", 0) or 0)
            open_p = int(item.get("stck_oprc", 0) or 0)
            high_p = int(item.get("stck_hgpr", 0) or 0)
            low_p = int(item.get("stck_lwpr", 0) or 0)
            volume = int(item.get("acml_vol", 0) or 0)
            rows.append({
                "date": d,
                "symbol": symbol,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "prev_close": prev_close,
            })
            prev_close = close_p
        except (ValueError, TypeError):
            continue

    return rows


def _parse_date(raw: str) -> str:
    """Convert 'YYYYMMDD' to 'YYYY-MM-DD'."""
    raw = raw.strip()
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _format_request_exception(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    body = (response.text or "").strip().replace("\n", " ")
    if len(body) > 200:
        body = body[:197] + "..."
    return f"HTTP {response.status_code} {response.reason}: {body}".strip()
