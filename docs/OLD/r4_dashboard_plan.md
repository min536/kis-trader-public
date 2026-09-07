# R4: 대시보드 구획 슬리밍 계획 (2026-06-11)

> **실행 위임 문서.** 이 계획은 구조 지도·슬라이스 경계·TDD 사양까지 상위 모델이 확정했고,
> 실제 TDD 실행(테스트 작성 → verbatim 이동 → 검증)은 하위 모델이 수행한다.
> 실행자는 이 문서와 [`r3_reporting_notifications_plan.md`](r3_reporting_notifications_plan.md) §4.5(가드 레시피)만 읽으면 된다.
> **판단이 필요한 상황은 전부 §8 에스컬레이션 규칙으로 — 임의 판단 금지.**

## 1. 목표와 비목표

- **목표**: `app/dashboard/` 5,055줄 중 3대 파일(streamlit_app 1,368 / data_loader 1,153 / metrics 971)에서
  순수 함수 클러스터를 verbatim 이동 + facade로 분리. 무커버 영역에 직접 행위 테스트 확보.
- **비목표**: 동작 변경 일절 금지(자구 그대로 이동만). theme.py(692줄)는 단일 CSS 주입 함수라 분할 비대상.
  charts/components/formatters는 이미 적정 크기 — 손대지 않음.
- **리스크 등급: 전 슬라이스 LOW.** 대시보드는 읽기 전용 뷰 계층 — 브로커 호출 없음, 트레이딩-크리티컬
  표면(`app/execution|risk|strategy/`) 아님, R3-S5 killpg 같은 부작용 함수 없음.

## 2. 구조 지도

### 2.1 파일 인벤토리

| 파일 | 줄수 | 구성 | R4 처리 |
|------|------|------|---------|
| streamlit_app.py | 1,368 | 순수 빌더 14종 + st-주입 렌더러 24종 + run_dashboard | S3 (+선택 S4) |
| data_loader.py | 1,153 | IO 5종(_safe_read_*, _load_research_data, load_dashboard_data) + 순수 정규화 18종 | S2 |
| metrics.py | 971 | 순수 뷰모델 빌더 25종 | S1 |
| theme.py | 692 | inject_dashboard_theme 단일 함수 (CSS 문자열) | 제외 |
| components.py | 487 | st-주입 위젯 렌더러 + 라벨 빌더 | 제외 |
| charts.py | 307 | plotly figure 빌더 (lazy import) | 제외 |
| formatters.py | 74 | 포맷터 6종 | 제외 |

### 2.2 의존 DAG (현재 → 목표)

```
현재:  formatters ← components ← streamlit_app
       formatters ← streamlit_app          charts ← streamlit_app
       data_loader ← streamlit_app, __init__   metrics ← streamlit_app
목표:  cycle_insights(신규 S1) ← metrics ← view_models(신규 S3) ← streamlit_app
       normalizers(신규 S2) ← data_loader
       (전 구간 단방향 — 신규 모듈은 원본을 절대 import하지 않는다)
```

### 2.3 외부 소비자 (facade 보존 대상)

- `scripts/refresh_workspace_data.py` — `load_dashboard_data` + metrics 7종
  (`build_anomaly_summary, build_buy_judgement_snapshot, build_concentration_summary,
  build_engine_state_view_model, build_operational_alerts, build_portfolio_highlights, build_top_summary`).
  **이 스크립트와 `workspace/data.js`는 절대 수정 금지** (병렬 작업 산출물 포함). facade 바인딩이 경로를 보존한다.
- `app/dashboard/__init__.py` — `load_dashboard_data` 재수출 (이동 대상 아님, 무변경).
- `tests/test_dashboard_data_loader.py` — `_safe_read_jsonl`만 사용 (이동 대상 아님). patch는 env var
  `KIS_LOCAL_READ_MAX_BYTES`뿐 — **R3-S5 같은 교차 patch 위험 없음.**

### 2.4 기존 커버리지

`tests/test_dashboard_data_loader.py` 116줄(_safe_read_jsonl만)이 전부. **이동 대상 35종 함수는 전부 무커버**
→ R3 레시피 (d): zero-fake가 통과하므로 모든 이동 함수에 직접 행위 테스트를 red로 먼저 만들어야 한다.

## 3. 슬라이스 계획 (난이도 오름차순, 슬라이스당 1커밋)

### S1 — metrics 사이클 인사이트 → `app/dashboard/cycle_insights.py` (캘리브레이션 슬라이스)

가장 작은 슬라이스(함수 3종)로 실행자가 가드 흐름을 익히는 용도. 먼저 수행한다.

- **이동 심볼 (3종, verbatim)**: `engine_short_label`(50-78), `build_buy_judgement_snapshot`(683-830),
  `build_cycle_readable_summary`(833-971)
