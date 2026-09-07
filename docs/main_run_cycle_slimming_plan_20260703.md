# main.py `run_cycle` 슬리밍 계획 — strangler 추출 (2026-07-03)

> **진행 상태 (2026-07-03):** Stage A **슬라이스 A1 완료** — 리포팅 헬퍼 9개(§2 표의 콘솔 3 + 빌더/직렬화 6)를 `app/reporting/console.py`·신규 `app/reporting/cycle_context.py`로 이주, main.py 4,781→4,695줄(top-level def 25→16), 신규 테스트 9개, 전체 스위트 2,887 passed green(위임 실행 + 오케스트레이터 재검증). 변경은 워킹 트리(미커밋). **Stage A2 완료 (2026-07-03)** — 이주 7/9(A2-c·A2-e 보류 재분류, §2.1), main.py 4,695→4,608(def 16→9). **Stage B 완료 (2026-07-04, Fable 설계·게이트·검토 + Opus 실행 — §3 실행 결과 원장)** — B-2 ctx 승격(1,386 리네임) → B-1 join 코어 dedup → 국면 추출 8슬라이스(F·G·H·I·J·K·L·M). 최종: **main.py 1,405줄**(시작 4,781 대비 **−3,376, −71%**), run_cycle 3,902→**~690줄**(init/클로저 배선 + lane 분기 + 국면 호출 9개 + except 분류 + finalize 호출), `app/runtime/cycle_phases/` 12모듈, 전체 스위트 **2,948 passed + 1 skipped + 719 subtests**(기준선 2,877→2,948, 회귀 0). run_cycle "<300줄 목표"는 미달 — 잔여 ~690줄의 대부분은 패치 표면 보존을 위한 국면 호출 kwargs(15~46개/호출)와 OrderGate 클로저 배선으로, CLAUDE.md의 "main.py = 배선" 원칙상 의도된 잔류. 추가 슬리밍은 특성화 테스트가 국면 모듈 패치로 이관된 후에만 안전(향후 Stage C 후보).

> **실행 위임 문서** (r4 형식, 다세션). 우선순위: **천천히** — 동작 무변경 리팩터. **trading-critical**: `run_cycle`은 주문 플로우 오케스트레이션이므로 슬라이스마다 risk-analyst 게이트 + 전체 스위트 green + 단독 커밋. tdd_guard: 테스트 1개씩.

## 0. 현실 (2026-07-03 실사)

- `app/main.py` 4,781줄. top-level def/class **25개**(CLAUDE.md의 "~120개"는 stale — 본 계획과 함께 문서 갱신).
- 지배 항목: **`run_cycle` 단일 함수 3,902줄**(754행→4656행). `main()` 125줄. 나머지 헬퍼 23개는 각 30~44줄.
- 가드레일: `trading_guard.py`는 main.py **net-new** top-level def/class만 차단 — 기존 def 삭제/이동/축소는 허용. 이 계획은 main.py 줄 수를 단조 감소시킨다.
- 참조 문서: `docs/current_cycle_pipeline.md`(사이클 국면 지도), `docs/lane_pipeline_design.md`(플래그 ON 대안 경로 — 본 계획과 독립, 통합은 비목표).

## 1. 원칙

1. **동작 무변경**: 추출은 코드 이동+파라미터화만. 조건/순서/부수효과 변경 금지.
2. **추출 전 behavior-lock**: 추출 대상 블록의 관측 가능 출력(반환 dict·프린트 라인·상태 필드)을 고정하는 테스트를 모듈 신설과 함께 작성 — "모듈을 테스트하고 main.py는 배선만".
3. **landing zones** (CLAUDE.md 표준): 프린트/콘솔 → `app/reporting/`·`app/runtime/`, 포맷 → `app/core/formatters.py`, 빌더 → 소유 패키지, API budget → `app/core/runtime_budget.py`, 세션 루프 → `app/runtime/session_loop.py`.
4. 슬라이스당: 신규 모듈 테스트 red→green → main.py 이동 배선 → 전체 스위트 green → 커밋 1개 → main.py 줄 수 보고(단조 감소 확인).

## 2. Stage A — 잔존 top-level 헬퍼 이주 (기계적, 저위험, 세션 1~2)

이동 대상과 목적지 (main.py 행번호는 2026-07-03 기준, 실행 시 재확인):

| 헬퍼 | 행 | 목적지 |
|---|---|---|
| `_print_cycle_conclusion`(604), `_print_buy_pre_gating_summary`(673), `_print_buy_runtime_filter_summary`(733) | 콘솔 | `app/reporting/console.py` (949줄 기존 — 섹션 추가) |
| `_build_position_sizing_block_context`(625), `_build_buy_analysis_block_context`(630), `_build_concentration_metrics`(635), `_build_rebalance_pair_evaluation`(657), `_serialize_rebalance_holding_option`(647), `_serialize_replacement_candidate_option`(652) | 빌더/직렬화 | `app/reporting/` 신규 `cycle_context.py` |
| `_api_budget_note_rate_limit`(506), `_wait_for_execution_request_budget`(537), `_build_runtime_rate_control`(438), `_request_metrics_delta`(396), `_buy_scan_reserve_active`(490) | 레이트/버짓 | `app/core/runtime_budget.py` |
| `_build_math_sizing_context`(375) | 사이징 컨텍스트 | `app/math_models/` 소유 모듈 |
| `_send_order_slack_notification`(322), `_write_slack_runtime_status_snapshot`(358), `_emit_status`(392) | 알림 | `app/notifications/` |
| `_build_cycle_id`(318), `_clear_daily_pnl_pause_if_expired`(578), `_build_regime_state`(582) | 상태/리스크 | `app/runtime/` / `app/risk/`(→risk 게이트) |
| `_run_sell_order_flow`(689) | **주문 플로우** | `app/execution/sell_flow.py` 인접 (→risk 게이트 필수) |

