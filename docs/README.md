# kis-trader Documentation Index

처음 방문했다면 [프로젝트 소개](../README.md), [API 사용 방식](API_USAGE.md), [설계 결정](DESIGN_DECISIONS.md), [공개 범위 검토](PUBLIC_SCOPE_AUDIT.md)부터 읽으세요.

아래 색인은 기존 개발 문서를 보존한 목록입니다. 문서의 진행 상태·대기 작업은 작성 당시 기록이며, 공개본의 현재 실행 상태나 제공 기능을 보장하지 않습니다. (색인 정리: 2026-07-03)

> **AI agents:** read [AI_AGENT_BRIEF.md](AI_AGENT_BRIEF.md) first.
> Rule files live at the repo root: `CLAUDE.md`, `AGENTS.md`, `CODEX.md`.

---

## 진행 중 (open — 다음 액션이 있는 문서)

| Doc | 상태 / 다음 액션 |
| :--- | :--- |
| todo.md (not included in this source snapshot) | **살아있는 작업 트래커** (수동매매 reconciliation 등 open 항목 보유) |
| minute_data_gap_audit_20260902.md (not included in this source snapshot) | 분봉 연·월·일 공백 감사 완료 — **2026-06-25 이후 4개 복구 창 실제 수집 대기** |
| [gate2_minute_backtest_plan_20260702.md](gate2_minute_backtest_plan_20260702.md) | 게이트 ①②③ DONE — **④ weight search 운영자 실행 대기** (r4 위임 형식) |
| [us_regime_weight_plan_20260702.md](us_regime_weight_plan_20260702.md) | Track M — gate2 ④ 완료가 선행 조건 (r4 위임 형식) |
| [main_run_cycle_slimming_plan_20260703.md](main_run_cycle_slimming_plan_20260703.md) | Stage A1 완료(main.py 4,695줄) — **A2는 risk-analyst 게이트 필수** |
| [lane_pipeline_hardening_plan.md](lane_pipeline_hardening_plan.md) | 2026-07-02 리뷰 후 잔여 강화 슬라이스 (실행 위임 형식) |
| [multi_account_parallelization_plan.md](multi_account_parallelization_plan.md) | Phase 1 완료·Phase 2 툴링 완료 — **실제 API sanity는 운영자 게이트** |
| [log_data_retention_plan.md](log_data_retention_plan.md) | rotate/gzip/retention 미착수 — §9 위임 슬라이스 D0~D4로 재구성(2026-07-03) |
| pipeline_design_materials_20260627.md (not included in this source snapshot) | lane pipeline 설계 재료 (hardening 트랙 참조용) |

## 운영 / 상시 기준 (current reference)

| Doc | Purpose |
| :--- | :--- |
| [AI_AGENT_BRIEF.md](AI_AGENT_BRIEF.md) | Minimum required context for any AI agent before working in this repo |
| [system_architecture_schema.md](system_architecture_schema.md) | kis-trader ↔ open-trading-api architecture map, ownership, trust boundaries |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Overall structure and directory roles |
| [current_cycle_pipeline.md](current_cycle_pipeline.md) | `run_cycle()` 사이클 국면 지도 (slimming Stage B의 기준 문서) |
| [lane_pipeline_design.md](lane_pipeline_design.md) | 레인 파이프라인 설계 (`LANE_SCHEDULER_ENABLED`, 기본 off) |
| [BACKTESTING.md](BACKTESTING.md) | Backtest environment and how to run it |
| [gs_quant_adoption_roadmap_20260902.md](gs_quant_adoption_roadmap_20260902.md) | GS Quant 포크 선별 도입 분류·절차·금지 경계와 1차 백테스트 위험진단 연결 기준 |
| [OPERATIONS.md](OPERATIONS.md) | Regular-session operation and environment setup |
| [postrun_audit_commands.md](postrun_audit_commands.md) | Commands to run after a regular session closes |
| [regular_session_observation_checklist.md](regular_session_observation_checklist.md) | What to watch during a regular session |
| [runbook_open_trading_api.md](runbook_open_trading_api.md) | open-trading-api 운영 runbook |
| [runbook_autotuner_loop.md](runbook_autotuner_loop.md) | autotuner 루프 운영 runbook |
| [live_autotuner_whitelist_bounds.md](live_autotuner_whitelist_bounds.md) | autotuner whitelist bounds (config/autotuner_whitelist.yaml 동반 기준) |
| [tools_inventory.md](tools_inventory.md) | `app/tools` 78개 전수 인벤토리 (2026-07-03 refresh — 분류·중복헬퍼 제안) |
| [api_budget_high_throughput_profile_20260612.md](api_budget_high_throughput_profile_20260612.md) | HT-1~4 API 예산 사다리 (env 오버라이드 전용 프로파일) |
| [SYMBOL_TAGS.md](SYMBOL_TAGS.md) | Symbol tagging system — current reference |
| [SLACK_ALERTS.md](SLACK_ALERTS.md) | Slack alert structure and rules |
| CHANGELOG.md (not included in this source snapshot) | Short record of major changes |

## 완료 기록 (closed — 참조 가치로 유지)

| Doc | 기록 내용 |
| :--- | :--- |
| [gate2_replay_plan_20260612.md](gate2_replay_plan_20260612.md) | gate2 리플레이 선행 계획 — §7/§8 게이트 증거 (20260702 계획의 선행 문서) |
| [overseas_order_flow_design_20260614.md](overseas_order_flow_design_20260614.md) | US 주문 플로우 설계 (shipped; live는 R7 운영자 게이트 유지) |
| [orchestrator_validation.md](orchestrator_validation.md) | 오케스트레이터 하네스 검증 증거 (`Agent`+`Task*` 경로) |
| [orchestrator_completion_plan.md](orchestrator_completion_plan.md) | 오케스트레이터 완성 계획 (2026-06-07 정합화 완료) |
| [gate2_shim_removal_plan_20260703.md](gate2_shim_removal_plan_20260703.md) | shim 제거 — **실행 완료** (`baf3f1f`/`eabecd7`, 소비자 0건) |
| [app_tools_inventory_plan_20260703.md](app_tools_inventory_plan_20260703.md) | tools 인벤토리 절차 — **실행 완료** (tools_inventory.md 2026-07-03 refresh) |
| [multi_account_phase2_runbook.md](multi_account_phase2_runbook.md) | multi-account Phase 2 운영자 runbook (툴링 완료 상태의 실행 안내) |
| [run_full154_fetch_inventory.md](run_full154_fetch_inventory.md) | `run_full154_fetch.sh` 참조 복구 조사 기록 |

## Agent workflows

Safe, read-only operational-analysis agent templates — [agent_workflows/README.md](agent_workflows/README.md) 참조 (postrun audit / reconciliation / config risk review / runtime monitor / backtest analyst / data quality).

## Solutions / decisions

Bug-fix knowledge base: [solutions/](solutions/) (`/ce-compound` 산출물).

## OLD (완료·대체 이력)

2026-07-03 정리로 이동된 51건: main split·R1~R10 리팩터 시리즈, autotuner 구축 시리즈, symbol-tags 감사/마이그레이션 시리즈, expected-return buffer 시리즈, 백테스터 W1~W7 노트, 0611plan·five-day replan 등 — [OLD/](OLD/). 그 이전 세대는 [archive/](archive/). 전부 이력 참조용이며 현행 운영 지침이 아니다. (완료 계획서 형식 예시가 필요하면 현행 [gate2_minute_backtest_plan_20260702.md](gate2_minute_backtest_plan_20260702.md)가 같은 r4 형식이다.)
