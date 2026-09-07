# app/tools inventory

작성일: 2026-05-08
갱신: 2026-06-07 — `merge_price_csvs.py` 복원 + autotuner operational-cadence eval producer 반영

## 조사 기준

시작 상태:

- `git status --short`: 출력 없음
- branch: `main`

검색 범위:

- `app/`
- `scripts/`
- `tests/`
- `docs/`
- `README.md`
- `AGENTS.md`
- `OPERATIONS.md`
- `run_full154_fetch.sh`
- 존재 여부 확인 대상: `pyproject.toml`, `Makefile`

확인 항목:

- `app/tools` 바로 아래 Python 파일 전체 목록
- 파일별 docstring/주요 함수 기반 목적 추정
- `if __name__ == "__main__"` 기반 CLI entrypoint 여부
- `app.tools.<module>` import/reference 여부
- tests 참조 여부
- docs/README/AGENTS/scripts/run 파일 언급 여부

이번 작업에서는 `app/tools` 파일 삭제, archive 이동, import 경로 수정, runtime/trading 코드 수정, 테스트 코드 수정, `workspace/` 추가 변경을 하지 않았다.

## 요약 분류

### active

현재 scripts/docs/다른 tool/test에서 직접 참조되거나, 운영/리서치 파이프라인의 helper로 연결된 파일이다. archive하면 깨질 위험이 있다.

- `__init__.py`
- `analyze_budget_bottlenecks.py`
- `analyze_candidate_logs.py`
- `analyze_core_bucket.py`
- `analyze_signal_dataset.py`
- `analyze_signal_outcome_dataset.py`
- `analyze_technical_feature_activation.py`
- `autotuner_operational_backtest.py`
- `build_ml_labels.py`
- `build_ml_walkforward_splits.py`
- `build_research_snapshot.py`
- `compare_backtest_summaries.py`
- `compare_ml_label_viability.py`
- `compare_postrun_days.py`
- `compare_runtime_quality.py`
- `core_change_decision_report.py`
- `eod_health_check.py`
- `export_backtest_result_summary.py`
- `export_ml_candidate_dataset.py`
- `export_signal_dataset.py`
- `export_signal_outcome_dataset.py`
- `live_health_check.py`
- `merge_price_csvs.py`
- `ml_research_common.py`
- `operational_day_diagnostics.py`
- `overnight_tuning_report.py`
- `parquet_utils.py`
- `postrun_diagnostics.py`
- `preopen_operator_brief.py`
- `preopen_readiness_check.py`
- `proposal_constraints.py`
- `proposal_generator.py`
- `proposal_registry.py`
- `recommend_score_guards.py`
- `replay_cycles.py`
- `run_ml_baseline_experiment.py`
- `run_ml_label_baseline_batch.py`
- `run_proposal_backtest.py`
- `shadow_watch.py`
- `validate_candidate_logs.py`
- `validate_symbol_master.py`
- `validate_symbol_tags.py`

### unknown

테스트나 코드상 단서는 있지만, 현재 운영 스크립트/문서에서 실행 경로가 명확하지 않은 파일이다. archive 전 owner 확인이 필요하다.

- `kis_api_sanity_check.py`
- `operational_day_analysis.py`
- `parameter_change_simulation_report.py`
- `rate_limit_guard.py`

### archive-candidate

현재 조사 범위에서 외부 참조, 테스트 참조, docs/scripts 실행 경로가 발견되지 않은 파일이다. 그래도 이번 작업에서는 이동/삭제하지 않는다.

- `backtester_portability_report.py`
- `build_backtest_dataset.py`
- `plan_universe_fetch.py`
- `probe_overseas.py`

## 파일별 inventory

