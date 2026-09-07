# BP-4 R6 리팩토링 — Risk Register (2026-07-04)

정본: master_blueprint_20260704.md (not included in this source snapshot) §7. **read-only 위험 평가 — 코드 변경 없음.**
생성: `/risk-assessment` 스킬 + 5-렌즈 병렬 워크플로우(순환import·패치서피스·급전경로·소비자호환·위임실행)
→ 원시 26건 → 통합 13건 → Critical/High **적대검증**. 렌즈별: circular-import=5, patch-surface=5,
money-path=4, consumer-compat=7, delegation-exec=5.

> **범위 경계**: 이 문서는 R6 착수 전 S0 게이트(`/risk-assessment`) 산출물이다. 실제 이동은
> 블루프린트 §7 슬라이스(S0~S4)와 위임 프로토콜을 따르며, 아래 위험은 그 게이트를 **보강**한다.
> 적대검증이 R6-03/05/06을 과장/오분류로 **기각·강등**해(과잉 위험 제거) 실제 위험에 집중시켰다.

## 요약 — 최종 등급(적대검증 후): Critical 2 · High 1 · Medium 4 · Low 6

| ID | Level | L/I | 요지 | Owner | Status |
|----|-------|-----|------|-------|--------|
| R6-01 | **Critical** | H/H | 결합 폐포(core 6+헬퍼 5+상수 2)를 원자 이동 안 하면 잔류→이동 back-import로 순환 재생성 | 상위모델 직접 | Open |
| R6-02 | **Critical** | M/H | 이동 산술이 리포트 아닌 2개 라이브 머니경로(HARD_STOP brake+포지션 사이징) 매 사이클 구동 | 상위모델 직접 | Open |
| R6-07 | **High** | M/H | completion_audit 3 사각지대(핀 존재-only·단일심볼 AST·--base 무검증)로 게이트 GREEN 오판 | 게이트+상위 | Open |
| R6-04 | Medium | M/H | 소비자 표면이 §7 2앵커보다 넓음(out-of-app scripts/refresh_workspace_data.py 포함) | 상위모델 직접 | Open |
| R6-08 | Medium | M/M | 순환 실정점은 performance↔performance_report 상호참조(클러스터 고유 결합 아님) | 상위모델 직접 | Open |
| R6-09 | Medium | M/M | finalize.py try/except가 import 오류를 조용히 삼켜 회귀 은폐 | 상위모델 직접 | Open |
| R6-11 | Medium | L/H | reporting/__init__ facade eager import가 leaf 배치 시 순환 유발 가능 | 상위모델 직접 | Open |
| R6-03 | Low | H/H→L | (기각) 이중 패치표면 무효화 — 전제가 두 심볼 혼동, 강등 | 상위모델 직접 | Accepted |
| R6-05 | Low | M/H→L | (기각) app/risk 목적지 순환 — 확대 메커니즘이 실코드와 상충, 강등 | 상위모델 직접 | Accepted |
| R6-06 | Low | M/H→L | (기각) BASIS 상수 문자열 실패 — 단일 정의점이라 비현실, 강등 | 상위모델 직접 | Accepted |
| R6-10 | Low | L/M | core→reporting.telegram 부수효과 결선 — leaf 순수성 유지로 회피 | 상위모델 직접 | Accepted |
| R6-12 | Low | L/M | main.py 넷-신규 def 훅 발동 가능 — 계획서에 import-교체 한정 명문화 | 상위모델 직접 | Accepted |
| R6-13 | Low | M/M | 순수 relocation ↔ tdd_guard stub-first 상충 — 실행자 프롬프트 레시피 | 상위+게이트 | Open |

## Critical / High 상세 (완화책 = §7 게이트 보강)

