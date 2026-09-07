# app/tools flat-parquet 진입 가이드 (E17, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E17** 산출물.
> [tools_inventory.md](tools_inventory.md) §중복 헬퍼(2026-07-03 refresh)가 실측한 **중복 3블록**을
> 근거로, 신규 `app/tools` 도구가 지켜야 할 **단일 진입 경로 규칙**을 성문화한다. docs-only —
> 코드 변경 없음. 기존 중복은 상환 대상(별건)이고, 본 가이드는 **신규 도구의 재발 방지**가 목적.

---

## 왜 이 가이드인가

`app/tools`에는 grep으로 확인된 **동일 로직 3중복 패턴**이 있다(tools_inventory §중복 헬퍼 실측).
공통화 없이 각 CLI가 자기 블록을 복붙하면 **안전 가드가 조용히 드리프트**한다(특히 #1 env-guard).
신규 도구가 같은 실수를 반복하지 않도록, 진입 경로를 아래로 고정한다.

## 중복 3블록 (tools_inventory §중복 헬퍼 인용)

| # | 패턴 | 현재 중복처(실측) | 정본 경로 |
|---|------|------------------|-----------|
| 1 | `BUY_SCAN_QUOTE_KIS_ENV` 실행 거부 env-guard (동일 3줄) | `build_backfill_parquet.py:97`, `build_gate2_records.py:293`, `run_gate2_weight_search.py:756` | (상환 목표) `assert_buy_scan_quote_env_unset(tool_name)` 단일 함수 — **live quote-token 발급 차단 안전 가드라 drift 위험 최상위** |
| 2 | **flat-parquet read/write** (분산된 pyarrow 직접 접근) | analysis 계열은 `parquet_utils`로 이미 수렴 ✅; gate2 계열(`build_backfill_parquet`/`build_gate2_records`/`run_gate2_weight_search`)은 `app/research/…` 별도 경로 | **`app.tools.parquet_utils`** (`read_flat_parquet`/`write_flat_parquet`) |
| 3 | jsonl 스트리밍 (공통 reader vs 로컬 재구현) | `export_signal_dataset`/`export_signal_outcome_dataset`/`operational_day_analysis`가 자체 `_read_jsonl`; `measure_slippage`/`recommend_score_guards`는 raw `json.loads` 루프 | `app.core.jsonl.read_jsonl_objects` / `app.core.file_read_limits.iter_lines_bounded` (bounded-read 안전성 포함) |

## 신규 도구 규칙 (성문화 — 이 절이 E17의 Green)

**신규 `app/tools` 도구를 추가할 때:**

1. **flat-parquet 진입은 `app.tools.parquet_utils` 단일 경로로만.** 새 pyarrow 로더/라이터를
   재작성하지 않는다. 스키마가 다르면 `parquet_utils`에 헬퍼를 **추가**하고 재사용한다
   (복붙 금지). gate2 research 경로(`app/research/…`)를 쓰는 신규 도구도 flat-parquet 구간은
   `parquet_utils`로 통일한다.
2. **`BUY_SCAN_QUOTE_KIS_ENV` 실행 거부가 필요한 오프라인 배치 CLI**는 3줄 블록을 복붙하지 말고,
   공통 가드(상환 후 `assert_buy_scan_quote_env_unset`)를 호출한다. 상환 전이라면 기존 3블록과
   **글자까지 동일**하게 유지(drift 금지)하고 tools_inventory에 새 중복처를 기록한다.
3. **jsonl 읽기는 `app.core.jsonl` 공통 reader.** bounded-read(대용량 파일 안전)를 공통 reader가
   이미 제공하므로 로컬 `_read_jsonl`/raw 루프를 새로 만들지 않는다.
4. `data/`·`logs/`·`results/`·`archive/`는 전체 read 금지 — bounded tail/head만(공통 reader가 강제).

## 검증 (Green 재현)

- 중복 3블록 인용은 [tools_inventory.md](tools_inventory.md) §중복 헬퍼 실측 라인과 일치.
- 규칙 4항이 신규 도구 진입 경로를 flat-parquet=`parquet_utils` 단일로 고정 → E17 Green 충족.
- 본 가이드는 CLAUDE.md에서 참조 가능(신규 tools 추가 시 이 문서 확인). 상환(중복 실제 제거)은
  별건 리팩토링 — 본 문서는 신규 재발 방지선.