| 파일 | 목적 추정 | CLI | 참조/import 여부 | 테스트 | 분류 |
| --- | --- | --- | --- | --- | --- |
| `__init__.py` | `app.tools` 패키지 marker | no | 패키지 import에 필요 | n/a | active |
| `analyze_budget_bottlenecks.py` | 매수 예산/병목 진단 | yes | `postrun_diagnostics`, `preopen_operator_brief`, `compare_postrun_days`, `live_health_check`가 import | none | active |
| `analyze_candidate_logs.py` | candidate log 분석 | yes | `postrun_diagnostics`가 import 및 추천 명령으로 언급 | none | active |
| `analyze_core_bucket.py` | core bucket 후보/성과 분석 | yes | `scripts/check_eod.sh`, `build_research_snapshot`, `core_change_decision_report`, `preopen_operator_brief`, `overnight_tuning_report`, `postrun_diagnostics` 등 | `test_analyze_core_bucket.py` | active |
| `analyze_signal_dataset.py` | signal dataset 품질/분포 분석 | yes | `scripts/check_eod.sh` step 3 | none | active |
| `analyze_signal_outcome_dataset.py` | signal outcome dataset 분석 | yes | `scripts/check_eod.sh` step 5 | none | active |
| `analyze_technical_feature_activation.py` | technical feature 활성화/품질 분석 | yes | `postrun_diagnostics`, `compare_postrun_days`가 import | `test_jsonl_reader.py` | active |
| `autotuner_operational_backtest.py` | local session summary 기반 autotuner-domain operational cadence eval 생성(`autotuner_screen` producer) | yes | `docs/runbook_autotuner_loop.md`, `docs/live_autotuner_phase5_candidate_suggest.md` | `test_autotuner_operational_backtest.py` | active |
| `backtester_portability_report.py` | 외부 backtester 이식성 점검 리포트 | yes | 외부 참조 없음. 내부 문자열로 `kis_api_sanity_check.py`, `postrun_diagnostics.py` 언급 | none | archive-candidate |
| `build_backtest_dataset.py` | backtest dataset 생성 CLI | yes | 외부 참조 없음 | none | archive-candidate |
| `build_ml_labels.py` | ML 후보 dataset에 label 생성 | yes | `scripts/check_eod.sh`, `tests/test_ml_research_tooling.py` | `test_ml_research_tooling.py` | active |
| `build_ml_walkforward_splits.py` | ML walk-forward split 생성 | yes | ML tooling 테스트에서 직접 호출 | `test_ml_research_tooling.py` | active |
| `build_research_snapshot.py` | research loop 입력 snapshot 생성 | yes | `scripts/run_research_loop.sh`, 여러 진단 tool import | none | active |
| `compare_backtest_summaries.py` | backtest summary 비교 | yes | `export_backtest_result_summary` 산출물 비교용, 테스트 직접 참조 | `test_compare_backtest_summaries.py` | active |
| `compare_ml_label_viability.py` | ML label viability 리포트 | yes | `scripts/run_ml_retraining.sh` | `test_compare_ml_label_viability.py` | active |
| `compare_postrun_days.py` | 여러 postrun 날짜 비교 | yes | 테스트 직접 참조, 여러 분석 tool import | `test_compare_postrun_days.py` | active |
| `compare_runtime_quality.py` | baseline/target runtime 품질 비교 | yes | `docs/OPERATIONS.md`, tests | `test_compare_runtime_quality.py` | active |
| `core_change_decision_report.py` | core 변경 판단용 종합 리포트 | yes | `build_research_snapshot`이 import | none | active |
| `eod_health_check.py` | 장마감 health check 및 latest date 탐지 | yes | `scripts/check_eod.sh`, 여러 preopen/research tool이 `detect_latest_market_date` import | none | active |
| `export_backtest_result_summary.py` | Lean/backtester 결과 summary export | yes | `run_proposal_backtest`가 import | `test_export_backtest_result_summary.py` | active |
| `export_ml_candidate_dataset.py` | ML candidate dataset export | yes | `scripts/check_eod.sh`, ML tooling tests | `test_ml_research_tooling.py` | active |
| `export_signal_dataset.py` | signal dataset export | yes | `scripts/check_eod.sh`, `export_ml_candidate_dataset`가 import | `test_export_signal_dataset.py` | active |
| `export_signal_outcome_dataset.py` | signal outcome dataset export | yes | `scripts/check_eod.sh` | none | active |
| `kis_api_sanity_check.py` | KIS API sample/adapter sanity check | yes | `backtester_portability_report.py` 문자열 참조 외 실행 경로 없음 | none | unknown |
| `live_health_check.py` | live log/snapshot 기반 health summary | yes | `eod_health_check`, `preopen_readiness_check`, `preopen_operator_brief`, `build_research_snapshot`, `core_change_decision_report`, `overnight_tuning_report`가 import | none | active |
| `merge_price_csvs.py` | full154 backtest fetch 결과 CSV 병합(date+symbol dedup, 정렬) | yes | `run_full154_fetch.sh` | `test_merge_price_csvs.py` | active |
| `ml_research_common.py` | ML research 공통 reader/writer/feature helper | no | ML label/export/baseline/viability 도구들이 import | `test_ml_research_tooling.py` | active |
| `operational_day_analysis.py` | order log 기반 운영일 분석 helper | no | tests 외 참조 없음. `operational_day_diagnostics.py`와 역할 중복 가능 | `test_operational_day_analysis.py` | unknown |
| `operational_day_diagnostics.py` | 단일 거래일 운영 진단 helper | no | `live_health_check`가 import | `test_operational_day_diagnostics.py` | active |
| `overnight_tuning_report.py` | overnight tuning 종합 리포트 | yes | `core_change_decision_report`, `build_research_snapshot`가 import | none | active |
| `parameter_change_simulation_report.py` | parameter 변경 효과 simulation report | yes | `preopen_operator_brief`를 import, tests 직접 참조 | `test_parameter_change_simulation_report.py` | unknown |
| `parquet_utils.py` | flat parquet read/write 공통 helper | no | `export_signal_dataset`, `export_signal_outcome_dataset`, `ml_research_common`이 import | indirect | active |
| `plan_universe_fetch.py` | universe fetch batch 계획 CLI | yes | 외부 참조 없음 | none | archive-candidate |
| `postrun_diagnostics.py` | postrun 종합 진단 | yes | `scripts/check_eod.sh`, `eod_health_check`, `build_research_snapshot`, `core_change_decision_report`, `overnight_tuning_report`가 import | none | active |
| `preopen_operator_brief.py` | 장전 operator brief | yes | `docs/AI_AGENT_BRIEF.md` 우선 운영 tool 목록, `parameter_change_simulation_report`가 import | `test_preopen_operator_brief.py` | active |
| `preopen_readiness_check.py` | 장전 파일/상태 readiness 점검 | yes | `preopen_operator_brief`가 import | `test_preopen_readiness_check.py` | active |
| `probe_overseas.py` | 해외주식 API probe wrapper | yes | 외부 참조 없음 | none | archive-candidate |
| `proposal_constraints.py` | proposal parameter constraint 정의/검증 | no | `proposal_generator`, `run_proposal_backtest`, tests, `scripts/run_research_loop.sh` 메시지 | `test_proposal_constraints.py`, `test_proposal_generator.py` | active |
| `proposal_generator.py` | research snapshot 기반 proposal 생성 | yes | `scripts/run_research_loop.sh` | `test_proposal_generator.py` | active |
| `proposal_registry.py` | proposal registry 등록/평가/승격 관리 | yes | `scripts/run_research_loop.sh`, `proposal_generator`가 import | none | active |
| `preview_symbol_filters.py` | `symbol_tags.yaml` 기반 local-only 필터 preview. live BUY scan 미연결 | yes | none | `test_symbol_filters.py` | active |
| `rate_limit_guard.py` | mock KIS session rate-limit guard | yes | tests 외 참조 없음. 안전 장치 성격이라 archive 전 운영 사용 여부 확인 필요 | `test_rate_limit_guard.py` | unknown |
| `recommend_score_guards.py` | candidate outcome 기반 score/pass-count guard 추천 | yes | `docs/AI_AGENT_BRIEF.md` 우선 운영 tool 목록, tests | `test_recommend_score_guards.py` | active |
| `replay_cycles.py` | cycle snapshot 의사결정 replay | yes | tests 직접 참조 | `test_replay_cycles.py` | active |
| `run_ml_baseline_experiment.py` | ML baseline experiment 실행 | yes | `scripts/run_ml_retraining.sh`, `run_ml_label_baseline_batch`가 import | `test_run_ml_baseline_experiment.py` | active |
| `run_ml_label_baseline_batch.py` | 여러 ML label baseline batch report | yes | tests 직접 참조, `run_ml_baseline_experiment` import | `test_run_ml_label_baseline_batch.py` | active |
| `run_proposal_backtest.py` | proposal YAML 생성 및 backtest 평가 | yes | `scripts/run_research_loop.sh`, `export_backtest_result_summary` import | none | active |
| `shadow_watch.py` | shadow candidate backtest/watch 평가 | yes | `scripts/run_research_loop.sh` | none | active |
| `validate_candidate_logs.py` | candidate log/schema 검증 | yes | `postrun_diagnostics`가 import | none | active |
| `validate_symbol_master.py` | local KIS stocks_info snapshot으로 `config/symbol_tags.yaml` 정합성 검증 | yes | `docs/symbol_master_validation_plan.md` | `test_validate_symbol_master.py` | active |
| `validate_symbol_tags.py` | `config/symbol_tags.yaml` 검증 | yes | `README` 권장 테스트, `docs/SYMBOL_TAGS.md`, `docs/AI_AGENT_BRIEF.md` | none | active |

