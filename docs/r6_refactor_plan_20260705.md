# BP-4 / E1 R6 리팩토링 위임 계획서 — S0 실사 + 슬라이스 (2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E1** /
> 블루프린트 §7. **프로파일: relocation**. **트랙: trading-critical** — `/risk-assessment` 완료
> ([r6_risk_register_20260704.md](r6_risk_register_20260704.md), Critical 2·High 1). **위임 티어:
> 상위모델 직접**(S2 verbatim 이동만 opus 보조, 게이트 에이전트 생략 불가). 본 문서는 **S0
> 실사 산출 + S1~S4 계획**이며, **S0는 코드 변경 없음**(이 세션 = 실사·계획까지).

---

## §0. S0 실사 결과 (이 세션 실측 — 앵커 전건 재검증)

### 0.1 결합 폐포 확정 (R6-01 완화 — 원자 이동 단위)

`app/reporting/performance.py`(959줄) 앵커를 전건 재검증(2026-07-05, `grep -n`). **이동 폐포 =
리스크 산술층 10 함수 + 2 상수** (아래 전부 exact-line 확인):

| # | 심볼 | 행 | 종류 |
|---|------|----|------|
| 0 | `_calculate_drawdowns` | 160 | private 헬퍼 (**2026-07-05 실행세션 추가** — 계획서 10심볼에 누락됐던 11번째. build_current_drawdown_state:527 **및** 잔류 build_performance_report:765 공유 → leaf 이동 필수, 아니면 leaf→performance 역간선) |
| 1 | `_load_performance_snapshots` | 181 | private 헬퍼 |
| 2 | `_uses_deployment_invariant_equity` | 189 | private 헬퍼 |
| 3 | `_select_equity_basis_compatible_history` | 202 | private 헬퍼 |
| 4 | `_is_snapshot_reliable_for_risk` | 216 | private 헬퍼 |
| 5 | `_build_deployment_invariant_equity` | 256 | private 헬퍼 |
| 6 | `_build_account_state` | 287 | core 산술 |
| 7 | `build_account_state_payload` | 347 | core 산술 (kw-주입 소비) |
| 8 | `select_risk_managed_current_equity_krw` | 358 | core 산술 |
| 9 | `build_daily_pnl_state` | 396 | core 산술 (pnl_brake 소비) |
| 10 | `build_current_drawdown_state` | 505 | core 산술 |
| C1 | `DEPLOYMENT_INVARIANT_EQUITY_BASIS` | 43 | 상수 (단일 정의원 — 복제 금지) |
| C2 | `LEGACY_DEPLOYMENT_INVARIANT_EQUITY_BASIS` | 50 | 상수 (동상) |

### 0.2 ⚠️ register 정정 — `build_performance_report`는 폐포 밖(소비자)

risk register R6-01은 `build_performance_report`(:545)를 "이동할 core 6"에 넣었으나, **실측
콜그래프는 그것이 폐포를 호출하는 소비자**임을 보인다(:554 `_build_account_state`, :564
`select_risk_managed_current_equity_krw` 호출 — performance.py:554/564). 즉 산술층이 아니라
**리포트 빌더**다. → **결정: `build_performance_report`는 performance.py에 잔류**하고 신 leaf를
import한다. 400줄 리포트 빌더를 중립 leaf(app/portfolio)로 옮기는 것은 목적지 오류.

### 0.3 중립 leaf 목적지 (R6-05 권고 + R6-11 완화)

**목적지: `app/portfolio/equity_state.py`** (신규, app/reporting **밖**). 이유:
- R6-11: `app/reporting/__init__.py` facade가 eager import(`:11-16`) → 폐포를 reporting 안에 두면
  facade 로딩이 순환 유발 가능. **밖에 둬야** 안전.
- R6-08 해소: performance.py **와** performance_report.py **둘 다** 신 leaf를 import → 현재
  `performance.py:15 → performance_report` 상호참조의 잔류 사유가 소멸(단방향 DAG).
- 순수성(R6-10): leaf는 순수 계산만 — 알림/주문로그/telegram 결선 추가 금지(추가 요구 = RED ①).

### 0.4 4-패턴 패치서피스 실사 (메모리 patch-surface-audit-pitfall)

| 패턴 | 실측 결과 |
|------|-----------|
| ① `patch.object(...performance...)` (멀티라인 포함) | tests/ **13건** — 이동 후 target 경로 갱신 대상 |
| ② `"app.reporting.performance...."` 점표기 문자열 patch | **0건** |
| ③ `"app.main.<cluster>"` 점표기 (main 재수출 경유) | **0건** (클러스터는 main 재수출 안 됨 — 표면 축소) |
| 소비자(직접 import, 비-test) | `app/risk/pnl_brake.py:9`(build_daily_pnl_state), `scripts/refresh_workspace_data.py:19`(build_performance_report+persist — **out-of-app**, R6-04) |
| 소비자(kw-주입 via main) | `build_account_state_payload` → `app/runtime/cycle_phases/account_snapshot.py:82`(param):301(호출), regime phase 동형 |
| facade 재수출 | ⚠️ **register 정정**: `app/reporting/__init__.py:15,29`가 `build_performance_report` **재수출함**(R6-04 "미재수출" 반박). build_performance_report 잔류 결정(0.2)이라 facade 무변경 — 폐포는 facade 미포함이라 영향 없음 |

