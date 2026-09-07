---
Purpose: Phase 3 (first runtime reader) 리스크 등록부 + 필수 완화책 — 구현 acceptance 기준
Read when: Phase 3 런타임 override 리더를 구현/검토하기 전, 또는 게이트가 안전한지 확인할 때
Do not use for: 라이브 활성화(이 문서는 게이트가 OFF 기본임을 명문화. 활성화는 사람 결정)
Status: RISK GATE — Phase 3 구현은 이 등록부의 모든 필수 완화책(M1–M10)을 충족해야 한다
---

# Phase 3 Runtime Override — Risk Register & Required Mitigations

Phase 3는 자동튜너의 **최초 런타임 리더**다: 승인된 Tier A override를 per-cycle
`cycle_settings = replace(settings, ...)` / `runtime_rate_control` seam에 주입한다. seam은
trading-critical surface(`app/main.py`)에 닿으므로 본 게이트를 먼저 통과한다.

## 핵심 안전 원리

기존 `build_runtime_rate_control`(`app/runtime/session_loop.py`)은 degraded/midday에서 **더 보수적으로
clamp**한다(interval `max(...)`=느리게, scan_max `min(...)`=적게). 자동튜너 override는 *다른 소스*이고
Tier A 범위 안에서 *더 공격적*으로 밀 수 있다. → **불변식: override는 보호 clamp를 절대 이기지 못한다
("safer-of" merge).**

## Risk Register

| # | 리스크 | L | I | 등급 | 완화책(구현이 충족해야 함) |
|---|--------|---|---|------|------------------------------|
| R1 | 라이브 모드에서 override가 적용됨 | Low | High | **High** | M5: 하드 게이트(enable flag OFF 기본) + KIS_ENV=mock 필수. 둘 다 아니면 `{}` |
| R2 | override가 운영 안전 범위보다 공격적으로 만듦(요청압력↑, 주문↑) | Med | High | **High** | M6: Tier A runtime-applicable allowlist만; M7: safer-of merge(보호 clamp 우선); 범위는 validator가 강제 |
| R3 | 만료(stale) override가 계속 적용 | Med | High | **High** | M3: apply 시점 `now` 대비 expiry 검사, 의심 시 `{}` |
| R4 | 위조/수기 편집 artifact가 승인 우회 | Med | High | **High** | M2: 읽는 시점에 `validate_proposal` 전체 재실행; status=approved + 승인 메타 필수 |
| R5 | 동시 다중 active bundle로 모호성 | Med | Med | **Med** | M4: 정확히 1개 active approved bundle, 아니면 `{}` + 경고 |
| R6 | 리더 예외가 run_cycle을 크래시 | Low | High | **High** | M1: 리더는 절대 raise 안 함(degrade-safe → `{}`) |
| R7 | seam이 모르는 파라미터 적용 | Low | Med | **Low** | M6: seam이 실제 지원하는 키만 emit |
| R8 | 테스트/기본 config에서 우발적 활성화 | Low | High | **High** | M5: enable flag 기본 OFF; 비활성 시 `{}` |
| R9 | main.py 재성장/boundary 위반 | Low | Med | **Low** | M8: 로직은 `app/autotuner/`; main.py는 import+call-site만(새 top-level def 금지) |
| R10 | 런타임 상태/락 오염, broker 호출 | Low | High | **High** | M9/M10: broker 호출 없음, 락 우회 없음, `_workspace/`에만 read; runtime_state/config 미수정 |

## 필수 완화책 (M1–M10) — Phase 3 acceptance 기준

- **M1** 리더는 어떤 입력에도 raise하지 않는다 → 실패 시 `{}` (collector 계약과 동일).
- **M2** 읽는 시점에 `validate_proposal`로 재검증; `status="approved"` + `mode="approved_low_risk"` +
  승인 메타(approved_by/at) + accepted 인 것만 적격.
- **M3** `ttl.expires_at`를 주입된 `now`(tz-aware)와 비교; 만료/파싱불가 → `{}`.
- **M4** active approved bundle이 정확히 1개일 때만; 0개 또는 2개+ → `{}`.
- **M5** **하드 게이트, 기본 OFF.** `enabled=False`면 `{}`. `env != "mock"`이면 `{}`. (라이브 경로는 Phase 3에
  구현하지 않음 — 사람이 별도로 연다.)
- **M6** Tier A **runtime-applicable allowlist**(seam 지원 키)만 emit; 그 외 파라미터는 무시.
- **M7** **safer-of merge.** override는 보호 clamp를 더 공격적 방향으로 이기지 못한다:
  interval은 `max`(더 느린 값 채택), scan/eval cap은 `min`(더 적은 값 채택). degraded/midday clamp 우선.
- **M8** 모든 새 로직은 `app/autotuner/`. `app/main.py`는 import + 기존 `_build_runtime_rate_control(...)`
  wrapper의 단일 call-site만(새 top-level def/class 금지 — boundary 훅 준수). `app/runtime/session_loop.py`는
  이 Phase 3 배선에서 미수정.
- **M9** `now`/`env`/`enabled` 주입(결정적·테스트가능). broker 호출·파일쓰기(runtime state) 없음.
- **M10** override bundle은 `_workspace/autotuner/proposals/`에서만 읽는다; `config/`·`data/` 미사용.

