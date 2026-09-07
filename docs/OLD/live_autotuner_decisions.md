---
Purpose: 미해결 설계 질문(Open Questions)에 대한 결정 기록(ADR-style) — Phase 2 착수 전 계약 고정
Read when: 자동튜너 Phase 2(shadow) 이상을 설계/구현하기 전, 또는 baseline/evidence/id 규칙을 확인할 때
Do not use for: 런타임 적용(이 문서는 결정 기록이며, 어떤 런타임 동작도 켜지 않는다)
Status: DECISIONS (Phase 1.5) — supersedes the "Open Questions" sections of the three design docs
Source docs: live_autotuner_plan.md, live_autotuner_whitelist_bounds.md, live_autotuner_proposal_schema.md
---

# Live Autotuner — Decisions (Open Questions 해소)

이 문서는 세 설계 문서에 흩어져 있던 **Open Questions를 결정으로 고정**한다. 각 결정은 보수적
기본값이며, 변경 시 이 표의 행을 갱신한다(코드가 아니라 계약이 먼저다). 어떤 결정도 broker 호출이나
credential 접근을 도입하지 않는다.

## 결정 요약 (D1–D7)

| # | 질문 | 결정 |
|---|------|------|
| D1 | Tier C(주문 사이징) bounds | **차단 유지.** 숫자 미정. 해제는 별도 절차(아래) |
| D2 | baseline 스냅샷 저장 위치 | `_workspace/autotuner/baselines/{baseline_id}.json` + artifact에 `baseline.values` 인라인 |
| D3 | `proposal_id` 규칙 | `atp_{YYYYMMDD}_{mode}_{NNNN}` (per-day 시퀀스, 디렉터리 충돌 검사) |
| D4 | evidence 소스 경로/스키마 | backtest→`results/backtests/`, live_log→`data/backtest/`, postrun→`_workspace/` (아래 표) |
| D5 | 승인 채널 | **`local_file`만.** Slack 승인은 미래 별도 결정까지 보류 |
| D6 | `superseded` 전이 | 새 bundle 승인 시 이전 active bundle → `superseded` (동시 active 1개 불변식) |
| D7 | shadow 증거 요구량 N | **N=3** comparable regular-session shadow 세션 (Phase 3 저위험 적격 전, 조정 가능) |

---

## D1 — Tier C bounds: 차단 유지, 해제 절차 명문화

**결정:** `buy_max_account_exposure_pct`, `buy_max_budget_per_trade_krw`, `buy_max_qty_per_trade`,
`buy_daily_max_order_submissions`의 bounds는 **이 단계에서 숫자를 채우지 않는다.** `blocked: true` 유지.

**이유:** 이들은 주문 크기·계좌 노출·일일 주문 수에 직접 작용하는 trading-critical 값이다. 1차 안에서 임의
숫자를 적으면 그 숫자가 "승인된 것처럼" 굳을 위험이 있다(whitelist 문서 §4-2 경고).

**해제(unblock) 절차 — 모두 충족해야 함:**
1. `/risk-assessment` 게이트 통과 (risk-analyst 리뷰).
2. 파라미터별 명시적 min/max/max_step 제안 + 리뷰 (이 문서 또는 whitelist 문서에 기록).
3. **≥N(=D7) shadow 세션** 증거 + 그중 live_log 교차검증 포함 (D4).
4. 사람 sign-off.
충족 시 `config/autotuner_whitelist.yaml`에서 해당 항목 `blocked: false` + 숫자 기입. 그 전까지 validator가
계속 거부한다(현재 동작 그대로).

---

## D2 — baseline 스냅샷 저장 위치

**결정:** 두 곳에 둔다 (자기완결성 + 내구성).
- **인라인:** artifact의 `baseline.values`에 비교에 쓴 값들을 그대로 담는다 → validator가 외부 파일 없이도
  검증 가능 (현재 generator/validator 동작과 일치).
- **durable 스냅샷:** `_workspace/autotuner/baselines/{baseline_id}.json` 에 전체 baseline을 저장 → 롤백 기준.

**이유:** `_workspace/`는 이미 orchestrator가 쓰는 비-런타임 산출물 디렉터리다(트레이딩 상태/설정과 분리).
`config/`나 `data/`(runtime_state)에 두지 않는다 — 그쪽은 런타임 신뢰 경계 안이라 오염 위험.

**`baseline_id` 형식:** `settings_{account}_{YYYYMMDDHHMMSS}` (예: `settings_mock_12345678_01_20260604_090000`).
account 없는 기본값 baseline은 `settings_default_{YYYYMMDD}`.

---

## D3 — `proposal_id` 규칙

**결정:** `atp_{YYYYMMDD}_{mode}_{NNNN}`
- `atp` = autotuner proposal prefix
- `YYYYMMDD` = 생성일(KST)
- `mode` = `mock` | `shadow` | `approved_low_risk` | `approved_high_risk`
- `NNNN` = 그날·그 mode의 zero-padded 시퀀스 (0001부터)

**유일성:** 중앙 카운터를 두지 않는다. 생성 시 대상 디렉터리(D2의 proposals 경로)에서 같은 prefix의 기존
파일을 스캔해 다음 `NNNN`을 고른다. 충돌 시 +1. (Phase 1 generator는 caller가 id를 주입하는 순수 함수이므로,
이 시퀀스 계산은 호출부/persist 레이어 책임 — generator는 순수성 유지.)

**proposals 저장 경로:** `_workspace/autotuner/proposals/{proposal_id}.json` (write_proposal 기본 디렉터리 후보).

