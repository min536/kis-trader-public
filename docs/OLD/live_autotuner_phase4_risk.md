---
Purpose: Phase 4 (Tier B 고위험 확장) 리스크 등록부 + 필수 완화책 — 구현 acceptance 기준
Read when: Phase 4 Tier B 런타임 override를 구현/검토하기 전
Do not use for: 라이브 활성화 / Tier C 해제(둘 다 사람의 별도 결정·절차)
Status: RISK GATE — Phase 4 구현은 M11–M15 + Phase 3의 M1–M10을 모두 충족해야 한다
---

# Phase 4 Tier B Runtime Override — Risk Register & Mitigations

Phase 4는 런타임 override를 **Tier B**(중위험: `rebuy_cooldown_minutes`,
`same_symbol_max_buys_per_day`)로 확장한다. Tier B는 매수 빈도/집중에 직접 작용하므로 Phase 3(Tier A,
스캔 타이밍)보다 **더 강한 게이트**를 요구한다.

## 불변(절대) 제약

- **Tier C는 계속 차단(D1).** 해제 절차(risk review + 검토된 bounds + ≥N 세션 + 사람 sign-off)가 수행되지
  않았다. Phase 4는 Tier C를 건드리지 않으며, 고위험 경로도 Tier C/D/blocked를 반드시 거부한다.
- **`approved_high_risk`는 기본 거부 유지(Phase 0 규칙).** 명시적 opt-in일 때만 Tier B 허용.
- **라이브 미적용.** mock 전용. 활성화는 `_build_regime_state` wrapper에 배선됨(Step 5)이나 기본 OFF;
  플래그 OFF면 main.py 동작 바이트 불변, session_loop 미수정, live 무영향.

## Risk Register (Phase 4 신규)

| # | 리스크 | L | I | 등급 | 완화책 |
|---|--------|---|---|------|--------|
| R11 | Tier C가 고위험 경로로 새어 적용 | Low | High | **High** | M11: blocked/Tier C/D는 어떤 경로로도 거부; 테스트로 증명 |
| R12 | approved_high_risk가 기본 활성화되어 광범위 변경 | Low | High | **High** | M12/M13: 별도 default-OFF 게이트 + validator opt-in(기본 거부) |
| R13 | 약한 증거로 매수파라미터 변경 | Med | High | **High** | M14: ≥2 live_log + risk_review 블록 필수 |
| R14 | Tier B override가 더 공격적으로 만듦(쿨다운↓, 집중↑) | Med | High | **High** | M15: safer-of(쿨다운=max, 집중한도=min); validator 범위 강제 |
| R15 | 두 경로(저/고위험) 동시 활성으로 혼선 | Low | Med | **Med** | 두 경로 독립 게이트; 고위험은 별도 함수/플래그 |

## 필수 완화책 (M11–M15) — Phase 3의 M1–M10 위에 추가

- **M11** 고위험 경로도 `blocked:true`(Tier C 포함)·Tier D를 반드시 거부 → Tier C/D는 절대 emit 안 됨.
- **M12** 별도 하드 게이트 `enabled_high_risk`, **기본 OFF**, Phase 3의 `enabled`와 독립. + `env=="mock"`.
- **M13** validator는 `allow_high_risk=True`일 때만 `approved_high_risk`를 허용; **기본 False → 거부**(Phase 0
  동작 보존, 기존 테스트 불변).
- **M14** 고위험 bundle은 **live_log 증거 ≥2개** + 비어있지 않은 `risk_review` 블록 필수.
- **M15** Tier B **runtime-applicable allowlist** + safer-of: `rebuy_cooldown_minutes`=max(쿨다운 길수록 안전),
  `same_symbol_max_buys_per_day`=min(한도 작을수록 안전). 적용은 `cycle_settings = replace(settings,...)` seam
  (settings 필드, effective_* 아님) — 사람이 게이트 후 wiring.

## 하드 게이트 (기본 OFF)

