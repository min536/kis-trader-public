# 공시 센티널 (DART Disclosure Sentinel) 위임 계획서 — 2026-07-18

프로파일: **신규 모듈 TDD** (AST 게이트 해당 없음 · 핀+스위트 게이트 적용)

## §0 배경 / 목표 / 비목표

**배경.** 봇은 보유종목이 거래정지·유상증자·감자·액면분할 공시를 내도 인지하지 못한다 (2026-07-18 전수 grep: DART/공시/기업행위 관련 코드는 `app/core/symbol_tags.py`의 태그 문자열이 전부). 실제 증권사의 공시 담당 직무를 에이전트화한다. 구조는 방금 검증된 Engine Sentinel 패턴(`app/notifications/engine_sentinel.py` + slack_bot 루프 tick)을 그대로 재사용한다.

**목표.**
1. DART OpenAPI `list.json`을 주기 폴링해 **보유종목** 공시를 감지, 위험 카테고리는 Slack 알림 + 전체 매치는 JSONL 이벤트 로그.
2. `DART_API_KEY` 미설정 시 완전 무해(1회 로그 후 비활성). 네트워크는 주입 transport로만 — 테스트는 절대 실네트워크 금지.
3. slack_bot 기존 헬스 루프에 순수 가산 wiring (기존 로직 문자 불변).

**비목표 (명시적 제외).**
- DART corpCode.xml 매핑 다운로드 — `list.json` 응답 row에 `stock_code`(6자리)가 이미 포함되므로 불필요.
- 공시 기반 자동 매매/청산 — 알림 전용. 트레이딩 표면 무접촉.
- 대시보드 표출 — 후속 트랙.
- KIS 공시 API — 미검증 엔드포인트에 설계하지 않는다.

## §1 실행 원칙 (가드)

- tdd_guard: 테스트 1개씩 stub-first red→green. 의미 동일해도 계획서 사양 문자 그대로.
- **금지 파일**: `app/execution/`, `app/risk/`, `app/strategy/`, `app/auth/settings.py`, `app/main.py`, `app/pipeline/order_gate.py|buy_lane.py|sell_lane.py`, `.env`, `.token_cache.json`. 이 트랙은 main.py를 전혀 건드리지 않는다.
- 테스트에서 실 `data/` 파일 읽기/쓰기 금지 — 모든 경로는 kw 주입 + `tmp_path`. conftest 격리 가드(`tests/conftest.py:60 _LIVE_SESSION_GLOBAL_FILES`)가 실파일 write를 ERROR 처리한다.
- 테스트에서 실 HTTP 금지 — transport는 항상 fake 주입.
- 코드 커밋(feat)과 문서 커밋(docs) 분리. 진행표에 실제 SHA 기입.

## §2 구조지도 (인용 검증용 — 재조사 불필요)

**패턴 템플릿 (Engine Sentinel — 이 구조를 미러링):**
- `app/notifications/engine_sentinel.py:27-36` — 모듈 상수/env 이름, `:40 SentinelAlert`(frozen dataclass), `:47 SentinelConfig`, `:64 _parse_window`, `:87 load_sentinel_config(env)`, `:103 class EngineSentinel`(`:109 evaluate`, `:134 _in_window`, `:144 _handle_down` latch+cooldown), `:174 run_engine_sentinel_tick` (kw-only 어댑터: env kill-switch → 평가 → `SlackNotifier(env=env)` 발송 → 실패 fail-safe 콘솔 브레드크럼).
- slack_bot wiring: `app/notifications/slack_bot.py:60-63` import, `:523` 루프 전 인스턴스화, `:528-529` `while not stop_event.wait(interval)` 루프 안 tick 호출.

**Slack 이벤트 등록 관습:** `app/notifications/slack.py:109-119` — `AUTOTUNER_EVENT_TYPE`/`RECONCILIATION_EVENT_TYPE`/`MORNING_REGIME_EVENT_TYPE`/`ENGINE_HEALTH_EVENT_TYPE` 선례: 상수 1줄 + `EVENT_CHANNEL_ENV_BY_TYPE[...] = OPERATOR_CHANNEL_ENV` 1줄. `OPERATOR_CHANNEL_ENV`는 `:53`.

