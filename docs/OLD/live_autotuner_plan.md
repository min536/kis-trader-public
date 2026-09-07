---
Purpose: 장중 파라미터 제안·승인·주입(live autotuner)의 단계적 설계안
Read when: "장중에 백테스트 돌려 세팅값 바꾸는 시스템"을 설계·구현하기 전, 또는 안전 경계를 검토할 때
Do not use for: 이 문서를 단독 진행 상태(roadmap status)로 읽지 말 것 — 아래 "구현 상태" 및
  docs/live_autotuner_decisions.md / *_phase3_risk.md / *_phase4_risk.md / *_phase5_candidate_suggest.md 참조
Status: PARTIALLY IMPLEMENTED (mock-only, default-OFF) — 원안은 설계, 일부 Phase는 구현 완료. 아래 표 참조.
---

> **구현 상태 (2026-06-07).** 이 문서는 원래 설계안이며, 이후 다음이 *구현*되었다(전부 mock-only,
> default-OFF, human-gated, 라이브 변경 0):
>
> | 영역 | 상태 | 위치 |
> |------|------|------|
> | 제안 schema/validator (whitelist·bounds·cross·max_step·self-approve) | 구현 | `app/autotuner/validator.py`, `config/autotuner_whitelist.yaml` |
> | persist / proposal_id 시퀀스 / baseline / supersede(D6) | 구현 | `app/autotuner/persist.py` |
> | evidence bridge (backtest·live_log, bounded read) | 구현 | `app/autotuner/evidence_sources.py` |
> | 승인 게이트 + CLI (draft→approved, TTL, self-approve 금지) | 구현 | `app/autotuner/approval.py`, `app/tools/autotuner_approve.py` |
> | Tier A 런타임 reader + activation (safer-of, default-OFF) | 구현 | `app/autotuner/runtime_override.py`·`runtime_activation.py` → `app/main.py` |
> | Tier B high-risk approval + reader (default-OFF, ≥2 live_log+risk_review) | 구현 — `--high-risk` opt-in, mock-only | `approval.approve_proposal`, `autotuner_approve`, `runtime_override.resolve_high_risk_overrides` |
> | candidate-suggest (MCP screening→plan) | 구현 | `app/autotuner/candidate_suggest.py`, `app/tools/autotuner_suggest.py` |
> | operational-cadence eval producer (screening source) | 구현 — offline/read-only proxy evidence | `app/autotuner/operational_backtest.py`, `app/tools/autotuner_operational_backtest.py` |
> | diagnostics / status CLI | 구현 — Tier A, Tier B, live-shadow read-only | `app/autotuner/diagnostics.py`, `app/tools/autotuner_status.py` |
>
> 아래 §9 Open Questions 중 다수는 결정·구현되었다(괄호 주석 참조). 남은 휴먼 게이트:
> activation 플래그 enable, Stage 2/3 soak 판정, `KIS_ENV=live` 전환, Tier C unblock(D1).

# Live Autotuner / Parameter Proposal — 설계안

> **요지:** "장중에 agent가 백테스트를 돌려 세팅값을 바꾸는" 시스템을 만들기 위한
> 단계적 설계. **핵심은 백테스트 실행이 아니라, 안전한 제안 → 사람 승인 → 경계가 걸린
> 주입(bounded injection)** 이다. 백테스트 엔진도, 장중 주입 seam도 *이미 존재*한다.

---

## 1. Executive Summary

세 가지 사실이 이 설계의 출발점이다.

1. **장중 주입 seam은 이미 있다.** `app/runtime/session_loop.py`의 `runtime_rate_control` 딕셔너리와
   `app/main.py`의 per-cycle `cycle_settings = replace(settings, ...)` 경로가, **프로세스 재시작 없이**
   매 사이클 약 20개 설정을 덮어쓴다. 신규로 "어떻게 장중에 값을 바꾸나"를 발명할 필요가 없다.

2. **백테스트/프록시 엔진도 이미 있다.** 공식 KIS `open-trading-api`(`$OPEN_TRADING_API_ROOT`)는
   Lean 기반 백테스터 + REST(`:8002`) + MCP(`:3846`)를 제공하고, kis-trader는 이미
   `app/tools/run_proposal_backtest.py`로 `localhost:8002/api/backtest/run-custom`에 POST한다.

