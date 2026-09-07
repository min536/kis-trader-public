---
Purpose: 프로젝트의 전체 구조 및 디렉터리 역할 요약 (main.py split 완료 후 기준)
Read when: 새 기능을 추가하거나 기존 로직이 어떤 모듈에 있는지 파악할 때
Do not use for: 상세 로직 확인 (해당 파이썬 모듈 코드 참조)
---

# Architecture (TL;DR)

`kis-trader`는 관심사별 모듈로 나누어져 있습니다. `app/main.py` split이 완료되어
도메인 로직 대부분이 하위 모듈로 분리되었고, `app/main.py`는 **오케스트레이션 엔트리포인트**
역할로 정리되었습니다.

> **AI 에이전트 주의**: `app/main.py`는 약 1,447줄(Stage A+B 슬리밍 완료, 2026-07-04 `wc -l` 실측)이며 `run_cycle()` 오케스트레이터와
> 일부 클로저 의존 런타임 코드가 남아 있습니다. 전체 파일을 무작정 읽지 말고 필요한 symbol/path
> 중심으로 검색하세요. 도메인 로직은 대부분 아래의 추출된 모듈에 있습니다.

## `app/main.py`의 역할 (split 완료 후)

`app/main.py`는 다음만 담당합니다:

- **app-main 락 / 시작 검증**: `acquire_app_main_lock()` 기반 per-account fcntl 락 획득, 시작 시 검증 (`main()`)
- **세션 오케스트레이션**: 상위 세션 흐름을 `app/runtime/session_loop.py`에 위임
- **`run_cycle()` 오케스트레이터**: 한 사이클의 흐름을 조립하고, SELL/BUY 실행과 스냅샷/리포팅을 추출된 모듈에 위임
- **의도적으로 남겨진 런타임 조각**: 클로저 의존성 등으로 분리하지 않은 일부 코드 (아래 "Safe boundary" 참조)

도메인 로직 자체(분류, 사이징, 스캔, 리스크 판정 등)는 `app/main.py`에 두지 않습니다.

## 추출된 모듈 패밀리

- `app/auth/`: 인증/설정. KIS 토큰 관리(`token.py`), 설정 팩토리(`settings.py`)
- `app/core/`: 공통 도메인/유틸리티 — error 분류(`error_classification.py`), runtime budget(`runtime_budget.py`),
  reconciliation(`reconciliation.py`), 포맷터(`formatters.py`), order log(`order_log.py`),
  시간/장세션(`time_utils.py`, `market_session.py`), 스로틀(`throttle.py`), 세션 락(`session_lock.py`),
  symbol master/tags, sell-watch budget/cursor 등
- `app/runtime/`: 런타임 루프 — `session_loop.py` (scheduler timing, schedule status, runtime rate control, 반복 세션 루프)
- `app/execution/`: 체결 흐름 — `sell_flow.py`(SELL 실행), `buy_flow.py`(BUY 실행), `rebalance.py`(리밸런스 평가/프리뷰),
  `order_guard.py`, `position_sizing.py` / `sell_position_sizing.py`, `schema.py`
- `app/scanner/`: 유니버스 스캔 — `runtime_scan.py`(프리게이팅/유니버스/스캔 가드/budget cap),
  `service.py`, `symbol_names.py`
- `app/reporting/`: 런타임 스냅샷/리포팅 — `runtime_snapshots.py`(funnel/action/스냅샷 헬퍼),
  `cycle_snapshots.py`, `candidate_outcome_logger.py`, `daily_summary.py`, `performance.py`
- `app/risk/`: 리스크 — `pnl_brake.py`, `regime.py`, `guards.py`, `schema.py`
- `app/notifications/`: 외부 알림 — `slack.py`, `slack_bot.py`, `bottleneck.py`, `order_events.py`,
  `runtime_status_snapshot.py`, `main_runtime_hooks.py`
- `app/strategy/`: 매수/매도 의사결정 — `buy_decision.py`, `sell_decision.py`, `reentry.py`, `symbol_filters.py`
- `app/domestic_stock/`: KIS 국내주식 API **어댑터(leaf)** — 주문(`order.py`), 잔고(`balance.py`),
  시세(`quote.py`), orderable(`orderable.py`)
- 기타: `app/tools/`(운영/분석 유틸), `workspace/claude-design/kis-trader-v2/`(기본 read-only v2 Operator Site),
  `workspace/`(legacy 정적 Operator Console),
  `app/dashboard/`(v2 site/legacy console 공통 데이터 loader),
  `app/backtest/`, `app/market_data/`, `app/math_models/`, `app/overseas_stock/`, `app/portfolio/`

## 런타임 흐름 개요

1. `main()` — app-main 락 획득 + 시작 검증 (`app/main.py`)
2. 세션 루프 — `app/runtime/session_loop.py`가 스케줄/rate control/반복 루프를 담당
3. `run_cycle()` — 한 사이클을 오케스트레이션 (`app/main.py`)
   - SELL 실행 → `app/execution/sell_flow.py`
   - BUY 실행 → `app/execution/buy_flow.py`
   - 유니버스 스캔 → `app/scanner/runtime_scan.py`
   - 런타임 스냅샷/리포팅 → `app/reporting/runtime_snapshots.py`
   - 리스크 판정 → `app/risk/` (pnl_brake, regime, guards)

## Safe boundary — `app/main.py`에 의도적으로 남긴 것

Stage 4는 안전 경계(safe boundary) 아래에서 종료되었습니다. 아래는 분리 시 위험/비용이 커서
의도적으로 `app/main.py`에 남긴 항목입니다:

- `run_cycle()` 내부 본문과 `finally` 블록 — 다수의 mutable local, 영속화(persistence), 런타임 스키마와
  폭넓게 결합되어 있어 분리 시 회귀 위험이 큼
- `note()` — `run_cycle()` 로컬 상태에 묶인 클로저
- `resolve_buy_universe_symbols()` / `log_buy_universe_source()` — `run_cycle()` 클로저 의존
- `_api_budget_note_rate_limit` — bottleneck 소유권(rate-limit 누적/알림)이 main에 있음
- `_wait_for_execution_request_budget` — 주문 pacing(실행 요청 budget 대기) 제어

상세 경위는 split 기록 문서를 참조하세요(아래 포인터).

## 의존성 방향 규칙

- 추출된 모듈은 **`app.main`을 import 하지 않습니다** (역방향 의존 금지).
- `app.main`은 추출된 모듈을 호출/조립할 수 있습니다.
- broker-facing 모듈(`app/domestic_stock/` 등)은 **leaf 어댑터**로 유지합니다.
- 테스트는 오케스트레이션을 의도적으로 검증하는 경우가 아니면 **추출된 모듈을 직접 import** 하는 것을 선호합니다.

## 문서 포인터

- 전체 문서 색인: [docs/README.md](README.md)
- main.py split 설계+실행 기록 (완료/FROZEN): [main_split_plan.md](OLD/main_split_plan.md),
  [main_split_accelerated_execution_plan.md](OLD/main_split_accelerated_execution_plan.md)
- 대체/완료된 과거 계획: [docs/archive/README.md](archive/README.md)

상세 구현은 각 폴더 내 소스코드를 참조하세요.