**보유종목 파일사이드 소스:** `data/runtime_state_<account>.json`의 `broker_last_synced_positions_by_symbol` 키 (2026-07-18 실측 확인). 계정 파일 열거는 glob `data/runtime_state_*.json` (단 `runtime_state.json` 무계정 파일 제외 아님 — 포함해도 무해, 키 없으면 빈 dict).

**JSONL 헬퍼:** `app/core/jsonl.py:47 decode_jsonl_objects`, `:67 read_jsonl_objects`, `:14 SNAPSHOT_READ_LINE_MAX_BYTES`.

**DART list.json 계약 (설계 고정):** GET `https://opendart.fss.or.kr/api/list.json`, params `crtfc_key, bgn_de=YYYYMMDD, end_de=YYYYMMDD, page_no, page_count=100`. 응답 `{"status": "000", "page_no": n, "total_page": m, "list": [{"rcept_no", "corp_name", "stock_code", "report_nm", "rcept_dt", "flr_nm", ...}]}`. `status=="013"` = 해당 없음(정상 빈 결과). 그 외 status는 오류 → 콘솔 브레드크럼 후 다음 tick.

## §3 슬라이스 사양

### S1 — 코어 모듈 `app/notifications/disclosure_sentinel.py`

- `DisclosureEvent` frozen dataclass: `rcept_no: str, corp_name: str, stock_code: str, report_nm: str, category: str, rcept_dt: str, url: str` (url = `https://dart.fss.or.kr/dsaf001/main.do?rcptNo=<rcept_no>`).
- 카테고리 분류 `classify_report(report_nm: str) -> str` (정규식, 우선순위 순):
  - `TRADING_HALT`: `거래정지|매매거래\s*정지`
  - `CORP_ACTION`: `유상증자|무상증자|감자|액면|합병|분할(?!납입)`
  - `WATCH`: `조회공시|불성실공시|풍문|관리종목`
  - `DIVIDEND`: `배당`
  - 그 외 → `OTHER`
- `DisclosureSentinelConfig` frozen dataclass + `load_disclosure_sentinel_config(env)`: `poll_interval_seconds`(기본 600, env `DISCLOSURE_SENTINEL_POLL_INTERVAL_SECONDS`), `window`(기본 `"07:00-18:00"`, env `DISCLOSURE_SENTINEL_WINDOW`, 파싱은 engine_sentinel `_parse_window` 로직 미러), `page_count`(고정 100), `max_pages`(기본 30, env `DISCLOSURE_SENTINEL_MAX_PAGES`).
- `class DisclosureSentinel`: `poll(*, now: datetime, holdings: set[str], transport, state: dict) -> list[DisclosureEvent]`
  - window 밖이면 `[]`. `state["next_poll_epoch"]`보다 이르면 `[]` (self-gating — 봇 루프는 30초지만 폴은 interval마다).
  - transport 시그니처: `transport(params: dict) -> dict` (URL 조립은 어댑터 소관 아님 — transport가 전담; 기본 구현만 실 URL 사용).
  - 오늘(bgn_de=end_de=now KST) 페이지 워크: page 1부터 `max_pages`까지, `state["seen_rcept_nos"]`(최근 보존 상한 2000개 리스트)로 dedupe, 전 페이지가 기수신분이면 조기 종료.
  - `stock_code`가 holdings에 포함된 row만 `DisclosureEvent`로 변환해 반환. poll 후 `state["next_poll_epoch"] = now + interval`.
- 발송 대상 판정 `should_notify(event) -> bool`: `TRADING_HALT|CORP_ACTION|WATCH`만 True (`DIVIDEND|OTHER`는 로그 전용).

