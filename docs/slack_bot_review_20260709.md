# Slack Bot 전면 검토 — 불일치 수정 설계안 + 신규 추가 설계안 (2026-07-09)

관련: `docs/slack_backtest_native_pipeline_design_20260707.md`(S5a 네이티브 전환),
`docs/daily_error_triage_design_20260707.md` §4(outbox), `docs/todo.md` §15,
메모리 `native-backtest-pipeline`.

## 0. 검토 범위와 근거

- **코드(전수 열람)**: `app/notifications/` — `slack_bot.py`(Socket Mode 봇),
  `slack.py`(발신 notifier), `backtest_control.py`, `runtime_status_snapshot.py`,
  `orders_today.py`, `slack_outbox.py`, `snapshot_shared.py`, `runtime_alerts.py`,
  `ops_warnings.py`, `main_runtime_hooks.py`; `scripts/run_slack_bot.sh`,
  `scripts/stop_slack_bot.sh`, `scripts/native_backtest_pipeline.sh`;
  `launchd/com.kis-trader.slack-bot.plist.example`.
- **문서**: `CLAUDE.md` §Slack Bot, `README.md`, `docs/SLACK_ALERTS.md`,
  `docs/runbook_open_trading_api.md`, `docs/system_architecture_schema.md`,
  `docs/OPERATIONS.md`, `docs/opus_handoff_kis_trader_design_and_verification.md`,
  `docs/todo.md` §15.
- **동작 검증(2026-07-09 실측)**: `.venv/bin/python -m pytest tests/test_slack_bot.py
  tests/test_slack_notifier.py tests/test_slack_outbox.py tests/test_backtest_control.py
  tests/test_slack_backtest_hardening.py tests/test_runtime_status_snapshot.py -q`
  → **166 passed, 26 subtests passed**.

## 1. 현행 구현 요약 (as-is, 실측 앵커)

Slack 표면은 **두 평면**으로 나뉜다.

### 1-A. 읽기전용 명령 봇 (Socket Mode, `app/notifications/slack_bot.py`)

- 멘션 이름: **`@kis-trader`** (`_PLAIN_BOT_NAME_RE = @?(?:project-)?signalor`,
  `slack_bot.py:127`). `app_mention` 이벤트만 처리, 그 외 이벤트/서브타입은 무시.
- 명령: `ping` `help` `status` `health` `positions` `orders` `bottlenecks`
  `backtest [YYYYMMDD] [--dry-run|--skip-run]` `backtest status` `backtest kill`
  (+별칭 `position`→`positions`, `order`→`orders`). 매매 컨트롤 명령은 설계상 없음.
- 권한 3단계 (`is_app_mention_authorized`, `slack_bot.py:253`):
  공개(`ping`/`help`/unknown) → 민감 명령(채널/유저/팀 allowlist env 중 **1개 이상
  구성 + 전부 일치**) → `backtest`(BACK_TESTER 채널 고정 + **유저 allowlist 필수**).
- 명령 핸들러는 **broker API를 호출하지 않는다**. 데이터 소스는
  `data/runtime/slack_status_snapshot.json`(런타임이 `app/main.py:418`에서 기록,
  stale 기준 15분 = `DEFAULT_STALE_AFTER_SEC`), account-scoped order log
  (`orders_today.py` — KST 오늘 필터 + 계좌 시그니처 필터), performance summary.
- 생존성: SDK 자동 재연결 감시(기본 30s 간격, 90s 유예, 강제 재연결 2회 후
  프로세스 종료 → supervisor 재시작), `scripts/run_slack_bot.sh`(단일 인스턴스 락 +
  백오프 재시작 루프), launchd `com.kis-trader.slack-bot`(KeepAlive).
  `.env`에서 **지정 키만**(`DOTENV_DEFAULT_KEYS`) fallback 로드.

### 1-B. 발신 알림 (`app/notifications/slack.py::SlackNotifier`)

- event_type → 채널 env 라우팅(7채널): OPERATOR / ORDERS / BOTTLENECKS / ACTIVATOR
  (`kis_rate_limit`) / BACK_TESTER(`postrun.*`) / SUMMARY / PATH_FINDER —
  전부 `SLACK_CHANNEL_PROJECT_*` env로 지정, **고정 채널명은 코드에 없음**.
  비-operator 이벤트는 채널 env 미설정 시 OPERATOR로 fallback.
