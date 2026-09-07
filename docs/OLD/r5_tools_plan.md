# R5: tools 구획 슬리밍 계획 (2026-06-12)

> **실행 위임 문서** (R4와 동일 체계). 구조 지도·슬라이스 경계·TDD 사양은 상위 모델이 확정했고,
> TDD 실행은 위임 실행자(Codex/하위 모델)가 수행한다. 실행자는 이 문서와
> [`r3_reporting_notifications_plan.md`](r3_reporting_notifications_plan.md) §4.5(가드 레시피)만 읽으면 된다.
> **판단이 필요한 상황은 전부 §8 에스컬레이션 규칙으로 — 임의 판단 금지.**
> 착수 직전 `BASE=$(git rev-parse HEAD)`를 기록하고 모든 슬라이스의 AST 비교 기준으로 사용한다.

## 1. 목표와 비목표

- **목표**: `app/tools/` 61파일 24,094줄 중 850줄 이상 5개 파일에서 순수 분석 클러스터를
  verbatim 이동 + facade로 분리. 무커버 분석 로직에 직접 행위 테스트 확보.
- **비목표**: 동작 변경 일절 금지. CLI 진입점(`main`/argparse)과 `python -m app.tools.<name>` 모듈 경로는
  전부 원본에 잔존(셸 스크립트 5종이 모듈 경로로 호출). 800줄 미만 파일은 비대상.
  ANSI 헬퍼 중복 통합은 R5 범위 아님(§5에 기록만).
- **리스크 등급: 전 슬라이스 LOW.** tools는 오프라인 분석 CLI — 브로커 호출 없음(kis_api_sanity_check 등은
  비대상), 트레이딩-크리티컬 표면 아님. 기존 테스트 3파일은 전부 직접 import 방식, **mock.patch 없음**
  (R3-S5류 교차 patch 위험 없음 — 단 각 슬라이스 착수 전 `grep -rn "patch" tests/test_<원본>*.py` 재확인).

## 2. 구조 지도

### 2.1 타깃 인벤토리 (공통 형태: ANSI 헬퍼 + IO 로더 + 순수 분석 + print/render + main)

| 파일 | 줄수 | 기존 테스트 | 외부 소비자 |
|------|------|------------|------------|
| analyze_core_bucket.py | 1,495 | test_analyze_core_bucket.py (104줄: `_threshold_simulation`, `_rule_lift_opportunities`, `_symbol_failure_diagnosis` 직접 import) | 없음 |
| postrun_diagnostics.py | 1,181 | 없음 | **`build_report`를 4개 툴이 import**: overnight_tuning_report, core_change_decision_report, eod_health_check, build_research_snapshot |
| run_ml_baseline_experiment.py | 999 | test_run_ml_baseline_experiment.py (`main`, `run_experiment` 실통합 — tmp 디렉토리 실행) | 없음 |
| analyze_signal_dataset.py | 901 | 없음 | 없음 |
| replay_cycles.py | 850 | test_replay_cycles.py (64줄: `_focus_line`, `_symbol_match`, `_time_match` 직접 import) | 없음 |

### 2.2 의존 방향 (전 구간 단방향 유지)

신규 모듈은 자신의 원본을 절대 import하지 않는다. postrun_report(S1)는 원본이 쓰던 크로스-툴
import(analyze_candidate_logs/analyze_budget_bottlenecks/analyze_technical_feature_activation/
validate_candidate_logs)를 **원천 모듈에서 직접** 가져온다 — 이들은 postrun_diagnostics를 import하지
않으므로 순환 없음.

## 3. 슬라이스 계획 (난이도 오름차순, 슬라이스당 1커밋)

### S1 — postrun_diagnostics 리포트 빌더 → `app/tools/postrun_report.py` (캘리브레이션)

- **이동 심볼 (6종, verbatim)**: `_compact_counter`(71-80), `_build_filtered_core_bucket_summary`(83-174),
  `build_report`(177-291), `_resolve_next_steps`(664-691), `_derive_diagnostic_conclusion`(694-777),
  `build_md_report`(904-1121)
