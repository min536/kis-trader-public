# Day1~Day5 실행 계획 재검토 (2026-06-11, 코드 실상 기준)

> 입력: `docs/todo.md` §A 5일 플랜(2026-06-10) + 코드 정찰 4축(비용 모델 / autotuner eval /
> 포트폴리오 분석 / 운영 견고화). 코드 수정 없음 — 계획 문서.
> 불변 원칙: live 전환·flag·세션 시작은 운영자 게이트. broker/order API 호출 금지.
> 코드/문서 커밋 분리. 테스트 `.venv/bin/python -m pytest -q` 100%.

## 1. Day1~Day5 현재 상태 (코드 대조)

| Day | 상태 | 근거 |
|-----|------|------|
| Day 1 리팩토링 | ✅ **DONE** (원 범위) + 확장 트랙 진행 중 | main.py 5,775→4,211 (06-10). 확장 백로그 R1·R2 완료, R3 3/8 슬라이스(S4~S8 잔여), R4~R10 미착수 ([r1_r2_core_slimming_20260611.md](r1_r2_core_slimming_20260611.md), [r3_reporting_notifications_plan.md](r3_reporting_notifications_plan.md)) |
| Day 2 비용 모델 | 🟡 **부분** — 프레임 완비 / 실측 0 | 비용 계산 중앙화 (`app/core/costs.py`, settings 5개 bps 필드, backtester 차감, scanner 게이트 3종 + 테스트). **없음**: slippage 측정 도구, 주문 레코드의 제출가/체결가 필드(실측 불가), 도구 간 정책 불일치(`shadow_watch.py:52-54`·`run_proposal_backtest.py:112-113` 하드코딩 세율 0.0023=23bps vs settings `SELL_TAX_BPS` 15bps, slippage 10bps vs 5bps) |
| Day 3 walk-forward | 🔴 **미착수** | `holdout`/`in_sample`/`out_of_sample` 개념 코드 전체 0건. 단 eval 파이프라인(`operational_backtest.py` → `screening.py` → `candidate_suggest.py`)은 순수 함수 + 테스트 완비라 플러그인 지점 명확 (`evaluate_candidate` 판정 지점 `operational_backtest.py:174`, screening 변환 `screening.py:32-74`) |
| Day 4 포트폴리오 분석 | 🟡 **부분** — 절반 존재 | `app/reporting/performance.py`에 equity curve·MDD·Sharpe/Sortino/Calmar·승률·profit factor·턴오버·대분류 attribution 기존재. **없음**: 심볼별/트리거별 PnL attribution, 심볼별 승률, 보유시간 분포, 심볼별 턴오버. `app/portfolio/`는 schema(196줄)뿐 — 분석 0 |
| Day 5 heartbeat+센티널 | 🟡 **부분** | live_snapshot worker heartbeat + staleness/TTL grace 존재(`app/market_data/live_snapshot.py:108-256` — scripts/live_snapshot.py 아님). 백테스트 모니터는 in-memory 데몬 스레드(봇 재시작 시 소멸) — R3-S5 완료로 `app/notifications/backtest_control.py:409`(`_start_backtest_completion_monitor`)로 이동(06-13 갱신, §8). **없음**: 영속 pipeline heartbeat, 데이터 품질 센티널(심볼별 신선도/결측/스프레드/이상치), 품질 경고 알림 |

## 2. 의존성 기준 재정렬 (Day 번호 해체)

순서 논리: ① 의존 없는 순수-offline 작업 먼저, ② **데이터 축적 시계가 걸린 작업**(주문 가격
캡처 — 랜딩이 늦을수록 캘리브레이션 데이터 시작이 늦음)은 소형 게이트 PR로 앞당김,
③ 프로세스/배선이 걸린 작업은 뒤로.