- 안전 기본값: `SLACK_ALERTS_ENABLED` 기본 **false**, `SLACK_ALERT_DRY_RUN` 기본
  **true**, (event_type, symbol, channel) 단위 60s 스로틀.
- 내구 아웃박스(`slack_outbox.py`, 2026-07 신설): 주문류 5종
  (`order_submitted/accepted/rejected/cancelled`, `order_gate_blocked`) 전송 실패 시
  `logs/slack_outbox.jsonl`(state_root 하위)에 적재, **다음 notify 시점에 선-flush**.
  TTL 1시간 / 최대 3회 시도 초과분은 조용히 drop.
- 에러 계열 라우팅: 메인루프 예외는 `record_main_loop_exception_if_needed` →
  `repeated_bottleneck` 이벤트 → **BOTTLENECKS 채널** (`runtime_alerts.py:59`).

### 1-C. Slack backtest 경로 (S5a-3 이후)

- 기본 파이프라인은 **`scripts/native_backtest_pipeline.sh`**
  (`backtest_control.py:44`, 2026-07-07 전환): `:8002` 사이드카·uv/npm·HTTP 서버
  없이 `app.tools.run_native_backtest`(분봉 parquet 리플레이)를 직접 실행.
  레거시 복귀는 `SLACK_BACKTEST_PIPELINE_SCRIPT` env 하나로 가능.
- single-flight pid 파일 + 프로세스 start-marker(PID 재사용 방어), 완료 모니터
  스레드 + heartbeat 파일(봇 재시작 시 "monitoring lost" 공지), max-runtime 워치독.

## 2. 불일치 목록과 수정 설계안 (D1~D8)

> 우선순위: **D1·D2·D3**(운영자가 실제로 보고 따라 하는 표면) → D4~D6(아키텍처
> 문서) → D7·D8(템플릿/절차 보강). 코드 변경은 D2 하나뿐이며 TDD 소형 슬라이스.

### D1. `CLAUDE.md` §Slack Bot — 봇 이름·명령·채널 전부 stale (P1)

- **현재 문구** (CLAUDE.md:101):
  `@kis-trader positions` → holdings | `@kis-trader orders today` → trades | Errors → `#trading-alerts`
- **실측**: 멘션 이름은 `@kis-trader`(`slack_bot.py:127`); 명령은 §1-A의
  10종; `#trading-alerts` 채널은 코드 어디에도 없음 — 에러/병목은
  `SLACK_CHANNEL_PROJECT_BOTTLENECKS`, 주문은 `..._ORDERS`, 운영자 대상은
  `..._OPERATOR` env 라우팅.
- **수정안** (교체 텍스트):

  ```markdown
  ## Slack Bot

  읽기전용 Socket Mode 봇 `@kis-trader` (`app/notifications/slack_bot.py`,
  supervisor `scripts/run_slack_bot.sh`, launchd `com.kis-trader.slack-bot`).
  명령: `ping`/`help`/`status`/`health`/`positions`/`orders`/`bottlenecks` +
  `backtest [YYYYMMDD]`·`backtest status`·`backtest kill`(BACK_TESTER 채널 전용,
  유저 allowlist 필수). 매매 컨트롤 명령은 없다(설계상 금지).
  발신 알림은 `SLACK_CHANNEL_PROJECT_*` env 라우팅 — 주문→ORDERS,
  병목/런타임 예외→BOTTLENECKS, rate-limit→ACTIVATOR, 운영자 대상→OPERATOR.
  ```

- 워크트리 사본(`.claude/worktrees/*/CLAUDE.md`)은 원본 갱신 후 자연 소멸 —
  별도 조치 불필요.

### D2. `backtest` 시작 응답의 flow 설명이 은퇴한 사이드카 flow (P1, 유일한 코드 수정)

- **위치**: `app/notifications/backtest_control.py:456` — 시작 성공 응답 마지막 줄
  `"flow: open-trading-api start → master collect → symbol validation → baselines → research loop"`.
- **실측**: 기본 스크립트는 native(`probe → native replay → summary`,
  `native_backtest_pipeline.sh`). 운영자가 Slack에서 매번 보는 문구가 존재하지
  않는 `:8002` 기동을 안내 중.