각 이동: 함수 시그니처 유지, main.py에는 `from app.x.y import _fn as _fn` 재배선(호출부 무변경) → 다음 슬라이스에서 호출부 직접 참조로 정리. private-name 유지로 외부 API 오염 방지.

### 2.1 Stage A2 실행 사양 (2026-07-03 확정 — 패치 표면 실사 완료)

**핵심 제약 — 테스트 패치 표면(늦은 바인딩) 보존.** 테스트가 헬퍼 **이름 자체**를 `app.main`에 패치하는 경우는 알리아스 재바인딩으로 보존된다(`run_cycle`이 호출 시점에 main 전역을 조회). 그러나 헬퍼 **본문이 읽는 협력자**를 main 스코프로 패치하는 테스트가 있으면, 정적 이주(목적지 직접 import)는 그 패치를 우회한다 — 이 경우 **이주 보류**. 기존 테스트 수정 금지(§5).

**보류 4건** (Stage B 국면 모듈에서 해당 테스트와 함께 의도적으로 이주):

| 헬퍼 | 보류 근거 (2026-07-03 관찰) |
|---|---|
| `_send_order_slack_notification` | `tests/test_main_slack_order_hooks.py`가 협력자 `_get_slack_notifier`를 main 스코프로 4회 패치 |
| `_wait_for_execution_request_budget` | `tests/test_buy_order_submit_budget.py:20-28` 등 3개 파일이 협력자 `_api_budget_backoff_active`/`_api_budget_transient_backoff_active`/`_api_budget_min_wait_for_request_slot`를 main 스코프 패치 |
| `_run_sell_order_flow` | `tests/test_sell_order_flow_characterization.py:280`이 협력자 `_print_sell_preview`를 main 스코프 패치 (동 파일이 이 헬퍼의 behavior-lock, 10개 호출부) |
| `_build_regime_state` | **risk 게이트가 초기 분류(이주 가능)를 반박** — `tests/test_autotuner_high_risk_activation.py:190-207`이 협력자 `_risk_build_regime_state`(:191)·`apply_high_risk_overrides`(:193)를 main 스코프 패치 후 `assert_called_once_with(..., project_root=main_module.PROJECT_ROOT, ...)` 단언. 정적 이주 시 이 테스트 파괴. 나머지 2개 참조(test_error_classification.py:358, test_buy_order_flow_characterization.py:324)는 이름 자체 패치라 알리아스-안전이지만, 1건의 협력자 패치만으로 보류 확정 |

**이주 대상 9건 — 슬라이스 6개, 순서 고정, 슬라이스당 suite green + `wc -l` 감소 보고:**

- **A2-a** → `app/core/runtime_budget.py`: `_request_metrics_delta`(순수), `_buy_scan_reserve_active`(순수). 테스트 참조 0건 확인.
  - TDD: (t1) 카테고리 델타 음수 클램프 + `elapsed_ms` 라운딩. (t2) REGULAR+reserve>0 → True / 비REGULAR 또는 not due → False.
- **A2-b** → `app/runtime/session_loop.py`: `_api_budget_note_rate_limit` → `note_rate_limit_backoff(...)`.
  - **소스텍스트 제약**: `tests/test_main_rate_limit_body_sources.py:58-62`가 이 함수의 `inspect.getsource`에 `KIS_RATE_LIMIT_BACKOFF`·`_record_bottleneck` 이름을 단언 — 목적지 모듈에서 main.py와 동일한 underscore 알리아스로 import(`record_bottleneck as _record_bottleneck`)하고 본문 verbatim 이동, 제약 명시 주석 1줄.
  - 목적지 근거: `app/core/runtime_budget.py`에 두면 core→notifications 역참조(레이어 역전) — runtime 레이어 착지. 사이클 없음 확인(runtime_alerts→core.error_classification/slack만; session_loop→runtime_budget/time_utils).
  - TDD: (t1) 연속 히트 지수 백오프 60→120→240→480→600 캡 + `backoff_until` 설정.
- **A2-c** → `app/notifications/runtime_status_snapshot.py`: `_write_slack_runtime_status_snapshot` → `write_runtime_status_snapshot_safely(...)`. 협력자 main-스코프 패치 없음 확인(이름 자체 패치 9건은 알리아스로 보존).
  - TDD: (t1) build가 raise → False 반환(예외 삼킴 계약).
- **A2-d** → `app/reporting/console.py`: `_emit_status` → `emit_status`; → `app/runtime/session_loop.py`: `_build_cycle_id` → `build_cycle_id`.
  - TDD: (t1) `[WARN] msg` 프린트 형식. (t2) cycle_id 형식 `YYYYMMDDTHHMMSS-<8hex>`.
- **A2-e** → `app/runtime/session_loop.py`: `_build_runtime_rate_control` → `build_runtime_rate_control_with_overrides(*, settings, api_budget_state, now, project_root=None)`(None→`PROJECT_ROOT` 자체 해석). 협력자 `apply_runtime_overrides`는 어떤 테스트에서도 패치 대상 문자열 아님(2026-07-03 quoted-grep 0건) — 이주 안전. `_build_regime_state`는 **risk 게이트 반박으로 보류표로 이동**(위 표 참조).
  - TDD: (t1) 오버라이드 no-op일 때 base rate control 그대로 반환.
- **A2-f** 재바인딩 2건(이주 아님 — def 삭제 + 알리아스): `_clear_daily_pnl_pause_if_expired = _risk_clear_daily_pnl_pause_if_expired`, `_build_math_sizing_context = _buy_flow.build_math_sizing_context`(키워드-온리 시그니처 동일 — `buy_flow.py:275` 확인).
  - TDD(tdd_guard 호환): 각 1건 — `main_module._x is <원본 함수>` 동일성 단언(재바인딩 전 red — 현재는 래퍼 def, 후 green).