| 게이트 | 기본값 | 의미 |
|--------|--------|------|
| `enabled_high_risk` | **False** | OFF면 Tier B override 0 |
| `env == "mock"` | mock | live면 0 |
| validator `allow_high_risk` | **False** | False면 approved_high_risk 거부(기본) |
| Tier C/blocked | 차단 | 어떤 경로로도 0 |

> **불변식:** 위 게이트가 모두 통과하고 단일 승인·미만료·**Tier B**·≥2 live_log·risk_review 보유 bundle일
> 때만 Tier B override emit. Tier C/D/blocked는 영구 0. 기본 설정에서 동작 무변경.

## 구현 상태 (Phase 4)

- `app/autotuner/validator.py` — `validate_proposal(proposal, *, allow_high_risk=False)`.
  기본 False면 `approved_high_risk` 거부(Phase 0 동작 보존). True일 때만 Tier B 허용 — Tier C는
  `blocked` 검사로 여전히 거부(M11/M13).
- `app/autotuner/runtime_override.py`
  - `resolve_high_risk_overrides(*, project_root, now, env, enabled_high_risk)` — Phase 3와 동일 골격에
    더 강한 게이트: `enabled_high_risk`(기본 OFF) + `env=="mock"` + 단일 승인·미만료 bundle +
    `validate(allow_high_risk=True)` + **live_log ≥2** + `risk_review` 보유. Tier B allowlist만 emit,
    Tier C/D 영구 0. 어떤 입력에도 raise 안 함.
  - `merge_safer_of_settings(current_values, overrides)` — Tier B safer-of(M15): cooldown=max(길게),
    same_symbol 한도=min(적게). 보호값이 항상 override를 이긴다.
- `app/autotuner/runtime_activation.py`
  - `apply_high_risk_overrides(regime_state, *, settings, project_root, now, environ=None)` — Phase 4
    활성화 게이트. 운영 환경변수 `AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED`(기본 OFF) + `env=="mock"`일 때만
    `resolve_high_risk_overrides`→`merge_safer_of_settings`를 regime_state의 보호 `effective_*` Tier B
    값에 safer-of로 병합. None-safe, 어떤 입력에도 raise 안 함. OFF/live/no-bundle이면 regime_state 그대로.
- **배선됨(Step 5).** `app/main.py`의 기존 `_build_regime_state(...)` wrapper가 `_risk_build_regime_state`
  결과에 `apply_high_risk_overrides(...)`를 적용한다(import + 단일 call-site, 새 top-level def 없음 — 경계 준수).
  이 wrapper는 run_cycle의 두 regime 빌드 지점에서 쓰이므로 두 경로가 동일 게이트를 공유한다. `app/runtime/`은 미수정.

## Activation (human-gated, mock 전용) — 배선 완료, 기본 OFF

배선은 들어갔지만 기본 미적용. 사람이 mock에서 명시적으로 켤 때만 소비:

- 플래그 미설정/`false`: `_build_regime_state`는 regime_state를 그대로 반환(기본 동작 바이트 불변).
- 플래그 `true` + `KIS_ENV=mock` + 단일 승인·미만료 Tier B bundle(≥2 live_log + risk_review): safer-of 병합으로
  cooldown은 max(길게)·same_symbol 한도는 min(적게) 방향으로만 조정. 보호 regime clamp를 절대 못 이긴다.
- 플래그 `true` + live(또는 mock 확인 실패): bundle을 읽지 않고 regime_state 그대로.

전제: `AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED=true` AND `KIS_ENV=mock` AND 단일 승인 Tier B bundle
(≥2 live_log + risk_review). Tier C / live는 범위 밖(별도 사람 결정).

## Phase 4 완료 게이트 (사후 리뷰)

M11–M15 충족, Tier C/D가 고위험 경로에서도 거부됨을 증명하는 테스트, approved_high_risk 기본 거부 유지(기존
테스트 불변), 라이브 경로 미수정, 전체 테스트 통과, broker/락/비밀파일 미접촉.