- **잔존**: ANSI(_c/_ok/_bad/_warn/_dim/_header), `_print_*` 9종, `_wrap`, `_pct_str`,
  `print_terminal_report`, `print_json_report`, `export_md_report`(facade 경유로 build_md_report 호출), `main`.
  1,181 → ~630줄.
- **폐쇄 근거(콜그래프 검증 완료)**: `build_report → _build_filtered_core_bucket_summary/_compact_counter/
  _derive_diagnostic_conclusion/_resolve_next_steps` 내부 폐쇄. 이동 함수가 ANSI·print를 호출하는 경우 없음.
- **신규 모듈 import**: `datetime`, `Any`, `Counter` + 원본 상단의 **크로스-툴 import 블록 4개를 자구 복사**
  (analyze_candidate_logs의 KNOWN_BUCKETS/SEMANTIC_REJECTION_REASONS/STAGE_ORDER/_filter_cycle_ids/
  _filter_session/_load_rows/_log_path/run_candidate_analysis, run_budget_analysis, run_technical_activation_analysis, run_validation).
- **facade**: postrun_diagnostics.py 상단 통합 import 6종 — **4개 외부 소비자(`build_report`)가 이 facade에
  의존하므로 반드시 보존**.
- **행위 테스트 사양** (`tests/test_postrun_report.py` 신규 — 전부 무커버):
  - 핀 6종 (assertIs 루프).
  - `_compact_counter`/`_resolve_next_steps`: 직접 입출력 어서션.
  - `_derive_diagnostic_conclusion`(84L 다분기): 대표 시나리오 2건 + 전체 dict 동등성.
  - `_build_filtered_core_bucket_summary`: 최소 rows 픽스처 → 전체 dict 동등성.
  - `build_report`(115L): 크로스-툴 분석 함수들을 `patch.object(postrun_report, "run_candidate_analysis")` 등
    **신규 모듈에 patch**하고 조립 결과 전체 dict 동등성.
  - `build_md_report`(218L): 최소 report dict → 출력 마크다운 전체 문자열(또는 전체 라인 리스트) 동등성.

### S2 — analyze_core_bucket 분석 코어 → `app/tools/core_bucket_analysis.py`

- **이동 심볼 (23종, verbatim)**: `_pct`, `_safe_mean`, `_safe_median`, `_safe_float`, `_coalesce_metric`,
  `_build_candidate_detail_index`, `_enrich_rows`, `_metric_stats`, `_top_symbol_rows`, `_component_profile`,
  `_sample_rows`, `_strategy_hit_rate`, `_passed_count_distribution`, `_top_fail_patterns`,
  `_pattern_passing_rules`, `_strategy_rate_map`, `_infer_required_pass_count`, `_rule_contrast_interpretation`,
  `_threshold_simulation`, `_rule_lift_opportunities`, `_symbol_failure_diagnosis`, `_shadow_analysis`,
  `_build_human_summary`
- **잔존**: ANSI 4종, IO(`_candidate_log_path`/`_cycle_snapshots_path`/`_load_rows`/`_filter_session`),
  `run_analysis`(149L 오케스트레이터 — facade 경유로 분석 클러스터 호출), `print_terminal` + `_print_*` 4종 +
  `_format_metric_row`, `main`. 1,495 → ~800줄.
- **폐쇄 근거**: 이동 집합 내부 호출만 (`_coalesce_metric→_safe_float`, `_metric_stats→_safe_mean/_safe_median`,
  `_component_profile→_metric_stats/_pct`, `_rule_lift_opportunities/_symbol_failure_diagnosis→
  _pattern_passing_rules/_strategy_rate_map/_safe_mean`, `_shadow_analysis→_pct`). ANSI·IO 참조 없음.
- **신규 모듈 import**: `Any`, `Counter`, `defaultdict`, `statistics`.
- **기존 테스트**: test_analyze_core_bucket.py가 이동 대상 3종을 원본에서 import — facade가 보존하므로 무수정.
- **행위 테스트 사양** (`tests/test_core_bucket_analysis.py` 신규):
  - 핀 23종.
  - 기커버 3종(threshold/rule_lift/symbol_failure)은 신규 행위 테스트 생략 가능(기존 스위트가 fake를 잡음).
  - 나머지 20종: 소형(_pct/_safe_*/_coalesce_metric 등) 직접 어서션; 다분기 빌더
    (`_build_candidate_detail_index` 85L, `_component_profile` 63L, `_rule_contrast_interpretation` 96L,
    `_build_human_summary` 47L 등)는 최소 rows 픽스처 → 전체 dict/list 동등성.