**공통 규칙**: main.py에는 알리아스만(신규 top-level def 금지 — trading_guard 차단), 시그니처/관측 출력 무변경, 이동 본문 verbatim(포맷 정리 금지). 예상 결과: top-level def **16→7**(`_send_order_slack_notification`·`_run_session_loop`·`_wait_for_execution_request_budget`·`_build_regime_state`·`_run_sell_order_flow`·`run_cycle`·`main` 잔류), 줄수 ~4,695→~4,5xx.

**실행 결과 (2026-07-03, Opus 위임 + Fable 검증)**: A2-a/b/d/f 완료(7건 이주·재바인딩). **A2-c·A2-e는 실행 중 보류 규칙 발동** — 사전 실사가 놓친 main-스코프 협력자 패치 2건이 실재: ① `tests/test_main_slack_order_hooks.py:270-281`이 `write_slack_status_snapshot`을 **멀티라인** `mock.patch.object(main_module, ...)` 형태로 패치(단일라인 grep 회피), ② `tests/test_autotuner_runtime_activation.py:161-189`가 `mock.patch("app.main.apply_runtime_overrides", ...)` **점표기 문자열**로 패치(quoted-name grep 회피). 두 래퍼는 `main.py-boundary: allow-helper` 마커 + 보류 사유 주석과 함께 main.py 잔류(본문 원형 무변경 — Fable diff 대조 확인). **실사 교훈**: 패치 표면 grep은 멀티라인 `patch.object`와 `"app.main.X"` 점표기 문자열 패턴을 반드시 포함할 것. 최종 def 9 = 보류 6(`_send_order_slack_notification`·`_write_slack_runtime_status_snapshot`·`_build_runtime_rate_control`·`_wait_for_execution_request_budget`·`_build_regime_state`·`_run_sell_order_flow`) + 시스템 3(`_run_session_loop`·`run_cycle`·`main`). 검증: 패치 표면 회귀 세트 100 passed, 전체 스위트 2,918 passed(Fable 직접 관찰). 소스텍스트 가드(`test_main_rate_limit_body_sources.py`) green — `note_rate_limit_backoff`가 `KIS_RATE_LIMIT_BACKOFF`/`_record_bottleneck` 리터럴 보존(session_loop.py:340-367 관찰).

**risk 게이트 기록 (2026-07-03)**: CS1(S7)·CS2(A2) 모두 PROCEED-WITH-CONDITIONS. 조건 — ① S7은 `settings_fields.py`(field/env/parse_bool/kwarg) + `settings.py` 필드의 **동시 배선**(한쪽 누락 시 `settings.py:612` `**asdict(scan_cadence)` splat에서 TypeError = settings 로드 하드 브레이크), 기본 텍스트 `"false"`, TDD는 실제 `get_settings()` 구성으로 env 반영 검증(SimpleNamespace 금지 — 미배선이 조용히 통과하는 것 차단). ② A2-e `_build_regime_state` 보류 재분류(상기 표). ③ A2-b는 본문 byte-for-byte + `record_bottleneck as _record_bottleneck` 알리아스 import로 소스텍스트 가드 유지. 머니패스 판정: 어느 변경도 주문 경로/백오프 타이밍 불변.

## 3. Stage B — `run_cycle` 국면 분할 (세션 3~6, Stage A 완료 후)

1. **국면 경계 실사**: `docs/current_cycle_pipeline.md` 기준으로 run_cycle 내부의 주석/블록 경계를 행번호로 지도화(위임 첫 슬라이스 산출물 = 국면 지도 표; 이 표를 본 문서에 추기하고 상위 모델 검토 후 진행).

#### 국면 지도 (2026-07-03 실사, 커밋 baeab92 기준)

> 실사 대상: `app/main.py::run_cycle` = **행 581–4480**(다음 top-level `main()`은 4483). 워킹 트리 `app/main.py`는 baeab92 이후 커밋(5bddde3)이 docs-only라 baeab92 직후 상태와 byte-identical(`git status` clean 확인). run_cycle은 단일 함수 3,900줄. 구조 골격: **581–946 초기화/상태 로드 + 로컬 클로저 정의 → 1011 단일 최상위 `try:` 진입 → (분기/조기 return 다수) → 3810 단일 `finally:` = 리포팅/스냅샷/state-write finalize → 4480 종료**. `except`(3746–3809)는 rate-limit/transient 분류 후 re-raise. 조기 `return`은 전부 `try` 본문 안에서 발생하므로 **어느 경로로 빠져나가도 `finally` finalize를 반드시 통과**한다 — 이것이 이 함수의 지배적 제어구조다.

