---
Purpose: live autotuner 제안 시스템의 1차(first-cut) 파라미터 whitelist 및 보수적 bounds 정의
Read when: 어떤 런타임 파라미터를 자동튜너가 제안할 수 있는지, 어떤 범위/단계에서 적격인지 확정할 때
Do not use for: 실제 코드 구현(이 문서는 안전 계약서이지 구현이 아니며, 그 자체로 라이브 변경을 켜지 않는다)
Status: DESIGN ONLY — first-cut safety contract. Does not enable any live change.
Source design doc: docs/live_autotuner_plan.md
---

# Live Autotuner — Whitelist & Bounds (1차 안)

> 상위 설계: [`docs/live_autotuner_plan.md`](OLD/live_autotuner_plan.md) §4(파라미터 분류)·§6(안전 envelope)의
> 구체화. 이 문서는 **"무엇을, 어느 범위까지, 어느 Phase에서"** 제안 가능한지를 못 박는 1차 안전 계약이다.

---

## 1. Purpose

- 이 문서는 미래의 제안 시스템(autotuner)이 **제안할 수 있는 후보 파라미터 whitelist**와,
  각 파라미터의 **보수적 bounds(하드 한계)** 및 **적격 Phase**를 정의한다.
- 이 문서는 **구현이 아니다.** 그 자체로는 어떤 라이브 변경도 가능하게 만들지 않는다.
- bounds·tier·Phase 값은 **1차 안(first-cut)** 이며, §8 Open Questions가 닫히기 전까지 확정값이 아니다.

---

## 2. Safety Principles

- **Whitelist-only** — 이 문서의 목록에 없는 파라미터는 어떤 경로로도 제안·변경 금지.
- **Bounds = 하드 한계** — 제안값이 min/max를 벗어나면 *무조건 거부*. 권고가 아니라 강제.
- **백테스트 출력이 안전 bounds를 덮을 수 없다** — 백테스트가 아무리 좋은 수치를 내도 bounds·tier·승인 규칙을 우회 못 함.
- **고위험일수록 강한 증거 + 사람 승인** — Tier가 높을수록 더 많은 증거와 명시적 승인을 요구.
- **동시 활성 override는 1개** — 나중에라도 active override bundle은 항상 단 하나.
- **모든 override는 TTL/만료 + baseline 롤백** — 만료 시 자동으로 baseline settings로 복귀.

---

## 3. Parameter Risk Tiers

> 확정 근거: 파라미터명/타입/기본값은 `app/auth/settings.py`(읽기 전용)에서 확인. 일부 항목은 base 설정이
> 없고 runtime seam(`app/runtime/session_loop.py`의 `effective_*`) 또는 다른 모듈에 존재 → 해당 기본값은 TBD.

### Tier A — 저위험 / Phase 3 최초 후보
스캔 효율·탐색 폭·타이밍에만 작용하고 주문 크기/노출/체결에 *직접* 작용하지 않는 값.
- `scan_symbols_max_per_cycle`
- `buy_scan_deep_eval_limit`
- `buy_scan_shallow_top_k`
- `buy_scan_interval_seconds` (스캔 주기 — 보수적 범위 내)
- `sell_check_interval_seconds` (매도 점검 주기 — 보수적 범위 내)
- `sell_watch_max_holdings_per_tick` (tick당 매도 감시 보유 수 — base 기본값 TBD)

### Tier B — 중위험 / 이후 승인 후보
요청 압력(request pressure)이나 매도 cadence/긴급도를 *유의미하게* 바꿀 수 있는 값.
- `rebuy_cooldown_minutes`
- `same_symbol_max_buys_per_day`
- `math_multiplier` — *매도 cadence/긴급도에 유의미하게 작용할 경우에 한해.* (settings 기본값 TBD, §8 참조)
- 그 외 요청 압력을 실질적으로 키울 수 있는 cadence 파라미터 일반

### Tier C — 고위험 / 강한 증거 전까지 차단
주문 크기·계좌 노출·일일 주문 수에 직접 작용 → **trading-critical surface.**
- `buy_max_account_exposure_pct`
- `buy_max_budget_per_trade_krw`
- `buy_max_qty_per_trade`
- `buy_daily_max_order_submissions`

### Tier D — autotuner 영구 금지 (Forbidden)
어떤 Phase에서도, 어떤 증거로도 제안·변경 불가.
- credentials (API 키/시크릿/비밀번호)
- account identifiers (계좌번호/계좌 식별자)
- broker base URLs (`KIS_BASE_URL` 등)
- token paths / 토큰 캐시 경로
- KIS_ENV / mock↔live 전환 (paper/live 제어 surface)
- order side 강제 (매수/매도 side를 강제 지정하는 모든 값)
- risk guard를 끄거나 우회하는 모든 값 (`app/risk`)
- order guard를 끄거나 우회하는 모든 값 (`app/execution`)
- reconciliation / runtime-state 신뢰성 검사 / 보안 하드닝을 비활성화하는 모든 값
- session lock(`acquire_app_main_lock`) 관련 모든 값

