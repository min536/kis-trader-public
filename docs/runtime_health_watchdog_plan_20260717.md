# Runtime Health Watchdog 위임 계획서 (2026-07-17)

- **프로파일**: 신규 모듈 TDD (4슬라이스)
- **실행자**: opus 서브에이전트 (tdd_guard stub-first 준수)
- **검증**: 상위 세션이 완료 감사 + 통합 게이트 수행. 실행자의 "완료" 보고는 감사 PASS 전까지 미완 취급.
- **브랜치**: `59` (클린 트리에서 시작 — 직접 편집, worktree 불필요)

## §0 목표 / 비목표

**목표**: 엔진 프로세스는 살아있는데 사이클이 연속 실패하는 "침묵 장애"를 감지해 Slack으로 에스컬레이션한다. 2026-07-17 실관측 근거: 대시보드에서 80/80 사이클 전부 `CYCLE_ERROR`(OPSQ0008 잔고조회 실패, 세션 중 231→263으로 증가), 체결 0, 그러나 어떤 알림도 없이 상태표시는 "running·정상".

**비목표 (v1에서 하지 않는 것)**:
- 루프 슬립 연장/백오프 등 **런타임 행동 변경 없음** — 순수 관측+알림 레이어. (백오프는 트레이딩 케이던스 의미론 = 위임 금지 원칙, §7 후속)
- 무주문 스트릭/스냅샷 불완전/웨지드 포트폴리오 감지 (§7 후속 — v1 데이터 소스 미확보)
- trading-critical 표면(`app/execution|risk|strategy`, `app/auth/settings.py`, `app/pipeline/order_gate.py|buy_lane.py|sell_lane.py`) 수정 없음
- `.env` 접근, `app.main` 실행, broker API 호출, launchd 조작 없음
- 대시보드 표면 변경 없음

## §1 근본 원인 — 왜 오늘 침묵했나 (조사 근거)

기존 알림 사슬과 그 구멍:

| # | 사실 | 근거 |
|---|---|---|
| RC1 | run_cycle 예외는 session_loop가 삼키고 계속 진행 | `app/runtime/session_loop.py:509-514` (`except Exception` → DEGRADED 콘솔 → `record_main_loop_exception_if_needed(exc)`) |
| RC2 | transient API 오류는 알림 경로에서 **조기 return** — 마커는 전부 영문 네트워크 문자열이라 OPSQ0008("잔고 조회 실패…MCI전송 오류")은 transient 아님 → `MAIN_LOOP_EXCEPTION` 병목 경로로 감 | `app/notifications/runtime_alerts.py:59-62`, `app/core/error_classification.py:8-21,62-66` |
| RC3 | 병목 경로는 윈도우-카운트+쿨다운 1회성 — `MAIN_LOOP_EXCEPTION` 임계값 기본 1이지만, 발송 실패/채널 미설정 포함 **모든 예외를 무음으로 삼킴**(`except Exception: return`), 그리고 쿨다운 후 재발송돼도 "N회 반복" 동일 문구뿐 — **지속시간·연속성·심각도 상승·회복 개념이 없음** | `app/notifications/runtime_alerts.py:34-57`, `app/notifications/bottleneck.py:15,39-44,97-137` |
| RC4 | 결론: "3시간째 100% 사이클 실패"와 "일시 블립 1회"가 알림상 구별 불가. 발송이 실제로 됐는지도 코드상 관측 불가(무음 삼킴). | RC1-RC3 종합 |

## §2 구조지도 (실행자는 재조사 없이 인용만 검증)

