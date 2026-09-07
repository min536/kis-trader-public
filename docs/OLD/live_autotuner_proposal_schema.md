---
Purpose: live autotuner 제안 artifact(JSON)의 Phase 0 스키마 계약 정의
Read when: 자동튜너 제안 파일의 필드/검증/거부 규칙을 구현하거나 검토하기 전
Do not use for: 런타임 적용(이 스키마는 계약이며, 그 자체로 어떤 런타임 override도 켜지 않는다)
Status: CONTRACT — schema contract used by generators, shadow artifacts, and the Phase 3 runtime reader.
Source docs: docs/live_autotuner_plan.md, docs/live_autotuner_whitelist_bounds.md
---

# Live Autotuner — Proposal Artifact Schema (Phase 0)

> 상위 문맥: [`live_autotuner_plan.md`](live_autotuner_plan.md)(아키텍처·신뢰 경계),
> `live_autotuner_whitelist_bounds.md` (not included in this source snapshot)(whitelist·tier·bounds).
> 이 문서는 그 위에서 **제안 파일 하나의 JSON shape**를 못 박는다.

---

## 1. Purpose

- Phase 0의 **제안 artifact 계약(contract)** 을 정의한다 — 필수 필드, 검증 규칙, 거부 케이스, 감사성, 승인 의미.
- 이 스키마 자체는 **런타임 override를 켜지 않는다.** Phase 3 런타임 리더는 별도 hard gate
  (`AUTOTUNER_RUNTIME_OVERRIDES_ENABLED` + mock env)를 통과할 때만 승인 artifact를 소비한다.
- 어떤 runtime reader든, seam이 제안을 소비하기 *전에* 반드시 이 계약에 대해 제안을 재검증해야 한다.

예시 artifact(아래 §9):
- `examples/live_autotuner_proposal_mock.json` (not included in this source snapshot)
- `examples/live_autotuner_proposal_shadow.json` (not included in this source snapshot)
- `examples/live_autotuner_proposal_rejected_tier_d.json` (not included in this source snapshot)

---

## 2. Proposal Modes

`mode` 허용값은 정확히 넷이다:

| mode | 의미 | 라이브 적용 |
|------|------|-----------|
| `mock` | 문서/테스트 artifact 전용 | 불가 (절대) |
| `shadow` | 로그/백테스트로 평가만, 적용 안 함 | 불가 |
| `approved_low_risk` | 명시적 승인 후에만 mock runtime override 적격, **Tier A 파라미터만** | Phase 3 default-OFF/mock-only reader |
| `approved_high_risk` | 더 강한 검토 후에만 적격 | 별도 default-OFF high-risk reader; live 적용 아님 |

- `mock`: 어떤 런타임도 소비하지 않는 순수 문서/테스트.
- `shadow`: 백테스트/라이브 로그에 대해 평가하되 **적용하지 않음**.
- `approved_low_risk`: 명시적 사람 승인 + 모든 change가 Tier A일 때만 Phase 3 mock runtime override 적격.
- `approved_high_risk`: 더 강한 검토 필요. 별도 high-risk reader가 있어도 default-OFF/mock-only이며 live 적용이 아니다.

> **Stage 3 producer (2026-06-07): `approved_high_risk` 승인은 명시 opt-in이다.**
> Tier B 파이프라인은 *제안*(candidate-suggest), 승인 producer(`autotuner_approve --high-risk --approve`),
> 런타임 reader(`resolve_high_risk_overrides`)로 분리된다. producer는 `approved_high_risk` 전이 시
> `validate_proposal(allow_high_risk=True)`를 재실행하고, Tier B only + ≥2 `live_log` + non-empty `risk_review`를
> 요구한다. reader는 여전히 default-OFF/mock-only이며 `AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED` +
> `KIS_ENV=mock` 없이는 `{}`를 반환한다. Tier C는 계속 BLOCKED(D1)다.

---

## 3. Required Top-Level Fields

