# Log & Data File Retention Plan

> 작성일: 2026-05-10 · **재구성: 2026-07-03 (fable 실행 위임 형식)**
> 상태: Dashboard loader recent/tail mode 완료(2026-06-07). **rotate/gzip/retention 구현은 미착수 — 잔여 작업은 §9 실행 위임 슬라이스로 정의.** §2~§3 인벤토리는 **2026-07-03 D0 재측정 스냅샷**(측정: `du -sh`/`find -size`/`wc -l`/`ls`, 메타데이터만).

## 1. 목적

kis-trader 프로젝트의 대용량 로그/데이터 파일 현황을 정리하고,
무엇을 유지(keep)하고 무엇을 rotate/gzip/archive/delete 후보로 둘지 정책을 명문화한다.

이 문서는 **정책 문서**이며, 실제 파일 삭제/압축/이동은 별도 작업에서 수행한다.

## 2. 현재 대용량 파일 현황

> 스냅샷: **2026-07-03** (D0 재측정). 최상위 디렉터리 합계: `data/` 14G · `logs/` 1.9G · `results/` 354M · `archive/` 1.0G (repo 총 18G).
> 05-10 스냅샷 수치는 삭제됨 — git 이력에서 참조 가능. 이번 측정 이후 `data/`가 크게 증가한 주 원인은 신규 `data/toss_minute_raw_backfill/` (11G, minute-bar 백필 배치).

### 2.1. Top Size 파일 (10MB 이상)

| 파일 | 크기 | 줄 수 | 분류 |
|------|------|-------|------|
| `data/cycle_snapshots_mock_12345678_01.jsonl` | 609MB | 30,654 | active runtime snapshot |
| `archive/orders_mock_acct_0123456789abcdef_oversized_20260701_1934.jsonl` | 384MB | 10,304 | historical (oversized-rotated) orders |
| `data/cycle_snapshots_mock_acct_0123456789abcdef.jsonl` | 348MB | 4,550 | active runtime snapshot (신규 acct) |
| `data/toss_minute_raw_backfill/fetch_manifest.jsonl` | 277MB | 1,523,959 | minute-bar backfill manifest (배치 산출) |
| `archive/orders_mock_acct_0123456789abcdef_oversized_20260703_090811.jsonl` | 227MB | 665 | historical (oversized-rotated) orders |
| `archive/data/cycle_snapshots_mock_12345678_01.jsonl` | 210MB | 10,889 | historical snapshot |
| `logs/orders_mock_acct_0123456789abcdef.jsonl` | 168MB | 392 | active orders log (신규 acct) |
| `logs/orders_mock_12345678_01.jsonl` | 105MB | 40,228 | active orders log |
| `logs/performance_summary_mock_acct_0123456789abcdef.jsonl` | 96MB | — | active perf summary (신규 acct) |
| `logs/performance_summary_mock_12345678_01.jsonl` | 95MB | — | active perf summary |
| `logs/ml/ml_candidate_dataset_mock_acct_0123456789abcdef_*_labeled.jsonl` | 25–84MB (각) | — | ML training data (다수 파일, 합계 902MB) |
| `archive/logs/app_stdout_20260415.log` | 74MB | 2,198,296 | historical stdout log |
| `archive/logs/performance_summary_mock_12345678_01.jsonl` | 45MB | — | historical perf summary |
| `logs/candidate_outcomes_mock_12345678_01_20260420.jsonl` | 39MB | — | historical candidate log |
| `archive/logs/orders_mock_12345678_01.jsonl` | 32MB | — | historical orders log |
| `archive/data/cycle_snapshots.jsonl` | 25MB | — | historical snapshot |
| `logs/slack_bot.log` | 17MB | 153,519 | slack bot log |
| `results/gate2_backtest/records/records_*_h{10,30,60}.parquet` | 14–15MB (각) | — | gate2 record parquet |
| `logs/app_stdout_20260508.log` | 15MB | 489,166 | recent stdout log |
| `archive/logs/candidate_outcomes_mock_12345678_01_20260414.jsonl` | 15MB | — | historical candidate log |
| `logs/candidate_outcomes_mock_acct_0123456789abcdef_20260630.jsonl` | 13MB | — | candidate log (신규 acct) |
| `logs/toss_minute_backfill_20260625.log` | 11MB | — | minute backfill run log |
| `logs/signal_dataset_mock_12345678_01_2026042{1,7}.jsonl` | 11MB (각) | — | signal dataset log |
| `archive/logs/performance_summary.jsonl` | 10MB | — | historical perf summary |