- **배선 지점**: `app/runtime/session_loop.py:372-537` `run_session_loop()` — 주입식 kw-only 파라미터 관행 (`run_cycle:382`, `record_main_loop_exception_if_needed:384`, `sleep:386`). 사이클 성공 = `run_cycle(...)` 정상 리턴(`:488-508`), 실패 = `except Exception as exc:509-514`. **주의**: `:340` 주석 "keep them verbatim" — `note_rate_limit_backoff`(`:341-370`)의 `_record_bottleneck` 리터럴 블록은 문자 하나도 건드리지 말 것.
- **Slack 발송**: `app/notifications/slack.py:428-436` `SlackNotifier.send(event_type, message, *, symbol=None, details=None, allow_smoke_test=False)`. 이벤트타입→채널 매핑 `EVENT_CHANNEL_ENV_BY_TYPE`(`:96-101`) + 개별 등록 관행(`:102-123`, 예: `AUTOTUNER_EVENT_TYPE = "autotuner_proposal"` → `OPERATOR_CHANNEL_ENV`). `EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE`(`:124-133`)에 OPERATOR 단독 타입은 `(OPERATOR_CHANNEL_ENV,)` 1줄 추가 관행(`:130-133`).
- **알림 어댑터 관행**: `app/notifications/runtime_alerts.py:14-24` 모듈 싱글턴(`get_slack_notifier()`), `:34-57` fail-safe try/except 패턴.
- **인메모리 latch 선례**: `app/notifications/account_alerts.py:10-26` — 재시작 시 re-arm은 설계 의도(C5). 워치독도 동일 원칙(영속화 없음).
- **env 파서 선례**: `app/notifications/bottleneck.py:60-79` `_parse_float`/`_parse_int` (module-private — import 금지, 워치독 모듈에 동형 로컬 파서 작성).
- **에러 분류기(재사용)**: `app/core/error_classification.py:62-66` `looks_like_transient_api_error`, `:24` `looks_like_rate_limit_error` — 워치독은 **분류 무관하게 모든 사이클 예외를 스트릭에 계상**한다(transient도 지속되면 장애다 — 오늘의 교훈).
- **패치-서피스 조사 결과**: `record_main_loop_exception_if_needed`/`BottleneckAggregator`를 참조·패치하는 테스트 = `tests/test_main_bottleneck_hooks.py`, `tests/test_bottleneck_notifications.py`, `tests/test_main_loop_characterization.py` 3파일. `run_session_loop(`를 직접 호출하는 테스트 grep 결과 0건이므로 **기본값 있는 kw-only 파라미터 추가는 시그니처 안전**. 신규 이름 `record_cycle_health`는 기존 패치 대상과 무충돌.
- **main.py 경계**: 배선은 import 1줄 + `run_session_loop(...)` 호출부 kwarg 1줄만. **새 top-level def/class 절대 금지** (trading_guard 훅이 차단함).

## §3 슬라이스 사양

### S1 — `app/notifications/health_watchdog.py`: 순수 상태기계 (신규)

`HealthWatchdog` 클래스 (표준 lib만, I/O 없음, 시계는 인자 주입):

- `record_cycle_outcome(*, error_text: str | None, now: datetime) -> list[HealthAlert]`
- 내부 상태: `consecutive_error_cycles`, `streak_started_at`, `last_alert_level`, `last_alert_at`
- `HealthAlert` frozen dataclass: `level`("WARN"|"CRITICAL"|"RECOVERED"), `consecutive_errors`, `duration_seconds`, `sample_error`(마지막 error_text 160자 절단), `message`(한국어 요약)
- 규칙:
  1. 에러 사이클 연속 `warn_streak`(기본 5)회 도달 → WARN 1건 (최초 1회 latch)
  2. 연속 `critical_streak`(기본 20)회 도달 → CRITICAL 1건 (latch)
  3. latch 후에도 스트릭 지속 시 `repeat_cooldown_seconds`(기본 1800)마다 현재 최고 레벨 재알림
  4. 에러 후 성공 사이클 → 스트릭 리셋, 직전에 WARN 이상 발화했었다면 RECOVERED 1건, latch 전부 해제(re-arm)
  5. WARN 미도달 회복은 무알림 (노이즈 억제)
- 설정: `HealthWatchdogConfig` dataclass + `load_health_watchdog_config(env: Mapping[str,str]) -> HealthWatchdogConfig` — env 키 `HEALTH_WATCHDOG_WARN_STREAK`/`CRITICAL_STREAK`/`REPEAT_COOLDOWN_SECONDS`, bottleneck.py:60-79 동형 로컬 파서(최솟값 가드 포함)

**테스트 (tests/test_health_watchdog.py, 이 순서로 1개씩 red→green)**:
1. 연속 4에러 → 알림 0건
2. 연속 5에러 → WARN 1건 (6번째 에러엔 추가 발화 없음)
3. 연속 20에러 → CRITICAL 1건
4. CRITICAL latch 후 쿨다운 경과 → 재알림 1건, 쿨다운 내엔 0건
5. WARN 후 성공 사이클 → RECOVERED 1건 + 이후 다시 5연속 에러 → WARN 재발화 (re-arm 증명)
6. WARN 미도달(3에러) 후 성공 → 알림 0건
7. env 파서: 결측→기본값, 비정상 문자열→기본값, 최솟값 클램프

### S2 — Slack 발송 어댑터 + 이벤트타입 등록

