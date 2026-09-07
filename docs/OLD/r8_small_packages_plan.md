# R8 소형 패키지 슬리밍 — 실행 위임 계획 (2026-06-12)

브랜치: `refactor/r1-core-slimming`, BASE: `3391bf4`.
방식: R5/R7과 동일 — **verbatim move + facade 재수출**, 계획(상위 모델) → TDD 실행(하위 모델 위임, 이번 라운드부터 **opus**) → 결정적 검증 게이트.

## 1. 대상과 기대 효과

| 파일 | 현재 | 목표 | 트랙 |
|------|------|------|------|
| `app/scanner/service.py` | 1,630줄 | ~530줄 | A (A1, A2) |
| `app/scanner/runtime_scan.py` | 1,013줄 | ~250줄 | B (B1, B2) |
| `app/overseas_stock/balance.py` | 767줄 | ~300줄 | C (C1, C2) |

보류(R8 잔여 백로그): `app/runtime_state.py`(687줄 — execution/risk가 직접 소비하는 주문 원장,
교차-patch 5종 밀집 → 트레이딩-크리티컬 인접으로 분류, R6 시점에 재검토),
`app/backtest/reconstruct.py`(581줄 — 효과 대비 우선순위 낮음).

## 2. 이동 금지 제약 (슬라이스 경계 하드 제약)

1. **`service.py` 진단 전역 클러스터**: `_LAST_SCAN_DIAGNOSTICS`(모듈 전역, `scan_target_symbols`가
   `global` 문으로 재바인딩), `get_last_scan_diagnostics`, `_mark_rate_limit_partial_stop`,
   `_looks_like_rate_limit_error`, `scan_target_symbols`, `_analyze_symbol_with_metrics`,
   `analyze_symbol` — **service.py 잔존**. (`ScanDiagnostics` 클래스 자체는 models로 이동 가능 —
   전역 인스턴스가 import된 클래스로 생성되면 동작 동일.)
2. **`runtime_scan.py` `get_korean_now` 호출 4종**: `count_symbol_buy_entries_today`,
   `is_symbol_in_reentry_cooldown`(+`resolve_sell_exit_reason`는 비호출이지만 sell_flow가 직접 import —
   facade로 충분), `build_buy_scan_pre_gating`, `apply_buy_runtime_guards_to_scan_results` —
   `tests/test_runtime_scan_helpers.py`가 `patch.object(runtime_scan_module, "get_korean_now")` 하므로
   **이 4종은 runtime_scan.py 잔존**.
3. **`balance.py` IO/오케스트레이터**: `_run_single_balance_attempt`(실제 API 호출),
   `probe_overseas_balance_matrix`, `probe_overseas_balance_account_matrix`, `probe_overseas_balance`
   — **balance.py 잔존**. 이동분은 전부 순수 헬퍼.
4. 트레이딩-크리티컬 표면(`app/execution|risk|strategy/`, `app/auth/settings.py`, `app/main.py`) 무수정.
   scanner는 게이트 대상이 아니지만 execution/strategy가 import하는 머니-인접 경로 —
   **facade 보존이 절대 조건** (`app/scanner/__init__.py`의 14종 재수출, `from app.scanner.service import ...`
   직접 import 5곳: reporting/console, backtest/signal_logger, execution/rebalance, execution/buy_flow(패키지 경유),
   strategy/core_shadow, backtester runner/sampling).

## 3. 슬라이스 사양

공통 규칙: 이동은 **byte-for-byte verbatim**(데코레이터·주석·공백 포함, 의미 동일 재작성 금지,
죽은 코드도 삭제 금지). 원본에는 신규 모듈로부터의 facade import를 추가하고 로컬 정의를 삭제.
이동으로 원본에서 더 이상 쓰이지 않게 된 import는 정리하되 **facade 재수출 import는 보존**.
테스트 기대값은 손으로 쓴 리터럴(모듈에서 기대값 import 금지), mock/patch 금지
(env는 monkeypatch.setenv 허용 — 실입력, SimpleNamespace/실객체 허용).

