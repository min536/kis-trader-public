---
Purpose: 라이브-오토튜너 게이트를 여는 순서와 단계별 go/no-go 기준 (운영 결정 기록)
Read when: autotuner 활성 플래그를 켜기/live 전환/Tier B·C를 검토하기 전
Do not use for: 코드가 자동으로 단계를 올리지 않는다 — 모든 승급(promotion)은 운영자(사람) 결정
Status: ACTIVE — Stage 2 GO 판정(2026-06-10, 운영자·N=2). Stage 3은 Tier B draft(atp_20260610_shadow_0001) 승인 + mock flag 준비 후 06-10 mock 관찰 #1 완료. Stage 4 승급은 보류, 추가 mock 관찰/운영자 go 결정 대기. Stage 4a 진단 경로 검증 완료, shadow 세션 N=3 대기. 승급은 사람이 한다.
---

# Autotuner Rollout Plan — staged gate opening

> **한 줄 원칙:** autotuner는 "기계가 제안하고 사람이 승인하는 **mock 운영 실험**"까지는 열어도 되고,
> **live와 high-risk는 아직 닫힌 문**으로 남겨둔다. 게이트는 한 번에 하나씩, 앞 단계가 안정된 뒤에만 연다.

이 문서는 **운영 결정**을 기록한다. 어떤 코드도 단계를 자동으로 올리지 않는다 — 각 승급은 운영자가
플래그/환경을 바꾸는 명시적 액션이다(에이전트 세션에서 `app.main`/세션/플래그를 켜지 않는다).

## 운영 기준 요약

- **단기:** Tier A mock activation만 실제로 켜서 관찰.
- **중기:** Tier B 승인 producer를 mock-only로 구현.
- **장기:** live는 read-only shadow → Tier A limited live → 그 후 Tier B 검토.
- **보류:** Tier C unblock.

---

## Stage 1 — 현상 유지 (현재 위치)

**상태:** 활성 플래그 default-OFF, live 미적용, Tier B 승인 producer는 준비됐지만 미사용(별도 사람 승인/flag 필요),
Tier C `blocked`.

**코드가 보장:** `resolve_runtime_overrides`/`resolve_high_risk_overrides`는 `enabled` flag가 꺼져 있거나
`env != "mock"`이면 `{}`(fail-safe). validator는 Tier C/Tier D를 거부, `approved_high_risk`를 기본 거부.

**이탈(→ Stage 2) 조건:** 운영자가 Tier A mock soak를 시작하기로 결정.

---

## Stage 2 — Tier A mock soak (단기에 여는 유일한 게이트)

**무엇:** Tier A(scan/throttle cadence) override를 **mock에서만** 실제 적용해 관찰.

**진입(go) 조건 / 운영자 액션:**
- 프로세스 환경에서(`.env` 아님): `KIS_ENV=mock`, `AUTOTUNER_RUNTIME_OVERRIDES_ENABLED=true`.
- approved_low_risk **Tier A만** 허용(validator가 강제).
- TTL **짧게** — 예: 1 trading day (`autotuner_approve --ttl-hours 8` 수준).
- 루프(전부 read-only/offline, 적용은 승인 후 mock 런타임):
  ```sh
  python -m app.tools.autotuner_operational_backtest --candidates candidates.json --baseline baseline.json --session-summary research/sessions/session_YYYYMMDD.json --out research/evaluations/eval_operational_YYYYMMDD.json
  python -m app.tools.autotuner_screen  --evals-dir research/evaluations --out screening.json
  python -m app.tools.autotuner_suggest --screening screening.json --baseline baseline.json --date $(date +%Y%m%d) --out plan.json
  python -m app.tools.autotuner_propose --plan plan.json            # dry-run 검토
  python -m app.tools.autotuner_approve --proposal-file <draft> --approved-by human:<you> --ttl-hours 8 --approve
  python -m app.tools.autotuner_status  --enabled                   # would_apply 관찰
  ```
- 관찰: **3~5회 정규 세션**.

**관찰 목표:** override가 **안전하게 적용/미적용**되고, 단일-active fail-closed(2개 active → `{}`)가
**운영자를 놀라게 하지 않는지**(supersede가 active 1개를 유지). `autotuner_status`가 ambiguous/expired/no-eligible를
구분해 보여줌.