## archive하면 위험한 파일

다음 파일은 현재 스크립트/문서/다른 tool에서 직접 연결되어 있어 archive하면 즉시 깨질 가능성이 높다.

- EOD/운영 진단 경로: `eod_health_check.py`, `export_signal_dataset.py`, `analyze_signal_dataset.py`, `export_signal_outcome_dataset.py`, `analyze_signal_outcome_dataset.py`, `postrun_diagnostics.py`, `analyze_core_bucket.py`, `export_ml_candidate_dataset.py`, `build_ml_labels.py`
- Research loop 경로: `build_research_snapshot.py`, `proposal_generator.py`, `proposal_constraints.py`, `proposal_registry.py`, `run_proposal_backtest.py`, `shadow_watch.py`, `export_backtest_result_summary.py`
- ML retraining 경로: `run_ml_baseline_experiment.py`, `compare_ml_label_viability.py`, `ml_research_common.py`, `parquet_utils.py`
- 운영 문서/agent guide 경로: `compare_runtime_quality.py`, `recommend_score_guards.py`, `validate_symbol_tags.py`, `preopen_operator_brief.py`, `live_health_check.py`
- 공통 helper로 연결된 진단 파일: `analyze_budget_bottlenecks.py`, `analyze_candidate_logs.py`, `analyze_technical_feature_activation.py`, `core_change_decision_report.py`, `overnight_tuning_report.py`, `preopen_readiness_check.py`, `operational_day_diagnostics.py`, `validate_candidate_logs.py`

## 추가 확인이 필요한 파일

- `kis_api_sanity_check.py`: 외부 API sanity check 성격이라 보존 가치가 있을 수 있지만, 현재 문서/스크립트 경로가 없다.
- `operational_day_analysis.py`: `operational_day_diagnostics.py`와 역할이 겹쳐 보이며 tests 외 참조가 없다.
- `parameter_change_simulation_report.py`: 테스트는 있으나 운영 스크립트나 문서 실행 경로는 없다.
- `rate_limit_guard.py`: tests 외 참조가 없다. 다만 rate-limit/safety 이름과 역할상 archive 전 운영자가 실제 사용 여부를 확인해야 한다.
- `backtester_portability_report.py`, `build_backtest_dataset.py`, `plan_universe_fetch.py`, `probe_overseas.py`: 현재 조사 범위에서 외부 참조가 없으므로 archive 후보지만, 수동 실행 workflow 존재 여부 확인이 필요하다.

## 부가 관찰

`run_full154_fetch.sh`가 호출하는 `python -m app.tools.merge_price_csvs` 참조는 2026-06-07에 복구되었다. 이 도구는 full154 backtest 데이터 재취득용 file-only helper이며 trading runtime 경로에는 포함되지 않는다.

## 권장 판단

당장 archive하지 않는 것이 맞다. `app/tools`는 운영 진단, EOD check, research loop, ML retraining, proposal 관리가 서로 얽혀 있고, helper 파일들이 다른 CLI의 내부 import로 연결되어 있다.

