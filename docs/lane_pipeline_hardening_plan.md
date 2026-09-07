# Lane Pipeline 강화 계획 (2026-07-02)

> **진행 상태 (2026-07-03 실사):** S1~S6 **전부 구현·커밋 완료.** 증거 — S1: `order_gate.py` summarize에 `order_gate_blocked_detached_count`(:370), handler_timeout 후 잔여 인텐트 `budget_exceeded` decision(:213-221). S2: SellIntent TTL `max(1.0, budget.remaining_seconds())`(`runtime_adapters.py:553-565`), pre-gate 드롭 제거 + 게이트 `expired` decision(`order_gate.py:176-185`, `lane_scheduler.py:225-229`). S3: `assert_read_only_quote_target` fail-closed allowlist(커밋 03620fa·c584cb5, `tests/test_quote_lane_read_only_guard.py` 8 passed). S4: 오버랩 배선+예산 스킵 시 드레인(커밋 ebc27c5·1cca0b5, `test_lane_scheduler_no_hooks.py`의 s4 테스트 5종 — off/오버랩증명/join-timeout/예산소진/드레인). S5: ①`buy_scan_exception`(`lane_scheduler.py:355`) ②per-symbol quote-age(커밋 8075aa2) ③detached 차단 Slack 경고(커밋 960e043). S6: `tests/test_lane_vs_legacy_equivalence.py` — 시나리오 1(SELL 트리거)·3(rate-limit) 고정, 시나리오 2(BUY)는 §7 규칙대로 unpinnable 판정을 파일 내 근거와 함께 기록. §1 선행 조건(특성화 10건 실패)도 해소 — 2026-07-03 `tests/test_main_loop_characterization.py` **11 passed** 관찰. 레인 기준선은 55→**68 passed**로 성장.
> **S7도 완료 (2026-07-03 Opus 위임 + Fable 직접 검증)** — `settings_fields.py` 4좌표(field/env/parse_bool/kwarg) + `settings.py` 필드 동시 배선, 기본 텍스트 `"false"`, settings 테스트 48 passed + 86 subtests, env→필드 end-to-end `flag = True` 관찰. **이로써 본 계획의 전 슬라이스(S1~S7) 종결.** 오버랩 활성화(`config/regular_session.env`에 `BUY_SCAN_PREFETCH_OVERLAP_ENABLED=true`)는 mock 세션 관찰 후 운영자 결정으로 남음.

> **실행 위임 문서.** 2026-07-02 파이프라인 전수 리뷰(버그 7건 수정 완료: OrderGate 타임아웃 오분류·detached 핸들러 중복 주문 차단·컨텍스트 분열·쿼터 혼동 등) 후 남은 강화 항목을 상위 모델이 슬라이스로 확정했다.
> TDD 실행(실패 테스트 → 최소 구현 → 통과)은 하위 모델이 수행한다. tdd_guard 훅: **테스트 1개씩**, stub-first.
> 실행 규칙: `.venv/bin/python -m pytest`만 사용, `app.main` 직접 실행 금지, 브로커 호출 금지, 커밋 금지(운영자 승인 후).
> 판단이 필요한 상황은 §7 에스컬레이션으로 — 임의 판단 금지.

## 1. 목표와 비목표

- **목표**: (P0) 주문 게이트의 안전 불변식을 코드 수준으로 완결, (P1) SELL 평가와 BUY quote prefetch의 **실제 오버랩** 달성(현재 레인 경로는 prefetch가 동기), (P2) 운영 관측성 공백 제거, (P3) 레거시↔레인 경로 동등성 회귀 방어.
- **비목표**: 주문 실행 내부(`app/execution/buy_flow.py`/`sell_flow.py`) 로직 변경 없음. 레거시 인라인 경로 제거 없음(플래그 이중 경로 유지). `app/main.py` 신규 헬퍼 추가 금지(main.py Boundary).
- **리스크 등급**: S1–S4는 trading-critical 표면(`order_gate.py`, `buy_lane.py`) — 슬라이스당 `/risk-assessment` 관점 자체 점검 후 착수. S5–S6 LOW.
- **선행 조건**: `tests/test_main_loop_characterization.py` 10건 실패(미커밋 주문-로그-회전 블록 원인, 별도 태스크 칩 존재)를 먼저 해소하거나 known-baseline으로 명시하고 시작한다.