**이탈(→ Stage 3) 조건:** 3~5세션 동안 surprise 없음, supersede/TTL/fail-closed가 예상대로 동작.
안정적이지 않으면 Stage 2에 머문다.

> **운영 결정 (2026-06-10): GO — N=2로 단축 수용.** 06-09(212사이클, EOD 정합 100%/error 0)·06-10
> 두 mock 세션에서 `buy_scan_shallow_top_k` 10→8 override가 전 사이클 적용됨을 로그로 확인
> (raw 10 vs effective 8; midday/degraded clamp가 각각 24/16이라 autotuner 병합 외 경로 없음).
> 근거: Tier B도 mock-only + safer-of + 사람 승인 게이트라 N=2 진입의 한계 리스크가 낮음.
> **단서:** supersede·2-active fail-closed·장중 TTL 만료는 실세션 미관찰(단위테스트만) — Stage 3
> 세션에서 Tier A를 켜둔 채로 계속 관찰한다.

> live는 아직 열지 않는다. Tier B는 mock-only 관찰에 한정하며, Tier C는 계속 닫아둔다.

---

## Stage 3 — Tier B 승인 producer (mock-only, 중기)

**무엇:** Tier B(`rebuy_cooldown_minutes`, `same_symbol_max_buys_per_day`) 제안을 사람이 승인할 수 있는
경로. **reader는 이미 존재**(`resolve_high_risk_overrides`, default-OFF); 승인 CLI는 `--high-risk`로 분리.

**코드 준비/후보 생산:** Stage 2 Tier A mock soak 안정 판정 전에도 진행 가능하다. 이 작업은
offline/read-only eval·proposal 준비일 뿐, runtime 적용이 아니다.

**운영 진입(go) 조건:** Stage 2 Tier A mock soak가 안정적이라고 운영자가 판단한 뒤에만 high-risk mock
flag와 승인 artifact를 실제 세션에 연결한다.

**승인 조건(설계 고정):**
- `approved_high_risk` 모드, `validate_proposal(allow_high_risk=True)`.
- `autotuner_approve --high-risk --approve`로 저위험 승인과 분리.
- `risk_review` **필수**, `live_log` 증거 **≥ 2**.
- TTL을 Tier A보다 **더 짧게**.
- **mock-only** — `AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED=true` + `KIS_ENV=mock`.

**여기까지도 live는 열지 않는다.**

**이탈(→ Stage 4) 조건:** Tier B mock 적용이 Tier A와 동등하게 안정적.

**operator 명령:**
```sh
python -m app.tools.autotuner_approve --proposal-file <draft> --approved-by human:<you> --ttl-hours 4 --high-risk --approve
python -m app.tools.autotuner_status --env mock --high-risk --enabled-high-risk
```

**현재 상태 (2026-06-10):** draft
`_workspace/autotuner/proposals/atp_20260610_shadow_0001.json` 승인 완료
(`mode=approved_high_risk`, `rebuy_cooldown_minutes` 20→35, TTL `2026-06-10 15:33 KST`).
session launchd env에는 `KIS_ENV=mock`, `AUTOTUNER_RUNTIME_OVERRIDES_ENABLED=true`,
`AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED=true`가 설정됐다.

**mock 관찰 #1 (2026-06-10):** EOD `reports/eod_20260610.txt`는 `Exit code 0`, signal/order aligned
`yes`, app stderr 0 bytes. Tier A는 raw config 10 대비 effective `BUY_SCAN_SHALLOW_TOP_K=8` 반복 출력으로
적용 확인. Tier B는 `rebuy_cooldown_minutes` 35가 RISK_OFF multiplier(4x)를 타는 expected effective 값
`effective reentry cooldown=140분`이 세션 내 반복 출력되어 적용 증거로 채택한다. ML viability는
`best_first_target=none` / `evidence is weak across labels`라 전략/ML 승급 근거로 쓰지 않는다.

