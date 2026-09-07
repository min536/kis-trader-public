# 운영 안전망 v2 위임 계획서 — Engine Sentinel + 테스트 인프라 (2026-07-17)

- **프로파일**: 신규 모듈 TDD (5슬라이스)
- **실행자**: opus 서브에이전트 (tdd_guard stub-first)
- **검증**: 상위 세션 완료 감사(completion-audit) PASS 전까지 미완 취급
- **브랜치**: `59` (클린 트리, 직접 편집)

## §0 배경 — 전반검토에서 나온 발견과 이 트랙의 위치

2026-07-17 시스템 전반검토 결과. 헬스 워치독(3e23cf3)은 **살아있는 엔진의 연속 실패**를 잡지만, 두 사각지대가 남아 있다:

- **F1 (프로세스 사망 사각지대)**: 엔진 프로세스 자체가 죽으면 워치독도 함께 죽어 어떤 알림도 없다. 워치독은 in-process 관측자다. 독립 프로세스인 슬랙 봇(launchd `com.kis-trader.slack-bot`, 세션 내내 상주)이 데드맨 스위치를 들어야 한다.
- **F2 (테스트 격리 가드 오탐, 금일 3회 실측)**: 장중 라이브 세션이 `data/live_snapshot.json`을 스위트 실행 도중 쓰면 conftest 가드가 ERROR — `test wrote to real repo state files (isolation leak): data/live_snapshot.json` (금일 풀스위트 3회 재현, 단독 실행 PASS, 메모리 `test-isolation-leak`의 알려진 패턴). 가드의 live-owned 제외 목록에 이 전역 파일이 빠져 있다.
- **F3 (RC3 잔여)**: `record_bottleneck`의 `except Exception: return` 무음 삼킴 — 발송 실패가 관측 불가 (워치독 계획서 §7 이관 항목).

**이번 트랙에서 하지 않는 것 (검토됐으나 제외, 사유 포함)**:
- 웨지드 포트폴리오/무주문 스트릭 감지 — "정당한 무신호 장세"와의 구별 설계(노이즈 억제)가 미완. 대시보드 Triage가 이미 가시화 중. 후속 백로그.
- OPSQ0008을 transient 마커에 추가(백오프 부여) — `error_classification`은 실행 흐름 백오프 의미론에 직결 = 트레이딩 케이던스 변경, 위임 금지. 운영자 결정 사항으로 이관.
- 휴장일 캘린더 연동 미기동 감지 — v1 규칙이 휴장 오탐을 구조적으로 회피하므로(§3 S1) 불필요 복잡도.
- 트레이딩-크리티컬 표면·`.env`·launchd·broker API: 전부 불가침 (이전 트랙과 동일).

## §1 근본 원인 / 요구 근거

| # | 사실 | 근거 |
|---|---|---|
| F1 | 봇에는 이미 30초 주기 헬스체크 루프가 있으나 Slack 연결성만 본다 — 엔진 생사는 아무도 안 본다 | `app/notifications/slack_bot.py:505-570` (`while not stop_event.wait(health_check_interval_sec)`) |
| F1b | 엔진 생존 신호는 이미 존재: 라이브 세션이 사이클마다 쓰는 `live_snapshot.json`의 mtime | `app/tools/multi_account_preflight.py:8` (`KIS_LIVE_SNAPSHOT_DIR` → `<dir>/live_snapshot.json`), 금일 가드 오탐이 곧 "장중 상시 write"의 실증 |
| F2 | 가드 전역 제외 목록에 `data/live_snapshot.json` 부재 | `tests/conftest.py:57-59` (`_LIVE_SESSION_GLOBAL_FILES = {"data/account_scope_meta.json", "data/account_scope_history.jsonl"}`) — 시그니처 기반 제외(`:63`, per-account 경로용)로는 전역 경로가 안 걸러짐 |
| F3 | 발송 실패 무음 | `app/notifications/runtime_alerts.py:34-57` (`except Exception: return`) |

## §2 구조지도 (실행자는 인용 검증만)