### 2.2. Top Line-Count 파일

| 파일 | 줄 수 | 비고 |
|------|-------|------|
| `archive/logs/app_stdout_20260415.log` | 2,198,296 | 최대 단일 로그 줄 수 |
| `data/toss_minute_raw_backfill/fetch_manifest.jsonl` | 1,523,959 | minute-bar 백필 manifest (신규) |
| `logs/app_stdout_20260508.log` | 489,166 | recent stdout log |
| `logs/slack_bot.log` | 153,519 | slack bot log |
| `results/full154_research_round2/*.json` | 각 ~92,700 | backtest result (다수 파일) |
| `logs/orders_mock_12345678_01.jsonl` | 40,228 | active orders log |
| `data/cycle_snapshots_mock_12345678_01.jsonl` | 30,654 | active snapshot (줄당 payload 대형) |

### 2.3. Git 추적 상태

모든 대용량 파일은 `.gitignore`에 의해 git 추적 대상에서 제외됨:

- `.gitignore:7:data/` → `data/` 전체
- `.gitignore:6:logs/` → `logs/` 전체
- `.gitignore:14:archive/` → `archive/` 전체
- `results/` → gitignore 대상

## 3. 900만 줄 이슈 해석

- **단일 900만 줄 파일은 발견되지 않았다.**
- 전체 데이터/로그성 파일(data/, logs/, results/, archive/ 하위 `*.jsonl`/`*.log`/`*.json`/`*.ndjson`)의 줄 수 합계 = 약 **21,553,086줄** (2026-07-03 `wc -l` total 기준; 05-10 대비 ~2배 증가).
- 증가 주 원인: 신규 `data/toss_minute_raw_backfill/fetch_manifest.jsonl` (1,523,959줄) 등 minute-bar 백필 산출물 + 누적 로그.
- 이 수치는 repo 전체 데이터 파일의 누적 합계이며, results/ 내 backtest JSON 파일들(각 ~9.3만 줄, 다수)이 합계의 상당 부분을 차지한다.
- 최대 단일 파일은 여전히 `archive/logs/app_stdout_20260415.log` (2,198,296줄, 74MB).

## 4. 삭제하면 안 되는 Active 파일

아래 파일들은 현재 runtime이 읽거나 쓰는 active 파일이므로 삭제/이동 금지:

| 파일 패턴 | 용도 |
|-----------|------|
| `data/runtime_state*.json` | runtime 상태 저장/복원 |
| `logs/orders_mock_12345678_01.jsonl` | 현재 주문 기록 |
| `data/cycle_snapshots_mock_12345678_01.jsonl` | 현재 cycle snapshot (dashboard 사용) |
| `logs/performance_summary_mock_12345678_01.jsonl` | 현재 성과 요약 |
| `data/universe_batches/*` | 현재 universe batch data |

## 5. Rotate/Gzip/Archive 후보

### 5.1. 즉시 gzip 가능 (historical, 읽기 전용)

| 대상 | 크기 | 사유 |
|------|------|------|
| `archive/logs/app_stdout_20260415.log` | 74MB | historical, 일상적으로 참조하지 않음 |
| `archive/data/cycle_snapshots_mock_12345678_01.jsonl` | 210MB | historical snapshot |
| `archive/logs/performance_summary_mock_12345678_01.jsonl` | 45MB | historical |
| `archive/logs/orders_mock_12345678_01.jsonl` | 32MB | historical |
| `archive/data/cycle_snapshots.jsonl` | 25MB | historical |
| `archive/logs/candidate_outcomes_mock_12345678_01_20260414.jsonl` | 15MB | historical |
| `archive/logs/performance_summary.jsonl` | 10MB | historical |

### 5.2. 날짜 경과 후 gzip 후보