3. **진짜 어려운 문제는 "안전한 제안 → 승인 → 경계가 걸린 주입"이다.** 백테스트를 돌리는 것은 쉽다.
   어려운 것은 (a) 백테스트 결과를 *얼마나 믿을지*, (b) 어떤 파라미터를 *얼마 범위까지* 바꿔도 되는지,
   (c) 누가 *최종 승인*하는지, (d) 잘못됐을 때 *어떻게 되돌리는지*다.

따라서 이 설계의 무게중심은 백테스트 자동화가 아니라 **신뢰 경계(trust boundary)와 안전 envelope**에 있다.

---

## 2. 현재 재사용 가능한 컴포넌트

| 컴포넌트 | 위치 | 역할 | 재사용 방식 |
|---------|------|------|-----------|
| 런타임 주입 seam | `app/runtime/session_loop.py` (`runtime_rate_control`), `app/main.py` (`cycle_settings = replace(settings, ...)`) | 재시작 없이 per-cycle 설정 덮어쓰기 | **override source를 "승인된 제안"도 받도록 확장** (지금은 내부 rate-controller만 source) |
| operational-cadence evaluator | `app/tools/autotuner_operational_backtest.py` | local session summary + whitelist/bounds 기반 autotuner-domain eval 생성. "offline/read-only, never applies" | `autotuner_screen`의 실제 producer |
| 제안 백테스트 브리지 | `app/tools/run_proposal_backtest.py` | strategy/research 제안값 → 외부 Lean 백테스터 REST POST. "research-only, never touches live settings" | autotuner evidence에 proxy로 첨부 가능. screening producer로 직접 쓰지 않음(도메인 불일치) |
| 제안 파이프라인 | `app/tools/proposal_generator.py`, `proposal_registry.py`, `proposal_constraints.py`, `parameter_change_simulation_report.py` | 제안 생성/등록/제약/시뮬레이션 리포트 | 제안 큐·제약 검사 토대 |
| 내부 백테스트 | `app/backtest/` (`reconstruct.py`, `schema.py`, `signal_logger.py`) | 로그에서 시그널 **재구성** | **라이브 로그 교차검증**용 (전략 엔진 아님 — 아래 §3 한계 참조) |
| 외부 백테스터/MCP | `$OPEN_TRADING_API_ROOT` (Lean, REST `:8002`, MCP `:3846`) | 프리셋 전략·80 지표·`.kis.yaml` DSL 백테스트 | 1차 후보 스크리너 (간접 활용) |
| Postrun 오케스트레이터 | `app/orchestrator/` (read-only, no-LLM) | 결정적 운영 감사 (orders/rate_limits/reconciliation/action_candidates) | 감사·증거 수집 패턴 참고 |

### app/backtest 의 한계 (중요)

`app/backtest/reconstruct.py`는 "기존 candidate/cycle 로그에서 backtest-ready 시그널을 만든다." 즉
**로그 재구성**이지 `app/strategy/buy_decision`·`sell_decision`을 호출하는 **전략 엔진 재생(replay)이 아니다.**
이 모듈은 라이브 전략 코드를 import하지 않는다. → 백테스트로 본 수익은 라이브 봇의 정확한 재현이 아니다.

---

## 3. 신뢰 경계 (Trust Boundary)

이 시스템의 안전성은 단 하나의 규칙으로 요약된다: **자동화는 "제안"까지, 라이브에 영향을 주는 "적용"은 사람이.**

- **MCP/백테스터가 라이브 설정을 직접 바꾸지 못한다.** 외부 도구는 *읽기/시뮬레이션*만. 라이브 seam에 값을
  쓰는 경로는 kis-trader 내부의 승인된 코드 path 하나로만 한정한다.
- **백테스트 성능을 ground truth로 취급하지 않는다.** 외부 Lean 백테스터는 *Lean DSL 프리셋 전략*을,
  내부 `app/backtest`는 *로그 재구성*을 돌린다 — **둘 다 라이브 전략 엔진(`app/strategy`)을 그대로
  재현하지 않는다(대표성 gap).** 백테스트 결과는 **프록시 증거(proxy evidence)** 일 뿐, 결정적 근거가 아니다.
- **라이브에 영향을 주는 변경은 사람 승인이 필수다.** human-in-the-loop. 자동 적용 없음 (저위험 파라미터의
  Phase 3 이후에도 "승인 후 적용" 원칙 유지).