## 2. 현재 상태 지도 (리뷰에서 확정된 사실)

| 영역 | 사실 | 함의 |
|------|------|------|
| OrderGate | detached 핸들러 레지스트리로 전면 차단(2026-07-02 신규). 단 `blocked_detached_handler`가 요약/Slack에 미노출, handler_timeout `break` 시 잔여 인텐트 decision 미기록 | 운영자가 "주문이 왜 안 나가는지" 볼 수 없음 |
| LaneScheduler | budget 만료 후 생성된 BuyIntent를 게이트 전달 전에 조용히 드롭(`lane_scheduler.py` ready_intents 조립부) | decision 레코드 공백 |
| Intents | `BuyIntent`만 `expires_at` 보유, `SellIntent`는 TTL 없음 | 오래된 분석 기반 SELL이 이론상 무기한 유효 |
| LiveQuoteLane | read-only는 **정책 문서로만** 보장(CLAUDE.md). live 자격증명 경로에 주문 URL 차단 코드 없음 | defense-in-depth 부재 |
| BuyLane.run | `LiveQuoteLane.prefetch`를 **메인 스레드에서 동기 호출**. 비동기 컨트롤러(`BuyScanLaneController.start_quote_prefetch`/`join_active_prefetch` — 가드·TTL·done-callback 레이스 처리 완비)는 레거시 경로만 사용 | "레인 오버랩"이 레인 경로에서 미완성 — 사이클 시간 = sell + prefetch 합산 |
| 텔레메트리 | `buy_result.exception` 미노출, `quote_age_*`는 prefetch elapsed 근사, `buy_quote_prefetch_join_wait_ms` 항상 0.0 | 장애 분석 공백 |

## 3. 슬라이스 계획 (난이도 오름차순, 슬라이스당 1커밋)

### S1 — OrderGate decision/텔레메트리 완결 (캘리브레이션, P0)

- **파일**: `app/pipeline/order_gate.py`, `tests/test_order_gate.py`
- **작업**:
  1. `summarize_order_gate_result`에 `order_gate_blocked_detached_count`(status=="blocked_detached_handler" 개수) 추가.
  2. handler_timeout으로 `break`할 때 잔여 인텐트 각각에 `status="budget_exceeded"`, reason `"cycle budget exhausted after handler timeout"` decision 기록. **주의**: 기존 테스트가 `last_decision.status == "handler_timeout"`을 단언 — 해당 시나리오는 잔여 인텐트가 없어 영향 없음을 확인하고, 잔여 인텐트가 있는 새 시나리오로만 검증.
- **TDD 사양**: (t1) detached 차단 결과의 summary에 count==큐 길이. (t2) SELL 타임아웃 + BUY 대기 상태에서 decisions 길이==2, 두 번째 status=="budget_exceeded".

### S2 — SellIntent TTL + 만료 드롭 가시화 (P0)

- **파일**: `app/pipeline/runtime_adapters.py`(`build_sell_intent_from_production`), `app/pipeline/lane_scheduler.py`, 테스트는 `tests/test_lane_scheduler_no_hooks.py`
- **작업**:
  1. SELL 인텐트에 `expires_at = created_at + max(1.0, budget.remaining_seconds())` 부여(BUY와 동일 패턴, `budget` kwarg는 팩토리에 이미 전달됨).
  2. `LaneScheduler.run_cycle`의 `if buy_result.intent is not None and not budget.expired()` pre-gate 드롭 제거 — 인텐트를 게이트에 그대로 전달하고 게이트의 expired/budget 분기가 명시적 decision을 남기게 한다. **주의**: `order_gate_queue_depth` 단언 테스트 전수 grep 후 착수.