| 순번 | 작업 (원 Day) | 의존 | 분류 |
|------|--------------|------|------|
| W1 | Walk-forward holdout eval + screening 강등 (Day3) | 없음 — 순수 offline. 진행 중인 autotuner Stage 3/4a 게이트(B-1~B-3)의 승인 라인 품질에 즉시 기여 | Core |
| W2 | 주문 레코드 가격 캡처 필드 (Day2-a) | 없음. **landing이 빠를수록 mock 세션마다 데이터 축적** | Core + **/risk-assessment 게이트** |
| W3 | `app/portfolio/analytics.py` 순수 메트릭 (Day4-a) | 없음. R3 S6/S7(performance.py 분할)과 파일 겹침 없음(신규 모듈) — 병행 가능 | Core |
| W4 | slippage 측정 도구 + 비용 정책 단일화 (Day2-b) | W2(캡처 필드) 이후가 정식. mock 한계: mock 체결은 시장가 즉시 — **실측 캘리브레이션 수치 확정은 live-shadow 데이터 필요(운영자 게이트)**. 지금은 측정 인프라 + 정책 단일화까지 | Core (도구) + Operator (수치 확정) |
| W5 | 시장 데이터 품질 센티널 — 순수 체크 + artifact (Day5-a) | 없음 (read-only, fail-safe: v1은 알림만, 거래 차단 없음) | Core |
| W6 | pipeline heartbeat 영속화 + Slack/main wiring (Day5-b) | W5. `slack_bot.py` 모니터 클러스터 = R3-S5 추출 대상과 동일 영역 → **R3-S5와 같은 회차에 처리 권장** | Core (배선) |
| W7 | analytics → EOD/Slack/dashboard 연동 (Day4-b) | W3. C-2 대시보드 백로그의 데이터 소스 겸용 | Core (배선) |
| BG | R3 S4~S8 → R4~R10 리팩토링 트랙 | 독립 — W 트랙과 인터리브. 충돌 주의 1건: R3 S6/S7이 performance.py를 분할하므로 W7에서 performance 소비 경로를 건드릴 때는 R3 S7 이후가 깔끔 | Core (확립된 패턴) |

## 3. 작업 분류 (Core / Safe-parallel / Review gate / Operator gate)

- **Core (Fable 직접)**: W1 판정 의미론, W2 주문 흐름 필드, W3 메트릭 모듈 골격+인터페이스,
  W4 도구 본체, W5 센티널 체크 설계, W6·W7 배선 일체, R-트랙 facade 추출(가드 싸움 포함).
- **Safe-parallel (보조 모델 가능)**: §6 목록 — 문서 초안, synthetic fixture, read-only 정합 감사,
  순수 포매터(golden test 검증), 텍스트 리포트 요약.
- **Review gate (read-only 리뷰 필요 지점)**: W2 머지 전 `/risk-assessment`; W1 머지 전 강등
  규칙이 whitelist/tier/human-gate 불변임을 diff로 확인; W6 머지 전 스레드 수명주기 리뷰
  (재시작 시나리오); 각 PR 머지 전 전체 스위트 green(06-13 기준 2,245+453 subtests).
- **Operator gate (사람)**: W4 캘리브레이션 *수치 적용*(settings bps 변경은 측정 리포트 검토 후
  운영자 결정), live-shadow 세션 실행(B-3), autotuner 재승인(B-1), PR 머지 자체.

## 4. Day별 상세 계획

### W1 — Walk-forward 검증 (Day3)

| 항목 | 내용 |
|------|------|
| 수정 파일 | `app/autotuner/operational_backtest.py` (`build_operational_cadence_eval`에 train/eval 세션 분리 + `verdict_reconciliation` 필드), `app/autotuner/screening.py` (out-of-sample fail 시 pass→`inconclusive` 강등), `app/tools/autotuner_operational_backtest.py` (`--eval-sessions`/`--holdout-dir` 플래그) |
| 신규 모듈 | (선택) `app/autotuner/holdout.py` — 세션 날짜 기준 split 순수 헬퍼 |
| 테스트 | `tests/test_autotuner_operational_backtest.py` 확장(in/out 분리 verdict, 모순 감지), `tests/test_autotuner_screening.py` 확장(강등 규칙) |
| broker 없는 검증 | 전부 synthetic session-summary JSON으로 단위 테스트. CLI는 `_workspace` fixture 디렉토리 드라이런 |
| 리스크 | 낮음 — runtime 미접촉, whitelist/tier/approve 불변. 단 verdict 의미 변경은 승인 라인에 영향 → 강등은 additive 필드+보수 방향(통과를 줄이는 쪽)만 |
| 커밋 단위 | ① holdout eval(코드) ② screening 강등(코드) ③ CLI 플래그(코드) ④ `runbook_autotuner_loop.md`+`live_autotuner_proposal_schema.md` 갱신(docs) |
| 보조 모델 | fixture JSON 세트·runbook 초안 가능 / 판정·강등 의미론 불가 |

### W2+W4 — 비용 모델 캘리브레이션 (Day2)