| field | 설명 |
|-------|------|
| `schema_version` | 스키마 버전 (semver 문자열). 명시 필수 |
| `proposal_id` | 안정적·유일한 식별자 |
| `created_at` | 생성 시각 (ISO-8601 + timezone) |
| `generated_by` | 생성 주체 (`kind` + 이름/버전) |
| `mode` | §2 허용값 중 하나 |
| `status` | §4 상태값 중 하나 |
| `reason` | 비어 있지 않은 사유 |
| `evidence` | 증거 배열 (참조 포함, §7) |
| `baseline` | 비교 기준 baseline 값 스냅샷 |
| `changes` | 파라미터 change 객체 배열 (§5) |
| `constraints_checked` | 제약 검사 결과 (whitelist/bounds/cross/single-bundle) |
| `risk_review` | risk 검토 블록 (없으면 null, 단 live-eligible엔 필수) |
| `approval` | 승인 블록 (§8). `status=approved`가 아니면 null |
| `ttl` | 만료 (mock 외 필수) |
| `rollback` | 롤백 baseline 참조 (live-eligible 필수) |
| `audit` | 이벤트 로그 배열 (생성/검증/승인/거부/만료) |

---

## 4. Field Rules

- **`schema_version`** — 명시 필수. 누락 시 거부.
- **`proposal_id`** — 안정적이고 유일. 재생성해도 동일 제안이면 동일 id 유지.
- **`created_at`** — ISO-8601 + timezone 필수 (예: `2026-06-03T10:15:00+09:00`). naive datetime 거부.
- **`mode`** — §2 허용값만.
- **`status`** — 다음을 구분: `draft`, `pending_review`, `approved`, `rejected`, `expired`, `superseded`.
- **`generated_by.kind`** — 출처 식별: `human` | `script` | `mcp_assisted_evaluator` | `other`.
- **`reason`** — 비어 있지 않아야 함.
- **`evidence`** — 주장(claim)만이 아니라 **참조**(source_path/source_id 등)를 포함해야 함.
- **`baseline`** — 비교에 쓴 baseline 값을 기록해야 함 (값 스냅샷 + baseline_id).
- **`changes`** — 파라미터 change 객체의 배열.
- **`approval`** — `status=approved`가 아니면 반드시 null.
- **`ttl`** — `mock`을 제외한 모든 모드에서 필수.
- **`rollback`** (rollback baseline) — live-eligible(`approved_low_risk`/`approved_high_risk`)에 필수.

---

## 5. Change Object Schema

`changes[]`의 각 원소:

| field | 설명 |
|-------|------|
| `parameter` | 파라미터 이름 (whitelist 대조 대상) |
| `from_value` | 현재/baseline 값 |
| `to_value` | 제안 값 |
| `type` | `int` \| `float` \| `str` \| `bool` |
| `risk_tier` | `A` \| `B` \| `C` \| `D` (whitelist 문서 기준) |
| `eligible_phase` | 적격 Phase (정수) 또는 null(Tier D) |
| `bound_min` | 허용 하한 (whitelist 문서 기준; Tier C는 TBD→null 가능) |
| `bound_max` | 허용 상한 |
| `max_step` | 1회 제안 최대 변경폭 |
| `validation_status` | `not_validated` \| `passed` \| `failed` \| `rejected` |
| `rationale` | 이 change의 사유 |
| `notes` | 비고/미확인 사항 |

---

## 6. Validation Rules

검증기는 다음을 **모두** 강제한다. 하나라도 위반하면 해당 모드에 맞게 거부(reject)한다.

1. **whitelist 외 파라미터 거부** — whitelist 문서에 없는 `parameter`는 거부.
2. **Tier D 항상 거부** — `risk_tier=D`(또는 Tier D 카테고리에 해당)인 change는 증거/모드 무관 즉시 거부.
3. **type/bounds 밖 거부** — whitelist type이 numeric(`int`/`float`)이면 `to_value`/`from_value`가 숫자여야
   하며, `to_value`가 `[bound_min, bound_max]` 밖이거나 변경폭이 `max_step` 초과면 거부.
4. **baseline 누락/불일치 거부** — `baseline` 또는 비교에 쓴 값이 없으면 거부. `baseline.values`에 해당
   parameter가 있으면 `from_value`와 일치해야 한다.
