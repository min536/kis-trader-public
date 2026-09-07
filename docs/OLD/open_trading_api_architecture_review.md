# 결론: **부분 개선** (구조 변경 불필요)

Review date: 2026-06-10  
Scope: kis-trader 통합 코드, runbook, Slack bot, pipeline wrapper, open-trading-api backtester/MCP, kis-trader 내장 `engine_backtest`.

> **구현 상태 (2026-06-10):** 본문은 리뷰 시점 스냅샷 그대로 보존.
> P0 3건 → [PR #54](https://github.com/example-user/kis-trader/pull/54) merged,
> P1 4건 → [PR #55](https://github.com/example-user/kis-trader/pull/55) merged.
> P2 잔여(heartbeat·비용모델·walk-forward)는 todo.md (not included in this source snapshot)의 5일 실행 계획 Day 2/3/5로 흡수.

현재 구조 — kis-trader가 orchestration을 소유하고, open-trading-api는 localhost-only research 서비스로 두고, Slack bot은 단일 wrapper 스크립트만 기동하는 방식 — 는 **방향이 맞다**. 경계 설계(어댑터의 allowlist subprocess, loopback 강제, 증거 도메인 분리)는 이미 수준급이다. 고쳐야 할 것은 구조가 아니라 **운영 안전장치(P0 3건)와 증거 해석 규율(P1)**이다.

---

## 1. open-trading-api 기반 backtest 자체의 적합성 판정

**판정: "시장/유니버스 레벨 proxy 연구 도구"로는 적합. "전략 엔진 검증 도구"로는 부적합 — 그리고 현재 코드는 이미 이 구분을 알고 있다.**

핵심 사실 관계:

| 축 | kis-trader 런타임 | open-trading-api backtester | 괴리 |
|---|---|---|---|
| 시간축 | 장중 cycle 기반 (세션당 ~212 cycle) | **Lean `Resolution.Daily`** (generator.py:649 (not included in this source snapshot)) | **치명적 — intraday 로직은 일봉으로 재현 불가** |
| 의사결정 코드 | `evaluate_buy_decision` 등 실제 엔진 함수 | DSL→codegen 근사 전략 (SMA/RSI) | 전략 YAML 스스로 "not a 1:1 live parity export"라고 명시 (kis_trader_core_family_approx.kis.yaml:4 (not included in this source snapshot)) |
| 비용 모델 | 실 체결 | 서버 기본: 수수료 0.015% + 매도세 0.2% + slippage 0. kis-trader `run_proposal_backtest` 경로: 수수료 0.015% + 매도세 0.23% + slippage 0.1% | 경로별 기본값 차이 있음. live 비용 모델과 1:1 동일하지 않음 |
| 가격 데이터 | 실시간 호가/체결 | KIS 일봉, 수정주가 적용 (`FID_ORG_ADJ_PRC="0"`) | OK |
| 유니버스 | layered universe + 실시간 스캔 | `.master` 현재 상장 종목 스냅샷 | **survivorship bias** — 검토 범위에서 상폐/거래정지/신규상장 보정 근거가 확인되지 않음 |

**그래서 결과를 autotuner/전략 evidence로 써도 되는가?** — **proxy로만, 그리고 이미 그렇게 강제되고 있다.** 이 부분이 이 아키텍처에서 가장 잘 설계된 지점이다:

- `run_proposal_backtest` eval은 research-domain(`family`/`param_id`)이고, `autotuner_screen`은 autotuner-domain(`{parameter,to_value}`)만 소비 → **research eval은 구조적으로 screening에 못 들어간다.**
- approved proposal은 여전히 `live_log` 교차검증(D4) 필요.
- eval에 `repo_head` provenance + "proxy evidence only" limitation이 박힌다 (실제 atp_20260610 proposal에서 확인).

이 규율을 유지하는 한 적합하다. 위험한 건 단 하나 — **사람이 Lean 결과의 sharpe/MDD verdict를 "전략이 좋아졌다"로 읽는 것.** Lean verdict는 "근사 전략의 파라미터 방향성"까지만 의미가 있다.

## 역할 분리 (3-엔진 체계로 명문화 권장)

지금 사실상 3개의 평가 엔진이 있는데, 문서상 역할 정의가 흩어져 있다:

| 엔진 | 무엇을 검증 | 신뢰 수준 |
|---|---|---|
| **open-trading-api (Lean)** | 시장 regime, 유니버스, 파라미터 *방향성* — 장기 daily 데이터 기반 비교 연구 | proxy (낮음) — 항상 교차검증 필요 |
| **kis-trader `engine_backtest`** | **실제 엔진 함수 replay** (runner.py (not included in this source snapshot)는 `evaluate_buy_decision` 등을 직접 호출) + live 로그 parity 검증 | 중간 — 일봉 근사지만 의사결정 코드는 진짜 |
| **operational eval (session summary / EOD)** | mock/live 런타임의 실측 — cadence/운영 파라미터의 유일한 1차 증거 | 높음 — autotuner D4의 기준 |

권장 규칙: **"전략 로직 변경 → engine_backtest + parity, 파라미터 방향 탐색 → open-trading-api, autotuner 승인 → operational eval + live_log"**. 이 한 줄을 runbook에 박아라.

---

## 2~3. Slack → shell pipeline → HTTP 방식, 그리고 MCP

**현재 방식 유지가 맞다.** 근거:

- **shell wrapper가 곧 감사 추적이다** — 단일 로그 파일, pid 파일, exit code, Slack thread 알림. MCP로 단계를 쪼개면 이 원자성이 깨진다.
- run_research_loop.sh:7-16 (not included in this source snapshot)에 이미 정답이 적혀 있다: MCP는 대용량 payload(equity_curve, full trades)를 LLM 컨텍스트로 반환 → 반복 파이프라인에는 비용·컨텍스트 낭비. **REST는 zero-context-cost.**

**MCP를 써야 하는 경우:** Claude Code 대화 안에서의 *대화형 탐색* — "이 후보 전략 한 번 돌려보고 equity curve 보여줘" 같은 단발 분석. `kis_mcp`의 job 비동기 패턴(`run_backtest` → `get_backtest_result_wait`)이 이 용도로 잘 만들어져 있다.
**쓰지 말아야 하는 경우:** Slack 트리거 자동 파이프라인, cron성 반복 실행, autotuner 증거 생산 경로. 여기에 MCP를 넣으면 결정성·감사성·비용 모두 나빠진다.

## 4. 실행 편의성: Docker/launchd/Makefile/wrapper 중 무엇?

**현 on-demand wrapper 유지 + `--stop-after`를 Slack 경로의 기본값으로.** 판단:

- **Docker**: 비추. Lean 엔진 + KIS API 인증 + 로컬 데이터 변환이 얽혀 있어 컨테이너화 비용이 크고, localhost 단일 사용자 연구 도구에 격리 이득이 없다.
- **launchd**: backtester를 *상시 서비스*로 쓸 때만 의미가 있는데, 사용 빈도(주기적 research)가 상시 점유를 정당화하지 않는다. 상시화하면 stale `.master` 데이터 문제만 생긴다.
- **Makefile**: 이미 wrapper가 one-command(`open_trading_api_pipeline.sh --account X`)라 추가 가치가 얇다.
- 단 하나 고칠 것: 아래 P0-2의 종료 시그널 처리.

---

## 5~6. 안전장치 점검 — 발견한 구멍

잘 되어 있는 것: pid 파일 + `os.kill(pid,0)` + zombie 체크로 single-flight 보장, loopback URL 이중 검증(bash regex + 어댑터 `is_loopback_url`), 환경변수 allowlist sanitize, 로그 tail bounded read, 실패 시 log tail을 Slack thread로 첨부.

구멍 (심각도순):

**P0-1. backtest 명령 인증이 "채널만"으로 열릴 수 있다.** slack_bot.py:501-521 (not included in this source snapshot) — `SLACK_BOT_ALLOWED_USER_IDS`가 미설정이면 통과(`not allowed_values or …`). 즉 채널에 들어올 수 있는 누구나 호스트에서 프로세스를 기동할 수 있다. 다른 SENSITIVE 명령은 "최소 1개 allowlist 설정 필수"(`configured > 0`)인데 backtest만 더 약하다. → backtest는 **user allowlist 필수**로 강화.

**P0-2. `--stop-after`의 종료가 프로세스 트리 전체를 보장하지 않는다.** open_trading_api_pipeline.sh:151-156 (not included in this source snapshot) — wrapper는 `nohup bash start.sh &`의 pid(bash)만 `kill`한다. `start.sh`에 `EXIT` trap이 있긴 하지만, `uvicorn --reload`/frontend dev server처럼 추가 자식을 만드는 프로세스 트리까지 항상 정리한다고 보기 어렵다. 실패하면 포트 8002 점유 + 다음 실행이 "already healthy"로 **오래된 코드/stale 서비스에 붙는다.** → `setsid` + process-group kill(`kill -- -$PID`)로 수정.

**P0-3. 파이프라인 전체 타임아웃이 없다.** Lean 백테스트가 hang하면 monitor 스레드는 `process.wait()`로 무한 대기, pid 파일이 살아 있어 이후 모든 backtest 명령이 `already_running`으로 거부된다. 운영자가 수동 kill하기 전까지 막힘. → wrapper에 max-runtime(`timeout` 또는 watchdog), 그리고 Slack에 `backtest status`/`backtest kill` 명령 추가.

**P1급:**
- **bot 재시작 시 완료 알림 유실** — monitor는 in-memory `process` 핸들 의존. bot이 재시작되면 파이프라인은 돌지만 완료/실패 알림이 영원히 안 온다. (pid 파일은 프로세스 종료 후 자연 해소되므로 차단은 안 됨.) → 재시작 시 pid 파일 발견하면 "monitoring lost, 로그 경로는 X" 안내라도 게시.
- **PID 재사용 오탐** — pid 파일이 명령 이름을 검증하지 않아 재부팅 후 같은 pid의 무관한 프로세스를 "already running"으로 오인 가능. pid 파일에 start-time/argv 기록.
- **master collect 단일 시도** — curl 1회 실패 = 전체 파이프라인 실패. 1회 재시도 + 실패 시 기존 `.master`로 진행 여부 옵션.
- **survivorship bias 미문서화** — eval limitation 문자열에 universe 한계 명시.
- **장시간 실행 중 진행 heartbeat 없음** — 시작/종료 알림 사이가 깜깜. n분마다 log tail 1줄 thread 게시(선택).

## 7. 책임 경계

**현재가 정답이다 — orchestration은 kis-trader가 소유한다.** open-trading-api는 "범용 백테스트 서비스"(누가 호출하든 동일), kis-trader는 "소비자이자 정책 소유자"(어댑터가 보안 경계, 파이프라인이 순서, autotuner가 증거 규율). 반대로 open-trading-api에 kis-trader 인지 로직을 넣기 시작하면(예: kis-trader 전용 endpoint) 두 레포가 양방향 결합된다 — 지금처럼 **kis-trader→open-trading-api 단방향 의존**을 유지하라. 전략 YAML 근사본이 kis-trader 쪽(backtester/strategies/ (not included in this source snapshot))에 있는 것도 올바른 배치다.

---

## 추천 아키텍처 (현행 + P0 보강, 변경 없음)

```
[Slack #project-backtester]
   @signalor backtest [date]
        │  (채널 + user allowlist 필수 ← P0-1)
        ▼
[kis-trader slack_bot] ──pid single-flight + max-runtime watchdog (P0-3)
        │ spawn (env allowlist)
        ▼
[open_trading_api_pipeline.sh]  ← orchestration 소유: kis-trader
   1. health/기동 (setsid, group-kill ← P0-2)
   2. POST /api/symbols/collect ──────────┐
   3. validate_symbol_master (report-only)│   [open-trading-api :8002]
   4. update_baselines.sh ────────────────┤   Lean Daily / DSL 근사
   5. run_research_loop.sh ───REST───────┘   = proxy evidence 전용
        │
        ▼ research-domain eval (family/param_id)
[proposal_registry]  ──(구조적으로 분리)──  [autotuner screening]
                                             ↑ operational eval + live_log만
[engine_backtest + parity] ← 전략 로직 검증은 여기 (실엔진 함수 replay)
```

---

## P0/P1/P2 작업 목록 (운영자 실행 vs AI 구현 분리)

### P0 — AI 구현 (코드 변경, TDD)

| # | 작업 | 대상 파일 | 테스트 | rollback |
|---|---|---|---|---|
| P0-1 | backtest 명령에 user allowlist 필수화 | slack_bot.py (not included in this source snapshot) `is_backtest_command_authorized` | `tests/test_slack_bot*.py`에 allowlist 미설정→거부 케이스 추가 | 커밋 revert (동작상 거부→허용으로 완화일 뿐) |
| P0-2 | backtester 기동을 `setsid`로, 종료를 process-group kill로 | open_trading_api_pipeline.sh (not included in this source snapshot) | `--dry-run` + 수동: 기동→`--stop-after`→`lsof -i :8002` 비어 있는지 | 스크립트 revert; 고아 프로세스는 수동 kill |
| P0-3 | 파이프라인 max-runtime + Slack `backtest status`/`kill` 명령 | slack_bot.py + pipeline.sh | 단위테스트(타임아웃 mock) + dry-run | revert; 기존 무제한 대기로 복귀 |

### P1 — AI 구현

| # | 작업 | 대상 |
|---|---|---|
| P1-1 | bot 재시작 시 잔존 pid 파일 감지 → "monitoring lost" 안내 게시 | slack_bot.py 기동 경로 |
| P1-2 | pid 파일에 start-time/argv 기록해 PID 재사용 오탐 제거 | slack_bot.py |
| P1-3 | master collect 1회 재시도 + stale-master 진행 옵션 | pipeline.sh |
| P1-4 | eval limitation에 survivorship/일봉/경로별 비용모델 한계 명시 + runbook에 3-엔진 역할표 추가 | run_proposal_backtest.py, runbook_open_trading_api.md (not included in this source snapshot) |

### P2 — 보류 가능

진행 heartbeat(thread log tail), 비용 모델 정책 정리(slippage/tax rate 기준), walk-forward 지원, Docker(비추 유지).

### 운영자 액션 (AI가 못 하는 것)

```sh
# 1. P0-1 배포 전, bot env에 user allowlist 설정 (process env, .env 아님)
SLACK_BOT_ALLOWED_USER_IDS=<your_member_id>
# 2. Slack 경로에 stop-after 기본 적용
SLACK_BACKTEST_STOP_AFTER=true
# 3. bot 재시작
scripts/stop_slack_bot.sh && scripts/run_slack_bot.sh
# 4. 현재 고아 backtester가 있는지 1회 점검
lsof -i :8002
```

---

리뷰 시점에는 코드 변경 없이 작성되었다 (읽기 전용 리뷰 요청대로). 이후 P0 3건은
[PR #54](https://github.com/example-user/kis-trader/pull/54), P1 4건은
[PR #55](https://github.com/example-user/kis-trader/pull/55)로 TDD 기반 구현·머지 완료. 운영자 액션
(user allowlist·stop-after env·bot 재시작)도 적용 확인됨. P2는 todo.md (not included in this source snapshot)에서 추적한다.