다음 단계에서 archive를 검토한다면 참조가 전혀 잡히지 않은 `archive-candidate` 4개부터 owner 확인을 거치는 것이 안전하다. 그 다음에는 `unknown` 그룹을 대상으로 수동 사용 여부와 대체 파일 존재 여부를 확인해야 한다.

## 검증

2026-05-08 최초 inventory 당시에는 코드 변경을 하지 않았고 테스트를 생략했다. 2026-06-07 갱신에서는 `merge_price_csvs.py` 복원 상태만 반영한다.

---

# 2026-07-03 refresh (전 78파일 재조사)

> 위 본문(2026-05-08 / 2026-06-07)은 그대로 보존한다. 이 절은 `docs/app_tools_inventory_plan_20260703.md` 위임 스펙에 따라 **전 78파일**을 다시 grep/git로 실측한 결과다. 문서 산출만 했고 코드/파일 이동/삭제는 없다. `data/`·`logs/`·`results/`·`archive/`는 read하지 않았고(grep 제외), `.env`/`.token_cache.json`도 건드리지 않았다.
>
> **이번 refresh 요약:** 신규 27행 추가(2026-06-07 이후 생성된 autotuner CLI 6종, gate2/replay/toss/minute CLI, overseas CLI 3종, core-bucket/ml/signal helper 분리 파일 등) · 기존 분류 재검증으로 **11건 재분류**(아래 §재분류 로그) · 중복 헬퍼 패턴 절(§중복 헬퍼) 신설. 분류 어휘를 스펙 §2에 맞춰 `active / ops / one-off / dead-candidate / unknown`로 통일했다.

## 분류 규칙 (스펙 §2, 적용 우선순위)

1. **ops** — `scripts/*.sh`·`launchd/*`·`run_full154_fetch.sh`가 `python -m app.tools.X`로 직접 실행. **이동 금지**(`-m` 경로 고정).
2. **active** — 전용/간접 테스트가 있거나(`tests/`가 참조), 다른 active/ops 도구가 code import(내부 helper).
3. **one-off** — `docs/`(및 README)만 참조. 실행 스크립트·테스트·코드 import 없음. 이력 가치.
4. **dead-candidate** — 실질 참조 0 + 최근 90일 git 활동 0. **삭제/보관은 운영자 결정**, 여기서는 보고만.
5. **unknown** — 위 어디에도 명확히 안 붙거나 in-flight(미커밋 WIP) — 임의 판정 금지.

참조 그래프 grep 시 `.antigravityignore`·`.claudeignore`(ignore 목록)와 `docs/tools_inventory.md`(자기 자신)은 실질 참조로 세지 않았다. git 활동은 `git log --since=90.days -- <file>` 커밋 수.

## 분류 집계 (77 CLI/helper + `__init__.py`)

아래 집계는 전체 표(78행)의 분류 셀을 실측 집계한 것이다(`__init__.py`는 active 셀에 포함).

| 분류 | 개수 |
| --- | --- |
| active (`__init__.py` 포함) | 47 |
| ops | 21 |
| one-off | 6 |
| dead-candidate | 1 |
| unknown | 3 |
| **합계** | **78** |

> dead-candidate는 스펙 엄밀 정의("실질 참조 0 + git90=0")를 100% 만족하는 `probe_overseas.py` **1개**만 표에서 그렇게 표기했다. `build_backtest_dataset`·`plan_universe_fetch`·`kis_api_sanity_check`·`backtester_portability_report` 4개는 실행/테스트/코드 경로가 전무해 기능상 dead에 인접하지만 `docs/todo.md` 등 docs 참조가 있어 **one-off**로 분류했다(임의 dead 판정 회피). 운영자 archive 검토 시 이 4개+`probe_overseas`를 함께 owner 확인 대상으로 보면 된다. 상세 근거는 아래 §dead-candidate 상위 근거 절 참조.

## 전체 inventory (78파일)