| # | 국면 | 행 범위 | 경계 근거 (해당 행 인용) | 관측 출력 | 조기 return | 얽힘/비고 | 기존 커버 |
|---|------|---------|--------------------------|-----------|------------|-----------|-----------|
| 0 | 사이클 초기화 · 메트릭 리셋 · budget/scheduler 세팅 | 589–630 | `589 reset_request_metrics()` / `592 cycle_budget = CycleBudget(` / `602 _print_runtime_mode(settings)` | 콘솔: runtime mode/applied settings/test mode/cycle header/engine schedule 프린트; `timing_summary["settings_scheduler"]`(679) | 없음 | `cycle_budget`·`api_budget_state`가 이후 전 국면에 흐르는 시드. 프린트 배너 6종이 한 덩어리 | 간접(모든 run_cycle 통합 테스트가 통과) |
| 1 | 계좌 스코프 sync · runtime_state 로드 · cycle_id 발급 | 631–678 | `631 account_scope_status = sync_account_scope_meta(settings)` / `647 state = load_runtime_state()` / `672 cycle_id = _build_cycle_id()` | `state[...]` 다수 필드 쓰기(account_signature/environment/last_sell_check_at 등); `[info] account scope` 프린트(633); `_write_slack_runtime_status_snapshot`(675) | 없음 | `start_cycle(state,...)`(674)로 state를 cycle-open 상태로 mutate. 이후 전 finalize가 이 `state` 객체를 공유 | 간접 |
| 2 | 국소 변수 대량 선언(약 130개 누적기/플래그) | 679–873 | `679 timing_summary: dict[...] = {` / `685 buy_scan_requested_count = 0` … `873 lane_scheduler_final_overrides = {}` | 없음(선언만) | 없음 | **추출 최대 난관**: `finally`(3810–4480)가 이 130개 변수 전부를 읽어 `state`/`timing_summary`/`cycle_snapshot`에 쓴다. 국면 함수로 쪼개려면 이 변수군을 반환 dataclass로 승격해 국면 간 전달해야 함 | 간접 |
| 3 | 로컬 클로저 정의(`note`, `record_order_gate_summary`, `order_gate_sell_order_flow`, `sell_order_flow` 배선, `resolve_buy_universe_symbols`, `log_buy_universe_source`) | 875–1009 | `875 def note(level, message)` / `901 def order_gate_sell_order_flow(**kwargs)` / `949 def resolve_buy_universe_symbols()` | `note`는 `cycle_warning_count`/`cycle_error_count` 증가(nonlocal); `sell_order_flow`는 OrderGate 경유 SELL 핸들러 | 없음 | 클로저들이 `nonlocal`로 국면2 변수(카운터·order_gate_*)를 mutate — 추출 시 클로저→모듈함수+명시 state 전달로 리라이트 필요 | 간접 |
| 4 | 셀프테스트/센티넬 조기 분기(sell_guard_selftest, sell_test) | 1011–1019 | `1011 try:` / `1012 if settings.enable_sell_guard_selftest:` / `1016 if _sell_test_active(settings):` | `cycle_environment="mock_test"` 설정 후 각 셀프테스트 함수 위임 | **있음**(1015, 1018) | 대형 `try:`(1011)가 여기서 열림 — finalize `finally` 지배구조의 시작점 | test_main_loop_characterization(간접) |
| 5 | **lane-scheduler 분기**(플래그 ON 대안 경로) | 1020–1090 | `1020 if lane_scheduler_enabled:` / `1021 lane_bridge = run_lane_scheduler_main_bridge(` / `1090 return` | `lane_scheduler_final_overrides`, sell/buy status·skip·evaluated·partial 필드 다수를 bridge 결과로 덮어씀; rate-limit 시 `_api_budget_note_rate_limit`(1040) | **있음**(1090) | ON일 때 여기서 return하지만 `finally` finalize는 통과(telemetry/스냅샷 기록). 플래그 OFF가 기본이라 아래 국면 6+가 레거시 인라인 경로 | `test_lane_vs_legacy_equivalence.py`, `test_lane_scheduler_runtime.py`, `test_lane_scheduler_no_hooks.py` |
| 6 | 세션 판정 · 경량 세션(장전/주문불가) 조기 HOLD | 1092–1165 | `1094 session_status = get_korean_market_session()` / `1114 if _should_run_light_session_cycle(...)` / `1165 return` | `state["last_market_session"]`, regime 프리셋; `_print_premarket_wait_notice`·`_print_cycle_conclusion`; `_record_cycle_action(action=WAITING_*)`(1148) | **있음**(1165) | regime_state 첫 세팅(계좌 읽기 전 DATA_INSUFFICIENT 프리셋). market_open 결정 | 간접 |
| 7 | API budget 게이트(reserve 활성화 + transient/backoff/request-budget 조기 HOLD) | 1166–1310 | `1166 buy_scan_budget_reserved = _buy_scan_reserve_active(` / `1189 if _api_budget_transient_backoff_active(...)` / `1227 if _api_budget_backoff_active(...)` / `1255` 재확인 / `1284 if not _api_budget_can_request(...)` | 4개 조기 HOLD 각각 `note("DEGRADED"...)`+`_print_cycle_conclusion`+`_record_cycle_action(HOLD_API_*)`+`_log_engine_event`; SELL-due backoff drain 시 `time.sleep`(1246) + `timing_summary["sell_watch_backoff_drain_ms"]` | **있음**(1226, 1283, 1310) | backoff drain(1227–1254)이 SELL watch를 위해 sleep한 뒤 fall-through — SELL 준비와 budget 게이트가 교차. `budget_now` 재계산으로 상태 흐름 얽힘 | `test_main_loop_characterization`(transient/backoff skip), `test_rate_limit_*` |
| 8 | 토큰 발급 · BUY quote prefetch 시작 · 잔고 조회(대형 try/except/finally) | 1311–1637 | `1314 try:` / `1316 token = issue_access_token()` / `1334 if buy_scan_due and market_open and buy_scan_separate_quote_lane:`(prefetch 시작) / `1416 balance_data = inquire_balance(token=token)` / `1616 finally:`(balance 메트릭) | `token`; `buy_quote_prefetch_future`/symbols/snapshot 세팅; `[info] BUY quote prefetch started`(1365); `portfolio_snapshot`(1469); `reconciliation_report`(1470); `timing_summary["balance_inquiry"]`(1634); 잔고 콘솔 프린트(1639–1656) | **있음**(1458 balance rate-limit, 1614 scan_only fallback) | **국면 자체가 중첩 try/except/finally**. BUY quote prefetch 시작(1334–1382)이 잔고 조회 국면 **안에** 물리적으로 박혀 있음(lane 오버랩 설계). except의 scan_only fallback(1474–1615)은 SELL+BUY 시뮬 전체를 품은 141줄 별도 경로 | `test_buy_scan_quote_account.py`, `test_scan_only_diagnostic.py`, `test_buy_scan_worker_recovery.py` |
| 9 | SELL watch 평가 루프(우선순위·커서·budget plan·per-symbol quote·rate-limit) | 1664–2065 | `1664 sell_eval_started_perf = time.perf_counter()` / `1669 if held_positions and sell_check_due:` / `1826 for position in ordered_positions:` / `1934 price_data = inquire_price(position.symbol, token=token)` | `sell_analysis_results`; `observed_market_snapshots`; `state["sell_watch_next_start_index"]`(2063); `sell_watch_*` 필드 다수; per-symbol `note`/`_log_engine_event`; rate-limit 시 telegram alert | 없음(루프 내부는 `break`/`continue`만) | **얽힘 최고 밀도**: budget-plan cap, BUY reserve 보존, quote/request 예산, rate-limit(200-OK body + ApiHttpError 두 경로), 커서 회전이 한 `for` 안에서 교차. rate-limit이 `state["sell_watch_retry_symbol"]` 쓰고 `break` | `test_sell_watch_budget.py`, `test_sell_watch_cursor.py`, `test_sell_watch_rate_limit_body.py`, `test_lane_vs_legacy(scenario3)` |
| 10 | SELL 후처리 · 계좌상태 payload · daily-pnl brake · **regime 확정** · 성과 프린트 | 2066–2199 | `2088 timing_summary["sell_evaluation"] = ...` / `2098 account_state_payload = build_account_state_payload(...)` / `2121 regime_state = _build_regime_state(...)` / `2130 effective_buy_settings = replace(settings, ...)` | `daily_pnl_brake_state`; `regime_state` 확정+`state["current_regime"]` 등; `effective_buy_settings`(regime로 조정된 BUY 예산); 성과/포지션/브레이크 프린트 5종; sell cadence 상태 텍스트(2167–2199) | 없음 | **regime 확정이 SELL 후·BUY 전에 낀 데이터 국면** — BUY 국면 전체가 `effective_buy_settings`/`regime_state`에 의존. SELL 리포팅과 리스크 상태 계산이 한 덩어리 | 간접 + `test_error_classification`(regime 경유) |
| 11 | SELL 후보 선정 · SELL 주문 실행(+scan_only 진단 BUY 경로) | 2201–2648 | `2202 selected_sell_candidate = select_top_sell_candidate(...)` / `2207 if settings.run_mode == "scan_only":`(진단 BUY 전체) / `2537 if selected_sell_candidate is not None:` / `2624 consumed_by_sell = sell_order_flow(...)` | scan_only: 진단 BUY scan+prefetch join 전체 후 `return`(2535); 정상: `sell_order_flow` 실행, `sell_risk_guard_payload`; `strict_sell_first` 시 BUY 생략 return(2646) | **있음**(2535 scan_only, 2583 sell rate-limit defer, 2646 strict-sell-first) | **scan_only 진단 BUY 경로(2244–2535)가 SELL-후보 블록 안에 292줄 통째로 중첩** — prefetch join + deep scan + ranking을 국면 8·12·13과 중복 구현. rate-limit defer(2537–2583)는 SELL을 다음 tick으로 넘기며 커서 재설정 | `test_sell_order_flow_characterization.py`, `test_scan_only_diagnostic.py` |
| 12 | BUY cadence 게이트 · budget 조기 HOLD · BUY 유니버스/레이어/pre-gating/shallow 빌드 | 2650–2915 | `2662 if not buy_scan_due:` / `2710 if cycle_budget.should_skip_stage(...)` / `2736 for line in build_universe_console_lines(...)` / `2753 buy_scan_profile_state = _select_buy_scan_profile(...)` / `2761 buy_pre_gating = _build_buy_scan_pre_gating(...)` | sell_watch rate-limit drain sleep(2658); cadence/budget skip HOLD 다수; `buy_scan_layered_universe`/`buy_pre_gating`/`buy_scan_shallow_plan`; `selection_details["staged_scan"]`; pre-gating/stage summary 프린트 | **있음**(2708 cadence, 2734 budget, 2876/2895 pre-gated, 2915 shallow empty) | sell_watch backoff drain(2650–2660)이 BUY 진입부에 또 sleep — SELL/BUY 경계 흐림. pre-gating 결과가 5가지 조기 HOLD 분기 | `test_buy_scan_budget_cap.py`(간접), pre-gating→`test_scan_only_diagnostic` |
| 13 | BUY deep-eval budget cap · request-slot guard · **prefetch join** · deep scan 실행 | 2917–3251 | `2917 scan_request_at = get_korean_now()` / `2953 _cap_buy_scan_deep_eval_symbols_for_api_budget(...)` / `3093 if buy_quote_prefetch_future is not None:`(join) / `3243 raw_scan_results = scan_target_symbols(...)` | deep_eval budget cap 프린트/state; prefetch join 메트릭 대량(`buy_quote_prefetch_*`, `buy_scan_quote_account_*`); `[info] BUY quote prefetch joined`(3142); `raw_scan_results` | **있음**(3003 budget-limited, 3066/3091 quote·request budget, 3240 prefetch 부족) | **prefetch join(3093–3214)이 국면 11의 scan_only join(2253–2377)과 거의 동일 코드** — 정상/scan_only 두 경로에 join 로직 중복. separate-lane 여부로 예산 가드 우회 분기 | `test_buy_scan_quote_account.py`, `test_buy_scan_worker_recovery.py` |
| 14 | BUY 랭킹 · 후보 선정 · scan 메트릭 집계 · scan_only 종료 · 빈 스캔 분류 | 3252–3587 | `3318 scan_results = _apply_buy_runtime_guards_to_scan_results(...)` / `3333 selected_candidate = select_top_candidate(...)` / `3427 if settings.run_mode == "scan_only":` / `3443 if selected_candidate is None:` | `scan_results`/`selected_candidate`/`top_analysis_result`; `timing_summary["buy_scan"]`; scan 콘솔 라인; 빈 스캔 시 rate-limit/benign 분류 후 HOLD 또는 `raise RuntimeError`(3533) | **있음**(3441 scan_only, 3493 rate-limit downgrade, 3532 benign, 3587 no-candidate hold) | 빈 스캔 분류(3443–3533)가 `_should_downgrade_empty_buy_scan_to_backoff`/`_is_benign_empty_buy_scan`로 rate-limit vs 정상 vs 진짜 에러를 가름 — 3533 raise가 국면 finally except로 전파 | `test_error_classification.py`(전용), `test_rate_limit_audit.py` |
| 15 | BUY 주문 실행(OrderGate 경유 · buy_flow_context 왕복) | 3589–3745 | `3589 if cycle_budget.should_skip_stage(min_remaining_seconds=2.0):` / `3644 def run_buy_order_flow_handler(_intent=None):` / `3694 OrderGate(buy_handler=...).process_ready_intents(...)` / `3725 buy_flow_result = run_buy_order_flow_handler()` | budget skip HOLD(3606); `buy_flow_result`→`buy_flow_context` 갱신 후 `_apply_buy_flow_context()`로 nonlocal(execution_snapshot/position_sizing/rebalance_*/rate_limit_source 등) 반영; 성공 시 `return`(3742) | **있음**(3606 budget, 3723 order-gate skip, 3742 정상 종료) | **리밸런스 평가는 별도 국면이 아니라 `_buy_flow.run_buy_order_flow`(3645) 내부** — run_cycle은 `rebalance_*`/`quality_rebalance_preview`를 context로 주고받기만. `_apply_buy_flow_context` 클로저(3622)가 예외 경로(3744)에서도 nonlocal 반영 후 re-raise | `test_buy_order_flow_characterization.py`(전용) |
| E | 최상위 except: rate-limit/transient 분류 후 re-raise | 3746–3809 | `3746 except Exception as exc:` / `3747 if _looks_like_rate_limit_error(exc):` / `3809 raise` | `note(ERROR/DEGRADED)`; `_api_budget_note_*`; `set_last_decision(action=CYCLE_ERROR)`(3793); `_log_engine_event(cycle_error)` | 없음(항상 `raise`) | 국면 4의 `try:`(1011) 짝. 여기서 re-raise돼도 아래 `finally`는 실행됨 | `test_error_classification`(3746 경로), `test_main_loop_characterization`(cycle exception) |
| F | **finally: 리포팅 · 스냅샷 · state-write finalize**(전 경로 공통 종착) | 3810–4480 | `3810 finally:` / `3811 if buy_quote_prefetch_future is not None:`(prefetch cleanup) / `3881 _run_cycle_market_data_quality_sentinel(...)` / `4206 cycle_snapshot = build_cycle_snapshot(...)` / `4415 state_write_ok = save_runtime_state(state)` | **국면2의 130개 변수 전부를 소비**: `timing_summary` 40+ 키; `state` 60+ 필드; `cycle_snapshot`/funnel/candidate outcomes 영속화; `_format_cycle_summary`→`state["last_cycle_result"]`; 성과/timing/API/SELL/BUY 메트릭 프린트; Slack 스냅샷; `save_runtime_state` | 없음(함수 자연 종료) | **이 함수의 최종 관측면 전체**. 어떤 조기 return도 여기로 수렴 → finalize를 국면 함수로 빼려면 앞 국면 전부가 반환 dataclass를 넘겨야 함. prefetch cleanup(3811–3877)도 국면 8·13·11의 join과 3번째 중복 | `test_cycle_snapshots.py`, `test_main_quality_sentinel_wiring.py`, 모든 통합 테스트가 finalize 통과 |

