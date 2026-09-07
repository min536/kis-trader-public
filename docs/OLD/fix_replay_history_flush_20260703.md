# Fix: ReplayHistoryWriter chunk-boundary flush contract (2026-07-03)

> **결말 (2026-07-03, 같은 날 상류 해결 — 본 계획 실행 불필요/폐기):** 본 문서 작성 시점(스냅샷: 1 failed, 2877 passed)과 위임 실행 시점 사이에 WIP 작성자가 병행 세션에서 해당 테스트의 계약 자체를 인메모리 버퍼 검증(`history_writer.build_symbol_histories(...)`)으로 재정의했고, 스캐너도 JSONL 파일이 아닌 버퍼를 소스로 읽도록 배선함(JSONL은 lazily-flushed 디버그 산출물로 격하). 위임 에이전트는 §6에 따라 구현 없이 에스컬레이션했고(전체 스위트 2878 passed, 0 failed 관찰), 오케스트레이터 판정은 **WIP 설계 수용(A)** — §2의 "파일 내구성 계약 복원"은 구 스냅샷 기준 판단으로 폐기. §3-§4는 실행하지 말 것.

> **실행 위임 문서** (r4 형식). 원인 규명은 상위 모델이 완료(재현 관찰 + diff 실사); 실행(red 확인 → 최소 구현 → green)은 위임 모델이 수행한다. tdd_guard: 테스트 1개씩, red 확인 후 구현. 판단이 필요하면 §6 에스컬레이션 — 임의 판단 금지.

## 1. 증상 (재현 확인됨)

```
.venv/bin/python -m pytest tests/test_build_gate2_records_cli.py::test_cli_shares_history_across_monthly_chunks -q
```

→ `FileNotFoundError: .../gate2_records_cli_*/cycle_snapshots.jsonl` (2026-07-03 전체 스위트에서 **유일한** 실패: 1 failed, 2877 passed, 1 skipped, 719 subtests).

## 2. 원인 사슬 (확정 — 재조사 불필요, 인용만 검증)

1. 미커밋 WIP(메모리 최적화)가 `ReplayHistoryWriter`(`app/research/replay/scan_driver.py:111`)를 버퍼링 방식으로 변경: `append_tick`은 `_ticks_since_flush >= self._history_refresh_ticks`(기본 390)일 때만 `_flush()` 호출. `reseed()`는 항상 `_flush()` 호출.
2. CLI `app/tools/build_gate2_records.py::run()`은 월 청크 루프에서 `build_records(...)`를 호출하고 청크 종료 시점에 flush하지 않는다.
3. 커밋된 테스트(`tests/test_build_gate2_records_cli.py:226`)는 각 청크 직후 spy에서 JSONL 파일 내용을 읽어 "reseed-not-wipe" 계약(6월 레코드 뒤 7월 레코드)을 검증한다. 테스트의 틱 수 < 390이고 첫 청크는 reseed가 없으므로 파일이 한 번도 생성되지 않음 → FileNotFoundError.

계약 판단: JSONL은 WIP에서 디버그 산출물로 격하됐지만, **청크 경계의 파일 내구성은 커밋된 테스트가 문서화한 계약**(월 경계 히스토리 연속성의 증명 수단)이다. 테스트를 고치지 말고 계약을 복원한다.

## 3. 수정 스펙 (최소 변경 2곳)

1. `ReplayHistoryWriter`에 공개 `flush()` 추가 (`app/research/replay/scan_driver.py`):
   - `_flush()` 위임 + `_ticks_since_flush = 0` 리셋 (현재 `_flush()` 내부에서 리셋하는지 먼저 읽고, 리셋 위치를 기존 코드와 일관되게).
   - 시맨틱: 버퍼가 비어 있어도 파일은 생성/갱신된다(`_flush()`가 전체 `self._records`를 write_text — 덮어쓰기 시맨틱 유지, append 금지).
2. CLI `run()` 월 청크 루프(`app/tools/build_gate2_records.py`)에서 각 `build_records(...)` 반환 직후(try/finally 블록 다음, parquet write 전) `history_writer.flush()` 호출.

변경하지 말 것: `history_refresh_ticks` 기본값(390), gc 제어 블록, `rollup_lookback_days`, in-memory histories 메커니즘, 런타임 모듈(`app/math_models/history.py` 포함 — 이미 WIP에 포함된 상태 그대로 둔다).

## 4. TDD 순서

1. **Red 확인**: §1 명령으로 기존 실패 재현을 먼저 관찰.
2. **유닛 red 1개 추가**: writer의 기존 유닛 테스트 파일을 찾아(`grep -rln "ReplayHistoryWriter" tests/`) `flush()`가 `history_refresh_ticks` 미달 상태에서도 버퍼를 파일로 기록함을 단언하는 테스트 추가 → red 관찰 → `flush()` 구현 → green.
3. CLI에 flush 호출 추가 → §1 테스트 green 관찰.
4. **전체 스위트**: `.venv/bin/python -m pytest -q` → 2,878+ passed(신규 1 포함), 0 failed 관찰. 결과 줄을 증거로 보고.

## 5. 제약 (kis-trader 공통)

- `.venv/bin/python -m pytest` (system python3 금지). 브로커/토스/KIS API 호출 금지. `app.main`/`run_session.sh` 실행 금지.
- research leaf 불변식: 런타임 모듈이 `app/research/`를 import하게 만들지 않는다.
- `data/`/`logs/`/`results/` 전체 read 금지. `.env`/`.token_cache.json` 접근 금지.
- **커밋 금지** — 워킹 트리에 변경만 남긴다(커밋은 운영자 결정).
- 지금 이 머신에서 분봉 변환 프로세스가 돌고 있을 수 있음 — 무시하고 진행(소스만 수정; data/ 접근 없음).

## 6. 에스컬레이션

- `_flush()` 시맨틱이 §3 가정과 다르면(append 방식이거나 리셋 위치가 다르면) 구현을 맞추되, 판단이 갈리는 지점은 보고로 반환.
- §1 외 추가 실패가 나타나면 고치지 말고 실패 목록을 그대로 보고.