---

## 4. Proposed Bounds Table (1차 안)

> **보수성 원칙:** 정확한 기본값을 안전하게 확인하지 못하면 추측 대신 `TBD`. min/max는 확인된
> 운영 가드레일(아래 ★)을 절대 침범하지 않도록 잡았다. "max step"은 1회 제안에서 허용하는 최대 변경폭.
>
> ★ 확인된 하드 가드레일 (`app/auth/settings.py` startup sanity, 읽기 전용 확인):
> - `buy_scan_interval_seconds >= 150` (line ~429)
> - `scan_symbols_max_per_cycle <= 40` (line ~434)
> autotuner bounds는 이 가드레일보다 **더 보수적**이어야 하며, 절대 완화하지 않는다.

| parameter | type | default* | min | max | max step/proposal | phase | tier | rationale | notes / unknowns |
|-----------|------|----------|-----|-----|-------------------|-------|------|-----------|------------------|
| `scan_symbols_max_per_cycle` | int | 30 | 15 | 40 | ±5 | 3 | A | 스캔 폭만 조절, 주문 미직결. max=40은 확인된 startup 상한과 일치(완화 아님) | 상한 40은 하드 가드레일; autotuner는 그 안에서만 |
| `buy_scan_deep_eval_limit` | int | 4 | 2 | 8 | ±1 | 3 | A | 심층평가 한도. 너무 크면 사이클 지연→간접 요청압력 | shallow_top_k 이하 제약(§5) |
| `buy_scan_shallow_top_k` | int | 10 | 5 | 20 | ±2 | 3 | A | 얕은평가 상위 K. 탐색 폭 | scan_max 이하 제약(§5) |
| `buy_scan_interval_seconds` | int | 240 | 180 | 600 | ±30 | 3 | A | 스캔 주기. min=180은 가드레일 150보다 보수적 | 절대 150 미만 제안 불가 |
| `sell_check_interval_seconds` | int | 35 | 30 | 120 | ±10 | 3 | A | 매도 점검 주기. 너무 짧으면 레이트리밋 압박 | 느리게 변경 시 §5 sell-cap 제약 동반 |
| `sell_watch_max_holdings_per_tick` | int | TBD | TBD | TBD | ±1 | 3 | A | tick당 매도 감시 수 | **base 기본값 미확인** — settings엔 degraded_mode_*/seam effective_* 만 존재. 확정 전 제안 비활성 |
| `rebuy_cooldown_minutes` | int | 30 | 15 | 240 | ±15 | 4 | B | 재매수 쿨다운. 줄이면 매수 빈도↑(요청압력↑) | 감소(완화)는 사람 승인 필수(§5) |
| `same_symbol_max_buys_per_day` | int | 3 | 1 | 5 | ±1 | 4 | B | 동일심볼 일일 매수 한도. 늘리면 집중 위험↑ | 증가는 노출 증가로 간주(§5) |
| `math_multiplier` | float | TBD | TBD | TBD | TBD | 4 | B | 사이징/매도 긴급도 배수 (효과가 넓음) | **settings 미존재**(`app/math_models/sizing.py`). 매도 cadence 영향 확정 전 제안 비활성 |
| `buy_max_account_exposure_pct` | float | 10 | TBD | TBD | TBD | (blocked→4+) | C | 계좌 노출 한도(%). 완화 시 직접적 손실 확대 위험 | 강한 증거 전 차단. 완화 방향 bounds는 보수 확정 필요 |
| `buy_max_budget_per_trade_krw` | int | 1000000 | TBD | TBD | TBD | (blocked→4+) | C | 거래당 예산. 주문 크기 직결 | 강한 증거 전 차단 |
| `buy_max_qty_per_trade` | int | 10 | TBD | TBD | TBD | (blocked→4+) | C | 거래당 수량. 주문 크기 직결 | 강한 증거 전 차단 |
| `buy_daily_max_order_submissions` | int | 30 | TBD | TBD | TBD | (blocked→4+) | C | 일일 주문 제출 한도. 빈도/노출 직결 | 강한 증거 전 차단 |

\* default = `app/auth/settings.py`의 `os.getenv(...)` 기본값(읽기 전용 확인). `TBD`는 안전하게 확인 못한 값.

> **Tier C bounds는 의도적으로 TBD로 남긴다.** 고위험 사이징 파라미터의 min/max를 이 1차 안에서 임의로
> 적으면 그 숫자가 "승인된 것처럼" 굳을 위험이 있다. Tier C는 §8 Open Questions 확정 + risk-analyst 검토
> 후에만 bounds를 채운다. 그 전까지 **차단(blocked)** 상태가 안전한 기본값이다.