5. **shadow/approved 모드의 evidence 누락 거부** — `mode ∈ {shadow, approved_low_risk, approved_high_risk}`인데 `evidence`가 비면 거부.
6. **승인 메타 없는 approved 거부** — `status=approved`인데 `approval.approved_by`/`approval.approved_at`가 없으면 거부.
7. **approved_low_risk에 비-Tier-A 포함 시 거부** — Tier B/C change가 하나라도 있으면 거부.
8. **approved_high_risk는 미래 구현이 명시 허용하기 전까지 거부** — 기본 비활성.
9. **활성 후보 2개 이상 번들 거부** — 동시 active proposal candidate가 1개를 초과하면 거부 (max one active bundle).
10. **만료 제안 거부** — `ttl.expires_at`이 지난 제안은 거부.
11. **cross-parameter 위반 거부** — whitelist 문서 §5의 교차 제약을 위반하면 거부:
    - `buy_scan_deep_eval_limit ≤ buy_scan_shallow_top_k`
    - `buy_scan_shallow_top_k ≤ scan_symbols_max_per_cycle`
    - 요청압력 결합이 안전 운영 가정보다 공격적이면 거부 (interval은 하드 가드레일 아래로 불가)
    - 초기 Phase에서 Tier C 사이징 + Tier A 스캔 공격성 동일 번들 금지
    - 매도 interval 증가 + `sell_watch_max_holdings_per_tick` 감소 동시 변경은 명시 승인 없이는 거부
    - 주문 빈도/노출을 늘리는 변경은 사람 승인 없이는 거부

---

## 7. Evidence Schema

`evidence[]`의 각 원소:

| field | 설명 |
|-------|------|
| `source_type` | `backtest` \| `live_log` \| `postrun_audit` \| `manual_review` \| `mcp_report` \| `other` |
| `source_path` 또는 `source_id` | 실제 참조 (둘 중 최소 하나) |
| `generated_at` | 증거 생성 시각 (ISO-8601 + tz) |
| `summary` | 요약 |
| `limitations` | 한계 (예: Lean DSL ≠ 라이브 전략) |
| `confidence` | `low` \| `medium` \| `high` |
| `notes` | 비고 |

**명시적 규칙:**
- **backtest 증거는 프록시(proxy) 증거일 뿐이다.** 외부 Lean 백테스터/내부 로그 재구성 모두 라이브 전략 엔진을 그대로 재현하지 않는다.
- **MCP/백테스터 출력은 라이브 적용의 *유일한* 승인 근거가 될 수 없다.**
- **live-eligible 승인 전에는 라이브 로그 교차검증(live_log)이 반드시 포함되어야 한다.**
- **operational-cadence backtest 증거는 W1 walk-forward holdout 검증을 거칠 수 있다.** holdout(out-of-sample) 윈도를 주면 eval의 각 `evaluations[]`가 `in_sample`/`out_of_sample`/`verdict_reconciliation`을 담고, train은 통과했지만 holdout이 반박하면(`status=holdout_contradicts`) `autotuner_screen`이 그 후보를 `inconclusive`로 **보수적 강등**해 suggestion이 되지 못한다(절차: `runbook_autotuner_loop.md` (not included in this source snapshot) Step 0). 강등은 후보 집합을 줄일 뿐, non-pass를 pass로 승격하지 않는다. 이는 proxy 증거를 강화할 뿐 사람 승인·live_log 요건을 대체하지 않는다.

---

## 8. Approval Semantics

`approval` 블록 (status=approved일 때만 non-null):

| field | 설명 |
|-------|------|
| `approved_by` | 승인한 사람 식별자 |
| `approved_at` | 승인 시각 (ISO-8601 + tz) |
| `approval_channel` | `local_file` \| (미래) `slack` 등 |
| `approval_scope` | 승인 범위 (어떤 change/번들에 한정되는지) |
| `expires_at` | 승인 만료 시각 |
| revocation/rollback | 만료/수동 트리거 시 baseline 복귀 동작 |