| 대상 | 크기 | 조건 |
|------|------|------|
| `logs/candidate_outcomes_*_20260420.jsonl` | 39MB | 20일 이상 경과 |
| `logs/candidate_outcomes_*_20260424.jsonl` | 9.0MB | 16일 이상 경과 |
| `logs/candidate_outcomes_*_20260421.jsonl` | 9.5MB | 19일 이상 경과 |
| `logs/candidate_outcomes_*_20260423.jsonl` | 6.7MB | 17일 이상 경과 |
| `logs/candidate_outcomes_*_20260417.jsonl` | 9.9MB | 23일 이상 경과 |
| `logs/candidate_outcomes_*_20260416.jsonl` | 8.8MB | 24일 이상 경과 |
| `logs/cycle_stats_*_20260419.jsonl` | 6.2MB | 21일 이상 경과 |
| `logs/cycle_stats_*_20260420.jsonl` | 5.5MB | 20일 이상 경과 |
| `logs/app_stdout_20260508.log` | 10MB | 2일 경과, 14일 후 gzip 후보 |

### 5.3. Research results archive 후보

| 대상 | 크기 | 비고 |
|------|------|------|
| `results/full154_research_round2/` | 47MB | experiment 단위 archive |
| `results/aggressive_grid_round2/` | 25MB | experiment 단위 archive |
| `results/aggressive_grid_round3/` | 17MB | experiment 단위 archive |
| `results/aggressive_grid_round1/` | 11MB | experiment 단위 archive |
| `results/diagnostics/` | 31MB | 개별 실험 결과 |
| `results/full154_compare/` | 7.8MB | experiment 결과 |
| `results/full154_budget_refine/` | 8.4MB | experiment 결과 |

## 6. Dashboard 성능 이슈

### 6.1. 문제 및 현재 상태

작성 당시 `app/dashboard/data_loader.py`의 `_safe_read_jsonl()` 함수는 JSONL 파일 전체를 메모리에 로드했다:

```python
# data_loader.py:42
for raw_line in path.read_text(encoding="utf-8").splitlines():
```

이로 인해 `data/cycle_snapshots_mock_12345678_01.jsonl` (342MB, 24,445줄)을 dashboard가 매번 전체 로드하며:
- 메모리 342MB+ 소비
- 줄당 payload가 대형이라 JSON parse 부담
- dashboard 초기 로딩 시간 증가

같은 패턴으로 `logs/orders_mock_12345678_01.jsonl` (35MB), `logs/performance_summary_mock_12345678_01.jsonl` (44MB)도 전체 로드됨.

2026-06-07 현재 dashboard loader는 `max_lines` 기반 recent/tail mode를 사용한다.

- `_safe_read_jsonl(..., max_lines=N)`은 `iter_tail_lines_bounded()`로 파일 끝에서 필요한 tail window만 읽음
- cycle snapshots, orders, performance summaries/snapshots는 dashboard 전용 max-lines 설정을 적용
- full read 경로는 여전히 전체 파일 크기 제한(`KIS_LOCAL_READ_MAX_BYTES`)을 적용
- tail mode도 읽기 window 및 줄 길이 제한을 적용해 과도한 단일 라인/무개행 파일을 fail-closed 처리

### 6.2. 사용처

- `data_loader.py` — cycle_snapshots tail read (`DASHBOARD_CYCLE_SNAPSHOTS_MAX_LINES`, 기본 1000)
- `data_loader.py` — orders tail read (`DASHBOARD_ORDERS_MAX_LINES`, 기본 2000)
- `data_loader.py` — performance_summary/performance_snapshots tail read (각 기본 1000)
- `workspace/claude-design/kis-trader-v2/server.py` — 위 데이터를 v2 site `/api/kt-data`로 렌더링
- `app/tools/eod_health_check.py` — candidate_outcomes, cycle_snapshots 참조
- `app/tools/compare_runtime_quality.py` — cycle_stats, orders 참조
- `app/tools/recommend_score_guards.py` — candidate_outcomes 참조

### 6.3. 권장 개선

- Dashboard recent/tail mode: 완료
- cycle_snapshots/orders/performance_summary: dashboard 로딩 시 최근 N건 제한 적용 완료
- 남은 개선: runtime 파일 자체의 rotate/gzip/archive 정책 구현
- 남은 개선: dashboard 외 batch/진단 도구의 대용량 JSONL 접근 패턴은 도구별로 별도 점검

## 7. 권장 Retention 정책