| 항목 | 내용 |
|------|------|
| 수정 파일 | W2: 주문 이벤트 기록 호출부(주문 흐름에서 `log_order_event` raw_response에 `reference_price_krw`·`quote_at_submit` additive 필드 — **trading-critical 인접**, try/except로 기록 실패가 주문에 영향 없게). W4: `app/tools/shadow_watch.py`·`app/tools/run_proposal_backtest.py` 하드코딩 비율 → settings/공유 상수로 단일화 |
| 신규 모듈 | `app/reporting/slippage_report.py`(순수 분석: 레코드 → 분포/percentile 리포트) + `app/tools/measure_slippage.py`(CLI, bounded read) |
| 테스트 | `tests/test_slippage_report.py`(synthetic 레코드), 기존 `test_backtest_portfolio.py`에 정책 단일화 회귀 |
| broker 없는 검증 | synthetic 주문 레코드 단위 테스트; 실데이터는 read-only CLI를 tail-bounded로 드라이런 |
| 리스크 | W2가 이번 5일 작업 중 유일한 주문-경로 인접 변경 — 최소 diff·additive-only·`/risk-assessment` 게이트. **비용 비율 자체는 이번에 변경하지 않음**(측정만; 수치 적용은 운영자 게이트) |
| 커밋 단위 | ① 가격 캡처(코드, 게이트) ② slippage 리포트+CLI(코드) ③ 정책 단일화(코드) ④ 비용 모델 문서(docs) |
| 보조 모델 | 리포트 텍스트 포매터·fixture 가능 / 캡처 필드·주문 흐름 불가 |

### W3+W7 — 포트폴리오 분석 레이어 (Day4)

| 항목 | 내용 |
|------|------|
| 수정 파일 | `app/portfolio/__init__.py`(재수출). W7에서만: `app/tools/eod_report_summary.py`·`app/notifications/daily_summary.py`(핵심 지표 연동) |
| 신규 모듈 | `app/portfolio/analytics.py` — per-symbol/per-trigger 실현 PnL·승률·평균 보유시간·심볼별 턴오버·보유시간 분포(percentile)·symbol×trigger 매트릭스. (선택) `app/portfolio/trade_records.py` — order log `sell_order_succeeded` → 정규화 trade 레코드 bounded 리더 |
| 테스트 | `tests/test_portfolio_analytics.py`(synthetic trade 레코드, subTest로 지표군 일괄) |
| broker 없는 검증 | 결정적 local artifact만. equity curve/MDD는 기존 `performance.py` 재사용(이동 금지) — analytics는 **신규 합성 지표만** 담당해 중복 회피 |
| 리스크 | 낮음. **performance.py 480–733(리스크 급전 클러스터) 불가침**. R3 S6/S7과 같은 파일을 건드리지 않게 신규 모듈로만 |
| 커밋 단위 | ① 순수 메트릭(코드) ② 레코드 리더(코드) ③ EOD/Slack 연동 W7(코드, 별도) ④ docs |
| 보조 모델 | 지표 수학 일부+fixture 가능(Fable이 dataclass 인터페이스 고정 후) / EOD·Slack 배선 불가 |

### W5+W6 — 운영 견고화 + 품질 센티널 (Day5)

| 항목 | 내용 |
|------|------|
| 수정 파일 | W6: `app/notifications/backtest_control.py`(`_start_backtest_completion_monitor`가 heartbeat 파일 기록 — R3-S5 추출 완료로 slack_bot.py가 아닌 이 모듈이 대상, §8), `app/notifications/main_runtime_hooks.py`(품질 경고 알림), `app/main.py`(센티널 호출 1줄 — import-first, net-new def 금지) |
| 신규 모듈 | `app/market_data/quality_sentinel.py` — 순수 체크(심볼별 신선도, 필수 필드 결측, 가격 점프 휴리스틱) + `data/runtime/market_data_quality.json` artifact writer. 재사용 신호: `live_snapshot_worker_health()`(`app/market_data/live_snapshot.py:108`), `summarize_api_budget_state()`(`app/core/runtime_budget.py:40`), `_parse_runtime_timestamp`(`app/market_data/live_snapshot_collect.py:43`, R10 추출·stdlib-only). **제약: `app/research/` import 금지** — leaf 불변식(§8); ingest의 offline 분봉 품질 리포트와 별개 유지 |
| 테스트 | `tests/test_quality_sentinel.py`(시간 주입·synthetic snapshot), `tests/test_slack_bot.py` 확장(heartbeat 기록/정리) |
| broker 없는 검증 | 순수 체크 단위 테스트; heartbeat는 가짜 프로세스/clock 주입 |
| 리스크 | 중간 — 스레드 수명주기 + main.py wiring. 원칙: 센티널 v1은 **알림 전용(fail-safe)**, 거래 차단·degraded clamp 연동은 후속 risk-gate. 신선도/staleness 임계는 고정 상수 대신 refresh-interval/settings에서 유도(`live_snapshot_worker_health`의 `max(interval*2, 360)` 패턴) — HT 예산 프로필(4→15 r/s, env 오버라이드)에서 케이던스가 변해도 유효해야 함(§8) |
| 커밋 단위 | ① 센티널 순수 체크(코드) ② artifact+알림(코드) ③ heartbeat(코드, R3-S5와 조율) ④ main wiring(코드, 최소 diff) ⑤ docs |
| 보조 모델 | 체크 함수 후보·테스트 일부 가능 / 스레드·배선·main.py 불가 |