**명시:**
- **사람 승인은 필수다.** (human-in-the-loop)
- **Slack 승인은 *허용한다면* 미래 결정사항**이다. 현재는 `local_file`만 상정.
- **어떤 제안도 self-approve 할 수 없다.** `generated_by`와 `approved_by`가 동일 자동 주체일 수 없다.
  (구현: `validate_proposal`이 `generated_by.kind`가 자동 주체(script/bot/mcp/automated/agent)이고
  `approved_by`가 그 주체(`name` 또는 `kind:name`)와 동일하면 거부한다. 자동 draft에 대한 사람 승인은 허용.)

---

## 9. Example Artifacts

`docs/examples/` 아래 3개 (secret/실계좌 식별자 없음):

1. **mock (Tier A only)** — `live_autotuner_proposal_mock.json` (not included in this source snapshot)
   `buy_scan_shallow_top_k` 10→12 한 건. mode=mock, status=draft, approval=null, ttl=null.
2. **shadow (증거 참조 포함)** — `live_autotuner_proposal_shadow.json` (not included in this source snapshot)
   backtest + live_log 증거 2건. **의도적으로** `buy_scan_deep_eval_limit` change에 max_step 위반(+2 > 1)을
   넣어 검증기가 거부해야 함을 보인다 (`constraints_checked.bounds=false`).
3. **rejected Tier D** — `live_autotuner_proposal_rejected_tier_d.json` (not included in this source snapshot)
   `kis_env` mock→live 시도. 증거가 무엇이든 Tier D는 즉시 hard reject (`whitelist_only=false`, status=rejected).

> 예시는 **계약 설명용**이다. Runtime reader는 `_workspace/autotuner/proposals/` 아래의 승인 artifact만 읽고,
> docs 예시 파일은 소비하지 않는다.

---

## 10. Validator Test Coverage

다음 검증기 단위 테스트가 계약을 고정한다:

1. **accepts** — 유효한 mock Tier A 제안을 통과시킨다.
2. **rejects unknown parameter** — whitelist 외 파라미터를 거부한다.
3. **rejects Tier D** — Tier D 파라미터를 거부한다.
4. **rejects out-of-bounds** — bounds/max_step 밖 change를 거부한다.
5. **rejects missing baseline** — baseline 없는 제안을 거부한다.
6. **rejects approved w/o metadata** — `status=approved`인데 `approved_by`/`approved_at` 없으면 거부한다.
7. **rejects approved_low_risk w/ Tier B/C** — Tier A 아닌 change 포함 시 거부한다.
8. **rejects expired** — `expires_at` 지난 제안을 거부한다.
9. **rejects cross-parameter violation** — §6.11 교차 제약 위반을 거부한다.
10. **rejects no-TTL for live-eligible** — live-eligible 모드인데 TTL 없으면 거부한다.

> Runtime reader 테스트는 Phase 3/4 risk 문서와 `tests/test_autotuner_runtime_*.py`에서 별도로 다룬다.

---

## 11. Non-goals

- 자율 활성화 없음. Runtime reader가 있어도 hard gate 없이는 적용되지 않는다.
- live 적용 없음. Phase 3는 mock-only/default-OFF다.
- broker 상호작용 없음.
- MCP 직접 write 경로 없음.
- 자율 라이브 튜닝 없음.

---

## Open Questions

1. `proposal_id` 생성 규칙(날짜+모드+seq vs UUID)과 충돌 회피.
2. baseline 스냅샷 저장 위치 (`_workspace/` vs `config/` vs artifact 내 인라인) — whitelist 문서 §8과 연동.
3. `approval_scope` 표현 방식 (change 단위 vs 번들 단위).
4. `superseded` 상태 전이 규칙 (새 번들이 이전 번들을 대체할 때의 정확한 절차).
5. Slack 승인 채널 허용 여부 (현재 `local_file`만 상정) — whitelist 문서 §8과 연동.
6. Tier C `bound_min/max`가 TBD인 동안 change 객체에서 null을 어떻게 다룰지 (현재: Tier C는 차단이 기본).