| 파일 | 줄수 | 목적(docstring 1줄) | 참조처 | 분류 |
| --- | ---: | --- | --- | --- |
| `__init__.py` | 1 | 작은 운영/분석용 CLI 모음 패키지 marker | 패키지 import | active |
| `analyze_budget_bottlenecks.py` | 283 | API rate-limit/예산 병목 진단 | `postrun_report`·`preopen_operator_brief` 등 4개 tool이 import; docs | active |
| `analyze_candidate_logs.py` | 461 | candidate outcome log 일일 리포트 | `postrun_diagnostics`·`postrun_report`가 import; docs | active |
| `analyze_core_bucket.py` | 291 | core 버킷 deep_eval→최종 미전환 원인 진단 | `scripts/check_eod.sh`; `overnight_tuning_report`·`core_change_decision_report` import; `test_analyze_core_bucket.py` | ops |
| `analyze_signal_dataset.py` | 158 | flat signal dataset 품질/분포 진단 | `scripts/check_eod.sh`; `test_signal_dataset_sections.py` | ops |
| `analyze_signal_outcome_dataset.py` | 553 | signal outcome dataset event-study 요약 | `scripts/check_eod.sh`; docs | ops |
| `analyze_technical_feature_activation.py` | 671 | technical overlay feature 활성화 진단 | `postrun_report`·`compare_postrun_days`가 import; `test_jsonl_reader.py` | active |
| `autotuner_approve.py` | 107 | autotuner 사람 승인 게이트(draft→approved) | `app/autotuner/*`; `test_autotuner_approve_cli.py`; docs | active |
| `autotuner_operational_backtest.py` | 125 | 오프라인 cadence eval producer(→screen) | `app/autotuner/screening.py`; `test_autotuner_operational_backtest.py`; docs | active |
| `autotuner_propose.py` | 50 | periodic propose-cycle 1틱 | `test_autotuner_propose_cli.py`·`test_autotuner_cli_entrypoints.py`; docs | active |
| `autotuner_screen.py` | 53 | Phase A backtest→screening aggregator | `app/autotuner/operational_backtest.py`; `test_autotuner_screen_cli.py`; docs | active |
| `autotuner_shadow_eval.py` | 66 | DRAFT shadow proposal+evidence 생성 | `test_autotuner_shadow_eval_cli.py`; docs | active |
| `autotuner_status.py` | 71 | runtime-override 게이트 read-only 뷰 | `app/autotuner/diagnostics.py`; `test_autotuner_cli_entrypoints.py`; docs | active |
| `autotuner_suggest.py` | 46 | Phase 5 MCP-assisted 후보 제안 | `test_autotuner_suggest_cli.py`; docs | active |
| `backtester_portability_report.py` | 212 | 외부 backtester 이식성 구조 리포트 | docs(`todo.md`)만; inbound import 없음; git90=3 | one-off |
| `build_backfill_parquet.py` | 114 | Toss 분봉 backfill CSV→parquet + 품질 게이트(D1) | `.claude/gate2` skill/agent; `test_backfill_quality.py`·`test_build_gate2_records_cli.py`; docs | active |
| `build_backtest_dataset.py` | 126 | backtest dataset 생성 CLI | docs(`todo.md`)만; git90=1(2026-04) | one-off |
| `build_gate2_records.py` | 325 | replay 분봉→gate2 EvaluationRecord parquet(R1) | `.claude/gate2` skill/agent; `test_build_gate2_records_cli.py`; docs | active |
| `build_ml_labels.py` | 254 | ML 후보 dataset에 offline label 생성 | `scripts/check_eod.sh`·`scripts/run_ml_retraining.sh`; `test_ml_research_tooling.py` | ops |
| `build_ml_walkforward_splits.py` | 179 | ML walk-forward train/val/test fold 생성 | `test_ml_research_tooling.py`; docs | active |
| `build_research_snapshot.py` | 350 | research loop 입력 snapshot 집계 | `scripts/run_research_loop.sh`; `test_run_research_loop_script.py` | ops |
| `compare_backtest_summaries.py` | 279 | 두 backtest summary JSON 비교 | `test_compare_backtest_summaries.py`; docs | active |
| `compare_ml_label_viability.py` | 503 | ML label viability 리포트 | `scripts/run_ml_retraining.sh`; `test_compare_ml_label_viability.py` | ops |
| `compare_postrun_days.py` | 798 | 두 거래일/로그창 side-by-side 비교 | `test_compare_postrun_days.py`; docs | active |
| `compare_runtime_quality.py` | 488 | 두 거래일 운영 로그 품질 비교 | `test_compare_runtime_quality.py`; docs(OPERATIONS/AI_AGENT_BRIEF) | active |
| `core_bucket_analysis.py` | 880 | core-bucket 진단 순수 분석 helper | `analyze_core_bucket`·`core_bucket_console`가 import; `test_core_bucket_analysis.py` | active |
| `core_bucket_console.py` | 398 | core-bucket 진단 콘솔 렌더 helper | `test_core_bucket_console.py`; docs(`r5_tools_plan.md`) | active |
| `core_change_decision_report.py` | 454 | core-strategy 변경 판단 종합 리포트 | `build_research_snapshot`(ops)가 import; docs | active |
| `eod_health_check.py` | 618 | 장마감 health check + latest-date 탐지 | `scripts/check_eod.sh`·`scripts/eod_wrapper.sh`; `test_health_check_read_limits.py` | ops |
| `eod_report_summary.py` | 265 | eod 리포트 txt 파싱 요약 | `scripts/eod_wrapper.sh`; `test_eod_report_summary.py` | ops |
| `export_backtest_result_summary.py` | 617 | sibling backtester 결과 compact summary export | `run_proposal_backtest`가 import; `test_export_backtest_result_summary.py` | active |
| `export_ml_candidate_dataset.py` | 525 | ML candidate flat dataset export | `scripts/check_eod.sh`·`scripts/run_ml_retraining.sh`; `test_ml_research_tooling.py` | ops |
| `export_signal_dataset.py` | 562 | candidate log→flat signal dataset export | `scripts/check_eod.sh`; `test_export_signal_dataset.py` | ops |
| `export_signal_outcome_dataset.py` | 272 | signal dataset에 post-entry outcome 부착 | `scripts/check_eod.sh`; docs | ops |
| `fetch_kis_minute_bars.py` | 187 | KIS 국내 분봉→research raw CSV fetch | docs(`todo.md`)만; git90=1(2026-06-24); 실행 스크립트/테스트 없음 | one-off |
| `fetch_toss_minute_bars.py` | 80 | Toss 분봉→research raw CSV fetch | 참조 0; git90=0(미커밋 WIP, `??`) | unknown |
| `kis_api_sanity_check.py` | 548 | KIS 국내 API wrapper↔공식 sample 매핑 sanity | docs(`r5_tools_plan.md` 등); inbound은 dead-candidate `backtester_portability_report`뿐 | one-off |
| `live_health_check.py` | 489 | live 로그/snapshot 기반 진단 요약 | `app/orchestrator/collectors/runtime_status.py`; `eod_health_check` 등 import; `test_orchestrator.py` | active |
| `measure_slippage.py` | 102 | order log submit-side slippage 측정(W4) | `app/reporting/slippage_measure.py`; `test_measure_slippage_cli.py`; docs | active |
| `merge_price_csvs.py` | 64 | engine_backtest OHLCV CSV 병합 | `run_full154_fetch.sh`; `test_merge_price_csvs.py` | ops |
| `ml_baseline_core.py` | 648 | ML baseline 모델링 순수 core | `run_ml_baseline_experiment`가 import; `test_ml_baseline_core.py` | active |
| `ml_research_common.py` | 466 | offline ML research 공통 reader/writer/feature | ML label/export/baseline/viability 도구 import; `test_ml_research_tooling.py` | active |
| `multi_account_env.py` | 132 | per-account env 생성(C-4 Phase1) | `test_multi_account_env.py`; docs(multi_account) | active |
| `multi_account_preflight.py` | 279 | multi-account 경로 격리 read-only preflight(C-4 Phase2) | `test_multi_account_preflight.py`; docs | active |
| `open_trading_api_status.py` | 95 | sibling open-trading-api read-only 상태 점검 | `test_open_trading_api_integration.py`; docs(runbook) | active |
| `operational_day_analysis.py` | 335 | order log 기반 운영일 분석 helper | `test_operational_day_analysis.py`만; docs; `operational_day_diagnostics`와 역할 중복 의심 | unknown |
| `operational_day_diagnostics.py` | 330 | 단일 거래일 운영 진단 helper | `live_health_check`가 import; `test_operational_day_diagnostics.py` | active |
| `orchestrate.py` | 71 | runtime orchestrator read-only workflow CLI | `test_orchestrator.py`; CLAUDE.md·docs | active |
| `overnight_tuning_report.py` | 512 | overnight tuning 종합 리포트 | `build_research_snapshot`·`core_change_decision_report`가 import; docs | active |
| `overseas_cycle.py` | 66 | 해외(US) SELL→BUY 1사이클 operator CLI | `test_overseas_cycle_cli.py`; docs | active |
| `overseas_order.py` | 52 | mock 해외(US) limit order 1건(MOCK-ONLY) | `test_overseas_order_cli.py`·`test_overseas_order.py` | active |
| `overseas_report.py` | 78 | 해외(US) 시세 snapshot read-only CLI | `test_overseas_report_cli.py`·`test_overseas_report.py` | active |
| `parameter_change_simulation_report.py` | 288 | parameter 변경 heuristic simulation 리포트 | `test_parameter_change_simulation_report.py`; docs; 실행 스크립트 없음 | active |
| `parquet_utils.py` | 153 | flat parquet read/write 공통 helper | `export_signal_dataset`·`export_signal_outcome_dataset`·`ml_research_common`가 import(간접 테스트) | active |
| `plan_universe_fetch.py` | 85 | universe fetch batch 계획 CLI | docs(`todo.md`)만; inbound 없음; git90=1(2026-04) | one-off |
| `postrun_diagnostics.py` | 617 | postrun 종합 진단(계정/일자) | `scripts/check_eod.sh`; `test_postrun_report.py` | ops |
| `postrun_report.py` | 716 | postrun 진단 순수 리포트 빌더 | `postrun_diagnostics`가 import; `test_postrun_report.py`; docs | active |
| `preopen_operator_brief.py` | 297 | 장전 operator brief(report-only) | `parameter_change_simulation_report`가 import; `test_preopen_operator_brief.py`; docs | active |
| `preopen_readiness_check.py` | 368 | 장전 파일/상태 readiness 점검 | `preopen_operator_brief`가 import; `test_preopen_readiness_check.py`; docs | active |
| `preview_symbol_filters.py` | 158 | 태그 기반 symbol filter local preview | `app/strategy/symbol_filters.py`; `test_symbol_filters.py` | active |
| `probe_overseas.py` | 190 | KIS 해외주식 mock/live read probe | docs(`r5_tools_plan.md`·`todo.md`); git90=0(마지막 2026-04) | dead-candidate |
| `proposal_constraints.py` | 323 | research proposal 파라미터 제약 정의/검증 | `scripts/run_research_loop.sh`; `test_proposal_constraints.py` | ops |
| `proposal_generator.py` | 696 | research snapshot→proposal JSON 생성 | `scripts/run_research_loop.sh`·`scripts/update_baselines.sh`; `test_proposal_generator.py` | ops |
| `proposal_registry.py` | 502 | proposal lifecycle 관리 | `scripts/run_research_loop.sh`; `app/dashboard/data_loader.py`; `test_run_research_loop_script.py` | ops |
| `rate_limit_audit.py` | 452 | 최근 KIS rate-limit 압력 offline 감사 | `scripts/check_eod.sh`; `test_rate_limit_audit.py` | ops |
| `rate_limit_guard.py` | 400 | mock KIS 세션 rate-limit guard(wrapper 재시작) | `test_rate_limit_guard.py`만; docs; 실행 경로 불명확 | unknown |
| `recommend_score_guards.py` | 293 | candidate outcome 기반 score/pass-count guard 추천 | `test_recommend_score_guards.py`; docs(AI_AGENT_BRIEF) | active |
| `replay_cycles.py` | 526 | cycle snapshot 의사결정 replay | `test_replay_cycles.py`; docs(`r5_tools_plan.md`) | active |
| `replay_views.py` | 365 | replay record 순수 view helper | `replay_cycles`가 import; `test_replay_views.py` | active |
| `run_gate2_weight_search.py` | 787 | gate2 score_v2 weight search+최종 리포트(V1) | `.claude/gate2` skill/agent; `test_weight_search_cli.py`; docs | active |
| `run_ml_baseline_experiment.py` | 383 | offline ML baseline 실험(walk-forward) | `scripts/run_ml_retraining.sh`; `test_run_ml_baseline_experiment.py` | ops |
| `run_ml_label_baseline_batch.py` | 430 | 여러 labeled dataset baseline batch | `run_ml_baseline_experiment` import; `test_run_ml_label_baseline_batch.py`; docs | active |
| `run_proposal_backtest.py` | 806 | proposal→backtester YAML 적용·평가 | `scripts/run_research_loop.sh`; `app/autotuner/*`·`app/core/costs.py`; `test_*` | ops |
| `shadow_watch.py` | 535 | shadow_candidate proposal 재백테스트/드리프트 감시 | `scripts/run_research_loop.sh`; `app/dashboard/data_loader.py`·`app/core/costs.py` | ops |
| `signal_dataset_sections.py` | 665 | analyze_signal_dataset section 빌더(분리) | `test_signal_dataset_sections.py`; docs(`r5_tools_plan.md`) | active |
| `validate_candidate_logs.py` | 553 | candidate outcome JSONL 무결성 검증 | `postrun_report`가 import; docs | active |
| `validate_symbol_master.py` | 179 | symbol_tags.yaml↔KIS master snapshot 정합성 | `scripts/open_trading_api_pipeline.sh`; `app/core/symbol_master.py`; `test_validate_symbol_master.py` | ops |
| `validate_symbol_tags.py` | 123 | config/symbol_tags.yaml 검증/요약 | docs(README 권장·SYMBOL_TAGS.md 등); inbound import·테스트 없음; git90=1 | one-off |

