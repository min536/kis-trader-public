# R1-5: `run_cycle()` 분해 설계 (2026-06-11)

대상: app/main.py (not included in this source snapshot) `run_cycle()` — **731~3,671줄, 2,941줄** (파일 3,780줄의 78%).
이 문서는 설계만 다룬다(코드 변경 없음). 실행은 슬라이스 단위 별도 작업.

## 1. 목표와 비목표

- **목표**: run_cycle을 "phase 오케스트레이터"로 축소 — 각 phase를 좁은 입출력 계약을
  가진 모듈 함수로 추출하고, run_cycle은 호출 순서·조기 반환·공유 상태 전달만 담당.
- **비목표**: 동작 변경, 성능 개선, phase 순서 재배열, 콘솔 출력 형식 변경. 전부 무변경.
- main.py Boundary 준수: 추출은 main.py 라인 수를 줄이는 방향만 허용. main.py에
  net-new top-level def 추가 금지(훅이 차단) — 모듈에 정의하고 import.

## 2. 현재 구조 (phase 지도 요약)

| # | Phase | 라인 | 비고 |
|---|-------|------|------|
| 0 | 초기화·헤더·로컬 상태 90여 개 | 739–961 | nested fn 4종 정의 포함 (965–1043) |
| 1 | 테스트모드/장운영 세션 게이트 | 1046–1128 | 조기 반환 3경로 |
| 2 | API budget 사전 게이트 3종 | 1129–1273 | 조기 반환 (HOLD) |
| 3 | 토큰 발급 + 잔고 조회 + 정합성 | 1274–1551 | rate-limit 처리, scan-only 폴백 |
| 4 | SELL watch 루프 (보유종목 시세·규칙 평가) | 1577–2003 | 커서 회전, budget 보호, 부분처리 |
| 5 | 계좌 해석 + regime/brake 재구축 | 2005–2064 | effective buy 한도 산출 |
| 6 | SELL 의사결정 + 주문 플로우 | 2078–2410 | **주문 제출 가능 지점** |
| 7 | BUY 유니버스 해석 + pre-gating | 2424–2639 | 조기 반환 (스킵/차단) |
| 8 | BUY shallow 랭킹 + deep-eval budget cap | 2620–2783 | 조기 반환 (budget) |
| 9 | BUY 스캔 실행 + 런타임 가드 | 2703–2949 | 시세 루프, 메트릭 수집 |
| 10 | BUY 후보 선택 + cost 게이트 | 2967–3072 | 조기 반환 (차단/스캔온리) |
| 11 | BUY 주문 플로우 | 3074–3157 | **주문 제출 가능 지점** |
| 12 | 예외 분류 (rate-limit/transient) | 3158–3221 | backoff 기록 후 re-raise |
| 13 | finally: 리포팅·스냅샷·상태 저장 | 3222–3671 | ~450줄, I/O 집중 |

핵심 관찰: phase 간 결합은 대부분 **공유 가변 dict** (아래 §3)와 **phase 산출
로컬 변수** (sell_analysis_results, scan_results, portfolio_snapshot,
regime_state, selected_candidate 등)를 통해서다. 각 phase의 read/define 집합이
명확해서 phase별 추출이 구조적으로 가능하다.

## 3. 공유 가변 상태 (추출 시 반드시 참조로 전달)

| 객체 | 생성 | 변이 phase | 비고 |
|------|------|-----------|------|
| `state` (runtime state) | load_runtime_state() | 거의 전부 | finally에서 save |
| `api_budget_state` | _build_api_budget_state() | 2,3,4,5,9,12,13 | scheduler 틱 간 공유 — **동일 dict 참조 유지 필수** |
| `scheduler_state` | 호출자 | (read-only) | run_cycle은 변이하지 않음 |
| `timing_summary` | phase 0 | 0,3,4,5,9,13 | perf_counter 블록 |
| `regime_state` / `daily_pnl_brake_state` | phase 0 초기 → phase 5 재구축 | 5 | 7/9/11이 소비 |
| `observed_market_snapshots` | phase 0 | 4,9 | 13이 소비 |
| `buy_pre_gating` / `buy_scan_layered_universe` / `buy_scan_shallow_plan` / `selection_details` | phase 7~8 | 7,8,9 | 13이 직렬화 |