## 5. Trading-critical 접점 명시

| 작업 | 접점 | 처리 |
|------|------|------|
| W2 가격 캡처 | 주문 흐름 호출부 (main.py `_run_*_order_flow` 인접) | **유일한 critical-인접 변경.** additive-only, try/except 격리, `/risk-assessment` 게이트, 소형 단독 PR |
| W1 강등 규칙 | 없음(승인 라인은 offline) — 단 운영 의사결정에 영향 | 보수 방향(통과 축소)만, human gate 불변 확인 리뷰 |
| W5 센티널 | 없음(read-only) — degraded mode 연동은 의도적으로 제외 | 거래 반응 연동은 별도 후속 + risk gate |
| R6 (성능 급전 클러스터, `app/execution|risk|strategy` 리팩토링) | 전면 | **기존 결정대로 최후순위 유지** — 이번 5일 범위 밖 |
| Day2 비용 *수치* 변경 (`settings_fields.py` bps) | scanner 매수 게이트 직결 | 이번 범위 아님 — 측정 리포트 → 운영자 결정 → 별도 risk-gate PR |

## 6. 보조 모델(저성능) 병렬 투입 판단

**결론: 제한적으로 도움 — "검증이 싼 lane"에서만.** 판단 기준은 *산출물 검증 비용*:
golden test/fixture/문서처럼 기계·대조로 싸게 검증되면 위임 이득이 있고, 의미론·수명주기처럼
깊은 읽기로만 검증되면 리뷰 비용이 직접 작성 비용을 초과한다. 이 repo 특수 요인 2가지가
위임 비용을 더 키운다: ① 엄격 TDD 가드(테스트 1개씩, minimal-fix 강제)가 가드 대상 소스
편집에서 약한 모델을 반복 거부시킴 — repo 소스 편집 위임은 비효율, ② `.env`/data 읽기
hook — 보조 작업 지시문에 bounded-read 제약을 명시해야 함.

**지금 바로 맡기면 좋은 5개 (저위험·검증 싼 lane):**
1. W1용 synthetic session-summary fixture JSON 세트 생성 (스키마는 `operational_backtest.py` 기존 테스트에 정의 — 대조 검증 쉬움)
2. `live_autotuner_*` 문서군 ↔ 현행 CLI 플래그 read-only drift 감사 (목록만 산출)
3. W1 runbook/proposal-schema 문서 갱신 *초안* (Fable이 스펙 확정 후, docs 커밋 전 리뷰)
4. W4 slippage 리포트 콘솔/Slack 텍스트 포매터 (순수 문자열, golden test로 자동 검증)
5. W3용 synthetic trade-record fixture + 지표 기대값 수계산 표 (테스트 입력 데이터)

**맡기면 안 되는 5개 (위험 또는 리뷰 비용 > 작성 비용):**
1. W2 주문 레코드 가격 캡처 — 주문 흐름 인접, 실패 격리 설계 필요
2. W1 screening 강등 *의미론* — autotuner 승인 라인의 준-트레이딩 결정
3. W6 heartbeat/모니터 스레드 수명주기 + slack_bot 배선 — 재시작·orphan 시나리오 추론 필요
4. `app/main.py`/`run_cycle` wiring 일체 — boundary hook + 최소 diff 원칙
5. settings/auth의 비용 파라미터·credential 경계 변경 — `.env` 인접, scanner 게이트 직결
(+ R-트랙 facade 추출도 비추 — TDD 가드 네비게이션 자체가 고난도)