**임시 판단:** Stage 3 mock #1은 **PASS / no surprise**. 다만 N=1이므로 Stage 4 승급은 하지 않는다.
Tier B TTL은 세션 종료 직후 만료됐고, 장중 TTL 만료·2-active fail-closed·supersede 실세션 관찰은 아직
미충족이다. 다음 gate는 같은 proposal 재승인(짧은 TTL) 후 추가 mock 세션 관찰 + EOD 리뷰다.

**다음 operator 명령(다음 mock 세션 전에):**
```sh
cd $KIS_TRADER_ROOT
.venv/bin/python -m app.tools.autotuner_approve \
  --proposal-file _workspace/autotuner/proposals/atp_20260610_shadow_0001.json \
  --approved-by human:example-user \
  --ttl-hours 4 \
  --high-risk \
  --approve
.venv/bin/python -m app.tools.autotuner_status --env mock --high-risk --enabled-high-risk
```

**후보 producer:** Stage 2 안정 판정 전에도 코드상 후보 생산은 가능하다. `autotuner_operational_backtest`
는 local session summary 기반 offline/read-only proxy eval만 만들며, 승인/적용은 여전히 별도 사람 게이트다.
Tier B 후보도 whitelist/bounds/max_step을 통과해야 하고, 실제 mock 적용은 위 `--high-risk` 승인과 high-risk
flag 없이는 불가능하다.

---

## Stage 4 — live activation (제일 마지막, 단계적)

live에서 처음부터 runtime override를 켜지 않는다. 순서:

**4a. live read-only shadow diagnostics (먼저):**
- live에서 "이 proposal이면 `would_apply`가 무엇이었는지"만 **기록**하고 **실제 적용은 하지 않음**.
- `diagnose_runtime_overrides`와 `resolve_runtime_overrides`의 기존 live short-circuit은 유지한다.
  live shadow는 별도 read-only 경로(`autotuner_status --live-shadow`)로만 계산한다.
- **operator 명령:**
  ```sh
  python -m app.tools.autotuner_status --env live --live-shadow
  ```
- 최소 **N=3 comparable shadow session**(D7)을 채운다.

**4b. live Tier A limited:**
- 위가 채워진 뒤에야 **live Tier A만 아주 제한적으로** 고려.
- `KIS_ENV=live`는 별도 하드 게이트(`/risk-assessment` 대상).

**4c. live Tier B:** 4b가 충분히 안정된 후에야 검토.

> D7 N=3은 **live 활성화 전 운영 게이트**다(mock은 D4 `live_log≥1`만 강제). 근거:
> [`docs/live_autotuner_decisions.md`](live_autotuner_decisions.md) D7.

---

## Stage 5 — Tier C unblock (당분간 보류)

**결정: 지금 하지 않는다.** Tier C(주문 사이징 등)는 전략 의미가 크고 손익/리스크 표면이 넓다.
autotuner 본체 완료 직후에 건드릴 이유가 없다.

**미래에 별도 프로젝트로:** 더 긴 backtest window + walk-forward + risk review + rollback drill을 붙인 뒤.
현재는 whitelist `blocked: true` + validator 거부로 기본 차단(D1).

---

## 모든 단계에 걸친 불변식 (절대 약화하지 않음)

- **mock-first, human-in-the-loop:** 어떤 적용도 사람 승인 뒤에만. 자동 적용 없음.
- **default-OFF:** 플래그를 켜는 건 항상 운영자 액션.
- **safer-of 병합:** override는 보호 clamp를 절대 이기지 못함(간격→max, cap→min, cooldown→max).
- **fail-closed:** active 번들이 정확히 1개가 아니면 `{}`. 연결 실패는 "evidence unavailable"로 끝나며 거래를 막지 않음.
- **proxy evidence:** 백테스트 결과는 proxy. approved 제안은 `live_log` 교차검증 필요(D4).
- **`.env`/credential 불가침, 에이전트가 세션/플래그를 켜지 않음.**

## 교차 참조
- 결정/불변식: [`live_autotuner_decisions.md`](live_autotuner_decisions.md) (D1·D4·D6·D7)
- 스키마/승인 계약: [`live_autotuner_proposal_schema.md`](live_autotuner_proposal_schema.md)
- 운영자 루프 SOP: `runbook_autotuner_loop.md` (not included in this source snapshot)
- 외부 백테스트 연결: `runbook_open_trading_api.md` (not included in this source snapshot)