- **봇 루프**: `app/notifications/slack_bot.py:387` `run_socket_mode_bot(*, env=None)` — `resolved_env = _env_with_dotenv_defaults(...)`(`:353-371`) 보유, 헬스 루프 `:505-570`. env 상수 관행 `:63-71` (`SLACK_BOT_*_ENV = "..."` + `DEFAULT_* = n`).
- **봇 루프 테스트 하네스**: `tests/test_slack_bot.py:422,505,603` `FakeSocketModeClient` + `run_socket_mode_bot(env=env)` 구동 패턴 (총 54 테스트) — S3는 이 패턴을 따른다.
- **Slack 발송**: `SlackNotifier.__init__(*, env=None, logger=None, transport=None, clock=None)` (`app/notifications/slack.py:262-268`) — **봇의 `resolved_env`를 주입**해 채널을 해석할 것 (os.environ 의존 금지: 봇 프로세스 env에 채널 키가 없을 수 있음 — 금일 H1 교훈). 이벤트타입 `engine_health`는 워치독 트랙에서 이미 OPERATOR 채널로 등록됨 (`slack.py`의 `ENGINE_HEALTH_EVENT_TYPE`).
- **latch/재-arm 선례**: `app/notifications/health_watchdog.py` (3e23cf3) — WARN/CRITICAL/RECOVERED latch·쿨다운·`load_*_config(env)` 로컬 파서 패턴을 그대로 따를 것 (import 재사용 아님, 관행 복제 — 센티널은 시간 기반이라 상태기계가 다름).
- **conftest 가드**: `tests/conftest.py:57-59` 전역 목록, `:171-201` `_watch_real_state_writes` 필터 로직. 가드 자체 테스트: `tests/test_conftest_isolation_guard.py` (5 테스트).
- **record_bottleneck**: `app/notifications/runtime_alerts.py:34-57`. 기존 테스트: `tests/test_bottleneck_notifications.py`.
- **패치-서피스**: `run_socket_mode_bot`은 tests/test_slack_bot.py가 FakeSocketModeClient 주입으로 구동 — 시그니처 불변이므로 안전. conftest 전역 목록은 frozenset 상수 — 값 추가만. 신규 이름(`engine_sentinel`, `run_engine_sentinel_tick`)은 기존 패치 대상과 무충돌.
- **main.py/트레이딩 표면**: 이번 트랙은 **건드리지 않는다** (봇 프로세스 + 테스트 인프라만).

## §3 슬라이스 사양

### S1 — `app/notifications/engine_sentinel.py`: 순수 평가기 (신규)

`EngineSentinel` 클래스 (표준 lib만, I/O 없음, `now`/mtime 주입):

- `evaluate(*, snapshot_mtime_epoch: float | None, now: datetime) -> list[SentinelAlert]`
- `SentinelAlert` frozen dataclass: `level`("DOWN"|"RECOVERED"), `stale_seconds`, `message`(한국어)
- **v1 규칙 (휴장 오탐 구조적 회피)**: DOWN 조건 = ①`now`가 거래창(config `window_text`, 기본 "09:00-15:40") 안 + 평일 ②mtime이 **오늘** ③`now - mtime > stale_threshold_seconds`(기본 180). ②가 핵심: 휴장일/미기동엔 mtime이 전일이라 절대 발화하지 않는다 — "오늘 돌다가 멈춘" 경우만 잡는다. 미기동 감지는 §7 후속.
- latch: DOWN 1회 발화 후 `repeat_cooldown_seconds`(기본 1800)마다 재알림; 신선해지면(RECOVERED 조건: 직전 DOWN latch 상태 + `now - mtime <= threshold`) RECOVERED 1건 + re-arm
- `SentinelConfig` + `load_sentinel_config(env)` — env 키 `ENGINE_SENTINEL_STALE_THRESHOLD_SECONDS`/`ENGINE_SENTINEL_REPEAT_COOLDOWN_SECONDS`/`ENGINE_SENTINEL_WINDOW`(기본 "09:00-15:40"), health_watchdog.py 동형 로컬 파서(최솟값 가드). 창 파서는 `app/runtime/session_loop.py:121` `parse_hhmm_window` **재사용 금지**(runtime import 얽힘 방지) — 동형 로컬 구현.

**테스트 (tests/test_engine_sentinel.py, 1개씩 red→green)**:
1. 거래창 밖(주말 포함) → mtime 아무리 stale해도 0건
2. 창 안 + mtime 오늘 + stale 초과 → DOWN 1건, 직후 재평가 0건 (latch)
3. 창 안 + mtime **어제** + stale 초과 → 0건 (휴장/미기동 오탐 회피 증명)
4. DOWN latch 후 쿨다운 경과 → 재알림 1건
5. DOWN 후 mtime 신선 → RECOVERED 1건 + 다시 stale 시 DOWN 재발화 (re-arm)
6. mtime None(파일 없음) → 0건 (엔진 한 번도 안 쓴 환경 무해)
7. env 파서: 결측→기본, 쓰레기→기본, 최솟값 클램프, 창 문자열 불량→기본 창