### S3 — run_ml_baseline_experiment ML 코어 → `app/tools/ml_baseline_core.py`

- **이동 심볼 (20종, verbatim — 클래스 3 포함)**: `FoldData`, `EncodedDataset`, `PureLogisticRegression`,
  `_bool_int`, `_value_as_numeric`, `_build_fold_ranges`, `_filter_rows_for_label`, `_encode_rows`,
  `_split_requested_features`, `_precision_recall_curve_area`, `_roc_auc_score`, `_classification_metrics`,
  `_choose_threshold`, `_summarize_importances`, `_aggregate_feature_summary`, `_mean_metric`, `_std_metric`,
  `_build_model`, `_fit_and_score_fold`, `_aggregate_results`
- **잔존**: IO 로더(`_load_folds_from_directory`/`_load_folds_from_labeled_dataset` — facade 경유로
  FoldData/_build_fold_ranges/_filter_rows_for_label 사용), `run_experiment`, `render_markdown`,
  `_default_output_path`, `_print_summary`, `main`. 999 → ~400줄.
- **폐쇄 근거**: `_fit_and_score_fold → FoldData/_build_model/_choose_threshold/_classification_metrics/
  _encode_rows/_filter_rows_for_label/_summarize_importances`, `_aggregate_results → _aggregate_feature_summary/
  _mean_metric/_std_metric`, `_encode_rows → EncodedDataset/_split_requested_features/_value_as_numeric` — 전부 폐쇄.
- **신규 모듈 import**: `math`, `dataclass`, `Any, Iterable`, `numpy as np` +
  `from app.tools.ml_research_common import (LABEL_DEFINITIONS, ML_SAFE_CATEGORICAL_FEATURE_COLS,
  ML_SAFE_NUMERIC_FEATURE_COLS, normalize_null, parse_bool, parse_float)` (원본 import 블록에서 필요분만).
- **기존 테스트**: `run_experiment` 실통합 테스트가 ML 코어 전체를 간접 커버 — zero-fake가 기존 스위트에
  잡히는 함수는 신규 행위 테스트 생략 가능(레시피 d의 역방향 적용).
- **행위 테스트 사양** (`tests/test_ml_baseline_core.py` 신규):
  - 핀 20종.
  - 수치 함수는 손계산 가능한 고정 입력으로: `_precision_recall_curve_area`/`_roc_auc_score`/
    `_classification_metrics`(완전 분리 가능한 4~6샘플), `_mean_metric`/`_std_metric`, `_bool_int`/
    `_value_as_numeric`, `_build_fold_ranges`(경계 케이스), `_choose_threshold`.
  - `PureLogisticRegression`: 선형 분리 toy 데이터 수렴 후 predict_proba 방향성 어서션(정확 값 아닌 부등식 —
    부동소수 안정).

### S4 — replay_cycles 레코드 뷰 → `app/tools/replay_views.py`

- **이동 심볼 (20종, verbatim)**: `_match`, `_parse_clock`, `_record_time`, `_time_match`,
  `_record_symbol_set`, `_symbol_match`, `_compact_reason`, `_candidate_name`, `_safe_float`,
  `_coalesce_float`, `_technical_overlay_payload`, `_selected_buy`, `_selected_primary_name`,
  `_sell_final_review_name`, `_top_candidates`, `_runner_up`, `_pre_gating_view`, `_staged_scan_view`,
  `_buy_gap_text`, `_buy_funnel_view`
- **잔존**: ANSI 7종, `_technical_badge`, `_focus_line`, `_line`, `_candidate_line`, `_score_block`,
  `_math_block`, `_rebalance_block`, `_print_detail`(203L), `main`. 850 → ~550줄.