---

## 5. Cross-Parameter Constraints

번들 단위로 검사한다. 하나라도 위반하면 번들 전체 거부.

1. **`buy_scan_deep_eval_limit ≤ buy_scan_shallow_top_k`** — 심층평가 한도가 얕은평가 상위 K를 넘을 수 없음.
2. **`buy_scan_shallow_top_k ≤ scan_symbols_max_per_cycle`** — 상위 K가 사이클 스캔 폭을 넘을 수 없음.
3. **요청압력 결합 금지** — request-pressure 관련 파라미터(스캔/매도 interval 단축, scan_max/deep/shallow 증가)가
   결합되어 *현재 안전 운영 가정보다 더 공격적인 프로필*을 만들 수 없다. (특히 interval은 확인된 하드
   가드레일 `buy_scan_interval_seconds ≥ 150` 아래로 절대 못 감.)
4. **초기 Phase 분리** — Phase 3~4 초기에는 **고위험 사이징(Tier C) 파라미터를 스캔 공격성(Tier A) 파라미터와
   같은 번들에서 함께 변경할 수 없다.** (한 번에 한 종류의 위험만.)
5. **매도 느림 + 감시 축소 동시 금지** — `sell_check_interval_seconds`를 느리게(증가) 하면서
   `sell_watch_max_holdings_per_tick`를 동시에 줄이는 것은 **명시적 사람 승인 없이는 금지**(매도 누락 위험).
6. **주문 빈도/노출 증가는 사람 승인 필수** — 주문 빈도나 계좌 노출을 늘리는 *모든* 변경
   (쿨다운 감소, 일일주문/심볼한도 증가, 예산/수량/노출% 증가)은 명시적 사람 승인을 요구한다.

---

## 6. Proposal Bundle Rules

미래 제안 artifact가 만족해야 할 규칙(스키마는 Phase 0에서 확정).

- **max one active bundle** — 동시 활성 override 번들은 1개. 새 승인은 이전 것을 명시적으로 대체.
- **required fields (미래 artifact 필수 필드):**
  - `ttl` / `expires_at` — **TTL 필수.** 만료 시 자동 baseline 복귀.
  - `reason` — **사유 필수.** 왜 이 변경을 제안하는지.
  - `evidence_refs` — **증거 참조 필수.** backtest id / shadow 세션 / postrun audit 참조.
  - `rollback_baseline` — **baseline 필수.** 되돌릴 기준 settings 스냅샷 식별자.
  - `approved_by` — **라이브 적용 전 필수.** 승인자. 비어 있으면 라이브 적용 불가.
  - `generated_by` — 생성 주체 기록 (어떤 generator/agent/버전이 만들었나).
  - `mode` — `mock` | `shadow` | `approved_low_risk` | `approved_high_risk` 중 하나.

---

## 7. Evidence Requirements by Tier

| tier | 요구 증거 |
|------|-----------|
| **A** | 설계 리뷰 + mock/shadow 증거 |
| **B** | 복수 shadow 세션 + risk regression 없음 확인 |
| **C** | 명시적 수동 승인 + 복수 세션 + 라이브 로그 증거 + (권장) postrun audit 검토 |
| **D** | 영구 부적격 — 어떤 증거로도 불가 |

---

## 8. Open Questions

1. **정확한 현재 기본값** — `sell_watch_max_holdings_per_tick`(base)·`math_multiplier`의 실제 기본값과
   런타임 결정 방식. Tier C 파라미터의 안전한 min/max.
2. **bounds의 profile-specific 여부** — normal/midday/degraded 등 프로필별로 bounds를 달리할지.
3. **시장 regime에 따른 bounds 강화** — 변동성/이벤트 구간에서 bounds를 자동으로 더 좁힐지.
4. **baseline 스냅샷 저장 위치** — 롤백 기준 settings를 어디에 둘지(`_workspace/` vs `config/` vs artifact).
5. **승인 채널** — 나중에 Slack 승인을 허용할지, 아니면 로컬 파일 승인만 허용할지.

---

## 9. Implementation Note

- **이 문서는 코드를 추가하지 않는다.** 안전 계약 정의일 뿐이다.
- 향후 어떤 구현이든 **런타임이 override 제안을 읽기 시작하기 전에 테스트를 먼저 추가**해야 한다(TDD).
  최소한 whitelist 강제, bounds 강제, cross-parameter 제약, TTL/롤백, "Tier D 거부"에 대한 실패 테스트가
  Green 이전에 존재해야 한다.
- Tier C bounds(현재 TBD)는 §8 확정 + risk-analyst 검토 후에만 채운다. 그 전까지 차단이 안전 기본값이다.