- **TDD 사양**: (t1) 팩토리 산출 SellIntent의 `expires_at`이 budget 잔여와 일치. (t2) 만료 인텐트가 게이트에서 `status="expired"` decision으로 기록(드롭 아님).

### S3 — LiveQuoteLane read-only 코드 강제 (P0, defense-in-depth)

- **파일**: `app/scanner/quote_account.py`(read-only 자격증명 요청 빌더 지점), 신규 테스트 `tests/test_quote_lane_read_only_guard.py`
- **작업**: live quote-lane 자격증명으로 나가는 요청 경로에 URL/TR-ID allowlist 가드(시세 조회 계열만 허용, 위반 시 즉시 `RuntimeError` — fail-closed). 기존 호출이 전부 allowlist 안임을 테스트로 고정.
- **TDD 사양**: (t1) 허용 시세 TR 통과. (t2) 주문 계열 TR/URL 시도 시 RuntimeError + 요청 미발신(fetch mock 호출 0회).

### S4 — BUY prefetch 비동기화: 실제 레인 오버랩 (P1, 최대 슬라이스)

- **파일**: `app/pipeline/lane_scheduler.py`, `app/pipeline/buy_lane.py`, 테스트 `tests/test_lane_scheduler_no_hooks.py`
- **설계**: `LaneScheduler.run_cycle` 시작부에서 buy_due이면 `BuyScanLaneController.start_quote_prefetch(...)`로 백그라운드 시작 → SellLane 평가 → `join_active_prefetch(timeout=budget.bounded_wait)` → 결과로 BuyIntent 팩토리 진행. 컨트롤러의 가드/TTL/`last_completed` 레이스 처리는 기존 구현·테스트를 그대로 재사용(신규 동시성 코드 작성 금지). 동기 경로는 `BUY_SCAN_PREFETCH_OVERLAP_ENABLED`(기본 false) 플래그로 보존 — 켜기 전까지 동작 불변.
- **안전 조건**: prefetch는 read-only 레인 전용(주문 오버랩 아님). join timeout 시 기존 detach 텔레메트리(`worker_detached`, guard 유지) 경로 사용.
- **TDD 사양**: (t1) 플래그 off → 기존 55개 테스트 전부 불변 통과. (t2) 플래그 on + fake clock: sell 평가 중 prefetch가 이미 시작되어 join_wait_ms < prefetch 소요(오버랩 증명). (t3) join timeout 시 BuyIntent 없음 + guard 미해제 + 다음 사이클 `previous_scan_running` 스킵. (t4) 예산 소진 시 prefetch 미시작.
- **롤아웃**: 코드 착지 후 mock 세션에서 플래그 on 관찰 → `config/regular_session.env` 반영은 운영자 결정.

### S5 — 관측성 공백 메우기 (P2, LOW)

- **작업**: ① 텔레메트리에 `buy_scan_exception`(BuyLane 예외 문자열) 추가, ② prefetch 결과에 심볼별 수집 시각을 실어 `quote_age_max/avg_ms` 실측화(S4 이후), ③ `blocked_detached_handler` 발생 시 기존 Slack 알림 경로(`send_order_slack_notification`)로 1회 경고.
- **TDD 사양**: 각 항목당 텔레메트리 키 단언 1건 + Slack mock 호출 단언 1건.

### S6 — 레거시↔레인 동등성 characterization (P3, LOW)

- **파일**: 신규 `tests/test_lane_vs_legacy_equivalence.py`
- **작업**: 동일 mock 입력(잔고/시세/후보) 아래 `LANE_SCHEDULER_ENABLED` on/off 두 경로의 **주문 결정**(주문 종류·심볼·수량 결정 인자)과 핵심 상태 키(`sell_watch_next_start_index`, 일일 카운터)가 일치함을 스냅샷으로 고정. 플래그 전환·롤백의 회귀 방어망.
- **TDD 사양**: 시나리오 3종 — 매도 트리거, 매수 후보, rate-limit 부분 평가.

### S7 — overlap 플래그 Settings 배선 (P1 마감, 2026-07-03 신설)

