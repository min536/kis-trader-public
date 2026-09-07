# Gate2 research shim 제거 계획 (2026-07-03)

> **실행 위임 문서** (r4 형식). 구조 실사는 상위 모델 완료; 실행(red→최소 구현→green)은 위임 모델이 수행. tdd_guard: 테스트 1개씩. 우선순위: **천천히** — 기능 영향 없음, 순수 부채 상환. 소요 예상: 1 세션.

## 0. 목표 / 비목표

- **목표**: `app/research/gate2/`의 호환 shim 3개(`adapter.py` 7줄, `schema.py` 17줄, `score_v2.py` 11줄 — 각각 "TODO: re-point importers … remove this shim" 보유)를 제거하고 모든 소비자를 정본 `app/gate2/`로 재지향.
- **비목표**: `weight_search.py`(473줄)·`shadow_report.py`(114줄)는 **research 전용 실모듈이므로 이동/승격하지 않는다**(런타임 미사용 — 승격은 별도 판단). `app/gate2/` 정본 3개 파일 내용 무변경.

## 1. 소비자 지도 (2026-07-03 실사 — 인용 검증만, 재조사 불필요)

shim 경유 import (재지향 대상):
- 내부: `app/research/gate2/weight_search.py:10,15` (schema, score_v2) · `app/research/gate2/shadow_report.py:7,8` (adapter, score_v2, schema)
- 테스트: `tests/test_gate2_schema.py:7` · `tests/test_gate2_adapter.py:5` · `tests/test_gate2_shadow_report.py:5` · `tests/test_gate2_weight_search.py:5` · `tests/test_gate2_score_v2.py:5` · `tests/test_weight_search_v2.py:10` (schema만 재지향; weight_search import는 유지)

shim 비경유 (변경 금지): `app/research/replay/record_runner.py:46`·`app/tools/run_gate2_weight_search.py:58`은 `weight_search`(실모듈)만 import — 그대로 둔다. `app/research/gate2/__init__.py`(0줄)는 패키지 유지를 위해 존속.

## 2. 슬라이스 (각 슬라이스 후 전체 스위트 green + 커밋 1개)

- **S1 내부 재지향**: `weight_search.py`·`shadow_report.py`의 shim import를 `app.gate2.*`로 교체. red 테스트가 없는 순수 재지향이므로 tdd_guard 예외 상황 — 기존 테스트 스위트가 계약을 잠근다: 교체 → `pytest tests/test_gate2_weight_search.py tests/test_gate2_shadow_report.py tests/test_weight_search_v2.py -q` green 관찰.
- **S2 테스트 재지향**: §1의 테스트 6파일에서 `from app.research.gate2 import schema` 류를 `from app.gate2 import schema` 류로 교체(단, `weight_search`·`shadow_report` import는 research 경로 유지). 파일별로 해당 테스트 green 관찰.
- **S3 shim 삭제**: `git rm app/research/gate2/{adapter,schema,score_v2}.py` → 전체 스위트 green + `grep -rn "app\.research\.gate2\.\(adapter\|schema\|score_v2\)\|from app\.research\.gate2 import \(adapter\|schema\|score_v2\)" app/ tests/` 0건 관찰.

## 3. 제약

- `.venv/bin/python -m pytest`. 브로커/API 호출 금지. 런타임 모듈 무변경(이 계획은 research/tests만 건드림).
- research leaf 불변식 유지: 런타임 → research import 신설 금지 (S1·S2는 research/tests 내부 변경이라 자동 충족; S3 후 무의존 grep으로 증명).
- 커밋 메시지: `refactor(research): re-point gate2 shim importers to app.gate2 (S1/S2)` / `chore(research): remove gate2 compat shims (S3)`.

## 4. 에스컬레이션

- grep에서 §1에 없는 shim 소비자가 새로 발견되면(WIP 병행 중 추가 가능) 중단 없이 같은 방식으로 재지향하되 보고서에 목록 명시.
- `shadow_report`가 shim 삭제 후 순환/누락 import를 드러내면 임의 재배치 금지 — 보고 후 대기.