**행위 테스트 (`tests/test_disclosure_sentinel.py`):**
1. `test_classify_report_categories` — 대표 report_nm 8종 분류 (거래정지/유상증자/감자/액면분할/조회공시/배당/기타/매매거래정지).
2. `test_poll_filters_to_holdings_only` — fake transport 2페이지, holdings 1종목만 이벤트화.
3. `test_poll_dedupes_by_rcept_no_across_calls` — 동일 rcept_no 2차 poll에서 미반환.
4. `test_poll_respects_window_and_interval_gate` — window 밖 `[]`, interval 미도래 `[]` + transport 미호출.
5. `test_poll_early_stops_when_page_all_seen` — 2페이지째 전부 기수신 → 3페이지 요청 안 함.
6. `test_poll_handles_status_013_as_empty` — 빈 결과 정상.
7. `test_should_notify_only_risk_categories` — 카테고리별 True/False.
8. `test_load_config_parses_env_and_defaults`.

### S2 — tick 어댑터 (같은 파일)

- `run_disclosure_sentinel_tick(*, env, sentinel, notifier=None, now=None, transport=None, holdings_reader=None, state_store=None) -> None`
  - kill-switch: `env.get("DISCLOSURE_SENTINEL_ENABLED")`가 `"0"/"false"` → 무동작. `DART_API_KEY` 부재 → 무동작(프로세스당 1회 콘솔 로그, 모듈 레벨 플래그).
  - `holdings_reader()` 기본 구현: `env.get("KIS_RUNTIME_STATE_DIR") or "data"` 아래 `runtime_state*.json` glob → 각 파일 `broker_last_synced_positions_by_symbol` 키 union (파싱 실패 파일은 skip).
  - `state_store`: `load() -> dict` / `save(dict)` 프로토콜. 기본 구현 JSON 파일 `<state_dir>/disclosure_sentinel_state.json` (`state_dir = env.get("DISCLOSURE_SENTINEL_STATE_DIR") or "data"`).
  - 이벤트 전건 JSONL append: `<state_dir>/disclosure_events.jsonl` (한 줄 = event dict + `detected_at`).
  - `should_notify` 이벤트만 `notifier.send(DISCLOSURE_EVENT_TYPE, text)` — notifier 기본 `SlackNotifier(env=env)`. 메시지 형식: `[공시] <corp_name>(<stock_code>) <category> — <report_nm> | <url>`.
  - 전체 try/except fail-safe: 예외는 콘솔 브레드크럼(`disclosure_sentinel_tick_failed: <exc>`) 후 return (봇 루프 절대 전파 금지 — engine_sentinel `:174` 어댑터와 동일 계약).

**행위 테스트 (`tests/test_disclosure_sentinel_tick.py`):**
1. `test_tick_noop_without_api_key` — transport/notifier 미호출.
2. `test_tick_disabled_by_kill_switch`.
3. `test_tick_appends_events_and_notifies_risk_only` — tmp_path state_dir, DIVIDEND는 로그만·CORP_ACTION은 발송.
4. `test_tick_survives_transport_exception` — 예외 삼킴 + 브레드크럼 출력.
5. `test_default_holdings_reader_unions_accounts` — tmp_path에 runtime_state 2파일.

### S3 — Slack 등록 + 봇 wiring

- `app/notifications/slack.py`: `DISCLOSURE_EVENT_TYPE = "disclosure_alert"` + `EVENT_CHANNEL_ENV_BY_TYPE[DISCLOSURE_EVENT_TYPE] = OPERATOR_CHANNEL_ENV` (`:118-119` ENGINE_HEALTH 선례 바로 아래, 2줄).
- `app/notifications/slack_bot.py`: import 블록(`:60-63` 아래)에 disclosure import 추가, `:523` 부근 `disclosure_sentinel = DisclosureSentinel(config=load_disclosure_sentinel_config(resolved_env))`, `:528-529` 루프 body에 `run_disclosure_sentinel_tick(env=resolved_env, sentinel=disclosure_sentinel)` 1줄 추가. **기존 라인 문자 불변 (순수 가산).**

**행위 테스트:** `tests/test_slack_bot.py`에 2건 추가 — `test_bot_loop_calls_disclosure_tick`(기존 sentinel tick 테스트 미러), `test_disclosure_event_type_routes_to_operator_channel`. 기존 봇 테스트 전건 green 유지.

### S4 — 마무리

- 전체 스위트 `.venv/bin/python -m pytest -q` green 확인 → feat 커밋. 진행표 SHA 기입 → docs 커밋(계획서만).