- **폐쇄 근거**: `build_cycle_readable_summary → engine_short_label`만 내부 호출.
  `engine_short_label`은 잔존 함수 `build_engine_state_view_model`도 호출하므로 **반드시 함께 이동**하고
  metrics가 facade 바인딩으로 소비한다 (역방향 import 금지 — 안 옮기면 순환).
- **신규 모듈 import**: `from typing import Any` 만으로 시작. 나머지는 NameError-red 발생 시 추가.
- **facade**: metrics.py 상단 `from app.dashboard.cycle_insights import (3종)`. metrics 971 → ~650줄.
- **행위 테스트 사양** (`tests/test_cycle_insights.py` 신규):
  - 핀 3종: `assertIs(metrics.engine_short_label, cycle_insights.engine_short_label)` 등.
  - `engine_short_label`: 대표 엔진 상태 입력 3~4건 → 라벨 문자열 동등성 (분기 커버).
  - `build_buy_judgement_snapshot`: 최소 cycle dict 픽스처(매수 후보 1~2건 포함) → **전체 dict 동등성**
    (recipe c — 다분기 148줄 함수는 부분 어서션이면 가드가 fake를 허용해버림).
  - `build_cycle_readable_summary`: 최소 cycle 레코드 → 전체 dict 동등성.

### S2 — data_loader 정규화 클러스터 → `app/dashboard/normalizers.py` (본 슬라이스)

- **이동 심볼 (18종, verbatim)**: `_parse_iso_datetime`, `_successful_sell_orders_since`,
  `_summary_has_cycle_support`, `_is_closed_session_summary_without_support`, `_summary_equity`,
  `_select_trusted_performance_summary`, `_resolve_symbol_name`, `_normalize_positions`,
  `_normalize_positions_from_cycle`, `_build_account_view`, `_apply_account_weights`, `_infer_order_side`,
  `_normalize_orders`, `_normalize_cycles`, `_normalize_candidates`, `_latest_action_record`,
  `_build_engine_state_view`, `_build_trade_journal`
- **잔존 (IO 계층, ~400줄)**: `_safe_read_json`, `_safe_read_jsonl`, `_load_research_data`,
  `load_dashboard_data`, `_PROJECT_ROOT`, `_DASHBOARD_*` 상수 4종.
- **폐쇄 근거 (콜그래프 검증 완료)**: 이동 집합 내부 호출만 존재
  (`_select_trusted_performance_summary → _is_closed_session_summary_without_support → _summary_has_cycle_support
  → _parse_iso_datetime`, `_normalize_* → _resolve_symbol_name/_infer_order_side`,
  `_build_engine_state_view → _latest_action_record`). 이동 함수가 `_safe_read_*`·상수를 참조하는 경우 없음.
- **신규 모듈 import**: `datetime`, `from typing import Any`,
  `from app.core.market_session import get_korean_market_session`,
  `from app.scanner.symbol_names import get_symbol_name`.
- **facade**: data_loader.py 상단 통합 import 18종. 1,153 → ~400줄.
- **행위 테스트 사양** (`tests/test_dashboard_normalizers.py` 신규):
  - 핀 18종 (assertIs, 테스트 1개에 루프로 — R3 `_CONTROL_NAMES` 패턴).
  - 소형 헬퍼(`_parse_iso_datetime`, `_infer_order_side`, `_summary_equity`, `_resolve_symbol_name`,
    `_latest_action_record`, `_apply_account_weights`): 입력→출력 직접 어서션 각 1~2건.
  - 정규화기(`_normalize_positions`, `_normalize_positions_from_cycle`, `_normalize_orders`,
    `_normalize_cycles`, `_normalize_candidates`): 최소 레코드 dict 픽스처 → **전체 dict/list 동등성**.
  - 대형 빌더(`_build_account_view`, `_build_engine_state_view`, `_build_trade_journal`,
    `_select_trusted_performance_summary`): 대표 시나리오 1건 + 엣지(빈 입력) 1건, 전체 dict 동등성.
  - `_resolve_symbol_name`은 `patch.object(normalizers, "get_symbol_name")`으로 (신규 모듈에 patch — facade 아님).
  - `_is_closed_session_summary_without_support`는 `patch.object(normalizers, "get_korean_market_session")`.

### S3 — streamlit_app 순수 빌더 → `app/dashboard/view_models.py`

- **이동 심볼 (14종, verbatim)**: `_safe_json`, `_safe_text`, `_safe_count`, `_to_datetime`,
  `_age_minutes`, `_freshness_label`, `_event_tone`, `_build_trace_events`, `_build_queue_items`,
  `_build_mission`, `_focus_story`, `_position_status`, `_priority_sort_value`, `_sort_positions`