### A1 — `app/scanner/models.py` + `app/scanner/scoring.py` (커밋: `refactor(scanner): extract models and scoring clusters to models/scoring (R8-A1)`)

- **models.py** (3심볼): `SymbolAnalysisResult`(34–83), `ScanDiagnostics`(87–112), `ShallowScanCandidate`(119–135).
  imports: `from dataclasses import dataclass, field`, `from app.market_data.schema import MarketSnapshot`,
  `from app.strategy.buy_decision import BuyDecision`.
- **scoring.py** (11심볼): `_effective_buy_min_score`(217), `_effective_buy_min_passed_count`(223),
  `_clamp`(468), `_percent_score`(472), `_threshold_strength`(476), `_range_strength`(482),
  `_rule_equivalent_count`(499), `calculate_selection_score`(511–764), `_score_component_labels`(767),
  `summarize_score_breakdown`(898), `build_analysis_sort_key`(999).
  imports: `from app.auth.settings import Settings`, `from app.market_data.schema import MarketSnapshot`,
  `from app.strategy.buy_decision import BuyDecision, estimate_prev_close`,
  `from app.strategy.schema import StrategyEvaluationResult`.
- service.py facade: `from app.scanner.models import ScanDiagnostics, ShallowScanCandidate, SymbolAnalysisResult`
  + scoring 11종 재수출 import. `_LAST_SCAN_DIAGNOSTICS = ScanDiagnostics()` 할당은 import 뒤 원위치 유지.
- **테스트** `tests/test_scanner_models.py`: 핀 3종(service↔models 동일성) + 각 클래스 실생성·필드 리터럴
  (ScanDiagnostics 기본값 3개 이상).
  `tests/test_scanner_scoring.py`: 핀 11종 + `_clamp`/`_percent_score`/`_threshold_strength`/`_range_strength`/
  `_rule_equivalent_count` 손 계산 리터럴 각 2케이스 이상(경계 포함) + `build_analysis_sort_key` 튜플 리터럴 +
  `summarize_score_breakdown`/`_score_component_labels` 손 dict → 전체 출력 동등성 +
  `_effective_buy_min_*` Settings 실객체 2케이스(기존 `tests/test_selection_score_strength.py`의 구성 패턴 재사용) +
  `calculate_selection_score` 직접 스모크 1케이스. 기존 `test_selection_score_strength.py`는 잔존 핀으로 green 유지.

### A2 — `app/scanner/presentation.py` (A1 뒤 직렬. 커밋: `refactor(scanner): extract candidate presentation cluster to presentation (R8-A2)`)

- 14심볼: `_safe_price`(235), `_safe_float`(244), `_intraday_change_pct`(252), `_rebound_from_low_pct`(260),
  `build_shallow_scan_candidates`(268–355), `_buy_status_letter`(358), `build_buy_strategy_summary`(364),
  `build_candidate_reason`(368–465), `select_top_candidate`(1431), `select_top_analysis_result`(1440),
  `build_selection_reason`(1448–1493), `serialize_selection_details`(1496–1558),
  `build_universe_console_lines`(1411), `build_scan_console_lines`(1561–1630).
  imports: `from typing import Mapping`, `from app.auth.settings import Settings`,
  `from app.core.time_utils import is_snapshot_fresh`, `from app.scanner.symbol_names import get_symbol_name`,
  `from app.scanner.models import ShallowScanCandidate, SymbolAnalysisResult`,
  `from app.strategy.buy_decision import BuyDecision`, `from app.strategy.schema import StrategyEvaluationResult`.
- 잔존 `_analyze_symbol_with_metrics`는 facade 바인딩 경유 호출(이 이름들을 patch하는 테스트 없음 — 확인 완료).
- **테스트** `tests/test_scanner_presentation.py`: 핀 14종 + 소형 헬퍼 리터럴 + `build_buy_strategy_summary`
  실객체 케이스 + `build_universe_console_lines`/`build_scan_console_lines` 전체 리스트 동등성 +
  `build_selection_reason`/`serialize_selection_details` 실 SymbolAnalysisResult → 전체 문자열/dict 동등성 +
  `build_candidate_reason` 분기 3케이스 이상 + `build_shallow_scan_candidates` 손 rows → 전체 후보 동등성 +
  `select_top_*` 선택 케이스.