**얽힘 요약 — 추출이 가장 어려운 top 3**

1. **국면 2(변수 선언 679–873) ↔ 국면 F(finally 3810–4480)의 130-변수 결합.** run_cycle의 관측 출력은 거의 전부 `finally`에서 나오는데, 이 finally는 함수 상단에서 선언된 ~130개의 국소 누적기/플래그를 읽는다. 중간 국면들은 이 변수들을 `nonlocal` 없이 직접 대입(같은 함수 스코프)으로 채운다. 국면을 `app/runtime/cycle_phases/<phase>.py`로 빼려면 이 변수군을 **국면 간 전달용 반환 dataclass**(예: `CycleAccumulators`)로 승격해 매 국면이 받고-갱신-반환해야 한다. 이 dataclass 설계가 Stage B 전체의 임계 경로다.

2. **prefetch join/cleanup 로직 3중 중복(국면 8 시작 · 11 scan_only join · 13 정상 join · F cleanup).** BUY quote prefetch는 (a) 국면 8(1334–1382)에서 시작, (b) 국면 11 scan_only 경로(2253–2377)와 (c) 국면 13 정상 경로(3093–3214)에서 거의 동일한 `join_active_prefetch`+`prefetch_metrics_from_result` 블록으로 각각 join, (d) 국면 F(3811–3877)에서 3번째 cleanup-join. 국면 분할 전에 이 join/metrics-flatten을 단일 헬퍼(`app/pipeline/` 또는 `app/execution/`)로 먼저 합치지 않으면 국면 11·13이 같은 로직을 두 모듈에 복제하게 된다.