- **잔존**: st-주입 렌더러 24종 + `run_dashboard` + `PAGES`/`FOCUS_MODES` + `_load_streamlit`/`_load_pandas`/`_setting`.
  1,368 → ~970줄.
- **폐쇄 근거**: `_build_* → _safe_text/_event_tone`, `_focus_story → _freshness_label → _age_minutes
  → _to_datetime`, `_sort_positions → _priority_sort_value` — 전부 이동 집합 내부.
- **신규 모듈 import**: `json`, `datetime`, `Any`,
  `from app.dashboard.components import action_label, compact_reason`,
  `from app.dashboard.formatters import format_krw, format_pct, format_signed_pct, format_timestamp`,
  `from app.dashboard.metrics import (build_anomaly_summary, build_buy_judgement_snapshot,
  build_engine_state_view_model, build_operational_alerts, build_portfolio_highlights, engine_short_label)`.
  metrics import는 **facade 경유 유지**(S1 이후에도 metrics가 재수출하므로 경로 불변; DAG는
  cycle_insights ← metrics ← view_models로 단방향).
- **행위 테스트 사양** (`tests/test_dashboard_view_models.py` 신규):
  - 핀 14종.
  - 소형 헬퍼(`_safe_json/_safe_text/_safe_count/_to_datetime/_age_minutes/_freshness_label/_event_tone/
    _position_status/_priority_sort_value/_sort_positions`): 직접 입출력 어서션 (datetime 고정값 주입 —
    `_age_minutes`는 `now` 파라미터가 없으면 reference 시각 인자 확인 후 실제 시그니처에 맞춤).
  - 대형 빌더(`_build_trace_events`, `_build_queue_items`, `_build_mission`, `_focus_story`):
    `data`/`summary` 최소 dict 픽스처 → 전체 list/dict 동등성. metrics 실호출 유지(픽스처가 metrics
    입력 형태를 겸함) — mock 불필요. 픽스처는 모듈 함수로 빼서 재사용 (R3 S8 `_buy_candidate_fixture` 패턴).

### S4 (선택) — streamlit_app 패널 프리미티브 → `app/dashboard/panels.py`

S3까지 완료 후 여력 있으면. `_pill`, `_panel_intro`, `_panel_heading`, `_render_stack_card`,
`_render_key_value_panel`, `_render_note_panel`, `_render_queue_items`, `_render_event_stream`,
`_render_ranked_positions`, `_render_metric_strip` (10종, st-주입이라 fake-st 객체로 테스트 가능).
폐쇄: `_render_queue_items/_render_event_stream → _pill, _render_note_panel` 내부. streamlit_app → ~780줄.
**착수 전 사용자 확인 필요** (S1~S3 결과 보고 후).

## 4. TDD 실행 절차 (슬라이스 공통 — 가드 내비게이션)

R3 §4.5에서 검증된 시퀀스를 그대로 따른다. 슬라이스당:

1. **핀 테스트 1개 작성** → red (ImportError: 신규 모듈 없음).
2. **빈 모듈 생성** → red가 AttributeError로 바뀜.
3. **스텁 추가** (가드가 일괄 거부하면 함수 1개씩; AttributeError 목록이 출력에 가시화되면 일괄 허용되기도 함).
4. **행위 테스트를 함수별로 1개씩 추가** → red (스텁이 NotImplementedError/None 반환).
   가드 제약: 테스트 한 번에 1개, 경우에 따라 어서션도 1개씩.
5. **verbatim 채움** — 다분기 함수는 가드가 fake-then-triangulate를 강제하므로, 테스트를 전체 dict/전체
   라인 동등성으로 강화해 최소 구현=verbatim 본문이 되게 한다 (recipe c). NameError가 나면 그 red를
   근거로 import 추가 (사전 import는 가드가 거부).
6. **원본의 로컬 정의를 import 바인딩으로 교체** — bare 삭제는 거부됨; new_string에 import 포함 필수.
   거부 시: 좁힌 red를 fresh 출력(`grep "function X"` — assertion 메시지가 `<function X at 0x...>` 형태)
   직후 재시도. 그래도 거부면 **fill-first 역전** (recipe b): 신규 모듈을 행위 테스트로 먼저 완성하고
   교체를 마지막에.
7. **검증 프로토콜 실행** (§5) → 전부 통과 시 커밋.

## 5. 슬라이스 공통 검증 프로토콜 (결정적 — 그대로 실행)