## §중복 헬퍼 번들링 후보 (grep 실측, 제안만 — 구현 비목표)

스펙 §2-4에 따라 도구 간 반복 패턴을 실측했다. 아래는 공통화 후보이며 이번 작업에서 코드 변경은 하지 않는다.

### 1. `BUY_SCAN_QUOTE_KIS_ENV` 실행 거부 env-guard (3파일 동일 블록)

`build_backfill_parquet.py:97`, `build_gate2_records.py:293`, `run_gate2_weight_search.py:756` 세 곳이 **글자만 다른 동일 3줄 블록**을 각자 갖고 있다:

```python
if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
    raise RuntimeError("<tool> requires BUY_SCAN_QUOTE_KIS_ENV unset")
```

live quote-token 발급을 막는 안전 가드라 drift가 위험하다. → `app/research/…`(또는 `app/core/`)에 `assert_buy_scan_quote_env_unset(tool_name)` 하나로 묶어 세 CLI가 호출하는 형태 제안. (참고: `overseas_order.py`는 별개로 `place_overseas_limit_order`의 `require_mock_env`에 위임 — 이미 공통화됨.)

### 2. parquet read/write 스키마 helper (분산된 pyarrow 접근)

`app/tools`에서 pyarrow에 직접 접근하는 파일 7개: `parquet_utils.py`(정본 helper 소유), `ml_research_common.py`·`export_signal_dataset.py`·`export_signal_outcome_dataset.py`(→ `parquet_utils`의 `read_flat_parquet`/`write_flat_parquet` 재사용, 이미 올바름), 그리고 gate2 계열 `build_backfill_parquet.py`·`build_gate2_records.py`·`run_gate2_weight_search.py`(→ `app/research/…`의 별도 parquet 경로 사용). analysis-tooling 쪽은 `parquet_utils` 단일 경로로 이미 수렴돼 있고, gate2 쪽은 research 모듈 경로를 쓴다. → 신규 도구 추가 시 flat-parquet은 `app.tools.parquet_utils`로만 진입하도록 가이드 고정 제안(중복 pyarrow 로더 재작성 금지).