- **수정안** (TDD 1슬라이스): 실패 테스트 먼저 —
  `test_backtest_started_reply_describes_native_flow` (응답에 `native minute-replay`
  포함, `open-trading-api` 미포함) → 문구를
  `"flow: parquet freshness probe → native minute-replay backtest → summary"` 로 교체.
  기존 스위트에 옛 문구를 고정한 어서션 없음(grep 확인) → 회귀 위험 낮음.
  레거시 스크립트로 롤백 운용하는 경우를 위해 문구에서 스크립트명을 단정하지
  않는다(스크립트 결정은 env가 소유).

### D3. `docs/runbook_open_trading_api.md` "Slack-triggered pipeline" — 옛 wrapper 전제 (P1)

- **현재**: "The Slack Socket Mode bot can start **the same wrapper**"(=
  `open_trading_api_pipeline.sh`), `OPEN_TRADING_API_ROOT` 를 필수 env처럼 나열,
  `SLACK_BACKTEST_STOP_AFTER=true` 권고 사유가 "no orphan :8002",
  "Master collect retry" 서술 — 전부 레거시 파이프라인 전용.
- **실측**: 기본은 native — `--stop-after`는 no-op 수용, `OPEN_TRADING_API_ROOT`
  불필요, master collect 단계 자체가 없음.
- **수정안**: 섹션을 둘로 분리 —
  1. **기본(native)**: 필수 env는 `SLACK_CHANNEL_PROJECT_BACK_TESTER`,
     `SLACK_BOT_ALLOWED_USER_IDS`(필수), `SLACK_BACKTEST_ACCOUNT` 3개.
     선택: `NATIVE_BACKTEST_MANIFEST`/`_MAX_DAYS` 등 native 노브,
     `SLACK_BACKTEST_MAX_RUNTIME_SEC`.
  2. **레거시 롤백 부록**: `SLACK_BACKTEST_PIPELINE_SCRIPT=scripts/open_trading_api_pipeline.sh`
     설정 시에만 `OPEN_TRADING_API_ROOT`·`STOP_AFTER`·master-collect retry 문단 유효.
  운영 하드닝 3문단(모니터 유실 공지·PID 재사용 방어·워치독)은 파이프라인 무관 —
  그대로 유지.

### D4. `docs/system_architecture_schema.md` — 백테스트 경로 다이어그램 2곳 stale (P2)

- **현재**: ASCII 다이어그램(§"Slack Backtest Path", :40대)과 mermaid
  "Backtest And Research Flow"(:223 인근) 모두
  `scripts/open_trading_api_pipeline.sh → :8002 → master collect → baselines →
  research loop` 로 표기.
- **수정안**: 두 다이어그램의 wrapper 노드를
  `scripts/native_backtest_pipeline.sh → app.tools.run_native_backtest →
  parquet store(_workspace/gate2/paths.json)` 로 교체하고, open-trading-api
  사이드카는 "retired — env 롤백 경로만 잔존" 주석 노드로 강등.
  하단 서술 "The normal automated path is Slack → kis-trader wrapper →
  open-trading-api REST → local evidence"도 native 문구로 갱신.

### D5. `docs/opus_handoff_kis_trader_design_and_verification.md:122` — `#trading-alerts` (P2)

- **현재**: "로깅/관측성: `logs/` + Slack (`app/notifications/`, `#trading-alerts`)".
- **수정안**: `#trading-alerts` → "`SLACK_CHANNEL_PROJECT_*` env 라우팅
  (orders/bottlenecks/operator 등 7채널)". `docs/OLD/` 하위 동일 표현은 보존
  (역사 문서 — 수정하지 않는 원칙).

### D6. `docs/SLACK_ALERTS.md` — 채널 목록 불완전 + 핵심 정책 누락 (P2)

- **현재**: 채널을 "Operator, Orders, Bottlenecks, Summary, Path Finder 등"으로
  나열(ACTIVATOR·BACK_TESTER 누락), 기본 안전값(alerts disabled + dry-run)과
  아웃박스·스로틀 미기재, 명령 봇 존재 자체가 없음.
- **수정안** (TL;DR 포맷 유지, +15줄 내):
  - 채널 표를 7종 env 전체로 교체하고 대표 event_type을 병기
    (`kis_rate_limit`→ACTIVATOR, `postrun.*`→BACK_TESTER 포함).
  - "기본 안전값" 줄 추가: `SLACK_ALERTS_ENABLED=false`,
    `SLACK_ALERT_DRY_RUN=true`, 60s 스로틀 — **명시적으로 켜기 전엔 아무것도
    나가지 않는다**.
  - "실패 내구성" 줄 추가: 주문류 5종은 outbox(`logs/slack_outbox.jsonl`,
    TTL 1h/3회) 재전송.
  - 마지막에 명령 봇 1줄 포인터: "읽기전용 명령 봇은 `slack_bot.py` +
    `docs/runbook_slack_bot.md`(D8) 참조".