3. **SELL watch 루프(국면 9, 1826–2039) 내부의 예산·rate-limit·커서 교차.** 하나의 `for position in ordered_positions:` 안에서 budget-plan cap 초과 `break`, BUY reserve 보존 실패 `break`, quote/request 예산 소진 `break`, rate-limit(200-OK body 경로 1936 + `ApiHttpError` 경로 1994)이 `state["sell_watch_retry_symbol"]`을 쓰고 `break`, 정상 평가 시 `_build_sell_analysis` append가 모두 뒤엉켜 있다. 이 루프는 "SELL 평가"라는 단일 국면으로 보이지만 실제로는 rate-limit 상태 전이·커서 회전·예산 회계가 한 몸이라, 순수 함수로 분리하려면 예산 상태(`api_budget_state`)와 커서 상태를 명시적 입출력으로 끊어내는 별도 리팩터가 선행돼야 한다. (참고: 계획 §2.1의 `_run_sell_order_flow` 보류와 별개 — 그건 주문 실행이고 이건 평가 루프다.)

**계획 예상 골격과의 차이(실사 확정)**

- 계획 2항의 "**리밸런스 평가**"는 run_cycle의 독립 국면이 **아니다**. 리밸런스는 국면 15의 `_buy_flow.run_buy_order_flow`(3645) **내부**에서 수행되고, run_cycle은 `rebalance_buy_preview`/`quality_rebalance_preview`/`rebalance_selection_ms`를 `buy_flow_context`로 주고받으며 finally에서 스냅샷·timing에 기록만 한다(4025, 4223). Stage B에서 별도 리밸런스 국면 모듈을 만들 대상이 run_cycle 안에는 없다.
- 계획 2항의 "**budget/sleep 계산**"도 run_cycle 안에 **없다**. 동적 sleep(`max(0, tick_target - elapsed)`)은 세션 루프(`_run_session_loop` → `app/runtime/session_loop.py`)에 있고, run_cycle은 finally 종료(4480)로 끝난다. run_cycle 내부의 sleep은 국면 7(backoff drain 1246)·국면 11/12(sell_watch drain 2658)·throttle guard(1860, 3040)뿐이며 이들은 국면 sleep이 아니라 rate-limit 보호용이다.
- "SELL 평가·주문 → BUY 스캔·선정 → BUY 주문"이라는 선형 골격과 달리, **regime 확정(국면 10)이 SELL과 BUY 사이에 낀 데이터 국면**이고 BUY 전체가 여기서 나온 `effective_buy_settings`에 의존한다. 또 **lane-scheduler(국면 5)는 함수 앞쪽 조기 return 분기**로, 계획이 참조한 `docs/lane_pipeline_design.md`의 ON 경로가 run_cycle의 1020–1090에 물리적으로 들어와 있음을 확인했다(단, `finally` finalize는 공유).
- **거대 try 블록 위치**: 국면 4(1011)에서 열려 국면 15(3745)까지 이어지는 단일 최상위 `try:`가 함수 본문 2,700줄을 감싼다. 그 안에 국면 8의 중첩 `try/except/finally`(1314/1474/1616)와 국면 15의 중첩 `try/except`(3673/3743)가 또 있다. 조기 return 20+개가 전부 이 최상위 try 안에서 발생하므로 finalize `finally`(3810)는 **모든 경로의 단일 종착점**이다.