### B1 — `app/scanner/scan_plan.py` (커밋: `refactor(scanner): extract buy-scan planning cluster to scan_plan (R8-B1)`)

- 6심볼: `select_buy_scan_profile`(426), `_take_circular_window`(445), `build_buy_scan_layered_universe`(462–649),
  `build_buy_scan_shallow_plan`(652–765), `build_buy_scan_deep_eval_symbols`(768),
  `cap_buy_scan_deep_eval_symbols_for_api_budget`(862–904).
  imports: `from __future__ import annotations`, `import math`, `from datetime import datetime`,
  `from app.core.runtime_budget import api_budget_remaining_quotes as _api_budget_remaining_quotes,
  api_budget_remaining_requests as _api_budget_remaining_requests`,
  `from app.scanner.service import build_shallow_scan_candidates`.
- **테스트** `tests/test_scan_plan.py`: 핀 6종 + `_take_circular_window` 랩어라운드 포함 3케이스 +
  `select_buy_scan_profile` 분기 전수 + `build_buy_scan_deep_eval_symbols` 리터럴 +
  `cap_buy_scan_deep_eval_symbols_for_api_budget` 손 `api_budget_state` dict 3케이스(여유/부족/floor 작동) —
  반환 tuple+사유 dict 전체 동등성 + `build_buy_scan_layered_universe`/`build_buy_scan_shallow_plan`
  손 입력 → 층 구성·순서 전체 동등성. 기존 `tests/test_runtime_scan_helpers.py` green 유지.

### B2 — `app/scanner/scan_funnel.py` (B1 뒤 직렬. 커밋: `refactor(scanner): extract funnel reason/summary cluster to scan_funnel (R8-B2)`)

- 9심볼: `resolve_sell_exit_reason`(64), `print_buy_pre_gating_summary`(160–223),
  `normalize_pre_gating_payload`(226–292), `normalize_buy_funnel_reason`(295–344),
  `resolve_deep_eval_rejection_reason`(347), `resolve_buy_candidate_rejection_reason`(372),
  `resolve_buy_candidate_selection_outcome`(404), `print_buy_scan_stage_summary`(790–859),
  `print_buy_runtime_filter_summary`(960–1013).
  imports: `from __future__ import annotations`, `from app.strategy.reentry import normalize_exit_reason`,
  `from app.strategy.sell_decision import SellAnalysisResult`.
- `resolve_sell_exit_reason`은 `app/execution/sell_flow.py`가 runtime_scan에서 직접 import — facade 보존 필수.
- **테스트** `tests/test_scan_funnel.py`: 핀 9종 + `normalize_buy_funnel_reason` 분기 표 전수 +
  `resolve_*` 리터럴 케이스 + `resolve_sell_exit_reason` SellAnalysisResult 실객체 +
  `normalize_pre_gating_payload` 손 payload → 전체 dict 동등성 + `print_*` 3종 capsys 전체 줄 동등성
  (기대 줄은 손 리터럴).

### C1 — `app/overseas_stock/balance_parsing.py` (커밋: `refactor(overseas): extract balance parsing cluster to balance_parsing (R8-C1)`)

- 13심볼: `_parse_number`(18), `_extract_api_error`(30), `_infer_supported`(48), `_is_rate_limited_text`(64),
  `_holding_symbol`(185), `_safe_row_preview`(197), `_response_status`(219), `_normalize_holdings`(227–314),
  `_valid_account_from_status`(317), `_account_validation_details`(329), `_interpret_balance_result`(337–376),
  `_attempt_priority`(379), `_best_attempt`(402).
  imports: `from typing import Any`, `from app.auth.token import ApiHttpError`,
  `from app.overseas_stock.models import OverseasBalanceAttemptResult, OverseasHolding`.
