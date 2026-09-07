# 게이트 항목 종결 원장 (E13 잔여 + T1 조건부, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) DoD §1-2
> 준거 — **에이전트가 지금 실행할 수 없는 항목**(운영자 게이트/의존 대기/dev-env 미설치)의
> **트리거·결정을 기록**해 종결한다. "착수 안 함 결정도 유효한 종결"(DoD §1). 여기 기록된 항목은
> 블루프린트 §14 상태와 정합하며, 트리거 발생 시 해당 계획서로 착수한다.

---

## §1. Trading-critical (실행 = `/risk-assessment` + 상위 직접)

| ID | 항목 | 상태·트리거 | 왜 지금 아닌가 |
|----|------|-------------|----------------|
| ~~E1 S1~S4~~ | R6 폐포 이동 | **DONE** (2026-07-05 실행세션, `a185529`+`2decbd7`) — 11함수+2상수 → `app/portfolio/equity_state.py`, AST verbatim·순환 근본해소·적대검증 3렌즈·전체 스위트 3,171 | 종결. 후속: F-23 1천줄 모듈 상환(R6 방법론 재사용, 10월) |
| **E2** | 미장 매도 트리거 포팅 | **BLOCKED: `/risk-assessment` 선행 미실시** | `app/strategy/`+overseas 주문경로 접촉. §8 S0 적용성 실사 전 risk-assessment 게이트 필요. 8월 배치 |
| **E15** | BP-14 S2 graceful halt 구현 | **BLOCKED: 운영자 설계 승인(결정-3)** → 후 `/risk-assessment` | 설계 입력 준비됨([risk_guard_catalog_20260705.md](risk_guard_catalog_20260705.md) §3). 운영자 승인 전 구현 금지 |
| **E12** | §17 잔여(USD pnl-brake/FX) | **CONDITIONAL: E3 S6 실데이터 품질(9월)** | BP-1 실행 결과가 실사용 궤도일 때만 착수 — 9월 체크포인트 |
| **F-09** | runtime_state 원자쓰기 | **백로그 P2, trading-critical 인접 → `/risk-assessment` 권장** | tmp+rename 전환은 상태파일 계약 접촉. F-19 감사에서 손상읽기 fail-closed 확인(급하지 않음) |

## §2. 운영자 게이트 이벤트 의존 (gate2 ④ 등)

| ID | 항목 | 상태·트리거 | 준비물 |
|----|------|-------------|--------|
| **E6** | gate2 ④ 사후 QA | **BLOCKED: ④ 완료 이벤트**(owner PID 95794 실행 중) | `gate2-artifact-qa` 에이전트 + gate-validation-checklists ④. ④ DONE 시 즉시 착수 |
| **E5 S2~S5** | 국내 v2 배선~브리프 | **BLOCKED: ④** (S1 재채점기는 완료 — `5b80bc6`) | S1 offline 재채점기 green. ④ 후 shadow 배선 |
| **E10** | Track M shadow 성과 분석 | **BLOCKED: O8-2 + 3~4주 축적** | `app/research/us_regime/` 자산. quant-analyst 에이전트 |
| **E9** | lane 승격 브리프 | **BLOCKED: O7-3 관찰 N(≥15영업일)** | lane_observation_plan_20260704.md (not included in this source snapshot) §S4 도구 완료 — 관찰 데이터만 대기 |

## §3. Dev-env 미설치 의존

| ID | 항목 | 상태·트리거 | 비고 |
|----|------|-------------|------|
| **E8** | BP-16 ruff 도입 | **BLOCKED: ruff 미설치**(dev 환경) | pyproject+E/F 룰셋+baseline. 8월 배치. 룰셋은 상위 결정 |
| **E16** | BP-15 S4 커버리지 기준선 | **BLOCKED: pytest-cov 미설치** (선택 — 게이트화 안 함) | 참고선 1회 계측. 미착수도 종결(선택 항목) |
| **F-10** | Slack critical 재시도 | **백로그 P2** — 코드 가능하나 F-19 §5-2와 통합 처리 권장 | notifications 23 except와 함께 별건 |

## §4. 조건부(사용 실적 축적)

| ID | 항목 | 상태·트리거 |
|----|------|-------------|
| **E11** | 하네스 2단계 go/no-go | **CONDITIONAL: 1단계 스킬 ≥2회 사용** — 이번 세션들(delegation-plan·adversarial-verify·completion-audit 계획 참조)로 축적 중. 9월 판정 |

## §5. 종결 판정 (DoD §1-2 정합)

- **위 전 항목은 "결정 기록"으로 종결** — go/no-go/보류/의존대기가 각각 유효한 종결 상태.
  트리거(④ 이벤트·운영자 승인·관찰 N·dev설치)가 발생하면 명시된 계획서로 착수.
- **에이전트 자율 실행 불가 사유가 각 행에 명기**(honesty) — 숨긴 미완 없음.
- 블루프린트 §14 진행표와 1:1 정합(E1 READY, E2/E5후반/E6/E9/E10 BLOCKED, E8/E16 dev-env,
  E11/E12 CONDITIONAL, F-09/F-10 백로그).