**상위 모델 검토 판정 (2026-07-03, Fable) — 통과.** 스팟 대조 8/8 일치(1011 `try:` · 1020/1090 lane 분기·return · 1826 SELL 루프 · 2121 regime · 2207/2535 scan_only · 3093 join · 3746 except · 3810 finally · 4415 state-write · 4483 `main()`). 정량 주장 검증: run_cycle 내 리밸런스 평가 함수 0건(언급 21건은 전부 context 왕복 — "리밸런스는 국면 아님" 확정), 국면2 변수 선언 128개(≈130 주장 일치), try 내 return 29개(≥20+ 주장 일치). **이로써 §5의 Stage B 착수 금지 조건 해제.** 검토가 부과하는 실행 순서(얽힘 요약이 근거):
- **B-1 (선행 리팩터, 추출 아님)**: prefetch join/metrics-flatten 중복 3~4곳(국면 8·11·13·F)을 단일 헬퍼로 통합. 주의 — 세 join의 가드/타임아웃 의미가 미묘하게 다를 수 있으므로 통합 전 세 블록 diff 대조 필수. 이것이 끝나기 전 국면 11·13 추출 금지(복제 방지).
- **B-2 (임계 경로 설계)**: `CycleAccumulators` 반환 dataclass 설계 — 국면2의 128개 변수 → finally 소비 지도를 먼저 만들고 상위 모델이 dataclass 분할(국면별 서브그룹)을 확정한 뒤에 추출 착수.
- **B-3+ (추출 순서)**: 저위험 게이트류(국면 6·7) → 데이터 국면(10, regime — §2.1 보류 `_build_regime_state`와 함께) → BUY 빌드(12·13·14) → SELL 평가 루프(9, 선행 리팩터 별도) → 주문 인접(11·15 — risk gate 필수).

#### Stage B 실행 사양 (2026-07-03 확정 — 아키텍처 risk 게이트 PROCEED-WITH-CONDITIONS)

**아키텍처 2기둥:**
1. **CycleContext 승격(B-2 선행)**: :679-873의 지역 누적기(정본 146필드, AST 추출) + 게이트 C3의 파라미터-교차 변수(`buy_scan_due` — :1351/:1372에서 변이, :2662에서 소비)를 `app/runtime/cycle_phases/context.py`의 dataclass 필드로 승격, run_cycle 본문 전 참조를 `ctx.<name>`으로 기계 변환(tokenize 기반 — 문자열 리터럴/키워드인자 LHS 보호). 클로저 3개(`note` :875, `record_order_gate_summary` :889, `_apply_buy_flow_context` :3622)는 **승격과 동시에** nonlocal 제거 + ctx 변이로 전환(게이트 C1 — 반쪽 이주 시 finally와 조용한 desync).
2. **deps 번들 주입(패치 표면 보존)**: 테스트가 main 스코프로 패치하는 이름 100+ (5패턴 grep 정본화). 국면 모듈은 이들을 **절대 import하지 않고**, run_cycle 진입 시점(테스트 패치 적용 후)에 main 전역을 해석한 번들을 kwargs로 받는다. 게이트 검증: mid-cycle main-attr swap 테스트 0건(전수 grep) — 진입 시점 해석은 안전. 시간 소스(`get_korean_now` 등 side_effect 시퀀스 mock 존재)는 **참조로 바인딩**(사전 호출 금지 — 호출 순서 보존).

**게이트 조건 기록**: C1(클로저 동시 전환) · C2(`effective_buy_settings` :2130 = 신규 지역, phase 10 단일 작성자 유지 + 하류 전부 ctx 경유) · C3(`buy_scan_due` ctx 승격) · C4(**B-1 join 통합은 기계적 dedup 아님** — scan_only/normal/finally 3блок이 backoff note 유무·심볼 제한 대상·`buy_scan_requested_count`·skip-reason·`buy_lane_running` 소스에서 실제로 다름. 모드 파라미터 명시 + 3-way diff-lock 선행, rate-limit 분기 통일 금지). 머니패스 4불변식(OrderGate 단일 작성자/백오프 타이밍/finalize 전경로 통과/커서 회전) 전부 위협 없음 판정 — 단 try/except/finally **골격은 run_cycle 잔류**, finally는 본문만 함수화. sell-watch 커서 :2559(phase 11 retry-index)는 phase 9/11 게이트 전 접촉 금지.