- **모든 제안값은 명시적 검사 3종을 통과해야 한다:** ① whitelist(허용 목록에 있는 파라미터인가)
  ② bounds(min/max 범위 안인가) ③ risk-envelope(단조 안전 제약·동시 변경 제한 등). 하나라도 실패하면 거부.

```
[backtest/MCP: 프록시 증거]  ──제안만──▶  [whitelist+bounds+risk 검사]
                                                │ pass
                                                ▼
                                       [사람 승인 게이트]  ──거부시 폐기
                                                │ approve
                                                ▼
                              [kis-trader 내부 승인 코드 path] ──▶ cycle_settings seam
                                                │
                                                ▼
                                         [audit log + TTL + rollback]
```

---

## 4. 파라미터 분류

런타임 seam이 이미 덮어쓸 수 있는 ~20개 중, **위험도에 따라 게이트 강도를 다르게** 둔다.

### 4-1. 저위험 초기 후보 (Phase 3 우선 대상)

주문 크기·계좌 노출·체결에 직접 영향을 주지 *않고*, 주로 스캔 효율/탐색 폭/타이밍에만 작용하는 값.

- `scan_symbols_max_per_cycle` — 사이클당 스캔 심볼 수
- `buy_scan_deep_eval_limit` — 심층 평가 한도
- `buy_scan_shallow_top_k` — 얕은 평가 상위 K
- scan intervals / `sell_check_interval_seconds` — **보수적 범위 내에서만** (너무 짧으면 API 예산/레이트리밋 압박)
- `sell_watch_max_holdings_per_tick` — tick당 매도 감시 보유 수

이들도 무제한은 아니다. interval을 과도하게 줄이면 EGW00201(레이트리밋) 위험 → bounds로 하한을 둔다.

### 4-2. 고위험 후보 (강한 게이트 필요)

주문 크기·계좌 노출·일일 주문 수에 직접 작용 → **trading-critical surface.** 더 많은 증거, 더 좁은 범위,
더 강한 risk 게이트, 더 늦은 Phase(4)에서만 다룬다.

- `buy_max_account_exposure_pct` — 계좌 노출 한도(%)
- `buy_max_budget_per_trade_krw` — 거래당 예산
- `buy_max_qty_per_trade` — 거래당 수량
- `buy_daily_max_order_submissions` — 일일 주문 제출 한도
- `same_symbol_max_buys_per_day` — 동일 심볼 일일 매수 한도
- `rebuy_cooldown_minutes` — 재매수 쿨다운
- `math_multiplier` — 사이징/스코어 배수 (효과가 넓게 퍼지므로 고위험)

> **명시적 경고:** 주문 크기(qty/budget), 계좌 노출(exposure_pct), 일일 주문 수(daily_max_submissions)에
> 관여하는 모든 값은 **trading-critical**이다. 이들은 `app/risk` / `app/execution`의 안전 가정과 직접
> 맞물리므로, 자동 적용 금지 + risk-analyst 게이트 + 사람 승인 + 좁은 bounds를 모두 요구한다.

---

## 5. 제안 아키텍처

```
┌──────────────────────────────────────────────────────────────────────┐
│  (선택) 주기적 트리거 — N분마다, 장중                                    │
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────┐   현재 settings + 최근 로그/시장 컨텍스트
│ Candidate       │   ──▶ whitelist 안에서만 후보 파라미터 묶음 생성
│ Generator       │       (proposal_generator 재사용/확장)
└─────────────────┘
        │ 후보 bundle
        ▼
┌─────────────────┐   open-trading-api REST(:8002)/MCP(:3846) 호출
│ Backtest /      │   ──▶ 후보들의 *상대적* 우열만 빠르게 스크리닝
│ Proxy Evaluator │       (run_proposal_backtest 재사용)  ※프록시 증거
└─────────────────┘
        │ 점수 매겨진 후보
        ▼
┌─────────────────┐   app/backtest reconstruct + 라이브 시그널 로그
│ Live-log        │   ──▶ "이 파라미터가 실제 라이브에서 어떻게 동작했나"
│ Cross-validator │       교차검증 → 대표성 gap 일부 보정
└─────────────────┘
        │ 검증된 제안
        ▼
┌─────────────────┐   whitelist + bounds + risk-envelope 검사
│ Proposal        │   ──▶ proposal_registry / proposal_constraints 재사용
│ File / Queue    │       검사 통과분만 큐에 등록 (감사 가능한 artifact)
└─────────────────┘
        │ 대기 중 제안
        ▼
┌─────────────────┐   사람이 검토 후 승인/거부 (Slack/CLI/파일)
│ Human Approval  │   ──▶ 승인 없이는 어떤 라이브 변경도 발생하지 않음
│ Gate            │
└─────────────────┘
        │ 승인된 override bundle (단 1개 active)
        ▼
┌─────────────────┐   기존 cycle_settings seam에 "승인된 source"로 병합
│ Runtime         │   ──▶ runtime_rate_control + replace(settings, ...)
│ Override Source │       (내부 rate-controller와 동일 메커니즘, 출처만 다름)
└─────────────────┘
        │
        ├──▶ Audit Log    : 무엇을/누가/언제/근거(백테스트 id)/적용결과
        └──▶ Rollback Path: TTL 만료 또는 수동 트리거 시 baseline settings 복귀
```

