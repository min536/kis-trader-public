# run_full154_fetch.sh merge 참조 복구 기록

> 상태: 2026-06-07 resolved — `app/tools/merge_price_csvs.py` 복원 완료

## 현재 발견 사항

`run_full154_fetch.sh`(프로젝트 루트)는 마지막 단계에서 `python -m app.tools.merge_price_csvs`를 호출합니다.
이 참조는 과거에는 누락되어 있었지만, 현재는 `app/tools/merge_price_csvs.py`가 복원되어 정상 import/CLI 실행 가능합니다.

## 참조 위치

| 파일 | 행 | 내용 |
|------|-----|------|
| `run_full154_fetch.sh` | 20 | `python -m app.tools.merge_price_csvs \` |
| `docs/tools_inventory.md` | 171 | 과거 누락 관찰 기록, 현재 resolved로 갱신 |
| `docs/workspace_inventory.md` | 41 | `run_full154_fetch.sh` 목록 포함 |
| `docs/AI_AGENT_BRIEF.md` | 49 | `full154` 유니버스 설명에서 참조 |
| `docs/BACKTESTING.md` | 10 | 백테스트 페치 스크립트로 설명 |

## 파일 존재 여부

| 경로 | 존재 여부 |
|------|----------|
| `app/tools/merge_price_csvs.py` | ✅ 있음 |
| `archive/tools/merge_price_csvs.py` | ✅ 있음 (복원 기준이 된 historical copy) |
| `app/tools/__pycache__/merge_price_csvs.cpython-313.pyc` | ✅ 있음 (캐시 잔재) |
| `app/tools/__pycache__/merge_price_csvs.cpython-314.pyc` | ✅ 있음 (캐시 잔재) |

## Git 이력

- **원본 생성**: `e01f7e1` — "Add resilient backtest fetch tooling and universe planning"
  - `app/tools/merge_price_csvs.py`로 최초 추가
- **아카이브 이동**: `2a28229` — "Archive unused analysis tools" (2026-05-08)
  - `app/tools/merge_price_csvs.py` → `archive/tools/merge_price_csvs.py`로 이동
  - `run_full154_fetch.sh`는 이 커밋에서 업데이트되지 않음

## 대체 후보 여부

- `archive/tools/merge_price_csvs.py`가 기능적으로 동일한 파일입니다.
- 단순 CSV 병합(date+symbol 기준 dedup, 정렬, DictWriter 출력) 기능만 수행합니다.
- 다른 대체 스크립트는 발견되지 않았습니다.

## 현재 운영 영향 가능성

| 시나리오 | 영향 |
|---------|------|
| 일상 trading session (app.main) | **영향 없음** — `run_full154_fetch.sh`는 별도 배치 스크립트이며 trading에 사용되지 않음 |
| 백테스트 데이터 재취득 시 | **merge 단계 복구됨** — `python -m app.tools.merge_price_csvs` 참조가 다시 유효함 |
| 기존 merged CSV 사용 | **영향 없음** — 이미 생성된 `data/prices_scan_symbols_full154.csv`가 있으면 사용 가능 |

## 위험도

**낮음** — trading 운영 경로에 포함되지 않으며, 백테스트 데이터 재취득 시에만 영향.

## 권장 다음 액션

1. `run_full154_fetch.sh`를 실행하기 전에는 별도 operator 판단으로 full154 재취득 필요성을 확인
2. `app/tools/__pycache__/` 내 `merge_price_csvs.cpython-*.pyc` 파일은 필요 시 캐시 정리
3. historical copy인 `archive/tools/merge_price_csvs.py`는 archive 정책에 따라 별도 검토

## 이번 작업에서의 조치

- `app/tools/merge_price_csvs.py`를 복원했습니다.
- `tests/test_merge_price_csvs.py`로 date+symbol dedup, 정렬, CLI 출력 경로를 검증했습니다.
- `run_full154_fetch.sh`를 변경하지 않았습니다.
- `archive/tools/merge_price_csvs.py`를 변경하지 않았습니다.