- **테스트** `tests/test_balance_parsing.py`: 핀 13종 + `_parse_number`/`_holding_symbol`/`_response_status`/
  `_is_rate_limited_text`/`_safe_row_preview` 리터럴 + `_extract_api_error` ApiHttpError 실객체 +
  `_infer_supported`/`_interpret_balance_result`/`_valid_account_from_status`/`_account_validation_details` 분기 +
  `_normalize_holdings` 손 payload → OverseasHolding 전체 리스트 동등성 + `_attempt_priority`/`_best_attempt` 정렬.

### C2 — `app/overseas_stock/balance_attempts.py` (C1 뒤 직렬. 커밋: `refactor(overseas): extract balance attempt builders to balance_attempts (R8-C2)`)

- 11심볼: 상수 `_ACCOUNT_MATRIX_FIXED_EXCHANGE`(14), `_ACCOUNT_MATRIX_FIXED_CURRENCY`(15) +
  `_market_candidates`(69), `_currency_candidates`(76), `_tr_id_candidates`(83),
  `_build_balance_attempts`(89–114), `_build_account_context_candidates`(117–155),
  `_build_balance_account_attempts`(158–182), `_recommended_matrix_next_step`(408),
  `_recommended_account_matrix_next_step`(418), `_attempt_to_probe_result`(426–472).
  imports: `from typing import Any`, `from app.auth.settings import get_settings`,
  `from app.overseas_stock.models import OverseasBalanceAttemptResult, OverseasBalanceMatrixProbeResult,
  OverseasBalanceProbeResult, OverseasHolding, ProbeAttempt`.
- **테스트** `tests/test_balance_attempts.py`: 핀 11종(상수는 값 리터럴 동등) + `_market/_currency/_tr_id_candidates`
  전체 리스트 리터럴 + `_recommended_*` 리터럴 + `_attempt_to_probe_result` 실객체 전체 동등성 +
  get_settings 의존 3종(`_build_balance_attempts`/`_build_account_context_candidates`/`_build_balance_account_attempts`)은
  monkeypatch.setenv로 mock-scope env 구성(기존 settings 테스트 패턴) 후 전체 구조 동등성 —
  env 구성이 깨지기 쉬우면 불변식(dedup, source 순서, 고정 NASD/USD) 어서션으로 대체 가능, 핀은 필수.
  **API 호출 함수(probe_*, _run_single_balance_attempt)는 테스트에서 절대 호출 금지.**

## 4. 실행 프로토콜

- 트랙 A/B/C는 파일 비중첩 → **워크트리 3병렬**(트랙당 실행자 1, 트랙 내 슬라이스 직렬).
- 실행자 시작 의무: `git rev-parse HEAD` 보고 + `git diff HEAD 3391bf4 -- <트랙 대상 파일> tests/` 공집합 확인,
  비공집합이면 STOP·보고.
- tdd_guard 상태 파일은 워크트리 간 공유됨 — 가드가 **무관 실패**(다른 트랙 테스트명)를 이유로 거부하면:
  자기 타깃 red를 fresh 재수립(자기 테스트 파일만 재실행) 직후 같은 편집을 재시도.
- pytest는 절대경로 `$KIS_TRADER_ROOT/.venv/bin/python -m pytest`(워크트리에 .venv 없음).
- 커밋은 슬라이스당 1개, **경로 지정 add만**, NOT-mine 파일(§CLAUDE.md 컨텍스트) 절대 미포함.
- 통합: 상위 모델이 SHA cherry-pick → 결정적 AST 재검증 → 게이트 2에이전트(테스트 품질/위생) →
  전체 스위트(메인 트리, 기준 2,014 passed + 245 subtests + 신규).

## 5. 검증 게이트 (상위 모델)

1. AST verbatim: `git show 3391bf4:<path>` 기준 — 이동분 동일·원본 로컬 잔존 0·잔존분 무변경·소실 0·임의추가 0
   (`from ast import dump as ast_dump` 별칭 필수 — trading_guard 오탐 회피).