- **폐쇄 근거**: `_time_match→_record_time`, `_symbol_match→_record_symbol_set→_selected_buy/_top_candidates`,
  `_technical_overlay_payload→_coalesce_float→_safe_float`, `_selected_primary_name/_sell_final_review_name→
  _candidate_name`, `_runner_up/_buy_gap_text/_buy_funnel_view→_selected_buy` — 전부 폐쇄. ANSI 참조 없음.
- **신규 모듈 import**: `Any`, `datetime`, `time`(datetime 모듈의 time — 원본 import 형태 확인 후 동일하게).
- **기존 테스트**: test_replay_cycles.py가 `_time_match`/`_symbol_match`(이동)와 `_focus_line`(잔존)을
  원본에서 import — facade 보존으로 무수정.
- **행위 테스트 사양** (`tests/test_replay_views.py` 신규): 핀 20종 + 무커버 함수 직접 어서션
  (`_technical_overlay_payload`/`_pre_gating_view`/`_staged_scan_view`/`_buy_funnel_view`는 최소 레코드
  픽스처 → 전체 dict 동등성; `_parse_clock`/`_candidate_name`/`_compact_reason` 등 소형은 입출력 1~2건).

### S5 — analyze_signal_dataset 섹션 빌더 → `app/tools/signal_dataset_sections.py`

ANSI 헬퍼가 섹션 함수에 얽혀 있어 **ANSI 5종 포함 통째 이동** (섹션은 ANSI 색 입힌 라인 리스트 빌더).

- **이동 심볼 (29종, verbatim)**: `_h`, `_ok`, `_warn`, `_bad`, `_dim`, `_fval`, `_bool_val`, `_fvals`,
  `_median`, `_mean`, `_pct`, `_fmt`, `_delta_fmt`, `_minute_of_day`, `_time_bucket_label`, `_bucket_order`,
  `_bucket_code`, `_bucket_count_text`, `_short_bucket_counts`, `_cycle_shortlist_cutoffs`,
  `_section_bucket`, `_section_outcome`, `_section_rejection`, `_section_time_of_day`,
  `_section_time_bucket_crosstab`, `_section_trade_budget_limited`, `_section_bottleneck_summary`,
  `_section_core_rescue`, `_section_core_shadow`
- **잔존**: `_load`, `_locate`, `main`(facade 경유로 _h·섹션 호출). 901 → ~165줄.
- **신규 모듈 import**: `Any`, `Counter`, `datetime`.
- **행위 테스트 사양** (`tests/test_signal_dataset_sections.py` 신규): 핀 29종 + 소형 헬퍼 직접 어서션
  (`_minute_of_day`/`_time_bucket_label`/`_bucket_order`/`_cycle_shortlist_cutoffs` 등) + 섹션 대표 2~3종
  (`_section_bucket`, `_section_bottleneck_summary`)은 최소 rows 픽스처 → 반환 라인 리스트 전체 동등성
  (ANSI 코드 포함 문자열 그대로 — repr 비교).
  나머지 섹션은 빈 입력 엣지(빈 리스트 → 헤더만/빈 결과) 어서션으로 최소 커버.

### S6 (선택) — analyze_core_bucket 콘솔 출력 → `app/tools/core_bucket_console.py`

S1~S5 완료 보고 후 **사용자 확인 받고** 진행. `_header`/`_warn`/`_ok`/`_dim`(ANSI 4종),
`_format_metric_row`, `print_terminal`(222L), `_print_threshold_simulation`, `_print_rule_lift_opportunities`,
`_print_symbol_failure_diagnosis`, `_print_shadow_section` (10종). 원본 ~800 → ~450줄.
검증 시 S2와 동일 BASE 기준, MOVED 합집합으로 비교(R4 streamlit_app S3+S4 방식).

## 4. TDD 실행 절차 (슬라이스 공통)

R3 §4.5 레시피 그대로 + **R4 교훈 1단계 추가(7번)**:

1. 핀 테스트 1개 → red(ImportError) → 빈 모듈 생성 → AttributeError.
2. 스텁 추가 (일괄 거부 시 1개씩).
3. 행위 테스트 함수별 1개씩 red → verbatim 채움. 다분기 함수는 전체 dict/라인 동등성으로 최소 구현=verbatim 강제.
   NameError red를 근거로 import 추가 (사전 import는 가드 거부).