**전체 속도 판단:** 정찰(read-only Explore 병렬)은 이미 검증된 명백한 이득. 산출물 lane은
위 5개 한정으로 wall-clock 10~20% 단축이 현실적 상한 — 핵심 구현은 인터페이스 확정(Fable)
→ 보조 산출 → 테스트로 자동 검증의 단방향 파이프라인일 때만 병렬이 성립하고, 그 외에는
coordination cost가 이득을 잠식한다.

## 7. 첫 착수 3개 (소형 PR 단위)

1. **PR-1: holdout eval + screening 강등** (W1 코드 커밋 ①~③ + docs ④ 분리) — 의존 없음,
   순수 offline, autotuner 운영 게이트에 즉시 가치.
2. **PR-2: 주문 레코드 가격 캡처** (W2 단독 소형 PR) — `/risk-assessment` 통과 후. 빨리
   랜딩할수록 mock 세션마다 캘리브레이션 데이터 축적 시작.
3. **PR-3: `app/portfolio/analytics.py` v1** (W3 커밋 ①~②) — 순수 메트릭만, 연동(W7) 제외.

병행: R3 S4~S8 리팩토링 트랙은 PR 사이 간격에 인터리브 (S5는 W6와 같은 회차).
> **06-13 갱신:** R-트랙은 R6만 잔여(R3~R5·R7~R10 완료) — 위 인터리브/S5 동시 처리 권고는 소멸. Day5 영향은 §8.

## 8. 2026-06-13 갱신 — 0611plan 연구 트랙·확장 리팩토링 반영 (Day5 영향)

이 계획(06-11) 이후 0611plan 연구 트랙과 R-트랙(R4~R10)이 완료되며 Day5(W5+W6)의 전제가
다음과 같이 바뀌었다. 본문 §1·§4·§7의 해당 표는 인라인 갱신 완료.

1. **W6 대상 모듈 이동 (R3-S5 완료):** 백테스트 모니터 클러스터는 `slack_bot.py:506-573`이
   아니라 `app/notifications/backtest_control.py:409`(`_start_backtest_completion_monitor`)에
   있다. 여전히 in-memory 데몬 스레드(재시작 시 소멸)라 W6의 영속 heartbeat 필요성 자체는
   유효. "R3-S5와 같은 회차 처리" 권고는 소멸 — W6 단독 회차로 진행.
2. **W5 leaf 불변식 제약 신설 (0611plan):** `app/research/`(gate2/replay/ingest)는 leaf
   패키지 — 런타임은 research를 절대 import하지 않는다(게이트 체크 항목,
   `docs/gate2_replay_plan_20260612.md` §7). `app/research/ingest`의 offline 분봉 품질
   리포트(`build_quality_console_lines`)와 W5 런타임 센티널은 **개념이 겹치지만 의도된
   분리** — 코드 공유 금지(research는 app도 import하지 않음), 센티널은 `app/market_data/`에
   독립 구현한다.
3. **W5 임계값 설계 제약 (0611plan HT 프로필):** `docs/api_budget_high_throughput_profile_20260612.md`가
   4→8→12→15 r/s env-오버라이드 사다리를 정의했으므로, 센티널의 신선도/staleness 임계는
   기본 케이던스(4 r/s, 30종목/cycle) 가정의 고정 상수로 두면 HT 프로필에서 오탐한다.
   `live_snapshot_worker_health`의 interval-유도 패턴을 따른다.
4. **W5 재사용 헬퍼 추가 (R10):** `app/market_data/live_snapshot_collect.py`(stdlib-only)의
   `_parse_runtime_timestamp` 등 순수 파서를 센티널 신선도 체크에 재사용 가능 — 재구현 금지.
5. **W6 wiring 영역 선후 조정 (gate2 나중 todo ③):** 보류 중인 runtime shadow 기록(Stage 5,
   score_v1 불변·would_select만 기록)은 W6와 같은 wiring 영역(`main_runtime_hooks.py`,
   `app/main.py`)에 랜딩 예정 — W6(알림 전용, fail-safe)를 먼저 랜딩하고 shadow 기록은 별도
   `/risk-assessment` 게이트로 후행시킨다.
6. **번호 충돌 주의 (변경 아님):** 본 문서의 W1(autotuner holdout eval — **여전히 미착수**,
   `app/autotuner`에 holdout 0건)과 gate2 트랙의 "W1"(`app/research/gate2/weight_search.py`,
   06-12 완료)은 번호만 같은 별개 작업이다. 둘 다 walk-forward라 혼동 위험 — gate2
   weight_search 완료가 본 문서 W1을 대체하지 않는다(도메인: replay 레코드 vs autotuner
   session-summary).
