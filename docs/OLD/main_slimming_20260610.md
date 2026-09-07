# main.py Slimming — Day 1 (2026-06-10)

> 5일 실행 계획의 Day 1 산출물 (todo.md (not included in this source snapshot) "다음 5일 실행 계획" 참조).
> 목표: **동작 변경 0**으로 `app/main.py`를 landing zone 규칙에 맞춰 감량.

## 결과

| 지표 | Before | After | 변화 |
|---|---|---|---|
| `app/main.py` 라인 수 | **5,775** | **4,211** | **−1,564 (−27.1%)** |
| top-level 함수 수 | 111 | 61 | −50 |
| 전체 테스트 | 1708 passed | 1708 passed | 변화 없음 (모든 배치마다 full green) |

새 모듈: app/reporting/console.py (not included in this source snapshot) (949줄, 콘솔 표시 계층).
확장 모듈: app/reporting/performance.py (not included in this source snapshot) (+68줄, 당일 실현손익 빌더).

## 핵심 기법 — alias-import seam 보존

characterization 테스트들이 `app.main`의 underscore 이름 ~60개를
`monkeypatch.setattr(main_module, name, stub)`으로 동적 패치하고, 일부 테스트는
`inspect.getsource(run_cycle)`로 소스 수준 불변식을 단언한다. 따라서 일반적인
"call site를 canonical 이름으로 교체" 방식은 테스트 계약을 깬다.

대신: **모듈의 canonical 함수를 wrapper의 underscore 이름으로 alias import**
(`from app.core.runtime_budget import build_api_budget_state as _build_api_budget_state`)
하고 wrapper 본문을 삭제했다. call site·패치 타깃·소스 단언이 전부 무손상으로 유지된다.

예외 처리한 케이스:
- **late-binding 의존**: 테스트가 *타깃 모듈* 속성을 패치하는 함수
  (`build_math_sizing_context`, `print_cycle_conclusion`)는 wrapper를 유지 — alias의
  early binding이 패치 전파를 끊기 때문.
- **콜백 주입 어댑터**: `_run_sell_order_flow`, `_print_buy_pre_gating_summary`,
  `_print_buy_runtime_filter_summary`는 main의 함수를 주입하므로 wrapper 유지.
- **위임 seam 전용 테스트**: `test_reconciliation_helpers`의 위임 테스트는 green 상태에서
  identity 단언(`main.print_reconciliation_report is core.print_reconciliation_report`)으로
  교체한 뒤 죽은 wrapper를 제거.

## 배치 내역 (커밋 단위)

| 배치 | 커밋 | 내용 | main.py |
|---|---|---|---|
| 1 | `076d996` | 이름-호출 pure wrapper 23개 → alias import | 5,775 → 5,553 |
| 2 | `8237321` | 모듈-속성-호출 pure wrapper 23개 → alias import | 5,553 → 5,263 |
| 3 | `51a0683` | 콘솔 표시 함수 23개 → `app/reporting/console.py` 추출 | 5,263 → 4,399 |
| 4 | `3020194` | 죽은 import 42개 제거 (테스트 참조 교차검증 후) | 4,399 → 4,349 |
| 5 | `61ce38a` | 당일 실현손익 클러스터 → `performance.py`/`console.py` | 4,349 → 4,236 |
| 6 | `0c24532` | 잔여 단일-식 wrapper 4개 정리 + seam 테스트 재작업 | 4,236 → 4,211 |

## 검증 증거

- **배치마다 full suite green** (1708 passed, 34 subtests).
- **패치 타깃 무결성**: 테스트가 `app.main`에서 패치/참조하는 92개 이름 전수 `hasattr` 확인 → 누락 0.
- **verbatim 이동 검증**: 이동한 26개 함수 본문을 `git show main:app/main.py` 기준과
  기계 비교(rename 정규화 후) → **전부 동일**.
- **생존 함수 byte 비교**: `run_cycle`(2,941줄)의 실변경은 의도된 2줄
  (`_print_reconciliation_report` → canonical 호출)뿐. `main()`, `_run_session_loop`,
  budget/slack/regime 함수 전부 byte-identical.
- **정적 검사**: 3개 파일 AST 기반 미정의-이름 검사 clean.
- trading-critical surface(`app/execution`, `app/risk`, `app/strategy`)는 **무수정** —
  main.py와 reporting 계층만 변경.

## 남은 감량 백로그 (Day 1 범위 밖)

| 대상 | 크기 | 비고 |
|---|---|---|
| `_run_sell_test_cycle` + `_run_sell_guard_selftest` | ~263줄 | `_build_sell_analysis`/`_print_cycle_conclusion` 콜백 주입 어댑터 설계 필요 |
| slack/bottleneck 훅 클러스터 (`_record_bottleneck`, `_get_slack_notifier`, 전역 2개) | ~80줄 | `_SLACK_NOTIFIER`/`_BOTTLENECK_AGGREGATOR` 전역을 테스트가 직접 setattr — 이동 시 테스트 마이그레이션 동반 |
| `_build_sell_analysis`, `_resolve_benchmark_snapshot` 등 빌더 | ~110줄 | 소유 패키지 결정 필요 (sell 도메인) |
| `run_cycle` 자체 분해 | 2,941줄 | 별도 계획 필요 — Day 1 범위 아님 |
