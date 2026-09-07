---
Purpose: 정규 세션 운영 및 환경 설정 흐름 안내
Read when: kis-trader의 실행, 스냅샷 워커, 안전한 stop/restart 등의 운영 작업을 수행하기 전
Do not use for: 코드/전략 변경
---

# Operations Guide (TL;DR)

정규 세션 운영에서 가장 먼저 볼 문서입니다.  
이 문서는 `kis-trader`의 현재 운영 진입점, 설정 우선순위, snapshot worker, 안전한 stop/restart 흐름만 간단히 정리합니다.

## 1. 어떤 진입점을 써야 하나
정규 세션의 운영 진입점은 launchd 잡입니다.

- 현재 정본 label: `com.kis-trader.session`
- wrapper: `scripts/run_session.sh` (`launchd`의 `ProgramArguments`)
- 운영자 단발 기동/재기동:

```bash
launchctl kickstart -k gui/$(id -u)/com.kis-trader.session
```

- 상태 확인:

```bash
launchctl list | rg 'com\.(kis-trader|kistrades)'
```

보조 진입점:
- 시작 전 안전 점검(운영자 전용, 세션 미기동):
  - `./scripts/run_session.sh --dry-run`
- 단발성 스캔 진단(운영자 전용, 외부 API/시세 조회 가능):
  - `RUN_MODE=scan_only RUN_ONCE=true python3 -m app.main`
- 수동 중지(운영자 전용):
  - `./scripts/stop_session.sh`

중요:
- `python3 -m app.main` 직접 실행은 정규 세션 운영 진입점이 아닙니다.
- AI agent는 `app.main`, `run_session.sh`, `stop_session.sh`, `restart_session.sh`, broker/API 진단 명령을 실행하지 않습니다.
- `com.kistrades.regularsession`는 legacy label입니다. 등록되어 있어도 현재 정규 세션 재기동/복구 대상은 `com.kis-trader.session`입니다.
- 정규 세션에서는 session lock, session-safe runtime-control, live snapshot refresh worker, day-scoped logs가 같이 관리됩니다.

## 2. 설정 우선순위
현재 운영 모델의 핵심은 `.env`와 정규 세션 설정을 분리하는 것입니다.

우선순위:
1. 코드 기본값
2. `.env`
3. `config/regular_session.env` (`run_session.sh`로 시작한 정규 세션에서 우선)
4. 런타임 code-enforced runtime control
   - adaptive midday mode
   - degraded mode

정리:
- `.env`는 공용 기본 설정에 가깝습니다.
- 정규 세션에서 안전하게 강제하고 싶은 runtime-control 값은 `config/regular_session.env`에 둡니다.
- 그래서 `.env`에 예전 값이 남아 있어도, 정규 세션 경로에서는 `config/regular_session.env`가 우선 적용됩니다.

## 3. 왜 `.env`가 세션 튜닝의 1순위가 아닌가
`.env`를 직접 세션 튜닝용으로 쓰면 아래가 함께 흔들릴 수 있습니다.
- 수동 실행
- `scan_only` 진단
- 테스트 재현
- 과거 실험값 재현

반대로 정규 세션용 값만 `config/regular_session.env`에 두면:
- 실제 정규 세션만 안전한 운영값을 강제할 수 있고
- `.env`를 수정하지 않아도 되며
- startup 로그에서 출처가 `session config file`로 명확히 보입니다.

## 4. 정규 세션에서 기대하는 핵심 runtime-control 값
구체적인 정규 세션 타이밍/리밋 값(예: `BUY_SCAN_INTERVAL_SECONDS`, `API_MIN_INTER_REQUEST_SECONDS` 등)은 문서에 하드코딩하지 않습니다.
**Source of Truth**: `config/regular_session.env`

정규 세션 시작 시 startup 로그에는 각 항목별로 아래가 찍힙니다.
- effective value
- source category
- recommended value
- status (`OK` / `MISMATCH`)

정규 세션 경로에서 mismatch가 남아 있으면 startup sanity / runtime enforcement가 fail-fast 할 수 있습니다.