### D7. `launchd/com.kis-trader.slack-bot.plist.example` — 레거시 env 잔재·필수 env 누락 (P3)

- **현재**: `OPEN_TRADING_API_ROOT`를 기본 env로 포함(네이티브 기본에선 불사용),
  backtest에 **필수**인 `SLACK_BOT_ALLOWED_USER_IDS`는 부재. (`.env` fallback이
  있어 동작은 가능하지만, 템플릿만 따라 하면 backtest 멘션이 전부 거부된다.)
- **수정안**: `SLACK_BOT_ALLOWED_USER_IDS` placeholder 추가 + `OPEN_TRADING_API_ROOT`
  는 "legacy pipeline 롤백 시에만" 주석과 함께 유지 또는 제거.

### D8. Slack bot 운영 절차가 OPERATIONS.md에 부재 (P3)

- **현재**: `docs/OPERATIONS.md`에 Slack 언급 0줄. 기동/정지/로그/헬스 지식이
  `run_slack_bot.sh` 주석과 `runbook_open_trading_api.md` 일부에 흩어져 있음.
- **수정안**: `docs/runbook_slack_bot.md` 신설(또는 OPERATIONS.md 內 짧은 섹션):
  기동(`launchctl kickstart -k gui/501/com.kis-trader.slack-bot` /
  `./scripts/run_slack_bot.sh`), 정지(`./scripts/stop_slack_bot.sh`),
  로그 위치(`logs/slack_bot*.log`), 단일 인스턴스 락·백오프 동작,
  세션 스크립트 연동(`SLACK_BOT_SUPERVISOR_ENABLED_OVERRIDE`, `run_session.sh:39`),
  자주 쓰는 진단(`ping` 무응답 시 supervisor pid 확인 순서). README 문서 목록에 링크.

## 3. 신규 추가 설계안 (N1~N5)

> 전부 읽기전용/알림 표면 — trading-critical surface 비접촉. 각 항목 독립 슬라이스.

### N1. `backtest` 완료 메시지에 결과 요약(RESULT 라인) 포함 (P1 추천)

- **문제**: 완료 알림이 `exit_code/duration/log path`뿐 — 성공 시 수익률을 보려면
  운영자가 log/JSON을 직접 열어야 한다. native CLI는 stdout 마지막에
  `RESULT: return=…% mdd=…% trades=… win=…%` 한 줄을 이미 출력한다(설계안 §4.1).
- **설계**: `backtest_control.py`에 `_extract_backtest_result_line(log_path)` 추가 —
  기존 `iter_tail_lines_bounded` 재사용, tail에서 `RESULT:` 프리픽스 라인 탐색
  (없으면 빈 문자열, never raises). 완료 모니터의 성공 분기 텍스트에
  `• result: {line}` 추가. 실패/타임아웃 분기는 현행 log-tail 유지.
- **테스트**: RESULT 라인 有/無/로그 부재 3케이스 + 성공 메시지 조립.

### N2. `status`·`health` 응답에 계좌 표시 + outbox 관측성 (P1 추천)

- **문제 1**: 스냅샷에 `masked_account_display`가 이미 있으나
  `render_status_snapshot_reply`가 표시하지 않는다. 계좌 로테이션 운영
  (메모리 `rotated-account-equity-gap`)에서 "어느 계좌의 status인가"가 안 보임.
- **문제 2**: outbox에 pending이 쌓여도(전송 실패 누적) 볼 방법이 없다.
  TTL 초과 drop은 조용히 사라진다.
- **설계**:
  - `status` 헤더 아래 `계좌: {masked_account_display}` 1줄 (없으면 생략).
  - `health`에 체크라인 추가: `slack_outbox.load_pending()` 건수 —
    0건 ✅ / N건 ⚠️ (`가장 오래된 것 {age}s`).
- **테스트**: 계좌 표시 유/무, outbox 0건/N건 렌더.

### N3. Outbox idle-flush + drop 통지 (P2)

