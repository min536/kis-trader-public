# 텔레그램 은퇴 + 구 워크스페이스 정리 + orchestrator EOD 부착 계획서 — 2026-07-19

프로파일: **정리(삭제) + 소형 wiring** (AST 게이트 해당 없음 · 핀+스위트 게이트 적용)

## §0 배경 / 목표 / 비목표

**배경 (전부 2026-07-19 실측).**
- 텔레그램 알림 시스템은 토큰 미설정(`telegram.py:44` 조기 return, 봇 env 불리언 실측 False)으로 **전 경로가 무음 no-op**. Slack 스택이 완비된 현재 이중 채널이며, 특히 주문 알림은 `app/notifications/order_events.py` Slack 인프라와 중복.
- 구 정적 워크스페이스(`workspace/*.html` + `scripts/refresh_workspace_data.py` + `app/dashboard/workspace_payload.py`)는 dashboard v2 launchd 잡이 완전 대체. `workspace_payload` 소비처는 refresh 스크립트뿐(실측).
- `app/orchestrator/`(postrun_audit) 참조는 자기 자신뿐 — 운영 wiring 0. 운영자 결정: 삭제 대신 **EOD 파이프라인에 부착**.

**목표.** ① 텔레그램 콜사이트 제거(단, pnl_brake 에스컬레이션은 Slack `risk_brake`로 **대체** — 유일하게 안전상 대체가 필요한 지점), ② 구 워크스페이스 표면 삭제, ③ `eod_wrapper.sh`에 postrun_audit best-effort 부착.

**비목표.** rate-limit 알림의 Slack 신설(가시성은 `runtime_status_snapshot.py:89,115,518` 카운터가 이미 담당 — 소음 회피), 대시보드 v2 변경, launchd 잡 로드/재시작.

## §1 레인 분리 (실행 순서 고정)

| 레인 | 담당 | 파일 | 순서 |
|---|---|---|---|
| **M1-M3** | 상위 모델 직접 (위임 금지: trading-critical/인접) | `app/notifications/slack.py`(등록), `app/risk/pnl_brake.py`, `app/runtime/cycle_phases/sell_watch_phase.py`, `sell_order_phase.py`, `app/main.py`(import 1줄), 해당 테스트 | **선행** |
| **E (S1~S6)** | opus 위임 | `app/core/order_log.py`, `app/core/throttle.py`, `app/research/replay/scan_driver.py`, workspace 표면, `scripts/eod_wrapper.sh`, 해당 테스트 | M 커밋 후 |
| **M4** | 상위 모델 직접 (금지 파일 `app/auth/settings.py`) | settings 필드 6종 + `settings_fields.py` + `app/reporting/telegram.py` 삭제 + 픽스처 2건 + 잔존 참조 0 게이트 | E 감사 PASS 후 |

## §2 구조지도 (실측 인용)

**텔레그램 콜사이트 전수:** `app/main.py:249`(import만 — 호출 0, `app.main.send_telegram` 테스트 patch 0 실측), `sell_watch_phase.py:76,410-412,468-470`, `sell_order_phase.py:76,296`, `order_log.py:18,446-451`, `throttle.py:179-184`, `pnl_brake.py:10,225-234`, `scan_driver.py:310`(설정 미러 1줄), `telegram.py`(본체), `settings.py:27,531-536,598`, `settings_fields.py::build_telegram_fields/TelegramFields`.

**테스트 patch 표면:** `tests/test_no_position_sell_detection.py:91` + `tests/test_pid_logging.py:29`(`app.core.order_log.send_telegram_alert`), `tests/test_daily_pnl_brake.py:791,809`(`app.risk.pnl_brake.send_telegram_alert`), 픽스처 필드 참조 `tests/test_error_classification.py:223`, `tests/test_sell_watch_phase.py:85`.

**Slack 등록 관습:** `slack.py` 선례 3줄(상수+BY_TYPE+OPTIONS — disclosure_alert 선례; OPTIONS 누락 시 무발송).

**주문 Slack 중복 근거:** `app/notifications/order_events.py:13,24` + `slack_outbox.py` + 발신자 `app/execution/buy_flow.py`/`order_guard.py`(실측 grep).

**workspace 삭제 체인:** `workspace/index.html`, `workspace/Operator Workspace.html`, `workspace/operator_workspace_standalone.html`, `workspace/README.md`, `scripts/refresh_workspace_data.py`, `app/dashboard/workspace_payload.py`, workspace 정적 UI 계약 테스트 파일. (`workspace/claude-design/` = dashboard v2 — **무접촉**.)

**EOD 부착 지점:** `scripts/eod_wrapper.sh` — `check_eod` 실행/리포트 append 후 `notify_postrun` 호출 구조(tail 실측). orchestrate CLI: `python -m app.tools.orchestrate postrun_audit --date YYYYMMDD --account <sig>` (`app/tools/orchestrate.py:9,32`).

## §3 E 레인 슬라이스 사양 (opus)

### S1 — `order_log.py` 텔레그램 제거
- `:18` import, `:446-451` 성공/실패 블록 삭제 (Slack order_events 중복 근거 §2). `settings.telegram_notify_*` 참조도 함께 제거.
- 테스트: `test_no_position_sell_detection.py:91`·`test_pid_logging.py:29`의 patch 라인 제거. 기존 테스트 green 유지.

### S2 — `throttle.py` 텔레그램 제거
- `:179-184` 블록 삭제 (default-off dead 경로; rate-limit 가시성은 카운터 유지 — §0 비목표).

