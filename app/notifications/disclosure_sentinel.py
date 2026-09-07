"""DART disclosure sentinel — polls DART OpenAPI for holdings' disclosures.

The bot is blind to corporate actions on the names it holds: a trading halt,
a rights/bonus issue, a capital reduction or a face-value split can land with
no operator signal. This module closes that gap by acting as the securities
firm's disclosure desk — polling DART's ``list.json`` for *today's* filings,
filtering to currently-held ``stock_code``s, logging every match to JSONL and
Slack-alerting the risk categories.

Structure mirrors ``engine_sentinel.py``: a pure/deterministic core (all
wall-clock time, the HTTP transport and the persisted state are injected) plus
a fail-safe tick adapter wired into the always-resident Slack bot's health
loop. With ``DART_API_KEY`` unset the tick is fully inert (one console log,
then no-op), and tests never touch the network or the real ``data/`` tree.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from glob import glob
from urllib.parse import urlencode

from app.core.time_utils import get_korean_now
from app.notifications.slack import DISCLOSURE_EVENT_TYPE, SlackNotifier

_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("TRADING_HALT", r"거래정지|매매거래\s*정지"),
    ("CORP_ACTION", r"유상증자|무상증자|감자|액면|합병|분할(?!납입)"),
    ("WATCH", r"조회공시|불성실공시|풍문|관리종목"),
    ("DIVIDEND", r"배당"),
)

_DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcptNo="
_SEEN_RCEPT_MAX = 2000

DISCLOSURE_SENTINEL_POLL_INTERVAL_ENV = "DISCLOSURE_SENTINEL_POLL_INTERVAL_SECONDS"
DISCLOSURE_SENTINEL_WINDOW_ENV = "DISCLOSURE_SENTINEL_WINDOW"
DISCLOSURE_SENTINEL_MAX_PAGES_ENV = "DISCLOSURE_SENTINEL_MAX_PAGES"
DISCLOSURE_SENTINEL_ENABLED_ENV = "DISCLOSURE_SENTINEL_ENABLED"
DISCLOSURE_SENTINEL_STATE_DIR_ENV = "DISCLOSURE_SENTINEL_STATE_DIR"
DART_API_KEY_ENV = "DART_API_KEY"
KIS_RUNTIME_STATE_DIR_ENV = "KIS_RUNTIME_STATE_DIR"

# Process-local: log the inert (no DART_API_KEY) state at most once per process.
_api_key_missing_logged = False

_DEFAULT_POLL_INTERVAL_SECONDS = 600
_DEFAULT_WINDOW = "07:00-18:00"
_DEFAULT_MAX_PAGES = 30
_PAGE_COUNT = 100


def classify_report(report_nm: str) -> str:
    """Map a DART ``report_nm`` to a risk category (priority-ordered regex)."""
    text = report_nm or ""
    for category, pattern in _CATEGORY_PATTERNS:
        if re.search(pattern, text):
            return category
    return "OTHER"


@dataclass(frozen=True)
class DisclosureEvent:
    rcept_no: str
    corp_name: str
    stock_code: str
    report_nm: str
    category: str
    rcept_dt: str
    url: str


@dataclass(frozen=True)
class DisclosureSentinelConfig:
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL_SECONDS
    window: str = _DEFAULT_WINDOW
    page_count: int = _PAGE_COUNT
    max_pages: int = _DEFAULT_MAX_PAGES


def _parse_window(window_text: str) -> tuple[int, int] | None:
    """Local twin of engine_sentinel._parse_window (runtime-decoupled parsing)."""
    text = str(window_text or "").strip()
    if "-" not in text:
        return None
    start_text, end_text = (part.strip() for part in text.split("-", 1))
    try:
        start_hour, start_minute = (int(part) for part in start_text.split(":", 1))
        end_hour, end_minute = (int(part) for part in end_text.split(":", 1))
    except (TypeError, ValueError):
        return None
    return (start_hour * 60 + start_minute, end_hour * 60 + end_minute)


def _event_from_row(row: dict) -> DisclosureEvent:
    rcept_no = str(row.get("rcept_no", ""))
    report_nm = str(row.get("report_nm", ""))
    return DisclosureEvent(
        rcept_no=rcept_no,
        corp_name=str(row.get("corp_name", "")),
        stock_code=str(row.get("stock_code", "")).strip(),
        report_nm=report_nm,
        category=classify_report(report_nm),
        rcept_dt=str(row.get("rcept_dt", "")),
        url=f"{_DART_VIEWER_URL}{rcept_no}",
    )


def _parse_int(value: object, *, default: int, minimum: int) -> int:
    """Engine_sentinel-isomorphic guard: bad or below-minimum -> default."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _parse_window_text(value: object) -> str:
    if value is None:
        return _DEFAULT_WINDOW
    text = str(value).strip()
    if _parse_window(text) is None:
        return _DEFAULT_WINDOW
    return text