---

## D4 — evidence 소스 경로 & 스키마 고정

evidence 엔트리(schema 문서 §7)의 `source_type`별 구체 경로/규칙:

| source_type | 경로/생성 | 비고 |
|-------------|-----------|------|
| `backtest` | `results/backtests/{bt_run_id}.json` (via `app/tools/run_proposal_backtest.py` → open-trading-api REST `:8002`) | **proxy 증거.** Lean DSL ≠ 라이브 전략. `limitations`에 명시 필수 |
| `live_log` | `data/backtest/reconstruct_{date}.jsonl` (via `app/backtest/reconstruct.py`) | 로그 재구성. 전략 엔진 재생 아님 |
| `postrun_audit` | `_workspace/postrun_audit_{account}_{date}.md` (via `app.tools.orchestrate`) | 운영 감사 참조 |
| `manual_review` | 자유 텍스트 `source_id` | 사람 검토 메모 |
| `mcp_report` | MCP run id (`:3846`) | proxy. 단독 승인 근거 불가 |

**불변식 (trust boundary 재확인):**
- backtest/mcp 증거는 **proxy** — 단독으로 live 적용 근거가 될 수 없다.
- **live-eligible 승인(approved_*)에는 최소 1개의 `live_log` 교차검증 증거가 필수.** ✅ **시행됨(enforced)** —
  `validate_proposal`이 live-eligible 모드에서 `source_type == "live_log"` 증거가 없으면 거부한다
  (`tests/test_autotuner_validator.py`: live_log 누락 거부 + live_log 포함 수락 테스트).

> `data/`·`results/` 는 전체 read 금지 규칙 대상 — evidence는 **참조(path/id)만** 담고, 내용 요약은
> 생성 시점에 bounded 하게 추출한다.

---

## D5 — 승인 채널

**결정:** Phase 0–3에서 `approval.approval_channel`은 **`local_file`만** 허용. Slack(`@kis-trader`) 승인은
**미래 별도 결정**까지 보류(별도 위험: 채널 위·변조, 권한). schema는 `slack` 값을 예약하되 validator는
현재 거부 대상으로 두지 않고(미래 확장), 운영상 local_file만 사용한다.

---

## D6 — `superseded` 전이 규칙

**결정:** "동시 active bundle 1개" 불변식을 다음으로 구현한다.
- 새 bundle이 `approved`로 전이될 때, 기존 active bundle의 `status` → `superseded`.
- `superseded`/`expired`/`rejected` 는 비활성. active = `approved` AND not expired.
- 교체는 명시적이어야 한다(자동 무효화 아님): 새 승인 액션이 이전 것을 대체한다는 audit 이벤트를 남긴다.

**구현 상태 (2026-06-06):** `persist.supersede_active_bundles`가 승인 시 이전 active 동일-mode 번들을
`superseded`로 atomic 전이하고 `event: superseded, by: <new_id>` audit 이벤트를 남긴다. approve CLI가
write 직후 호출한다. 이전엔 미구현이라 2번째 승인 시 active 번들 2개 → 런타임 reader가
`len(eligible)!=1`로 `{}` fail-closed(아무것도 적용 안 됨)되는 운영 함정이 있었다.

---

## D7 — shadow 증거 요구량 N

**결정:** **N = 3** comparable regular-session shadow 세션을, 저위험(Tier A) 파라미터가
**live**-eligible 되기 전 요구한다. (시작값이며, 운영 데이터로 조정 가능. `compare_runtime_quality`의
confidence(low/medium/high)와 연계해 "comparable" 판정.)

**구현 상태 (정합화 2026-06-06):** D7의 N=3은 **live 활성화 전 운영 게이트**다. 현재 코드 경로는
*mock-only* 활성화이므로(`KIS_ENV=live`는 아직 켜지지 않은 별도 하드 게이트), validator는 mock
적격 기준으로 **D4 불변식 = `live_log` 증거 ≥ 1**만 강제한다 (`validate_proposal`). 즉:
- **mock 활성화 (현재):** 승인 번들에 `live_log` 증거 **≥ 1** (D4). validator가 강제.
- **live 활성화 (미래):** 위에 더해 **N = 3** comparable shadow 세션 (D7). 이는 `KIS_ENV=live`
  전환과 묶인 운영 게이트이며, live 경로를 켤 때 validator/eligibility에 코드로 끌어올린다.

이 분리는 의도적이다 — N을 mock 단계에서 하드 강제하면 mock 루프를 막으면서도 라이브 안전엔
기여하지 못한다. 라이브 게이트를 켜는 시점에 N=3을 코드 강제로 승격한다.

---

## 다음 단계 (Phase 2 착수 조건)

이 결정들이 고정되면 Phase 2(shadow 평가)는 다음을 구현한다 (여전히 라이브 변경 0):
1. `_workspace/autotuner/` 디렉터리 레이아웃(baselines/proposals) + persist 레이어(`proposal_id` 시퀀스, D3).
2. evidence 수집 어댑터: `run_proposal_backtest` 결과 → backtest evidence, `reconstruct` → live_log evidence (D4).
3. validator 강화: live-eligible 모드에 **live_log 증거 ≥1** 요구 (D4 불변식).
4. shadow 러너: 제안 bundle을 라이브 로그에 대해 평가(적용 없음), 결과를 evidence로 첨부.

> 어떤 단계도 broker/order API 호출이나 credential 접근, 런타임 설정 쓰기를 포함하지 않는다. 최초의
> 런타임 reader는 Phase 3이며 `/risk-assessment` 게이트 대상이다.