### S2 — 어댑터 `run_engine_sentinel_tick` (같은 모듈)

- `run_engine_sentinel_tick(*, env: Mapping[str, str], sentinel: EngineSentinel, notifier=None, now=None, mtime_reader=None) -> None`
  - 스냅샷 경로: `env.get("KIS_LIVE_SNAPSHOT_DIR") or "data"` + `/live_snapshot.json` (§1 F1b 인용 준거), `mtime_reader` 기본 = `os.path.getmtime` 시도, 부재 시 None
  - kill-switch: `ENGINE_SENTINEL_ENABLED`(기본 "1"), "0"/"false" → 즉시 return
  - notifier 기본 = `SlackNotifier(env=env)` — **반드시 env 주입** (§2)
  - 알림 발송 `send(ENGINE_HEALTH_EVENT_TYPE, alert.message, details={...})` + **발송 결과/예외 콘솔 print 1줄** (RC3 규율), 모든 예외 fail-safe (봇 루프를 죽이면 안 됨)

**테스트 (tests/test_engine_sentinel_tick.py)**:
1. stale 시나리오 → fake notifier에 engine_health 1건
2. 파일 부재 → 무예외·무발송
3. `ENGINE_SENTINEL_ENABLED=0` → 무발송
4. notifier 예외 → 전파 없음 + 콘솔 1줄

### S3 — 봇 루프 배선

- `slack_bot.py`: import 1줄 + 루프 진입 전 `sentinel = EngineSentinel(config=load_sentinel_config(resolved_env))` 1줄 + `while` 본문에 `run_engine_sentinel_tick(env=resolved_env, sentinel=sentinel)` 호출 1줄 (연결성 체크 블록과 독립, 그 앞이든 뒤든 매 tick 실행). 기존 루프 로직 문자 불변.
- **테스트 (test_slack_bot.py에 추가, 기존 FakeSocketModeClient 패턴)**:
  1. 루프 N tick 후 정지 시 sentinel tick이 호출되었음 (monkeypatch로 `slack_bot.run_engine_sentinel_tick` 치환·카운트)
  2. `ENGINE_SENTINEL_ENABLED=0`이어도 봇 루프는 정상 (tick 함수 내부 gate이므로 호출은 되되 무발송 — 호출-무해성만 확인)
- 기존 54 테스트 green 유지

### S4 — 테스트 인프라 + RC3 잔여

- `tests/conftest.py:57-59` `_LIVE_SESSION_GLOBAL_FILES`에 `"data/live_snapshot.json"` 추가 (frozenset 리터럴에 1항목) + 주석 1줄(금일 오탐 사례 언급)
- `tests/test_conftest_isolation_guard.py`에 테스트 1건: live_snapshot.json 변경이 leak으로 분류되지 않음 (기존 5테스트의 픽스처 패턴 준수)
- `app/notifications/runtime_alerts.py` `record_bottleneck`: 발송 성공/실패를 콘솔 print 1줄로 남김 — **단, 조기 return 경로(`should_alert` False)는 침묵 유지**(사이클마다 노이즈 금지), 시그니처·예외 삼킴 의미론 불변
- `tests/test_bottleneck_notifications.py`에 테스트 1건: 발송 예외 시 print 발생 (capsys)

### S5 — 마무리

- 본 계획서 §6 진행표 갱신 — **반드시 `| 슬라이스 | 상태 | 커밋 | 증거 |` 4열 규격** (completion-audit 파서 규격, 3열째가 커밋 SHA)
- `.venv/bin/python -m pytest -q` 전체 green 확인 (주의: 장중이면 `data/live_snapshot.json` 가드 오탐이 S4 수정으로 **사라져야 정상** — 남아 있으면 S4 미완)
- 커밋 2개: code(`feat(notifications): engine sentinel …`, app/+tests/ 전부) / docs(계획서만), 각각 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## §4 계획-단계 체크리스트 판정 (5항목)