## §4 계획-단계 체크리스트 판정

1. **구조지도 인용 검증가능성** — §2 전 항목 file:line 실측 인용. PASS
2. **패치-서피스** — 신규 모듈이라 main.py 패치 표면 무관; slack_bot 기존 테스트가 patch하는 이름(`run_engine_sentinel_tick` 등) 불변. PASS
3. **trading-critical 접촉** — 없음 (notifications 계층만). PASS
4. **import 도출** — 신규 import는 stdlib(json, re, datetime, urllib.request 기본 transport)+기존 앱 모듈만. 신규 의존성 금지. PASS
5. **테스트 격리** — 모든 경로 kw 주입 + tmp_path; 실 data/ 접근 금지 명문화(§1). PASS

## §5 에스컬레이션

- DART 응답 계약이 §2 고정 스키마와 다르게 파싱 불가한 설계 모순 발견 시 — 임의 변경 말고 중단 후 보고.
- slack_bot 기존 테스트가 wiring 추가로 red가 되면 기존 테스트 수정 금지 — 중단 후 보고.

## §6 진행표

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| S1 코어 | DONE | 65a1d80 | `tests/test_disclosure_sentinel.py` 핀 8/8 green (classify·poll filter/dedupe/window·interval/early-stop/013·should_notify·load_config) |
| S2 tick 어댑터 | DONE | 65a1d80 | `tests/test_disclosure_sentinel_tick.py` 핀 5/5 green (no-key noop·kill-switch·append+notify risk-only·transport 예외 삼킴·holdings union) |
| S3 Slack+봇 wiring | DONE | 65a1d80 | `tests/test_slack_bot.py` 핀 2/2 green (operator 라우팅·봇 루프 tick 호출); slack_bot 기존 71 테스트 전건 green (순수 가산) |
| S4 마무리 | DONE | 65a1d80 | 전체 스위트 `.venv/bin/python -m pytest -q` → **3472 passed, 2 skipped, 726 subtests passed** (112s) |

(실행자: 각 슬라이스 완료 시 상태 DONE + 실제 커밋 SHA + 증거(테스트 결과 요약)를 기입할 것. SHA 없는 DONE은 감사에서 FAIL 처리된다.)

**실행 노트 — 계획 이탈 1건.** S3 slack.py 등록은 계획 §3의 "2줄"이 아니라 **3줄**로 구현했다: 상수(`DISCLOSURE_EVENT_TYPE`) + `EVENT_CHANNEL_ENV_BY_TYPE[...] = OPERATOR_CHANNEL_ENV` + `EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[DISCLOSURE_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)`. 근거: `resolve_channel_env_vars`(slack.py:186)는 `EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE`만 읽고, 그 dict를 만드는 컴프리헨션(:129-133)은 `channel_env != OPERATOR_CHANNEL_ENV`로 operator-매핑 이벤트를 **제외**한다. 따라서 "2줄"만으로는 `resolve_channel_env_vars("disclosure_alert") == ()`가 되어 실제 알림이 어떤 채널로도 라우팅되지 않는다(무발송 잠복 버그, 실측 RED로 확인). 세 번째 줄은 ENGINE_HEALTH 선례(slack.py:138)를 그대로 미러한 것으로, §2가 지시한 "ENGINE_HEALTH 선례 미러" 및 테스트명 "routes to operator channel"의 의도에 부합한다.

### §6.1 핀 테스트 목록

test_classify_report_categories, test_poll_filters_to_holdings_only, test_poll_dedupes_by_rcept_no_across_calls, test_poll_respects_window_and_interval_gate, test_poll_early_stops_when_page_all_seen, test_poll_handles_status_013_as_empty, test_should_notify_only_risk_categories, test_load_config_parses_env_and_defaults, test_tick_noop_without_api_key, test_tick_disabled_by_kill_switch, test_tick_appends_events_and_notifies_risk_only, test_tick_survives_transport_exception, test_default_holdings_reader_unions_accounts, test_bot_loop_calls_disclosure_tick, test_disclosure_event_type_routes_to_operator_channel