**구성요소 요약:**
- **Candidate Generator** — whitelist 안에서만 후보 묶음 생성 (`proposal_generator` 확장).
- **Backtest/Proxy Evaluator** — `open-trading-api` REST/MCP로 후보 스크리닝 (프록시 증거).
- **Live-log Cross-validator** — `app/backtest` 재구성 + 라이브 로그로 대표성 gap 보정.
- **Proposal File/Queue** — 검사 통과 제안을 감사 가능한 artifact로 등록 (`proposal_registry`).
- **Human Approval Gate** — 승인 없이는 라이브 변경 0건.
- **Runtime Override Source** — 승인된 bundle을 기존 seam에 "또 하나의 source"로 병합.
- **Audit Log** — 모든 적용/롤백을 추적.
- **Rollback Path** — TTL/수동으로 baseline 복귀.

---

## 6. 안전 Envelope

라이브를 보호하는 불변식(invariant) 목록. 구현 시 이 전부를 코드로 강제한다.

- **whitelist only** — 목록에 없는 파라미터는 어떤 경로로도 변경 불가.
- **per-parameter min/max bounds** — 모든 whitelist 항목에 명시적 상·하한. 범위 밖이면 거부.
- **monotonic safety constraints** — 가능한 경우 단조 안전 제약. 예: 노출/예산/일일주문 한도는 baseline
  대비 *완화(증가)* 시 더 강한 게이트, *강화(감소)* 는 상대적으로 안전.
- **no credential access** — `.env`/`kis_devlp.yaml`/토큰 캐시/계좌 파일 접근 금지.
- **no direct broker calls** — 제안·평가·검증 경로에서 broker/order API 호출 금지.
- **고위험 파라미터 자동 적용 금지** — §4-2 항목은 사람 승인 없이는 절대 seam에 반영되지 않음.
- **max one active override bundle** — 동시에 활성 override는 1개. 새 승인은 이전 것을 명시적으로 대체.
- **TTL/expiry on overrides** — 모든 override는 만료시각을 가짐. 만료 시 자동으로 baseline 복귀.
- **rollback to baseline** — 언제든 baseline settings로 1-step 복귀 가능 (수동/자동).
- **dry-run & shadow first** — 라이브 쓰기 전, dry-run(쓰기 없음)·shadow(라이브 로그 대비 평가만) 모드를
  먼저 통과.

---

## 7. Phase 계획

각 Phase는 이전 Phase의 증거가 쌓인 뒤에만 다음으로 넘어간다. **mock-first, live는 hard gate 뒤.**

| Phase | 내용 | 라이브 영향 | 게이트 |
|-------|------|-----------|--------|
| **0** | 문서 + 스키마만 (이 문서, 제안/감사 스키마 정의) | 없음 | — |
| **1** | mock 제안 파일 생성. 런타임 주입 *없음* (artifact만 생성·검토) | 없음 | 코드리뷰 |
| **2** | shadow 평가 — 라이브 로그 대비 제안 평가. 라이브 변경 *없음* | 없음 | shadow 증거 N세션 |
| **3** | 승인된 **저위험(§4-1)** 런타임 override만. human-in-loop | mock 우선, live는 별도 hard gate | 승인 + bounds + TTL |
| **4** | 감사 증거 축적 후, **고위험(§4-2)** 으로 신중히 확장 | trading-critical — 최강 게이트 | 승인 + risk-analyst + 좁은 bounds |
| **5** | (선택) MCP 보조 후보 생성. 여전히 라이브 직접 쓰기 권한 *없음* | 없음 (제안까지만) | §3 신뢰 경계 유지 |