**슬라이스 순서(확정)**: B-2(ctx) → B-1(join 코어 헬퍼 — 평탄화 17대입+가드 6복사만, 분기 꼬리는 호출부 유지) → F(finalize 본문) → 6·7(게이트류) → 8(토큰/잔고/prefetch 시작+scan_only fallback) → 10(regime) → 12·13·14(BUY 빌드) → **[2차 risk 게이트]** → 9(SELL watch) → 11(SELL 주문+scan_only diag) → 15(BUY 주문). 국면 0~5·E는 run_cycle 잔류(배선은 main의 일). 조기 return 29개는 국면 outcome 신호→run_cycle에서 `return` 1:1 매핑.

#### Stage B 실행 결과 원장 (2026-07-04 완료)

| 슬라이스 | 내용 | 커밋 | main.py | 스위트 |
|---|---|---|---|---|
| B-2 | CycleContext 147필드 승격, 1,386 리네임, nonlocal 3클로저 제거 | 4122bd7 | 4,608→4,439 | 2,921 |
| B-1 | join 공통 코어 2헬퍼(가드 7복사+평탄화 16대입), C4 준수(분기 꼬리 호출부 유지) | 46c9305 | →4,363 | 2,924 |
| F | finalize_cycle(44 kw-param, 본문 byte-verbatim) — finally 골격 잔류 | eafa71a | →3,765 | 2,926 |
| G | session_gate + budget_gate (조기 return bool 규약 도입) | a701e6f | →3,590 | 2,931 |
| H | account_snapshot(토큰/prefetch시작/잔고+scan_only fallback, 중첩 try 통째) | 1e1cb0b | →3,280 | 2,933 |
| I | regime_phase (C2: effective_buy_settings 단일 작성자, replace kw-주입) | cf98cab | →3,194 | 2,935 |
| J | buy_scan_phase (12·13·14 단일 함수 — 블록 지역 공유로 승격 0) | 77f27d8 | →2,327 | 2,938 |
| K | sell_watch_phase (2차 게이트 K-1: prioritized_positions 선행 ctx 승격) | 2280557 | →1,923 | 2,940 |
| L | sell_order_phase (L-2: OrderGate 클로저 run_cycle 잔류·참조 주입; defer→sell 순서 잠금 유지) | e607bef | →1,539 | 2,944 |
| M | buy_order_phase (M-2: except 시 _apply_buy_flow_context 후 재전파 verbatim) | 282c3e6 | →**1,405** | **2,948** |

**2차 risk 게이트 기록 (주문 인접 K/L/M, 2026-07-04)**: K·L PROCEED-WITH-CONDITIONS, M PROCEED. 결정적 발견 = `prioritized_positions` K→L 교차(미승격 시 재시도 커서 조용한 파손) — K-1로 선행 승격. 적대적 탐색 결과: 이중 제출 경로 없음(OrderGate 단일 작성자 + detached 레지스트리 module-global), defer-before-sell 순서 단언 유지로 rate-limit 후 이중 SELL 위험 잠금 지속.

**소스 핀 재조준 원장 (판정 원칙: 핀은 코드를 따라간다 — 검색 문자열/대상 소스만, 단언 구조·의미 불변)**: B-2 앵커 6문자열(4파일) · F 센티넬 사슬+스냅샷 순서 분할 · G drain 사슬 · H 잔고 narrow-try 2건 · K 4건(rate-limit 2경로+budget-plan 2건) · L 2건(defer 순서 단언 모듈 소스에서 유지). 모든 재조준은 run_cycle 쪽 "국면 호출 존재" 사슬 단언을 추가해 무단절.

**잔류 구조(의도)**: 국면 0~3(init·클로저 배선)·4(셀프테스트)·5(lane 분기)·E(except 분류)는 run_cycle 잔류 — 배선은 main의 일. §2.1 보류 6 헬퍼도 잔류(그 테스트들이 main 스코프 패치를 유지하는 동안).

2. ~~예상 국면(검증 필요)~~ → 상기 국면 지도(0~15+E+F)로 대체됨(실사 확정). 추출 완료 상태는 "실행 결과 원장" 참조.
3. 국면당 1 슬라이스: `CyclePhaseContext` dataclass(입력)와 반환 dataclass를 정의해 `app/runtime/cycle_phases/<phase>.py`로 추출. 추출 전 해당 국면 behavior-lock 테스트(기존 통합 테스트로 커버되는 국면은 그 테스트를 게이트로 명시).
4. 최종 상태: `run_cycle`은 국면 함수 순차 호출 + 상태 전달만(<300줄 목표).

## 4. 검증 게이트 (슬라이스 공통)

- 전체 스위트 green (`.venv/bin/python -m pytest -q` 요약 줄 증거 보고).
- `wc -l app/main.py` 단조 감소 보고.
- 주문/리스크 인접 슬라이스(§2의 sell flow·regime, §3의 SELL/BUY 국면)는 risk-analyst 게이트 통과 기록 첨부.
- 커밋 분리: 코드 커밋과 (있다면) 문서 커밋 분리.

## 5. 에스컬레이션

- 이동 대상이 main.py 모듈 전역 상태(전역 변수, lock)를 참조하면 — 전역을 파라미터로 승격할 수 없을 때 임의 전역 재배치 금지, 해당 헬퍼 skip + 보고.
- behavior-lock 테스트가 비결정 출력(시각, 랜덤)을 만나면 seam 주입로 고정하되, seam이 런타임 기본 동작을 바꾸면 중단·보고.
- Stage B는 국면 지도 표가 상위 모델 검토를 통과하기 전에는 착수 금지.