### [R6-01] Critical — 결합 폐포 원자 이동 (적대검증 CONFIRMED)
- **위험**: 리스크 클러스터를 '단일 심볼'이 아니라 '결합 폐포(transitive closure)'로 이동하지 않으면 잔류 심볼이 이동 심볼을 back-import해 순환/역방향 간선을 재생성한다. 이동 대상 core 6종(`_build_account_state`:287, `build_account_state_payload`:347, `select_risk_managed_current_equity_krw`:358, `build_performance_report`:545, `build_daily_pnl_state`:396, `build_current_drawdown_state`:505) + 공유 private 헬퍼 5종(`_build_deployment_invariant_equity`:256, `_is_snapshot_reliable_for_risk`:216, `_uses_deployment_invariant_equity`:189, `_select_equity_basis_compatible_history`:202, `_load_performance_snapshots`:181) + 상수 2종(`DEPLOYMENT_INVARIANT_EQUITY_BASIS`:43, `LEGACY_...`:50).
- **실패 시나리오**: 실행자가 core 2개만 옮기고 헬퍼/상수를 performance.py에 남김 → 신모듈이 `from app.reporting.performance import _is_snapshot_reliable_for_risk, DEPLOYMENT_INVARIANT_EQUITY_BASIS` → 신모듈→performance 역간선. `pnl_brake.py:9`(risk→reporting)와 얽혀 `import app.main`이 partially-initialized ImportError. **--ast-compare `::build_performance_report`는 본문 verbatim이라 PASS 반환 → 게이트 GREEN 오판정.**
- **근거**: `app/reporting/performance.py:43-52,181-278,287,347,358,396,505,545`; `app/risk/pnl_brake.py:9`; `completion_audit.py:104-147`(단일 심볼 AST); §7 RED ②
- **완화**: S0에서 이동 단위를 '결합 폐포 전체(core 6+헬퍼 5+상수 2)'로 원자 정의·명시. 상수 복제 절대 금지(단일 정의원). 게이트 GREEN에 **정적 import-역간선 grep(import-linter/pydeps)을 `--ast-compare`와 별도로** 추가 — AST 동등만으론 DAG 방향성 증명 불가.
- **적대검증**: CONFIRMED. --run-suite는 *하드* ImportError는 잡지만, **acyclic 역간선(방향성 회귀)은 completion_audit 1-5 전 검사를 통과**한다 — 정적 acyclicity 단언 필수.

### [R6-02] Critical — 리포트 우회 라이브 머니경로 (적대검증 CONFIRMED)
- **위험**: 이동된 산술이 리포트가 아닌 **2개의 독립 라이브 머니 경로**를 구동한다. `account_snapshot.py:301/310-313`과 `regime_phase.py:67/75-76`이 `build_account_state_payload` 출력의 `deployment_invariant_equity_krw`·`operating_equity_krw`를 `int()` 캐스팅으로 `_build_daily_pnl_brake_state`(HARD_STOP 게이트)와 `_build_regime_state`(포지션 사이징)에 **매 사이클 직접 투입** — 리포트/persist 경로와 무관.
- **실패 시나리오**: 재배치 중 반환 dict 키 오탈자/재명명 또는 `held_positions` fallback 분기(:315-317, `unrealized_net += position.gross_pnl`로 net==gross) 이중계상 → deployment_invariant_equity 팽창 → baseline 팽창 → 장중 하락이 작게 읽혀 **HARD_STOP 미발화, 손실 세션에 BUY 풀예산 계속 진입**. 2026-04-23형 false HARD_STOP의 역방향 재발.
- **근거**: `regime_phase.py:67-90`; `account_snapshot.py:301-339`; `performance.py:287-346`; `tests/test_brake_equity_cash_leg.py`(2026-04-23 회귀)
- **완화**: 특성화 핀에 `_build_account_state` 분기 매트릭스 전수((a)sell_analysis 매칭 (b)fallback net==gross) + 반환 dict **모든 키 verbatim 단언**. **regime_phase/account_snapshot 레벨 통합 핀**으로 동일 equity가 brake에 흐르는지 재배치 후 단언. 이 루프 산술·분기 수정 요구 순간 = RED ①.
- **§7 게이트 보강**: §7의 'byte-동일 리포트 출력'(consumer 스켑틱)은 **리포트를 우회하는 라이브 경로를 안 잡는다** — regime_phase/account_snapshot 레벨 equity-흐름 핀을 §7 S4에 추가해야 보강됨.

### [R6-07] High — completion_audit 게이트 3 사각지대 (적대검증 CONFIRMED)
- **위험**: (1) `check_pins`(:155-175)는 계획서 텍스트 정규식으로 test_* 이름 추출 후 `tests/`에 `def` 존재만 확인(:171 `f"def {n}" not in blob`) — 실행/신규/Red관찰 미검증(기존 동명 테스트 재기입으로 이동 없이 PASS 가능). (2) `check_ast_compare`는 단일심볼만(동반 헬퍼 누락 미검출 — R6-01과 결합). (3) `--base` 적정성 무검증(신경로 SHA 오지정 시 자기비교 위양성).
- **완화**: 계획서 핀은 '이 슬라이스 신규 작성' 핀만, 이름을 `test_r6_char_*` 접두로 강제(기존명 재사용 차단). **게이트 보고서에 신규 핀의 이동 전 green + 이동 후 green pytest 출력 2건을 verbatim 인용 필수화**(존재-only 보완). --ast-compare를 이동 심볼+동반 헬퍼 전수 개별 반복.
- **적대검증**: CONFIRMED — 3 사각지대 모두 스크립트 소스에서 확인.