- **발견**: S4가 읽는 `getattr(settings, "buy_scan_prefetch_overlap_enabled", False)`(`lane_scheduler.py:111-113`)에 대응하는 `Settings` 필드와 `BUY_SCAN_PREFETCH_OVERLAP_ENABLED` env 파싱이 없다(2026-07-03 전수 grep: 플래그 문자열이 본 문서에만 존재). 테스트는 fake settings에 속성을 직접 넣어 통과하지만, 운영 활성화 경로가 없음.
- **파일**: `app/auth/settings_fields.py`(필드 :876, env 텍스트 :943, parse_bool :1044, kwargs 배선 :1166 — deadline 플래그 앵커 미러) + `app/auth/settings.py`(:484 필드 선언 미러). 둘 다 trading-critical 제어면 → risk gate. 테스트는 `tests/test_settings_fields.py`/`tests/test_auth_settings.py` 중 deadline 플래그를 다루는 쪽에 추가.
- **작업**: `buy_scan_prefetch_deadline_enabled` 패턴을 그대로 미러해 `buy_scan_prefetch_overlap_enabled: bool` + env `BUY_SCAN_PREFETCH_OVERLAP_ENABLED` 추가. **단, 기본 텍스트는 deadline의 `"true"`와 달리 `"false"`** — 동작 무변경, 활성화 경로만 개통.
- **TDD 사양**: (t1) env 부재 → False. (t2) env truthy("true"/"1") → True. (t3) 기본 mock Settings 인스턴스에 필드 존재 + False. **risk 게이트 조건(2026-07-03)**: t2/t3는 반드시 **실제 `get_settings()`(또는 실제 `Settings` 구성 경로)로 env 반영을 검증** — SimpleNamespace 수제 객체 금지. 이유: 소비처가 `getattr(..., False)` 프로브라서 `settings_fields.py` 배선 누락이 조용히 False로 떨어져 테스트만 통과하는 함정. 또한 필드는 `ScanCadenceFields`(settings_fields.py)와 `Settings`(settings.py) **양쪽에 동시 추가** — 한쪽 누락 시 `settings.py:612`의 `**asdict(scan_cadence)` splat이 TypeError로 settings 로드 자체를 깨뜨림.
- **롤아웃**: 켜는 것은 운영자 결정(S4 롤아웃 노트와 동일 — mock 세션 관찰 후 `config/regular_session.env`).

## 4. 실행 순서와 의존

S1 → S2 → S3 → S4 → S5 → S6. S5-②는 S4 이후. S6은 독립(병행 가능하나 tdd_guard 충돌 방지 위해 순차 권장). **S7은 독립 — 2026-07-03 기준 유일한 잔존 슬라이스.**

## 5. 검증 게이트 (슬라이스 공통)

1. 신규 테스트 red 확인 → 구현 → green.
2. `.venv/bin/python -m pytest tests/test_order_gate.py tests/test_lane_scheduler_no_hooks.py tests/test_lane_scheduler_runtime.py tests/test_lane_scheduler_production_wiring.py tests/test_lane_scheduler_persistence.py tests/test_lane_pipeline_simulation.py -q` 100% (현재 기준선 55 passed).
3. 슬라이스 종료 시 전체 스위트 1회(§1 선행 조건의 known-baseline 제외 100%).

## 6. 명시적 제외 (하지 않는 것)

- 사이클 간(cross-cycle) 선행 prefetch — S4 안정화 전 금지.
- OrderGate 핸들러 강제 종료 시도 — Python 스레드 특성상 불가, detach+차단 설계 유지.
- 주문 멱등성 키의 브로커 연동(KIS ord_no 대사) — 별도 트랙.

## 7. 에스컬레이션 규칙

- 기존 테스트가 슬라이스 사양과 충돌하면 **수정하지 말고 중단 후 보고**.
- trading-critical 파일에서 사양 외 변경이 필요해 보이면 중단 후 보고.
- 플래그 기본값 변경(`config/*.env`)은 실행자가 하지 않는다 — 운영자 결정.