R2-2에서 검증한 원칙 재적용: **mutable 상태는 같은 객체를 그대로 넘긴다**
(복사 금지). 추출 함수는 이 dict들을 인자로 받아 in-place 변이한다.

## 4. 분해 방식 — phase 함수 + 결과 dataclass

R2-3 빌더 패턴의 run_cycle 버전:

- phase마다 `run_<phase>(...) -> <Phase>Result` 모듈 함수. `<Phase>Result`는
  frozen dataclass로 "이후 phase가 소비하는 로컬"만 담는다 (조기 반환 신호 포함:
  `should_return: bool` + `return_reason`).
- run_cycle 본체는: `r = run_x(ctx...)` → `if r.should_return: return` → 다음 phase.
- 공유 dict(§3)는 결과 객체에 넣지 않고 인자로 전달해 in-place 변이 유지.
- **nested fn 4종** (`note()`, `resolve_buy_universe_symbols()`,
  `log_buy_universe_source()`, `_apply_buy_flow_context()`)은 클로저 의존을
  명시 인자로 바꿔 모듈로 이동 — note()는 호출부가 많아 가장 마지막에.
- 콘솔 출력은 각 phase 함수 안에 그대로 동행 이동 (출력 순서 불변).

## 5. 슬라이스 계획 — 리스크 낮은 순서로 실행

트레이딩-크리티컬(주문 제출 경로)은 마지막. 슬라이스당 1커밋, 매 슬라이스 전체
스위트 green + 검증 프로토콜(§6) 통과 후 다음으로.

| 슬라이스 | 대상 phase | 랜딩 존 | 규모 | 리스크 |
|---------|-----------|---------|------|--------|
| S1 | 1 진입 게이트 (테스트모드/장세션) | `app/runtime/cycle_entry_gates.py` | ~80줄 | 낮음 |
| S2 | 2 API budget 사전 게이트 | `app/core/runtime_budget.py` 확장 | ~150줄 | 낮음 |
| S3 | 13 finally 리포팅/스냅샷 | `app/reporting/cycle_conclusion.py` | ~450줄 | 낮음(I/O만)·입력 多 |
| S4 | 8 deep-eval budget cap | `app/core/buy_scan_budget.py` (sell_watch_budget 대칭) | ~160줄 | 낮음 |
| S5 | 7 BUY 유니버스 + pre-gating | `app/scanner/buy_universe_resolution.py` | ~170줄 | 중간 |
| S6 | 9–10 BUY 스캔 실행 + 가드 + cost 게이트 | `app/scanner/buy_scan_execution.py` | ~280줄 | 중간 |
| S7 | 3 토큰+잔고+정합성 | `app/runtime/balance_phase.py` | ~280줄 | 중간 (rate-limit 분기 복잡) |
| S8 | 4 SELL watch 루프 | `app/runtime/sell_watch_loop.py` | ~400줄 | 중상 (커서/부분처리 상태) |
| S9 | 6 SELL 의사결정+주문 | `app/execution/sell_order_phase.py` | ~330줄 | **높음 — /risk-assessment 게이트** |
| S10 | 11 BUY 주문 플로우 호출부 + `_apply_buy_flow_context` | `app/execution/buy_order_phase.py` | ~150줄 | **높음 — /risk-assessment 게이트** |

phase 0(초기화)·5(regime 재구축)·12(예외 분류)는 run_cycle 본체에 남긴다 —
0은 모든 phase의 입력 생성부라 마지막까지 본체 소유, 12는 try/except 구조
자체라 이동 불가, 5는 이미 `app/risk/` 함수 호출 위주(~60줄)라 잔존 비용이 낮다.