1. **stale-import**: 해당 없음 — 신규 모듈, 이동 없음
2. **facade-import**: 해당 없음
3. **금지 심볼/부작용**: trading-critical 표면·`app/main.py`·`app/auth/settings.py` 무접촉(이번 트랙은 봇+테스트 인프라만), `.env` 불가침(dotenv 로딩은 봇의 기존 `_env_with_dotenv_defaults` 경유), 봇 헬스 루프 기존 로직 문자 불변, `record_bottleneck` 조기-return 침묵 유지, conftest는 frozenset 1항목+주석만
4. **패치-서피스 grep**: 수행(§2) — `run_socket_mode_bot` 시그니처 불변, FakeSocketModeClient 하네스 호환, 신규 이름 무충돌, conftest 상수는 테스트가 직접 참조(`test_conftest_isolation_guard.py`)하므로 그 파일 기존 5테스트 green 필수
5. **worktree**: 불필요 — branch 59 클린 트리. 허용 파일: `app/notifications/engine_sentinel.py`(신규), `app/notifications/slack_bot.py`(import+2줄), `app/notifications/runtime_alerts.py`(print 라인), `tests/conftest.py`(1항목+주석), `tests/test_engine_sentinel.py`(신규), `tests/test_engine_sentinel_tick.py`(신규), `tests/test_slack_bot.py`(테스트 추가), `tests/test_conftest_isolation_guard.py`(테스트 추가), `tests/test_bottleneck_notifications.py`(테스트 추가), 본 계획서

## §5 게이트

- 실행자: 슬라이스별 신규 테스트 green + S5 전체 스위트 green
- 상위: completion-audit(`--base 6bd991b --run-suite`) FAIL 없음 + 센티널 스모크(오늘-mtime stale 시나리오 → DOWN 메시지 실측) + 봇 회귀 54+2 green. PASS 전까지 미완.

## §6 진행표 (실행자가 갱신)

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| S1 평가기 | DONE | 698dcd1 | tests/test_engine_sentinel.py 7 red→green: 창밖/주말 무발화, 창안-오늘-stale DOWN+latch, 어제-mtime 무발화, 쿨다운 재알림, RECOVERED+re-arm, mtime None 무해, env 파서(결측·쓰레기·최솟값가드·불량창→기본) |
| S2 tick 어댑터 | DONE | 698dcd1 | tests/test_engine_sentinel_tick.py 4 red→green: stale→engine_health 1건, 파일부재(OSError)→무발송·무예외, ENABLED=0 킬스위치→무발송, notifier 예외→무전파+콘솔 breadcrumb. notifier 기본=SlackNotifier(env=env), now 기본=get_korean_now(app.core.time_utils, KST) |
| S3 봇 배선 | DONE | 698dcd1 | slack_bot.py 순수 가산 3편집(import 블록 + 루프전 생성 1줄 + 루프 top 호출 1줄; git diff로 기존 루프 로직 문자 불변 확인). tests/test_slack_bot.py +2: 매 tick 호출·env/sentinel 주입, ENABLED=0 실제 tick 무해+루프 정상. 기존 54 green 유지 → 56 passed |
| S4 인프라+RC3 | DONE | 698dcd1 | conftest.py: _LIVE_SESSION_GLOBAL_FILES에 data/live_snapshot.json 1항목+주석(금일 오탐). test_conftest_isolation_guard.py +1(멤버십+픽스처 배제 예측자; 기존 5 green 유지→6). runtime_alerts.record_bottleneck: 발송 성공/실패 콘솔 print(조기 return 침묵 유지, 시그니처·except 삼킴 불변). test_bottleneck_notifications.py +1(발송 예외→breadcrumb capsys). 18 passed |
| S5 마무리 | DONE | be7d799 | 전체 스위트 `3457 passed, 2 skipped, 726 subtests passed in 106.15s` (장중 live_snapshot 오탐 없음 → S4 확인). 센티널 스모크: 오늘-mtime 5분 stale → engine_health 1건 "[다운] 엔진 스냅샷이 약 5분째 갱신되지 않았습니다 (거래시간 중 엔진 프로세스 중단 의심)." code 커밋=698dcd1 |

## §7 후속 백로그

- 미기동 감지 (거래일 캘린더 필요 — 휴장 오탐 설계 선행)
- 웨지드 포트폴리오 감지 (무신호 장세 구별 설계)
- OPSQ0008 백오프 분류 — 운영자 결정 (트레이딩 케이던스 의미론)
- EOD 다이제스트 확장 (daily_summary 확장 여부 검토)