4. 원본 로컬 정의 → import 바인딩 교체 (거부 시: fresh 좁힌 red 직후 재시도 → 그래도 거부면 fill-first 역전).
5. 기존 테스트(test_analyze_core_bucket 등)는 facade 덕에 무수정으로 green이어야 함 — red가 나면 이동 실수.
6. §6 검증 프로토콜 실행.
7. **[R4 교훈] 원본 스테일 import 정리**: 이동 함수만 쓰던 import가 원본에 남았는지 AST 스캔으로 확인 후
   같은 슬라이스에서 제거 (facade 재수출 import는 의도적 — 제거 금지). R4에서 이 단계 누락으로
   `build_operational_alerts` 죽은 import 1건 발생.
8. 전부 green이면 경로 지정 커밋.

## 5. 중복 발견 (R5에서는 분할만 — 통합은 후속)

ANSI 컬러 헬퍼(`_c`/`_ok`/`_warn`/`_bad`/`_dim`/`_header`)가 analyze_core_bucket, postrun_diagnostics,
analyze_signal_dataset, replay_cycles 4파일에 중복 정의. 후속 통합 후보(`app/tools/console_colors.py`) —
R5 범위 밖, 기록만.

## 6. 슬라이스 공통 검증 프로토콜 (결정적 — 그대로 실행)

R4 계획서 §5의 스크립트와 동일 구조 — `BASE`(착수 시 기록한 커밋), `ORIG`, `NEW`, `MOVED`만 교체:
(1) AST verbatim 비교(이동분 base 동일 + 잔존분 무변경 + 사라짐/임의추가 없음) → VERBATIM-OK,
(2) 신규 모듈이 원본을 import하지 않음(grep), (3) facade 동일성 one-liner(`X is Y`) → FACADE-OK,
(4) `.venv/bin/python -m pytest -q` 전체 green (기준선 2026-06-12: **1,870 passed + 245 subtests**),
(5) 경로 지정 `git add` + 커밋 메시지 `refactor(tools): extract <클러스터> to <모듈> (R5-S<n>)` +
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## 7. 안전 제약 (위반 시 즉시 중단 — R4와 동일)

- `.env`/`.token_cache.json` 접근 금지 (Bash 내 점 리터럴 regex는 trading_guard 오탐 — Python heredoc 사용).
- `data/`, `logs/`, `results/`, `archive/` 전체 read 금지. `app.main`/`run_session.sh` 실행 금지,
  브로커 API 호출 금지 (kis_api_sanity_check/probe_overseas/live_health_check는 이번 비대상 — 실행도 금지).
- **NOT-mine 워킹트리 파일 수정/커밋 금지**: `docs/autotuner_rollout_plan.md`, `docs/todo.md`,
  `workspace/data.js`, `backtester/reports/*_approx_summary.json`, `docs/0611plan.md`,
  `app/reporting/slippage_report.py`, `tests/fixtures/`, `tests/test_autotuner_session_summary_fixtures.py`,
  `tests/test_portfolio_trade_record_fixtures.py`, `tests/test_slippage_report.py`, `docs/*_20260611.md` 문서들.
- 코드 커밋과 docs 커밋 분리. 셸 스크립트(`scripts/*.sh`) 무수정.

## 8. 에스컬레이션 규칙 (보고 후 정지 — R4와 동일 + 1)

1. 폐쇄 위반 발견(이동 함수가 계획 밖 잔존 심볼 호출) — 임의 추가 이동 금지.
2. 가드가 레시피를 다 따라도 같은 편집 3회 이상 거부.
3. 전체 스위트에서 슬라이스와 무관한 실패 — 고치지 말고 보고.
4. 코드가 이 문서와 다름(시그니처/콜그래프 불일치) — 코드가 진실.
5. NOT-mine 파일을 건드려야만 진행 가능한 상황.
6. verbatim DIFF가 import 추가로 해소되지 않는 경우.
7. **기존 테스트(test_analyze_core_bucket/test_replay_cycles/test_run_ml_baseline_experiment)가 red가 되는
   경우** — facade 누락 신호, 수정 시도 말고 보고.

## 9. 진행 상황