## 5. startup에서 무엇을 확인해야 하나
운영자는 `./scripts/run_session.sh --dry-run`에서 최소한 아래를 확인하세요.

- session env file 경로
- runtime-control summary
- live snapshot refresh 설정
- stdout/stderr day-scoped 로그 경로
- stop 시각

예:
```bash
./scripts/run_session.sh --dry-run
```

`scan_only` 사전 진단:
```bash
RUN_MODE=scan_only RUN_ONCE=true python3 -m app.main
```

이 진단은 외부 API/시세 조회가 발생할 수 있으므로 운영자 전용입니다. AI agent는 실행하지 않습니다.

이 경로에서는 아래를 확인할 수 있습니다.
- runtime mode (`normal` / `midday` / `degraded`)
- effective interval / cap
- snapshot source (`live_snapshot` / `settings fallback`)
- snapshot freshness 상태
- api budget 상태
- daily pnl brake 상태
- diagnostic summary

## 6. live snapshot refresh worker
정규 세션에서는 `run_session.sh`가 `app.main`만 띄우는 것이 아니라, live snapshot refresh worker도 같이 시작합니다.

동작:
- 세션 시작과 함께 worker 시작
- 목표 cadence: `180초`
- snapshot TTL: `420초`
- 세션 stop/restart와 함께 worker도 정리

관찰 포인트:
- pid file
  - `logs/kis_trader_live_snapshot.pid`
- heartbeat file
  - `logs/kis_trader_live_snapshot.heartbeat`
- 수동 점검
  - `python3 scripts/live_snapshot.py --top-n 30 --ttl 420`

운영 로그에서는 아래를 볼 수 있습니다.
- worker started/stopped
- refresh success/failure
- heartbeat stale 경고
- consecutive refresh failure 경고
- worker missing/unhealthy 경고

## 7. session lock / stop / restart 흐름
정규 세션은 single-instance lock으로 보호됩니다.

의도한 사용법:
- 예약 시작:
  - launchd `com.kis-trader.session`
- 단발 기동/재기동:
  - `launchctl kickstart -k gui/$(id -u)/com.kis-trader.session`
- 시작 전 점검:
  - `./scripts/run_session.sh --dry-run`
- 수동 중지:
  - `./scripts/stop_session.sh`

동작 요약:
- 중복 시작은 차단
- stale lock은 안전하게 reclaim
- launchd kickstart는 단발로만 사용 (`pkill`+이중 kickstart 금지)
- snapshot worker도 stop/restart와 함께 정리

읽기 전용 상태 점검:

```bash
python3 scripts/session_lock.py status --lock-path logs/kis_trader.session.lock --summary
ps -axo pid,ppid,etime,command | rg -i 'kis-trader/scripts/run_session|python .*app\.main|session_lock.py hold|live_snapshot.py'
```

주요 pid/health 파일:
- `logs/kis_trader.pid` — `app.main`
- `logs/kis_trader_session_lock.pid` — session lock holder
- `logs/kis_trader_live_snapshot.pid` — live snapshot refresh worker
- `logs/kis_trader_live_snapshot.heartbeat` — live snapshot refresh heartbeat

## 8. day-scoped 운영 로그
정규 세션의 stdout/stderr는 날짜별 파일로 저장됩니다.

예:
- `logs/app_stdout_YYYYMMDD.log`
- `logs/app_stderr_YYYYMMDD.log`

편의 경로:
- `logs/app_stdout.log`
- `logs/app_stderr.log`

이 경로는 현재 세션 날짜 파일을 가리키는 symlink입니다.

이 구조 덕분에 post-session 분석에서 날짜별로 아래를 안정적으로 셀 수 있습니다.
- `live_snapshot_usage_count`
- `stale_or_invalid fallback count`
- `HARD_STOP_READY occurrence`

## 9. 세션 종료 후 비교
baseline:
- `20260417`

