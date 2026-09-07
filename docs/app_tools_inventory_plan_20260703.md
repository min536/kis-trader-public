# app/tools 인벤토리·정리 계획 (2026-07-03)

> **실행 위임 문서**. 우선순위: **천천히** — 위험 낮음(도구는 런타임 미참조), 가치는 탐색성·유지비 절감. 1 세션.

## 0. 현실

`app/tools/` **78파일 / 53,158줄** — 프로젝트 최대 패키지. 일회성 분석 CLI가 누적된 형태(예: `core_bucket_analysis.py` 880줄). 어떤 도구가 살아있는지(문서/스크립트/launchd가 참조) vs 일회성 잔재인지 구분이 없음.

## 1. 산출물

`docs/app_tools_inventory_20260703.md` — 전 78파일 표: `파일 | 줄수 | 목적(모듈 docstring 1줄) | 참조처(grep) | 분류(active/ops/one-off/dead-candidate)`.

## 2. 절차

1. **참조 그래프**: 각 파일명에 대해 `grep -rln "<name>" docs/ scripts/ launchd/ .claude/ app/ tests/ --exclude-dir=app/tools` — 참조 0건 + 최근 90일 git 활동 없음(`git log --since=90.days --oneline -- <file>`) → dead-candidate.
2. **분류 규칙**: launchd/스크립트 참조 = `ops`(이동 금지 — `python -m app.tools.x` 경로 고정) · 테스트 보유 = `active` · docs만 참조 = `one-off`(이력 가치) · 무참조 = `dead-candidate`.
3. **처분**: dead-candidate는 **삭제하지 않고** 표로 보고만 — 삭제/보관은 운영자 결정(git 이력으로 복원 가능하나, 연구 도구는 재사용 빈도가 불규칙). `app/tools/archive/` 이동은 `-m` 실행 경로를 깨므로 금지.
4. **중복 헬퍼 번들링 후보**: 도구 간 반복 패턴(argparse env-guard, parquet 스키마 헬퍼, jsonl 스트리밍) grep 실측 → 공통화 후보를 표의 별도 절로 제안(구현은 비목표).

## 3. 제약 / 에스컬레이션

- 코드 무변경(문서 산출만). data/·results/ 전체 read 금지. 분류가 모호한 파일은 `unknown`으로 표기하고 임의 판정 금지.