완료 시 추정: run_cycle ~2,941줄 → **~500줄** (phase 호출 + 조기 반환 + 예외
프레임), main.py ~3,780줄 → ~1,300줄.

## 6. 슬라이스 공통 검증 프로토콜

1. **추출 전 특성 핀 보강**: 해당 phase의 조기 반환/출력/상태 변이를 고정하는
   특성 테스트가 없으면 먼저 추가 (기존: test_main_loop_characterization 등 9개
   파일이 run_cycle 경로를 덮음 — §7).
2. **TDD 순서**: 신규 모듈 대상 테스트(red) → 모듈 생성 → run_cycle 배선
   (seam 테스트: `app.main.run_<phase>` patch 후 반영 확인) → wide-edit으로
   본체 블록 제거.
3. **patch-target 스윕**: tests가 `app.main`에 patch/setattr하는 이름(현재 98개)
   전부 존재 확인. 추출로 이름이 모듈로 이동하면 main.py에 alias 바인딩 유지.
4. **전체 스위트 100% green** + 출력 골든 비교가 가능한 phase는 stdout 캡처 비교.
5. **트레이딩-크리티컬 슬라이스(S9/S10)**: 사전 /risk-assessment, 주문 가드
   (`evaluate_*_order_guard`) 호출 경로 byte-identical 확인, order_log 이벤트
   시퀀스 핀 고정.

## 7. 기존 테스트 커버리지 (추출 시 깨지면 안 되는 핀)

- `tests/test_main_loop_characterization.py` — main()→run_cycle 순서, 스케줄러
  파라미터, 조기 반환 경로. patch 대상: `app.main.get_settings`,
  `_build_api_budget_state`, `_build_scheduler_tick_decision`, `_is_due`,
  `_api_budget_*backoff*`, `_emit_status` 등.
- rate-limit 계열: `test_main_rate_limit_body_sources`, `test_sell_watch_rate_limit_body`
- 주문 플로우: `test_buy_order_flow_characterization`, `test_buy_order_submit_budget`,
  `test_rebalance_candidate`
- 기타: `test_autotuner_high_risk_activation`, `test_main_slack_order_hooks`,
  `test_main_note_call_arity`, `test_runtime_rate_control`

## 8. 트레이딩-크리티컬 존 (주문/리스크 상태 — 최고 주의)

1. 잔고 조회 rate-limit 처리 (1328–1372) — 오탐 시 불필요 backoff, 미탐 시 다음 사이클 패널티
2. SELL watch 시세 루프 rate-limit (1754–1951) — retry anchor/부분처리 상태
3. **SELL 주문 플로우 (2299–2410)** — `_run_sell_order_flow()` 주문 제출
4. BUY 스캔 rate-limit (2785–2836)
5. **BUY 주문 플로우 (3111–3137)** — `run_buy_order_flow()` 주문 제출 + 리밸런스
6. API budget 강제 (2641–2783) — 한도 계산 오류 = 기회 상실 or rate-limit
7. 런타임 가드 적용 (2852–2859) — 가드 오류 = 위험 거래 통과/정상 거래 차단
8. regime/brake 재구축 (2032–2059) — 자본 계산 오류 = 사이징/리듬 오류

## 9. 미해결 질문 (실행 시 결정)

- S3(finally 리포팅)의 입력이 30+개 — 단일 `CycleTelemetry` 가변 객체로 묶어
  각 phase가 채우게 할지(추가 리팩토링), 일단 인자 나열로 갈지. **권장: 1차는
  인자 나열(무변경 원칙), telemetry 객체는 후속 개선.**
- `note()` 클로저(액션 기록)는 S1~S8 동안 본체에 남기고, 호출이 모듈로 넘어가는
  슬라이스에서는 콜백 인자로 전달.