def load_disclosure_sentinel_config(env) -> DisclosureSentinelConfig:
    return DisclosureSentinelConfig(
        poll_interval_seconds=_parse_int(
            env.get(DISCLOSURE_SENTINEL_POLL_INTERVAL_ENV),
            default=_DEFAULT_POLL_INTERVAL_SECONDS,
            minimum=1,
        ),
        window=_parse_window_text(env.get(DISCLOSURE_SENTINEL_WINDOW_ENV)),
        page_count=_PAGE_COUNT,
        max_pages=_parse_int(
            env.get(DISCLOSURE_SENTINEL_MAX_PAGES_ENV),
            default=_DEFAULT_MAX_PAGES,
            minimum=1,
        ),
    )


_NOTIFY_CATEGORIES = frozenset({"TRADING_HALT", "CORP_ACTION", "WATCH"})


def should_notify(event: DisclosureEvent) -> bool:
    """Only the risk categories are Slack-worthy; DIVIDEND/OTHER are log-only."""
    return event.category in _NOTIFY_CATEGORIES


class DisclosureSentinel:
    def __init__(self, *, config: DisclosureSentinelConfig | None = None) -> None:
        self._config = config or DisclosureSentinelConfig()

    def _in_window(self, now: datetime) -> bool:
        parsed = _parse_window(self._config.window)
        if parsed is None:
            return False
        start_minutes, end_minutes = parsed
        current_minutes = now.hour * 60 + now.minute
        return start_minutes <= current_minutes <= end_minutes

    def poll(
        self,
        *,
        now: datetime,
        holdings: set[str],
        transport,
        state: dict,
    ) -> list[DisclosureEvent]:
        if not self._in_window(now):
            return []
        next_poll = state.get("next_poll_epoch")
        if next_poll is not None and now.timestamp() < next_poll:
            return []
        date_str = now.strftime("%Y%m%d")
        seen = state.get("seen_rcept_nos") or []
        seen_set = set(seen)
        new_rcept_nos: list[str] = []
        events: list[DisclosureEvent] = []
        for page_no in range(1, self._config.max_pages + 1):
            params = {
                "bgn_de": date_str,
                "end_de": date_str,
                "page_no": page_no,
                "page_count": self._config.page_count,
            }
            response = transport(params)
            status = str(response.get("status", ""))
            if status == "013":
                break
            if status != "000":
                print(f"[warn] disclosure sentinel dart status={status}")
                break
            rows = response.get("list") or []
            if not rows:
                break
            any_new_on_page = False
            for row in rows:
                rcept_no = str(row.get("rcept_no", ""))
                if rcept_no in seen_set:
                    continue
                any_new_on_page = True
                seen_set.add(rcept_no)
                new_rcept_nos.append(rcept_no)
                stock_code = str(row.get("stock_code", "")).strip()
                if stock_code in holdings:
                    events.append(_event_from_row(row))
            if not any_new_on_page:
                break
            total_page = response.get("total_page")
            if total_page is not None and page_no >= int(total_page):
                break
        if new_rcept_nos:
            state["seen_rcept_nos"] = (seen + new_rcept_nos)[-_SEEN_RCEPT_MAX:]
        state["next_poll_epoch"] = now.timestamp() + self._config.poll_interval_seconds
        return events


_DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_DART_REQUEST_TIMEOUT_SEC = 10.0
_STATE_FILENAME = "disclosure_sentinel_state.json"
_EVENTS_FILENAME = "disclosure_events.jsonl"


def _state_dir(env) -> str:
    return env.get(DISCLOSURE_SENTINEL_STATE_DIR_ENV) or "data"


def _make_default_transport(api_key: str):
    """Runtime transport: injects crtfc_key and GETs DART list.json (never in tests)."""

    def _transport(params: dict) -> dict:
        query = {"crtfc_key": api_key, **params}
        url = f"{_DART_LIST_URL}?{urlencode(query)}"
        with urllib.request.urlopen(
            url, timeout=_DART_REQUEST_TIMEOUT_SEC
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    return _transport


class _FileStateStore:
    def __init__(self, state_dir: str) -> None:
        self._path = os.path.join(state_dir, _STATE_FILENAME)

    def load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, state: dict) -> None:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)


def _append_event_jsonl(state_dir: str, event: DisclosureEvent, now: datetime) -> None:
    os.makedirs(state_dir, exist_ok=True)
    record = {**asdict(event), "detected_at": now.isoformat()}
    path = os.path.join(state_dir, _EVENTS_FILENAME)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _default_holdings_reader(env) -> set[str]:
    """Union of held symbols across the account runtime_state files (file-side)."""
    state_dir = env.get(KIS_RUNTIME_STATE_DIR_ENV) or "data"
    holdings: set[str] = set()
    for path in glob(os.path.join(state_dir, "runtime_state*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        positions = data.get("broker_last_synced_positions_by_symbol")
        if isinstance(positions, dict):
            holdings.update(str(symbol) for symbol in positions)
    return holdings


def run_disclosure_sentinel_tick(
    *,
    env,
    sentinel: DisclosureSentinel,
    notifier=None,
    now: datetime | None = None,
    transport=None,
    holdings_reader=None,
    state_store=None,
) -> None:
    global _api_key_missing_logged
    try:
        enabled = str(env.get(DISCLOSURE_SENTINEL_ENABLED_ENV, "1")).strip().lower()
        if enabled in {"0", "false", "no", "off"}:
            return
        api_key = env.get(DART_API_KEY_ENV)
        if not api_key:
            if not _api_key_missing_logged:
                print("[info] disclosure sentinel inactive: DART_API_KEY not set")
                _api_key_missing_logged = True
            return
        if now is None:
            now = get_korean_now()
        if notifier is None:
            notifier = SlackNotifier(env=env)
        if transport is None:
            transport = _make_default_transport(api_key)
        if holdings_reader is None:

            def holdings_reader():
                return _default_holdings_reader(env)

        state_dir = _state_dir(env)
        if state_store is None:
            state_store = _FileStateStore(state_dir)
        holdings = holdings_reader()
        state = state_store.load()
        events = sentinel.poll(
            now=now, holdings=holdings, transport=transport, state=state
        )
        state_store.save(state)
        for event in events:
            _append_event_jsonl(state_dir, event, now)
            if should_notify(event):
                text = (
                    f"[공시] {event.corp_name}({event.stock_code}) {event.category}"
                    f" — {event.report_nm} | {event.url}"
                )
                notifier.send(DISCLOSURE_EVENT_TYPE, text)
    except Exception as exc:
        # Fail-safe: a broken poll/alert path must never kill the bot loop, but
        # leave a console breadcrumb so the failure is itself observable.
        print(f"[warn] disclosure_sentinel_tick_failed: {exc}")
        return