- `app/notifications/slack.py`: `ENGINE_HEALTH_EVENT_TYPE = "engine_health"` → `OPERATOR_CHANNEL_ENV` 등록 + OPTIONS `(OPERATOR_CHANNEL_ENV,)` 1줄 — `:107-123`의 AUTOTUNER/RECONCILIATION 등록 블록과 **동일 형식** 2~3줄만 추가, 그 외 slack.py 불변
- `app/notifications/runtime_alerts.py`에 추가:
  - 모듈 싱글턴 `get_health_watchdog()` (기존 `:14-24` 관행 동형)
  - `record_cycle_health(error: Exception | None) -> None`: 워치독에 계상 → 반환된 알림을 `get_slack_notifier().send(ENGINE_HEALTH_EVENT_TYPE, alert.message, details={...})` 발송. **RC3 교훈 반영**: 발송 결과/예외를 무음 삼키지 말고 `print()`로 콘솔에 1줄 남길 것 (기존 코드는 수정하지 않고 신규 함수에만 적용)
  - kill-switch: env `HEALTH_WATCHDOG_ENABLED`(기본 "1") — "0"/"false"면 계상만 하고 발송 스킵
  - 테스트 재-arm용 `reset_health_watchdog()` (account_alerts `:39-49` 관행 동형)

**테스트 (tests/test_health_watchdog_alerts.py)**:
1. 5연속 에러 주입 → fake notifier에 engine_health 1건 수신 (monkeypatch로 notifier 주입)
2. notifier가 예외 던져도 record_cycle_health는 전파 안 함 + 콘솔 1줄
3. HEALTH_WATCHDOG_ENABLED=0 → 발송 0건
4. 이벤트타입 채널 해석: `resolve_channel_env_var("engine_health")` == OPERATOR env 키

### S3 — 배선: session_loop + main.py

- `app/runtime/session_loop.py`: `run_session_loop`에 kw-only `record_cycle_health: Callable[[Exception | None], None] | None = None` 추가. 호출 2곳 — `run_cycle(...)` 정상 리턴 직후 `record_cycle_health(None)`, `except` 블록(`:509-514`)의 `record_main_loop_exception_if_needed(exc)` 다음 줄에 `record_cycle_health(exc)`. None 가드 필수. **`:340-370` verbatim 블록 및 기존 코드 문자 불변.**
- `app/main.py`: import 1줄(`from app.notifications.runtime_alerts import record_cycle_health`) + `run_session_loop(...)` 호출부에 `record_cycle_health=record_cycle_health,` kwarg 1줄. **새 top-level def 금지.**

**테스트 (tests/test_session_loop_health_wiring.py)**:
1. fake run_cycle 성공 → record_cycle_health가 None으로 호출됨
2. fake run_cycle 예외 → record_cycle_health가 그 예외로 호출되고 루프는 계속
3. record_cycle_health=None(기본) → 기존 동작 무변화 (예외 없이 완주)
- 회귀: `tests/test_main_bottleneck_hooks.py`, `tests/test_bottleneck_notifications.py`, `tests/test_main_loop_characterization.py` 전부 green 유지

### S4 — 마무리

- 본 계획서 §6 진행표 갱신 (슬라이스별 DONE + 증거 1줄)
- 전체 스위트 `.venv/bin/python -m pytest -q` green 확인 후 **code 커밋** (docs 커밋 분리 — 계획서/문서는 별도 커밋)

## §4 계획-단계 체크리스트 판정 (5항목, 공란 금지)

1. **stale-import**: 해당 없음 — 이동/삭제 없는 신규 모듈 트랙
2. **facade-import 도출**: 해당 없음 — facade 재수출 없음
3. **금지 심볼/부작용**: `app/auth/settings.py` 수정 금지(설정은 env 직접 파싱), `session_loop.py:340-370` verbatim 유지, `main.py` 새 def 금지(훅 차단), 기존 `record_bottleneck`/`record_main_loop_exception_if_needed` 동작 불변, `.env`·`data/`·`logs/` 접근 금지, `app.main` 실행 금지
4. **패치-서피스 grep**: 수행 완료(§2 기록) — 영향 테스트 3파일 식별, 신규 kw-only 파라미터는 기본값으로 시그니처 호환, 신규 이름 무충돌
5. **worktree 판단**: 불필요 — branch 59 클린 트리, 허용 파일만 직접 편집: `app/notifications/health_watchdog.py`(신규), `app/notifications/runtime_alerts.py`, `app/notifications/slack.py`(이벤트타입 등록 블록만), `app/runtime/session_loop.py`(파라미터+호출 2줄), `app/main.py`(import+kwarg 2줄), `tests/test_health_watchdog.py`(신규), `tests/test_health_watchdog_alerts.py`(신규), `tests/test_session_loop_health_wiring.py`(신규), 본 계획서