비교 실행:
```bash
python3 -m app.tools.compare_runtime_quality \
  --account mock_12345678_01 \
  --baseline-date 20260417 \
  --target-date YYYYMMDD
```

이 툴은 이제 아래까지 자동으로 요약합니다.
- comparable regular-session day 여부
- confidence (`low` / `medium` / `high`)
- regression highlight
- false hard-stop / buy-scan bottleneck / live snapshot persistence verdict

## 9-1. Postrun audit — 한눈 요약 (`app.tools.orchestrate`)

세션 종료 후 **read-only**로 그날 운영 상태를 한 장의 Markdown으로 요약한다. broker/API 호출
없음, 결정적(시계/랜덤 없음), 대용량 로그는 bounded/역방향으로만 읽는다.

```bash
# stdout로 출력 (기본: read-only)
python3 -m app.tools.orchestrate postrun_audit --date YYYYMMDD --account mock_12345678_01

# 추가로 _workspace/ 에 저장 (gated write)
python3 -m app.tools.orchestrate postrun_audit --date YYYYMMDD --account mock_12345678_01 --allow-write
```

리포트 섹션:
- **Runtime Status** — EOD 빌더(`live_health_check`) + lock 파일 + stderr tail (traceback 유무)
- **Orders** — 그날 주문 submitted/succeeded/failed/blocked + `failure_category` (날짜 인식 역방향 스캔)
- **Rate Limits** — `EGW00201` backoff / `MAIN_LOOP_EXCEPTION` 카운트 (stdout tail)
- **Reconciliation** — broker-synced 포지션 수 / pending sell intent 수 (`runtime_state`)
- **Action Candidates** — 위 지표에서 결정적 규칙으로 도출된 후속 액션 (failed 주문, pending intent,
  rate-limit backoff, MAIN_LOOP_EXCEPTION 등). 신호 없으면 "no candidates"로 명시

각 섹션은 소스가 없으면 `_not collected_`로 정직하게 표기하며 수치를 지어내지 않는다.

> 비고: 운영 중(`app.main` 실행 중)에는 쓰기를 피하고 기본 read-only로 사용한다. `--allow-write`는
> 산출물을 `_workspace/`에만 쓰며 트레이딩 상태/설정을 건드리지 않는다.

## 10. 운영자가 먼저 읽을 순서
1. `README.md`의 `핵심 명령어 (Quick Reference)`
2. 이 문서 `OPERATIONS.md`
3. 시작 전:
   - `./scripts/run_session.sh --dry-run`
   - 필요 시 `RUN_MODE=scan_only RUN_ONCE=true python3 -m app.main` (외부 API/시세 조회 가능)
4. 기동/재기동:
   - `launchctl kickstart -k gui/$(id -u)/com.kis-trader.session`
5. 세션 종료 후:
   - `python3 -m app.tools.compare_runtime_quality --account mock_12345678_01 --baseline-date 20260417 --target-date YYYYMMDD`

## 11. 자주 쓰는 명령 모음
```bash
# read-only 상태 확인
launchctl list | rg 'com\.(kis-trader|kistrades)'
python3 scripts/session_lock.py status --lock-path logs/kis_trader.session.lock --summary
ps -axo pid,ppid,etime,command | rg -i 'kis-trader/scripts/run_session|python .*app\.main|session_lock.py hold|live_snapshot.py'

# 운영자 전용: 시작 전 점검 / 기동 / 중지
./scripts/run_session.sh --dry-run
launchctl kickstart -k gui/$(id -u)/com.kis-trader.session
./scripts/stop_session.sh

# 운영자 전용: 외부 API/시세 조회 가능
RUN_MODE=scan_only RUN_ONCE=true python3 -m app.main
python3 scripts/live_snapshot.py --top-n 30 --ttl 420

# 세션 종료 후 read-only 분석
python3 -m app.tools.compare_runtime_quality --account mock_12345678_01 --baseline-date 20260417 --target-date YYYYMMDD
python3 -m app.tools.orchestrate postrun_audit --date YYYYMMDD --account mock_12345678_01
```