### 0.5 라이브 경로 equity-흐름 핀 설계 (R6-02 완화)

폐포 산술은 리포트가 아닌 **2 라이브 머니경로**를 매 사이클 구동 — byte-diff로 안 잡힘:
- `build_account_state_payload` → `deployment_invariant_equity_krw`/`operating_equity_krw` →
  HARD_STOP brake(`build_daily_pnl_state`) + 포지션 사이징(regime).
- **핀(S4 필수, 구현됨)**: (a) `_build_account_state` 분기 매트릭스 — sell_analysis 매칭 vs
  `held_positions` fallback(net==gross), (b) 반환 dict 키 단언, (c) **통합 핀** —
  build_account_state_payload→select_risk_managed→build_daily_pnl_state로 동일 equity가 brake에
  흐르는지 leaf 경유 단언(2026-04-23 false HARD_STOP 역회귀 방지). 구현: 신규 통합 핀
  test_equity_flows_from_account_state_into_brake_via_leaf + 기존 브레이크 캐시-레그 회귀
  테스트(leaf 재지향).

---

## §1. 슬라이스 순서와 Red→Green

| 슬라이스 | 상태 | 커밋 |
|----------|------|------|
| S0 실사(§0) — 폐포·목적지·패치서피스·핀 확정 | DONE | `7e0eeb1` |
| S1 폐포 11함수+2상수 원자 이동 → equity_state.py (AST verbatim 11건, 역간선 0) | DONE | `a185529` |
| S2 소비자 재배선(pnl_brake·console·main·test) + 순환 근본해소(_timestamp_to_datetime→core) | DONE | `a185529` |
| S3 스테일 재수출 트림(6심볼) + 트림 소비자 leaf 재지향 | DONE | `2decbd7` |
| S4 equity-흐름 통합 핀(§0.5) test_equity_flows_from_account_state_into_brake_via_leaf | DONE | `2decbd7` |

## §2. 게이트 판정 (R6 보강판)

- **GREEN(구획 종결)**: ① `--ast-compare`(이동 심볼+동반 헬퍼 **전수 개별** 반복, `git show
  <BASE>:app/reporting/performance.py` 기준) ② 특성화 핀 전수 green ③ **정적 acyclicity 단언**
  (역간선 grep 0 — AST 동등만으론 방향성 회귀 못 잡음, R6-01) ④ equity-흐름 통합 핀 green ⑤
  전체 스위트 green + 스테일 import 0 + 패치서피스 ①13건 target 갱신 무손상.
- **RED(즉시 중단)**: ① 이동이 산술·분기 **수정**을 요구(범위 밖) ② 순환 발견 ③ 실행자 스톨
  600s(재위임 금지, 부분 AST 검증 후 상위 마무리) ④ main.py 넷-신규 def 훅 발동.

## §3. 위임·검증 프로토콜 + 체크리스트 (delegation-plan §)

- **①사전게이트**: `/risk-assessment` ✅(risk register). 슬라이스별 자체 점검 유지.
- **②프로파일**: relocation. §0 앵커 = 검증된 좌표(2026-07-05 재실측).
- **③실행**: 상위 직접. S2 verbatim만 opus 보조 — 프롬프트에 verbatim·죽은코드-삭제-금지·
  pin-only-금지·스톨시 상위인계 **verbatim**(executor-prompt-recipes). tdd_guard: 순수 relocation은
  §0.2 예외(기존 스위트가 계약 잠금) — 근거 명문화(R6-13).
- **④완료감사**: `completion_audit.py --plan docs/r6_refactor_plan_20260705.md --base <착수SHA>
  --ast-compare app/portfolio/equity_state.py:<신경로>::<심볼> ...(전 심볼 반복) --hygiene
  app/reporting/performance.py --run-suite`. **보고서에 신규 핀 이동 전 green + 이동 후 green
  pytest 출력 2건 verbatim 인용**(R6-07 핀 존재-only 사각지대 보완).
- **⑤적대검증**: 3-스켑틱 backward-compat(패치서피스·소비자)/safety(산술 무변경)/consumer
  (리포트 byte-동일 + **라이브 equity-흐름** 핀). 고위험이라 5-렌즈 2-패스 대상.

**체크리스트 5항목**: (1) 프로파일=relocation ✅ (2) 구조지도=§0 검증앵커 ✅ (3) 이동/이동금지
심볼 확정=§0.1(이동)+§0.2(잔류) ✅ (4) 게이트=§2 5종(정적 acyclicity 포함) ✅ (5) 실패/RED
경로=§2 RED 4종 ✅. **공란 없음 → 실행 착수 가능(상위 직접)**.

## §4. S0 검증 (Green 재현)

- 폐포 12심볼 exact-line = `grep -n` 재현(§0.1). build_performance_report 소비자 판정 =
  performance.py:554/564 호출 실측(§0.2). 패치서피스 4패턴 = grep 카운트(§0.4, ②③=0, ①=13).
  facade 재수출 = `reporting/__init__.py:15,29` 실측(register 정정). **코드 변경 0**(S0 = 실사).