### S3 — `scan_driver.py:310` 미러 1줄 제거
- 해당 dict에서 `"telegram_notify_rate_limit": False` 키 제거. (`telegram.py` 본체 삭제는 M4 — 여기서 하지 않는다.)

### S4 — workspace 은퇴
- §2 삭제 체인 7파일 `git rm`. 게이트: 삭제 후 `grep -rn "refresh_workspace_data\|workspace_payload\|Operator Workspace" app/ scripts/ tests/` = 0건, dashboard v2 경로(`workspace/claude-design/`) diff 0.
- 신규 테스트 불요(삭제 트랙) — 대신 위 게이트 출력을 보고에 인용.

### S5 — orchestrator EOD 부착 (`scripts/eod_wrapper.sh`)
- `notify_postrun` 호출 **직전**에 best-effort 블록 추가: `"${PYTHON_BIN}" -m app.tools.orchestrate postrun_audit --date "${RESOLVED_DATE}" --account "${ACCOUNT}"` 출력을 리포트에 append. **반드시 `|| true` + EOD exit_code 불변** (감사 실패가 EOD 실패로 승격되지 않게). 스크립트 상단 주석 1줄로 계약 명시.
- 셸 계약 테스트가 있으면(grep `eod_wrapper` tests/) 갱신, 없으면 전용 계약 테스트 파일 신설 — 최소 2핀: `test_eod_wrapper_invokes_postrun_audit_best_effort`(스크립트 텍스트에 orchestrate 호출+`|| true` 존재), `test_eod_wrapper_preserves_exit_code_contract`(exit "${exit_code}" 라인 유지).

### S6 — 마무리
- 전체 스위트 green → feat 커밋 1개("chore(cleanup): 텔레그램 비크리티컬 콜사이트 제거 + 구 워크스페이스 은퇴 + orchestrator EOD 부착") → 진행표 SHA 기입 docs 커밋.

## §4 계획-단계 체크리스트 판정

1. **구조지도 인용** — §2 전 항목 실측 file:line. PASS
2. **패치-서피스** — patch 표면 전수 §2 명시(2패턴 grep 완료: `app.main.send_telegram` 0건, 모듈 경로 patch 4건 전부 E/M 레인에 배정). PASS
3. **trading-critical 접촉** — E 레인 0 (order_log/throttle는 core이나 금지 목록 외; 금지·인접 파일은 전부 M 레인). PASS
4. **import 도출** — 삭제 전용 + 셸 1블록. PASS
5. **테스트 격리** — 삭제 트랙; 신규 핀은 셸 텍스트 계약 2건(파일 읽기만). PASS

## §5 에스컬레이션 (E 레인)

- S1에서 patch 라인 제거만으로 기존 테스트가 red가 되면(텔레그램 호출을 행위로 검증하는 테스트 발견 시) 해당 테스트 삭제/수정을 임의로 하지 말고 중단 보고.
- S4 게이트 grep에서 예상 외 참조 발견 시 삭제 중단 보고.

## §6 진행표

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| M1-M3 크리티컬 선행 | DONE | ac9ca14 | slack.py risk_brake 3줄 등록 + pnl_brake 에스컬레이션 Slack 대체(_notify_brake_escalation seam, red→green) + 페이즈 rate-limit 3블록·main.py 스테일 import 삭제; 영향 테스트 112 passed |
| S1 order_log | DONE | b575061 | import+성공/실패 블록+고아 get_settings 제거, 테스트 patch 2건 정리; `grep telegram\|get_settings app/core/order_log.py` 0건; pid/no_position 테스트 25 passed |
| S2 throttle | DONE | b575061 | :179-184 블록+고아 `_LAST_RATE_LIMIT_ALERT_TS`·account_scope import 제거; 잔존 grep 0건; test_throttle_adaptive_pacing 6 passed |
| S3 scan_driver | DONE | b575061 | 미러 1줄 제거; `replay_settings().telegram_notify_rate_limit` → `__getattr__` False 폴백으로 행위 동일 실증; replay 테스트 18 passed, 1 skipped |
| S4 workspace 은퇴 | DONE | b575061 | 체인 7파일 + tracked 잔재(app.js/styles.css/shot×6) + workspace 전용 테스트 2파일(payload·console_renderer — §2 미기재 소비처, 코디네이터 지시로 확장) git rm + console_v1의 Wiring 테스트클래스 제거; 게이트 grep `refresh_workspace_data\|workspace_payload\|Operator Workspace` app/ scripts/ tests/ = 0건; `workspace/claude-design/` diff 0 |
| S5 EOD 부착 | DONE | b575061 | notify_postrun 직전 `orchestrate postrun_audit` best-effort 블록(`\|\| true`+`EOD_WRAPPER_POSTRUN_AUDIT` 게이트, 상단 주석 계약 명시); `exit "${exit_code}"` 불변; 핀 2건 red→green; test_eod_wrapper 4 passed; bash -n OK |
| S6 마무리 | DONE | b575061 | 전체 스위트 3492 passed, 1 skipped, 723 subtests (0 failed) |
| M4 settings/telegram.py 최종 제거 | DONE | 0fd3790 | settings 필드 6종+빌더+모듈 본체 삭제, 픽스처 7파일 정리; 리포 전체 telegram 식별자 참조 0 게이트 PASS(pnl_brake 역사 독스트링 1줄 의도 보존); 전체 스위트 3491 passed, 1 skipped, 723 subtests |

(실행자: E 레인 행만 기입 — 상태 DONE + 실제 커밋 SHA + 증거. SHA 없는 DONE은 감사 FAIL.)

### §6.1 핀 테스트 목록

test_eod_wrapper_invokes_postrun_audit_best_effort, test_eod_wrapper_preserves_exit_code_contract