## Medium / Low (요약)

- **[R6-04] Medium** — 소비자 표면이 §7 2앵커보다 넓음: `main.py:237-244`, `console.py:26-27`, `pnl_brake.py:9`, **`scripts/refresh_workspace_data.py:19`(out-of-app CLI, §7 목록에 없음)**, `regime_phase.py:49`, `account_snapshot.py:82`. facade `__all__`은 클러스터 미재수출이라 전원 직접 import → back-compat re-export 누락 시 부팅 ImportError. _완화_: S0 소비자 인벤토리에 scripts/console 명시 포함, S3 스테일 제거는 `refs.count==0` 확인분만.
- **[R6-08] Medium** — 순환 실정점은 `performance↔performance_report` 상호참조(`performance.py:15`가 performance_report import)이지 클러스터 고유 결합 아님. _완화_: 잔류 사유를 진짜 소거하려면 클러스터를 공통 leaf로 내리고 performance_report가 그 leaf를 import.
- **[R6-09] Medium** — `finalize.py:159-172`가 build_performance_report를 try/except로 감싸 import 오류를 조용히 삼킴. _완화_: S4에 finalize 경로 stub-없는 통합 핀(실제 persist 검증).
- **[R6-11] Medium** — reporting/__init__ facade eager import(:11-16). _완화_: 신 leaf를 app/reporting **밖**(app/portfolio/·app/core/)에 배치.
- **[R6-03] Low(기각)** — 이중 패치표면. _적대검증_: 전제가 두 심볼 혼동 — 강등.
- **[R6-05] Low(기각)** — app/risk 목적지 순환. _적대검증_: 구조 핵심은 실재하나 확대 메커니즘이 실코드와 상충 — 강등. (단, R6-05의 "중립 leaf 목적지" 권고는 R6-01/R6-11 완화에 유효)
- **[R6-06] Low(기각)** — BASIS 상수 문자열 실패. _적대검증_: 단일 정의점이라 비현실 — 강등.
- **[R6-10] Low(수용)** — core→reporting.telegram 부수효과. _완화_: leaf 순수 계산층 유지(알림/주문로그 결선 추가 금지 = RED ①).
- **[R6-12] Low(수용)** — main.py 넷-신규 def 훅. _완화_: 계획서에 'main.py는 import 교체+kw-주입 갱신 한정, 신규 def 금지' 명문화.
- **[R6-13] Low** — 순수 relocation ↔ tdd_guard stub-first 상충. _완화_: S2 opus 위임 프롬프트에 §0.2 예외 근거+pin-only+스톨시 상위인계 절차 verbatim.

## 종합 판정

- **최상위(진행 전 필수 반영)**: **R6-01**(폐포 원자 이동 + 정적 import-역간선 grep을 `--ast-compare`와 별도로), **R6-02**(리포트 우회 라이브 머니경로 → regime_phase/account_snapshot 레벨 equity-흐름 핀), **R6-07**(completion_audit 3 사각지대 → 게이트 보고서에 신규 핀 이동 전/후 green 2건 verbatim 인용).
- **블루프린트 §7과의 관계**: §7 RED ①(산술·분기 수정=중단)·②(순환→중단)는 유효하나, 본 평가가 **게이트의 실효성 사각지대**(단일심볼 AST·핀 존재-only·byte-diff가 못 잡는 라이브 경로·acyclic 역간선)를 드러냈다. R6 계획서(`docs/r6_refactor_plan_<date>.md`) S0에 위 완화책을 반영해야 GREEN 판정이 신뢰 가능.
- **결정**: R6는 **착수 가능하되**, S0에서 (1)결합 폐포 확정, (2)중립 leaf 목적지 선정, (3)4-패턴 패치서피스 실사, (4)라이브 경로 equity-흐름 핀 설계를 **선행 조건**으로 못박은 뒤 S1 진입. 상위모델 직접(§3 위임 티어). trading-critical → 모든 슬라이스에 게이트 에이전트 생략 불가.