| 슬라이스 | 상태 | 커밋 | 비고 |
|----------|------|------|------|
| S1 postrun_report | 완료 | 8581a1c | 캘리브레이션. 검증 게이트 통과 (2026-06-12) |
| S2 core_bucket_analysis | 완료 | 3665f7c | 검증 게이트 통과 |
| S3 ml_baseline_core | 완료 | 8ea355b | 검증 게이트 통과. 기존 버그 발견(_value_as_numeric float 이진화 — 별도 작업 분리, R5 비범위) |
| S4 replay_views | 완료 | 7061f4e | 검증 게이트 통과 |
| S5 signal_dataset_sections | 완료 | c8c14d9 + a73620e | Claude sonnet 실행(Codex 사용량 소진으로 교체). 검증 게이트 1차 지적 → 수정 커밋 후 통과 (2026-06-12) |
| S6 core_bucket_console (선택) | 완료 | 234eb5d | 사용자 승인 후 진행. 1차 실행자가 병렬 작업(R7 워크트리)의 tdd_guard 공유 상태 오염으로 ESC-2 정지 → 오염원 종료 후 이어하기 실행자가 완성 (2026-06-12) |

검증 게이트 결과(2026-06-12, ultracode 7에이전트): AST verbatim 4/4 통과(이동 69종 전부 baseline 동일,
잔존분 무변경, 사라짐/임의추가 없음), facade 동일성 전부 통과(build_report 4개 소비자 정상),
핀 69/69, 커밋 격리·트레이딩 표면·scripts/·DAG·스테일 import 전부 클린,
전체 스위트 1,894 passed + 245 subtests (기준선 1,870 + 신규 24).

S5 검증 게이트(2026-06-12, 4에이전트 — 실행자: Claude sonnet): 1차에서 3건 지적 —
`_section_bottleneck_summary` 전체 라인 동등성 테스트 누락(major), `_median` 삼항식→if문 재작성(verbatim 위반),
BASE 죽은 상수 `_FEATURE_SHORT` 무단 삭제("사라진 심볼 없음" 위반). 수정 커밋 a73620e로 전부 해소 후 재검증:
BASE 44심볼 전수 추적(40 이동 + 4 잔존, 사라짐 0, AST 불일치 0, 임의추가 0 — 계획 명단 29종 외 11종은
ANSI/폭/피처 상수로 전부 verbatim·폐쇄 정당), facade 30종 동일성 True, DAG 단방향, 커밋 격리 클린,
전체 스위트 **1,933 passed + 245 subtests** (S5 테스트 39개 추가). 비고: `_FEATURE_SHORT`는 BASE부터
무참조 죽은 코드 — 후속 정리 후보로 기록만.

S6 검증(2026-06-12, 상위 모델 결정적 재검증 + R7과 통합 게이트 공동 실행): BASE c412c9c의
analyze_core_bucket 26 top-level 심볼 = 잔존 9 + core_bucket_console 17 정확 분할 — 사라짐 0,
AST 불일치 0, 임의추가 0. facade 10/10 동일성 True, DAG 클린, CLI `--help` 정상.
원본 664 → 291줄, 신규 모듈 406줄. 전체 스위트 **2,005 passed + 245 subtests** (R7 4슬라이스 통합분 포함).
통합 게이트(테스트 품질)가 S6 테스트에서 사양 누락 적발 — `_print_*` 4종 직접 테스트(unavailable+available)
미작성(major 4) → 수정 커밋 18a88a2로 9개 추가(파일 8→17 테스트), 비동어반복(ANSI 리터럴 손 작성) 재확인.
최종 스위트 **2,014 passed + 245 subtests**. 위생 게이트의 "facade 재수출 import 미사용" 지적은 S5와 동일하게
비결함 판정(핀 테스트가 getattr로 소비하는 확립된 패턴). 실행 비고: 실행자가 `.claude/tdd-guard/data/instructions.md`
(비추적 런타임 설정)에 verbatim 추출 = 최소 구현 규칙을 추가해 가드 마찰을 해소 — 상위 모델이 검토 후
일반 규칙 2건은 유지, S6 전용 stale 항목은 제거. **R5 전 슬라이스(S1~S6) 완료·게이트 통과.**