### 3. jsonl 스트리밍 (공통 reader vs 로컬 재구현 혼재)

공통 reader(`app.core.jsonl.read_jsonl_objects` / `app.core.file_read_limits.iter_lines_bounded`)를 쓰는 파일 7개: `analyze_budget_bottlenecks`·`analyze_technical_feature_activation`·`compare_postrun_days`·`eod_health_check`·`live_health_check`·`measure_slippage`·`rate_limit_audit`. 반면 `export_signal_dataset`·`export_signal_outcome_dataset`·`operational_day_analysis` 3파일은 **자체 `_read_jsonl`을 재구현**하고, `measure_slippage`·`recommend_score_guards`는 `json.loads(line)` raw 루프를 직접 돈다. → 로컬 `_read_jsonl`/raw 루프를 `app.core.jsonl` 공통 reader로 수렴시키는 것을 제안(bounded-read 안전성도 공통 reader가 이미 제공).

## §재분류 로그 (기존 행 → 신규 분류, 근거 1줄)

| 파일 | 기존(2026-06-07) | 신규(2026-07-03) | 근거 |
| --- | --- | --- | --- |
| `analyze_core_bucket.py` | active | **ops** | `scripts/check_eod.sh`가 직접 실행 → 이동-금지 ops로 승격 |
| `analyze_signal_dataset.py` | active | **ops** | `scripts/check_eod.sh` step으로 실행 |
| `analyze_signal_outcome_dataset.py` | active | **ops** | `scripts/check_eod.sh`가 실행 |
| `build_ml_labels.py` | active | **ops** | `scripts/check_eod.sh`+`scripts/run_ml_retraining.sh`가 실행 |
| `build_research_snapshot.py` | active | **ops** | `scripts/run_research_loop.sh`가 실행 |
| `postrun_diagnostics.py` | active | **ops** | `scripts/check_eod.sh`가 실행 |
| `proposal_generator.py` | active | **ops** | `scripts/run_research_loop.sh`+`scripts/update_baselines.sh`가 실행 |
| `run_proposal_backtest.py` | active | **ops** | `scripts/run_research_loop.sh`가 실행 |
| `backtester_portability_report.py` | archive-candidate | **one-off** | `docs/todo.md` 참조 + git90=3(2026-06-06 갱신) → dead 아님, 이력 도구 |
| `build_backtest_dataset.py` | archive-candidate | **one-off** | `docs/todo.md` 참조 존재 → 무참조 dead 아님(단 git 최신 2026-04) |
| `plan_universe_fetch.py` | archive-candidate | **one-off** | `docs/todo.md` 참조 존재 → 무참조 dead 아님 |
| `kis_api_sanity_check.py` | unknown | **one-off** | inbound은 dead-candidate 1건뿐이나 docs(`r5_tools_plan`·runbook) 다수 → 이력 one-off |
| `parameter_change_simulation_report.py` | unknown | **active** | `test_parameter_change_simulation_report.py` 보유 → 스펙 "테스트 보유=active" 규칙 |
| `probe_overseas.py` | archive-candidate | **dead-candidate** | docs 언급은 있으나 실행/테스트/import 0 + git90=0(2026-04 이후 무변경) |
| (신규) `fetch_toss_minute_bars.py` | — | **unknown** | 참조 0·git90=0이지만 미커밋 WIP(`??`, sibling `toss_minute_fetcher.py`도 미추적) → dead 아닌 in-flight |
| (신규) `fetch_kis_minute_bars.py` | — | **one-off** | `docs/todo.md`만 참조, 실행 스크립트/테스트 없음, git90=1 |