| 범주 | 보존 기간 | 형식 | 비고 |
|------|-----------|------|------|
| Active current session | 무기한 유지 | plain JSONL/JSON | runtime_state, 현재 orders, 현재 cycle_snapshots |
| Recent logs (7일 이내) | 7일 | plain JSONL/log | candidate_outcomes, cycle_stats, app_stdout |
| Recent logs (7~14일) | 14일 | plain JSONL/log | 필요 시 즉시 참조 가능 |
| Old logs (14일 초과) | 90일 보관 후 삭제 검토 | gzip 압축 | archive/ 하위로 이동 후 gzip |
| Archive historical | 180일 보관 후 삭제 검토 | gzip 압축 | archive/ 내 파일 |
| Research results | 실험 단위로 보관 | tar.gz archive | experiment 완료 후 압축 |
| ML training data | 실험 단위로 보관 | plain or gzip | 재현성 위해 장기 보관 권장 |

## 8. 실제 정리 전 금지사항

1. **Active file 삭제 금지** — runtime_state, 현재 orders, 현재 cycle_snapshots, 현재 performance_summary는 절대 삭제하지 않는다.
2. **app.main 실행 중 rotate 금지** — 트레이딩 실행 중에는 로그 파일을 이동/압축/삭제하지 않는다.
3. **Archive라도 확인 없이 삭제 금지** — archive/ 내 파일도 삭제 전 내용을 확인한다.
4. **Dashboard/postrun이 읽는 파일을 임의 이동 금지** — data_loader.py, eod_health_check.py, compare_runtime_quality.py가 참조하는 경로의 파일을 이동할 때는 코드 수정이 선행되어야 한다.
5. **코드 수정 없이 파일 경로 변경 금지** — 파일을 이동하면 참조하는 코드도 함께 수정한다.

## 9. 잔여 작업 — 실행 위임 슬라이스 (2026-07-03 재구성, r4 형식)

> tdd_guard: 테스트 1개씩, red 확인 후 구현. §8 금지사항이 모든 슬라이스에 우선한다.
> **파일 삭제/압축/이동의 실제 실행은 전부 운영자 게이트** — 에이전트는 도구·정책 검사기를 만들고 드라이런 출력까지만.

- **D0 재인벤토리 (선행, read-only)**: §2 스냅샷(2026-05-10)을 현재 값으로 갱신 — `du`/`ls`/`wc -l`(bounded, per-file tail 금지 불필요 — 메타데이터만)로 §2 표 재작성. 산출물: 본 문서 §2 갱신 + 신규 후보 목록. 코드 무변경.
- **D1 cycle snapshot rotation — 리스크 게이트 판정 반영(2026-07-03, `_workspace/retention_d1_risk_register.md`):** 원안(런타임 writer 날짜별 분리 + reader 집계)은 **DO-NOT-PROCEED** — ① `history.py:227-234`가 단일 파일만 읽고 전일 병합이 없어 회전 시 아침 빈 히스토리가 라이브 스코어링 5개 math 모듈에 유입(CRITICAL), ② seam 우회 하드코딩 reader 10개(`export_signal_dataset.py:170`, `eod_health_check.py:96` 등)가 조용히 드리프트, ③ F2-d 패리티/gate2 심 시그니처 파손. **채택안: (c) 오프라인 운영자 rotation 스크립트** — 런타임 무변경, D2와 동일한 분류·세션락 fail-closed 가드·apply 패턴으로 active oversized 파일(D3 검사기가 경고하는 100MB 초과 4건)을 세션 외 시간에 회전. 구현은 D2 모듈 확장(`--rotate-active` 모드 또는 별도 도구), 실행은 항상 운영자. (b) 세션 시작 rename rollover는 런타임 회전이 정말 필요해질 때의 조건부 경로(경로 해석기 무변경 + 락 fail-closed + byte-identical off 증명 조건)로만 남긴다.
- **D2 gzip archive 스크립트 (도구만, 실행은 운영자)**: `app/tools/retention_gzip.py` — §7 정책표를 코드화(14일 초과 → archive/ 이동+gzip 후보 목록), **기본 dry-run**(목록 출력만), `--apply`는 §8 금지사항 검사(active-file 패턴 제외, `app.main` 프로세스 감지 시 거부) 통과 시에만. TDD: 후보 판정 순수함수부터.
- **D3 retention 검사기 (read-only)**: `app/tools/retention_check.py` — §7 정책 위반(보존기간 초과 plain 파일 등) 탐지·보고서 출력. postrun 루틴(`docs/postrun_audit_commands.md`)에 1줄 추가 제안까지.
- **D4 results 실험 아카이브 (운영자 실행)**: 실험 단위 tar.gz 후보 목록을 D3 검사기가 산출 — 압축 실행은 운영자.