2. facade 동일성: `getattr(원본, n) is getattr(신규, n)` 전수 + `app/scanner/__init__` 14종 재수출 생존.
3. DAG: models ← scoring/presentation ← service; scan_plan/scan_funnel ← runtime_scan;
   balance_parsing/balance_attempts ← balance. 신규 모듈 소스에 원본 모듈명 문자열 없음
   (예외: scan_plan의 `from app.scanner.service import build_shallow_scan_candidates`는 BASE의 기존 의존 — 허용).
4. 테스트 품질: 계획 명시 케이스 전수 존재, 비동어반복(기대값 리터럴), 전체 동등성.
5. 위생: 스테일 import 정리 확인(facade 재수출은 비결함), NOT-mine 미오염, 커밋 격리.

## 6. 에스컬레이션

- 가드 4회 연속 거부(레시피 적용 후에도) → STOP, 부분 산출물 명세와 함께 보고.
- verbatim 이동이 불가능해 보이는 경우(숨은 의존 발견) → 수정하지 말고 STOP·보고.
- 세션 한도 중단 → 작업물은 워크트리 보존, 이어하기 실행자로 재개.

## 7. 진행표 (2026-06-12 완료)

| 슬라이스 | 상태 | 커밋 (메인 브랜치) |
|---------|------|------|
| A1 models+scoring | 완료 | dd15535 |
| A2 presentation | 완료 | f16f831 |
| B1 scan_plan | 완료 | 00c7238 |
| B2 scan_funnel | 완료 | 6775b9f |
| C1 balance_parsing | 완료 | 392647e |
| C2 balance_attempts | 완료 | c1658ec |
| C2 테스트 보강 (게이트 적발) | 완료 | 8bc25fe |
| 스테일 import 정리 (게이트 적발) | 완료 | 24e0109 |

### 결과 라인 수

| 파일 | 이전 | 이후 |
|------|------|------|
| `app/scanner/service.py` | 1,630 | 725 (models 106 + scoring 382 + presentation 463 분리) |
| `app/scanner/runtime_scan.py` | 1,013 | 204 (scan_plan 424 + scan_funnel 420 분리) |
| `app/overseas_stock/balance.py` | 767 | 333 (balance_parsing 278 + balance_attempts 196 분리) |

### 게이트 결과

1. **AST verbatim** (3391bf4 기준, 통합 후 재검증): 3원본 모두 CLEAN — service 37 = 이동 28 + 잔존 9,
   runtime_scan 19 = 이동 15 + 잔존 4, balance 28 = 이동 24 + 잔존 4. 소실 0, 임의추가 0, DAG 위반 0.
2. **facade 동일성**: 이동 67심볼 전수 `is` 동일, `app/scanner/__init__` 재수출 14종 생존.
3. **테스트 품질 게이트** (opus, read-only): 6/7 CLEAN. 적발 1건(major) — C2 테스트가 pin-only
   (스톨 실행자의 미완 산출물). → 8bc25fe로 계획 §3-C2 케이스 4그룹 전부 보강.
4. **위생 게이트** (opus, read-only): 커밋 격리·NOT-mine·트레이딩-크리티컬·가드 설정 전부 CLEAN.
   적발 — service.py 스테일 import 5건 + balance.py 2건. → 24e0109로 정리.
5. **전체 스위트**: 2,166 passed + 320 subtests, 100% green (기준 2,014+245 대비 +152/+75 전부 신규).

### 병렬 실행 기록 (재발 방지 메모)

- 워크트리 3병렬(트랙당 opus 실행자 1). Track B는 완주, Track A는 A2 코드 이동까지 끝낸 뒤 세션 한도
  중단(커밋은 상위 모델이 검증 후 수행), Track C는 C1 완주 후 C2에서 tdd_guard 교차 오염으로 STOP →
  이어하기 실행자도 스톨 → 잔여 작업(facade 편집·커밋)이 작아 상위 모델이 직접 마무리.
- tdd_guard 공유 상태 경합은 병렬 트랙 수가 줄어들면 소멸 — C2 마무리는 경합 0에서 무마찰.
- 가드는 상위 모델의 일괄 테스트 추가(18케이스 1회 Write)도 거부 — 1테스트(또는 1클래스)씩
  Edit→pytest 사이클로 통과(9사이클).