> **mock → live hard gate:** Phase 3 이상에서 live 적용은 mock에서의 충분한 증거 + 명시적 KIS_ENV=live
> 확인 + 사람 승인을 모두 요구한다. mock에서 검증되지 않은 override는 live에 적용하지 않는다.

---

## 8. Non-goals (명시적 비목표)

- **자율 트레이딩 에이전트가 아니다.** 에이전트가 스스로 매매하지 않는다. 제안만 한다.
- **MCP 직접 라이브 제어 없음.** 외부 MCP/백테스터는 라이브 설정에 쓰기 권한이 없다.
- **백테스트 PnL만으로 자동 self-tuning 없음.** 프록시 증거만으로 자동 적용하지 않는다.
- **기존 risk 관리 대체 아님.** `app/risk`/`app/execution`의 기존 안전장치를 우회·대체하지 않고, 그 *위에* 게이트를 추가한다.

---

## 9. Open Questions (결정 필요)

구현 착수 전 사용자와 확정해야 할 항목. *(대부분 결정·구현됨 — 괄호 주석은 현재 상태.)*

1. **정확한 whitelist와 bounds** — §4의 분류는 초안. 각 파라미터의 실제 min/max와 단조 제약을 확정해야 한다.
   *(해결: `config/autotuner_whitelist.yaml`이 단일 출처. validator가 bounds·max_step·cross 제약 강제.)*
2. **제안 파일 포맷** — 기존 `proposal_registry` 스키마 재사용 vs 신규. 필드(근거 backtest id, TTL, 승인자 등) 정의.
   *(해결: 신규 schema — `docs/live_autotuner_proposal_schema.md`, `app/autotuner/generator.py`.)*
3. **audit log 위치** — `_workspace/` vs `logs/` vs 별도 추적 파일. 보존 정책.
   *(해결: 제안 artifact 내 `audit.events`(supersede/approve 포함), `_workspace/autotuner/` 저장.)*
4. **런타임 override 저장 위치** — config vs 로컬 JSON vs 생성된 제안 artifact 중 어디를 source of truth로 둘지.
   *(해결: 승인된 제안 artifact가 source of truth — `_workspace/autotuner/proposals/`, reader가 직접 소비.)*
5. **shadow 증거 요구량** — 승인된 라이브 override를 켜기 전, 몇 세션치 shadow 증거를 요구할지(N).
   *(결정: D7 — mock=`live_log`≥1(D4, 강제), live 전 N=3(운영 게이트). `decisions.md` D7 참조.)*
6. **트리거 주기** — 장중 N분(설계상 "주기적") 구체값과, 시장 변동성에 따른 가변 여부.
   *(미정 — 운영 게이트. propose-cycle은 수동/주기 트리거 모두 지원하도록 무상태로 설계됨.)*
7. **승인 채널** — Slack 봇(`@kis-trader`) vs CLI vs 파일 기반. 누가 승인 권한을 갖는지.
   *(결정: D5 — 현재 `local_file`(CLI)만. Slack 승인은 미래 별도 결정.)*

---

## 부록 A. 관련 파일 인덱스

- 런타임 seam: `app/runtime/session_loop.py`, `app/main.py` (`cycle_settings = replace(settings, ...)`)
- 제안/백테스트: `app/tools/run_proposal_backtest.py`, `app/tools/proposal_generator.py`, `app/tools/proposal_registry.py`, `app/tools/proposal_constraints.py`, `app/tools/parameter_change_simulation_report.py`
- 내부 백테스트(재구성): `app/backtest/reconstruct.py`, `app/backtest/schema.py`, `app/backtest/signal_logger.py`
- 외부 백테스터/MCP: `$OPEN_TRADING_API_ROOT` (Lean, REST `:8002`, MCP `:3846`)
- 설정 surface: `app/auth/settings.py` (KIS_ENV mock 기본), `config/regular_session.env`
- 감사 패턴 참고: `app/orchestrator/` (read-only, no-LLM)

> 이 문서는 **설계 전용**이다. Phase 0(문서/스키마)·Phase 1(mock artifact) 외의 구현은 위 Open Questions
> 확정과 사용자 승인 후에만 착수한다. 어떤 단계도 broker/order API 호출이나 credential 접근을 포함하지 않는다.