검증 게이트(슬라이스 공통): 전체 스위트 green + D1은 rotation off 상태 byte-identical 동작 증명(스냅샷 비교 테스트) + 각 도구는 dry-run 출력 증거 첨부. 에스컬레이션: reader 목록(§6.2)에 없는 신규 소비자가 발견되면 중단·보고; `data/` 경로를 쓰는 테스트 픽스처 간섭 발견 시 중단·보고.

### 9.1 상세 실행 사양 (2026-07-03 실사 기반 — 인용 검증만, 재조사 불필요)

**구조 지도:**
- 스냅샷 writer: `app/reporting/cycle_snapshots.py:446` (`open("a")` append 1곳). 경로 해석: `app/auth/account_scope.py:178::get_cycle_snapshots_path(settings)` — 단일 경로 계약.
- reader 12+ 파일: `app/main.py`, `app/math_models/history.py`(`_cycle_snapshots_file`), dashboard `data_loader`, `app/reporting/cycle_serializers.py`, tools(`export_signal_dataset`, `analyze_budget_bottlenecks`, `core_bucket_console`, `preopen_readiness_check`, …), research replay(테스트 심). **D1은 이 전 소비자 계약에 걸린다 → risk-analyst 게이트 결과에 따라 설계 확정.**
- 라이브 세션 감지(D2 `--apply` 가드): `app/core/session_lock.py::read_lock_metadata`(75) + `process_alive`(26) + `same_program_running`(64) — 락을 건드리지 않는 read-only 판정. **metadata 부재/판정불가 = unknown → apply 거부(fail-closed).**
- bounded read 유틸: `app/core/file_read_limits.py`, `app/core/jsonl.py` — 신규 도구는 이것만 사용(원본 전체 파싱 금지; D0는 `du`/`ls`/`wc -l` 메타데이터만).

**D2 사양** (`app/tools/retention_gzip.py` + `tests/test_retention_gzip.py`):
1. 순수 분류 함수 `classify_retention_candidates(entries, *, today, policy)` — 입력은 `(path, size, mtime)` 메타데이터 시퀀스(파일 내용 비접촉). §7 정책 코드화: `logs/`·`data/` 하위 14일 초과 → archive/{logs|data}/ 이동+gzip 후보; §4 active 패턴(`runtime_state*.json`, 날짜 접미사 없는 현행 `orders_*`/`cycle_snapshots_*`/`performance_summary_*`, `universe_batches/`) 무조건 제외; 판정불가 파일은 `skip(reason)`으로 보고(§8-3).
2. CLI 기본 = **dry-run**(후보 표 출력만). `--apply`: (a) 세션 락 판정으로 라이브 app.main 감지 시/판정불가 시 거부, (b) 파일별 gzip(stdlib)+이동, 실패 시 해당 파일 skip+사유(부분 실패가 전체 중단 아님), (c) 원본 삭제는 gzip 무결성 확인(`gzip -t` 상당: 재열기 1회) 후.
3. TDD 순서: 분류 경계(13일/14일/15일) → active 제외 → unknown-lock 거부 → apply 부분실패 격리. tmp_path 픽스처만 사용, 실제 `data/`·`logs/` 접촉 금지(테스트·구현 공통).

**D3 사양** (`app/tools/retention_check.py` + `tests/test_retention_check.py`): D2 분류 함수 재사용(중복 구현 금지), 위반 보고서(정책 초과 plain 파일, 100MB 초과 active 파일 경고)를 stdout+`--json <path>`로. 쓰기는 보고서 파일뿐. `docs/postrun_audit_commands.md`에 검사기 1줄 추가.

**D0 사양**: §2 표를 2026-07-03 값으로 교체(스냅샷 날짜 갱신, 05-10 수치는 삭제 — git 이력에 있음). 측정은 `du -sh`·`find -size`·`wc -l`·`ls`만.

## 10. 이력 메모

- 2026-05-12 월요일 세션 전 정리 금지 권고(원문 §10)는 기간 경과로 소멸 — §8 금지사항은 상시 유효.