## §5 게이트

- **실행자 게이트**: 슬라이스별 신규 테스트 green + S4에서 전체 스위트 green
- **상위 통합 게이트**: (1) 완료 감사 — 진행표 vs git log·허용 파일 스코프·금지사항 준수, (2) 전체 스위트 재실행, (3) 워치독 시뮬레이션 스모크(테스트 하네스에서 5연속 에러 → WARN 메시지 내용 확인). PASS 전까지 전체 미완.

## §6 진행표 (실행자가 슬라이스 완료마다 갱신. 상위 통합 게이트 PASS 전까지 전체 미완.)

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| S1 상태기계 | DONE | 3e23cf3 | tests/test_health_watchdog.py 7개 전부 green (streak 4→0, 5→WARN, 20→CRITICAL, 쿨다운 재알림, RECOVERED 재-arm, 미도달 무음, env 파서); `.venv/bin/python -m pytest tests/test_health_watchdog.py -q` → 7 passed |
| S2 발송 어댑터 | DONE | 3e23cf3 | slack.py에 engine_health 이벤트타입 3줄 등록(constant+채널맵+OPTIONS), runtime_alerts에 get/reset_health_watchdog + record_cycle_health(fail-safe print, kill-switch); tests/test_health_watchdog_alerts.py 4개 green (발송1건·예외무전파+콘솔·kill-switch·채널해석) |
| S3 배선 | DONE | 3e23cf3 | session_loop.run_session_loop에 kw-only record_cycle_health(기본 None) + 성공/except 2곳 None-가드 호출, main.py import 1줄+kwarg 1줄(새 def 없음); tests/test_session_loop_health_wiring.py 3개 green + 회귀 4파일(bottleneck_hooks/notifications/characterization/rate_limit_body_sources) green; verbatim 340-370 불변 |
| S4 마무리 | DONE | 9cad39f | `.venv/bin/python -m pytest -q` → 3442 passed, 1 skipped, 726 subtests passed, 1 failed; 유일 실패는 `test_workspace_static_ui_contract.py`(workspace/data.js 미존재)로 워치독 변경과 무관한 사전존재 baseline — 트래킹 파일 stash 후 clean 트리에서 동일 실패 재현 확인(§0 비목표: 대시보드/workspace 표면 미변경). code/docs 커밋 분리 완료 |

**상위 게이트 후속 조치 (2026-07-17)**: baseline 실패의 근본 원인은 `b40e466`이 workspace/data.js를 generated 아티팩트로 의도적 untrack했는데(생성기 `scripts/refresh_workspace_data.py`) 계약 테스트가 디스크 사본에 잠복 의존해온 것 — 58 머지 체크아웃이 디스크 사본을 제거하며 노출. 수정: 미생성 시 `pytest.skip` 가드 (`tests/test_workspace_static_ui_contract.py`). 워치독 스모크: OPSQ0008 실제 메시지 22연속 주입 → WARN(streak=5, 2분)·CRITICAL(streak=20, 9분)·회복 시 RECOVERED(22회, 11분) 발화 실측.

## §6.1 핀 테스트 (감사용 — 계획 §3 케이스 ↔ 실구현 함수명 대응)

- S1 (tests/test_health_watchdog.py): `test_four_consecutive_errors_emit_no_alert`, `test_fifth_consecutive_error_emits_single_warn`, `test_twentieth_consecutive_error_escalates_to_single_critical`, `test_critical_latch_re_alerts_only_after_cooldown`, `test_success_after_warn_emits_recovered_then_re_arms`, `test_recovery_below_warn_threshold_is_silent`, `test_load_config_defaults_invalid_and_min_guard`
- S2 (tests/test_health_watchdog_alerts.py): `test_five_errors_send_single_engine_health_event`, `test_notifier_exception_is_not_propagated_and_is_logged`, `test_kill_switch_disables_sending`, `test_engine_health_resolves_to_operator_channel`
- S3 (tests/test_session_loop_health_wiring.py): `test_successful_cycle_records_health_with_none`, `test_cycle_exception_records_health_and_loop_continues`, `test_default_none_health_hook_preserves_behavior`

## §7 후속 (v2 백로그 — 이번 위임 범위 아님)

- 무주문 스트릭/웨지드 포트폴리오 감지 (데이터 소스: last_decision 게터 주입 설계 필요)
- 스냅샷 불완전 신호 연동
- 플래그-게이트 백오프 어드바이저리 (운영자 승인 후 별도 트랙)
- 침묵-발송-실패 관측성: `record_bottleneck`의 무음 `except Exception: return`에 콘솔 로그 추가 검토