## 하드 게이트 (기본 OFF)

| 게이트 | 기본값 | 의미 |
|--------|--------|------|
| `enabled` (env/param) | **False** | OFF면 override 0 — 라이브 동작 무변경 |
| `env == "mock"` | mock | live면 override 0 (Phase 3는 mock 전용) |
| 단일 active bundle | — | 0/2+면 override 0 |

> **불변식: 두 하드 게이트가 모두 통과하고, 정확히 1개의 승인·미만료·Tier-A bundle이 있을 때만** override를
> emit한다. 그 외 모든 경우 `{}`. 기본 설정에서 라이브 봇 동작은 **바이트 단위로 불변**이어야 한다(테스트로 증명).

## 구현 상태 (Phase 3)

- `app/autotuner/runtime_override.py`
  - `resolve_runtime_overrides(*, project_root, now, env, enabled)` — 게이트된 리더(M1–M6, M9, M10).
    두 하드 게이트(`enabled`/`env=="mock"`) + 정확히 1개의 승인·미만료·재검증 통과 Tier-A bundle일 때만
    `{settings_name: value}` emit; 그 외 모든 경우(비활성/라이브/0·2+ bundle/검증실패/만료/손상/잘못된 now) → `{}`.
    어떤 입력에도 raise 안 함.
  - Proposal JSON read는 파일당 64KB 상한으로 bounded 처리한다. 상한 초과 파일은 의심 입력으로 보고 skip하며,
    resolver는 no-op(`{}`)을 유지한다. 후속 개선: status/report 경로에 "oversized/corrupt 때문에 no-op" 사유를
    노출해 운영자가 cleanup 대상을 쉽게 찾게 한다.
  - `merge_safer_of(runtime_rate_control, overrides)` — safer-of 병합(M7). interval=max(느림), cap=min(적음).
    보호 clamp가 항상 override를 이긴다.
- `app/autotuner/runtime_activation.py`
  - `apply_runtime_overrides(runtime_rate_control, *, settings, project_root, now)` — 운영 환경변수
    `AUTOTUNER_RUNTIME_OVERRIDES_ENABLED`를 읽는 Phase 3 활성화 게이트. 기본값은 OFF이며, OFF면 proposal
    디렉터리도 읽지 않고 기존 `runtime_rate_control` 객체를 그대로 반환한다.
  - 플래그가 ON이어도 현재 runtime env가 `mock`으로 확인되지 않으면 resolver/merge를 호출하지 않는다.
- `app/main.py`
  - 기존 `_build_runtime_rate_control(...)` wrapper에서 `build_runtime_rate_control(...)` 직후
    `apply_runtime_overrides(...)`를 호출한다. 새 top-level def/class 없음(M8).
  - 이 wrapper는 run-once와 `run_session_loop(...)` callback 양쪽에서 이미 사용되므로, 두 실제 cycle 경로가
    동일한 default-OFF/mock-only gate를 공유한다.
- `app/runtime/session_loop.py`는 이 Phase 3 배선에서 미수정이다. 세션 루프는 기존 callback으로 받은
  `runtime_rate_control`만 소비한다.

## Activation (human-gated, mock 전용)

Phase 3 배선은 들어갔지만 기본 OFF다. 사람이 mock에서 명시적으로 켤 때만 override를 소비한다:

```python
# app/main.py: 기존 _build_runtime_rate_control wrapper 안
runtime_rate_control = build_runtime_rate_control(
    settings=settings,
    api_budget_state=api_budget_state,
    now=now,
)
return apply_runtime_overrides(
    runtime_rate_control,
    settings=settings,
    project_root=PROJECT_ROOT,
    now=now,
)
```

활성화 전제: `AUTOTUNER_RUNTIME_OVERRIDES_ENABLED=true` AND 현재 KIS runtime env가 `mock` AND
`_workspace/autotuner/proposals/`에 승인·미만료·재검증 통과한 단일 Tier-A bundle. 라이브(`KIS_ENV=live` 또는
live 공식 base URL)에서는 플래그가 켜져도 resolver/merge를 호출하지 않는다. 라이브 적용은 Phase 3 범위 밖 —
별도 사람 결정/게이트가 필요하다.

운영자가 알아야 할 기본 동작:

- 플래그 미설정/`false`: 기존 `runtime_rate_control` 객체를 그대로 반환한다(기본 동작 불변).
- 플래그 `true` + mock + 단일 승인 bundle: `resolve_runtime_overrides(..., enabled=True, env="mock")`를 호출하고,
  반환된 override가 있을 때만 `merge_safer_of(...)`로 병합한다.
- 플래그 `true` + live 또는 env 확인 실패: proposal을 읽지 않고 기존 값 그대로 반환한다.

## Phase 3 → Phase 4 진행 게이트 (사후 리뷰)

Phase 3 완료 후 다음을 확인해야 Phase 4로 간다: M1–M10 충족, 기본 OFF에서 no-op 증명,
main.py boundary 위반 없음(새 top-level def/class 0개), 전체 테스트 통과, broker/락/비밀파일 미접촉.