- **문제**: 재전송이 **다음 notify 시점**에만 일어난다(`slack.py:395`). 알림이
  뜸한 날엔 pending이 flush 기회를 못 얻고 TTL(1h)을 넘겨 조용히 drop —
  outbox가 지키려던 주문 알림이 결국 유실될 수 있다.
- **설계** (두 단계, 소극적 우선):
  1. **런타임 finalize 훅에서 flush**: 사이클마다 도는 finalize 단계에서
     `resend_pending`을 best-effort 호출(장중엔 사이클이 항상 돌므로 idle 창 제거).
     파일 rewrite는 원자적이지만 notifier와 경합 가능 → 같은 프로세스이므로 실제
     경합 없음(둘 다 메인 런타임). 봇 프로세스는 outbox에 관여하지 않는다(현행 유지).
  2. **drop 가시화**: `resend_pending` 반환값의 `dropped>0`이면
     `ops_warnings` 계열로 OPERATOR 채널에 1회 통지
     ("주문 알림 N건이 재시도 한도 초과로 유실됨 — order log는 보존됨").
- **경계**: never-raises 계약 유지, 사이클 지연 예산 내(파일 없으면 즉시 return).

### N4. 문서-구현 패리티 결정적 테스트 (P2 — 이번 드리프트 재발 방지)

- **문제**: 이번 검토의 D1~D5가 전부 "구현은 진화했는데 문서가 안 따라온" 클래스.
  같은 드리프트가 재발해도 감지 장치가 없다.
- **설계**: `tests/test_docs_slack_parity.py` —
  1. `HELP_TEXT`에 나열된 명령 == `SUPPORTED_COMMANDS`(별칭 제외) 상호 포함.
  2. `CLAUDE.md` §Slack Bot 블록에 `kis-trader` 문자열 존재 +
     `@kis-trader`/`#trading-alerts` 부재.
  3. `runbook`(D3 개편 후)의 필수 env 3종 문자열 존재.
  전부 정적 텍스트 대조 — 네트워크/브로커 무관, `completion-audit`의 결정적 게이트
  철학과 동일.

### N5. 봇 수명주기 자가 보고 (P3, 옵트인)

- **문제**: 봇의 silent death 이력(`run_slack_bot.sh` 헤더 주석). supervisor가
  재시작은 해주지만 **재시작이 반복되고 있다는 사실** 자체는 아무도 모른다.
- **설계**: 옵트인 env `SLACK_BOT_LIFECYCLE_NOTICE=true`일 때만 —
  connect 성공 직후 OPERATOR 채널로 1줄 (`bot up, pid, git short-sha`),
  forced-reconnect 소진으로 종료할 때 1줄. `ping` 응답에 `uptime`/`sha` 병기.
  기본 off — 채널 노이즈 우려가 있고 supervisor 백오프가 폭주를 이미 방지하므로
  운영자 선택으로 남긴다.

### 고려 후 제외

- **매매 컨트롤 명령 추가(매수/매도/중지)**: HELP_TEXT가 명시적으로 금지하는
  설계 원칙(읽기전용 봇 + 운영자 게이트) — 유지.
- **멀티계좌 per-account 스냅샷 파일 분리**: 스냅샷 last-writer-wins 문제는
  실재하나, 대시보드 account routing 설계(`docs/dashboard_account_routing_design_20260707.md`)와
  겹치는 상류 결정 — N2(계좌 표시)로 가시화만 먼저 하고 별도 설계로 분리.

## 4. 실행 순서 제안

| 순서 | 항목 | 종류 | 크기 |
|------|------|------|------|
| 1 | D2 (backtest 응답 문구) | 코드+테스트 | XS |
| 2 | D1·D5 (CLAUDE.md·handoff 문구) | 문서 | XS |
| 3 | D3·D4 (runbook·schema 재작성) | 문서 | S |
| 4 | N1·N2 (RESULT 요약·status 계좌/outbox) | 코드+테스트 | S |
| 5 | D6·D7·D8 (SLACK_ALERTS 확장·plist·봇 runbook) | 문서/템플릿 | S |
| 6 | N4 (패리티 테스트) — D1·D3 완료 후 | 테스트 | XS |
| 7 | N3 (idle-flush) → N5 (lifecycle notice) | 코드+테스트 | M |

- 코드 커밋과 문서 커밋 분리(리포 규칙). N1~N3은 `/adversarial-verify` 대상
  아님(계약 변경 없음)이나, N3은 notifier 경로를 건드리므로 통합 직전 1회 권장.