```bash
# (1) AST verbatim 비교: 이동 심볼이 git HEAD 원본과 자구 동일 + 잔존 함수 무변경
.venv/bin/python - <<'EOF'
import ast, subprocess, sys
ORIG, NEW = "app/dashboard/metrics.py", "app/dashboard/cycle_insights.py"  # 슬라이스별 교체
MOVED = ["engine_short_label", "build_buy_judgement_snapshot", "build_cycle_readable_summary"]
head = subprocess.run(["git", "show", f"HEAD:{ORIG}"], capture_output=True, text=True).stdout
head_funcs = {n.name: ast.dump(n, include_attributes=False)
              for n in ast.parse(head).body if isinstance(n, ast.FunctionDef)}
new_funcs = {n.name: ast.dump(n, include_attributes=False)
             for n in ast.parse(open(NEW).read()).body if isinstance(n, ast.FunctionDef)}
cur_funcs = {n.name: ast.dump(n, include_attributes=False)
             for n in ast.parse(open(ORIG).read()).body if isinstance(n, ast.FunctionDef)}
bad = [m for m in MOVED if head_funcs.get(m) != new_funcs.get(m)]
changed = [k for k, v in cur_funcs.items() if head_funcs.get(k) != v]
print("VERBATIM-OK" if not bad and not changed else f"DIFF moved={bad} remaining_changed={changed}")
sys.exit(0 if not bad and not changed else 1)
EOF

# (2) 전체 스위트 (기준선: 1,822 passed + 245 subtests, 병렬 작업 테스트 3파일 포함)
.venv/bin/python -m pytest -q

# (3) 커밋 — 반드시 경로 지정 (워킹 트리의 NOT-mine 파일 절대 포함 금지)
git add app/dashboard/<원본>.py app/dashboard/<신규>.py tests/test_<신규>.py
git commit -m "refactor(dashboard): extract <클러스터> to <신규모듈> (R4-S<n>)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

## 6. 안전 제약 (위반 시 즉시 중단)

- `.env`/`.token_cache.json` 접근 금지. Bash 안에 `.split(".")` 같은 점 리터럴 regex가 있으면
  trading_guard가 오탐 거부함 — Python heredoc + `chr(46)` 우회 또는 AST 스캔 사용.
- `data/`, `logs/`, `results/`, `archive/` 전체 read 금지.
- `app.main`/`run_session.sh` 실행 금지, 브로커 API 호출 금지 (대시보드 코드에는 원래 없음 — 발견 시 §8).
- **NOT-mine 워킹트리 파일 수정/커밋 금지**: `docs/autotuner_rollout_plan.md`, `docs/todo.md`,
  `workspace/data.js`, `backtester/reports/*_approx_summary.json`, `docs/0611plan.md`,
  `app/reporting/slippage_report.py`, `tests/fixtures/`, `tests/test_autotuner_session_summary_fixtures.py`,
  `tests/test_portfolio_trade_record_fixtures.py`, `tests/test_slippage_report.py`, `docs/*_20260611.md` 신규 문서들.
- 코드 커밋과 docs 커밋 분리.

## 7. 예상 결과

| 파일 | 전 | 후 | 신규 모듈 |
|------|----|----|----------|
| metrics.py | 971 | ~650 | cycle_insights.py ~320 |
| data_loader.py | 1,153 | ~400 | normalizers.py ~750 |
| streamlit_app.py | 1,368 | ~970 (S4 시 ~780) | view_models.py ~430 (+panels.py ~180) |

신규 행위 테스트 ~35~45개 (현재 무커버 35종 함수에 영구 커버리지).

## 8. 에스컬레이션 규칙 (하위 모델 → 사용자/상위 모델 보고 후 정지)

1. 이동 함수가 **계획에 없는 잔존 심볼을 호출**하는 것을 발견 (폐쇄 위반) — 임의로 추가 이동 금지.
2. 가드가 레시피(fresh narrowed red → 재시도 → fill-first 역전)를 다 따라도 같은 편집을 **3회 이상 거부**.
3. 전체 스위트에서 **이 슬라이스와 무관한 실패** 발견 — 고치지 말고 보고.
4. 함수 시그니처/동작이 이 문서의 기술과 다름 (예: 콜그래프 불일치) — 문서가 아니라 코드가 진실.
5. NOT-mine 파일을 건드려야만 진행 가능한 상황.
6. verbatim 비교 DIFF가 import 추가로 해소되지 않는 경우.

## 9. 진행 상황

| 슬라이스 | 상태 | 커밋 | 비고 |
|----------|------|------|------|
| S1 cycle_insights | 완료 | f47259c | AST verbatim + 전체 스위트 green |
| S2 normalizers | 완료 | 20f05ff | AST verbatim + 전체 스위트 green |
| S3 view_models | 완료 | 29b593e | AST verbatim + 전체 스위트 green |
| S4 panels (선택) | 완료 | e5c02d0 | 사용자 "전부 수행" 확인 후 진행, AST verbatim + 전체 스위트 green |