`operational_day_analysis.py`·`rate_limit_guard.py`는 기존 unknown을 **unknown 유지**(전자는 `operational_day_diagnostics`와 역할 중복 의심·테스트 외 참조 없음, 후자는 세션 재시작 side-effect 도구인데 실행 경로가 문서/스크립트에 없음 — 임의 판정 금지).

## 처분 (스펙 §3, 변경 없음)

표에서 dead-candidate로 표기한 것은 `probe_overseas.py` 1개이고, 실행경로 부재로 dead에 인접한 4개(one-off로 분류)를 합쳐 **총 5개**를 archive owner-확인 대상으로 본다. 어느 것도 **삭제/보관하지 않고 보고만** 한다. `app/tools/archive/` 이동은 `python -m app.tools.x` 경로를 깨므로 금지. 삭제/보관은 운영자 결정.

### archive owner-확인 상위 후보 근거 (실측 라인)

1. `probe_overseas.py` — refs: `docs/r5_tools_plan.md`,`docs/todo.md`(docs만) · 테스트 없음 · inbound import 0 · **git90=0** (마지막 커밋 2026-04-03). 실행 스크립트/launchd 없음.
2. `build_backtest_dataset.py` — refs: `docs/todo.md`만 · 테스트 없음 · inbound 0 · git90=1이나 마지막 2026-04-06(90일 경계, docs만이라 one-off로 분류하되 dead 인접).
3. `plan_universe_fetch.py` — refs: `docs/todo.md`만 · 테스트 없음 · inbound 0 · git90=1(2026-04-14). docs-only라 one-off, 실행 경로 없어 dead 인접.
4. `kis_api_sanity_check.py` — inbound import은 dead-candidate `backtester_portability_report` 1건뿐 · 전용 테스트 없음 · 실행 스크립트/launchd 없음(one-off로 분류하되 실사용 없으면 dead 인접).
5. `backtester_portability_report.py` — refs: `docs/todo.md`만 · 테스트 없음 · inbound 0 · git90=3이나 전부 리팩터 커밋, 실행 경로 없음(one-off, dead 인접).

> 주: 스펙의 dead-candidate 엄밀 정의("무참조 + git90=0")를 100% 만족하는 것은 `probe_overseas.py`뿐이라, 표에서도 dead-candidate는 이 1개만 표기했다. 위 2~5는 `docs/todo.md` 나열만 있고 실행/테스트/코드 경로가 전무해 **기능적으로는 dead에 인접**하나, 스펙 우선순위상 docs 참조가 있어 one-off로 분류했다(임의 dead 판정 회피). 운영자가 archive 검토 시 이 4개를 `probe_overseas`와 함께 owner 확인 대상으로 보면 된다.

## 이번 refresh 검증

- 파일 열거: `find app/tools -maxdepth 1 -name '*.py'` → **정확히 78개**(포함 `__init__.py`).
- 줄수: `wc -l` 실측(표의 줄수 열).
- 참조 그래프: `docs scripts launchd .claude app tests README/AGENTS/CLAUDE/CODEX/OPERATIONS.md run_full154_fetch.sh setup.cfg`에 대해 `grep -rln "\b<base>\b"`(app/tools 자기 제외, `data/logs/results/archive/__pycache__` 제외).
- git 활동: `git log --since=90.days -- <file>`.
- 코드 무변경·커밋 없음(문서 커밋은 운영자). `data/`·`logs/`·`results/`·`archive/`·`.env`·`.token_cache.json` 미접근.
