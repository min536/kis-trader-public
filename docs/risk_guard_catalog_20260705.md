# 리스크 가드 카탈로그 (E13 F-24, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E13 백로그
> F-24** 산출물. 실재 리스크 가드가 **코드에만 존재하고 인벤토리 문서가 없어**(블루프린트 §16.1
> F-24) 갭 분석의 기준선이 없었다. 본 문서가 그 기준 카탈로그다. **read-only 실측**(app/ grep,
> 코드 변경 없음). 이 카탈로그는 **E15(BP-14 S2 graceful halt 설계)의 입력**으로 인용된다.

---

## §1. 실재 가드 전수 (실측 앵커)

| 가드 | 무엇을 막나 | 트리거측 | 소스 앵커 | 실패시 동작 |
|------|-------------|:--------:|-----------|-------------|
| **주문로그 무결성** | 주문 로그 손상·불일치 시 신규 주문 | BUY+SELL | `app/risk/guards.py::evaluate_order_log_integrity`(:195) | block(fail-closed) |
| **일일 주문수 상한** | 하루 주문 건수 초과 | BUY+SELL | `guards.py::evaluate_daily_order_limit`(:222) | block |
| **일일 명목 상한** | 하루 누적 명목금액 초과 | BUY+SELL | `guards.py::evaluate_daily_notional_limit`(:260) | block |
| **PnL 브레이크 — WARNING** | (관찰) 일중 손실 경고선 | 표시 | `app/risk/pnl_brake.py::build_daily_pnl_state`(warning_pct) | 경고 로그 |
| **PnL 브레이크 — BUY_PAUSE** | 일중 손실 시 **신규 매수** | BUY only | `pnl_brake.py`(buy_pause_pct) + cooldown(`daily_pnl_cooldown_minutes`:209) | BUY pause(쿨다운 후 자동 해제) |
| **PnL 브레이크 — HARD_STOP** | 일중 손실 하드스톱 | BUY(신규) | `pnl_brake.py`(hard_stop_pct) | hard stop |
| **PnL 브레이크 교차검증** | cash-settlement 타이밍 오탐(가짜 손실) | — | `pnl_brake.py:120+` operating-equity 대조 | 오탐 시 브레이크 억제(NORMAL 복귀) |
| **수동 매수 정지** | 운영자 수동 BUY 정지 | BUY only | `pnl_brake.py::is_manual_buy_pause_override_active`(:51) 플래그 파일 | BUY_PAUSE 강제(관찰 유지) |
| **계좌 노출% 상한** | 계좌 대비 과노출 매수 | BUY | `settings.buy_max_account_exposure_pct` + position_sizing | 수량 축소/차단 |
| **레짐 스케일링** | RISK_OFF 시 명목·노출·수량·재진입 축소 | BUY | `app/risk/regime.py::build_regime_state`(:15, exposure×multiplier·cooldown×2/4) | 축소 |
| **재진입 쿨다운** | 동일 심볼 과빈도 재매수 | BUY | `regime.py` effective_rebuy_cooldown(:101) + `app/strategy/reentry.py` | 쿨다운 차단 |
| **중복 서명/주문 방지** | 사이클 간 중복 주문 | BUY+SELL | `order_gate` 중복 방지(사이클 간) + account_signature(`pnl_brake.py:232`) | 중복 제거 |
| **stale 시세 가드** | 오래된 스캔 워커 시세로 주문 | BUY 스캔 | `app/pipeline/buy_lane.py`(:34 stale, :126 mark_stale) + `lane_scheduler.py:317-319` | 스캔 스킵/워커 재기동 |

**공통 성질**: 대부분 **BUY측 편중**. SELL을 막는 것은 무결성·일일주문수·일일명목 3개뿐이고,
손실·노출·레짐·쿨다운 계열은 전부 **신규 매수 억제**다. 이게 F-08/BP-14 S2의 문제 정의로 직결된다.

---

## §2. 갭 (F-24가 지목한 부재 가드)

| 갭 | 현재 상태 | 왜 갭인가 |
|----|-----------|-----------|
| **심볼 집중도 halt** | 노출%는 계좌 총량 기준. 단일 심볼/섹터 집중 상한은 없음 | 한 종목에 노출이 몰려도 계좌 노출%가 한도 내면 통과 |
| **연속손실 halt** | 일중 손실률(PnL 브레이크)은 있으나 **연속 N회 손실 트레이드** 기반 정지는 없음 | 손실률이 임계 미만이어도 연패가 누적되면 무개입 |
| **SELL 포함 전면 정지(graceful halt)** | **부재** — 신규 주문 전면 중단(BUY+SELL) + 모니터링 유지 모드 없음 | 긴급 시 pkill(프로세스 킬)이 유일. F-08/BP-14 S2 |
| **flatten-only 모드** | **부재** — 신규 진입 금지·기존 포지션 청산만 허용하는 모드 없음 | 위험 회피 청산을 자동화할 수단 없음 |

---

## §3. E15(BP-14 S2 graceful halt) 설계 입력

본 카탈로그가 E15에 넘기는 사실:

1. **재사용할 패턴**: `is_manual_buy_pause_override_active`(플래그 파일 방식)가 이미 "관찰 유지 +
   BUY 정지"를 구현. E15의 graceful halt는 이 패턴을 **SELL 포함 신규 주문 전면**으로 확장하는
   형태가 자연스럽다(operator_decision_briefs §BP-14 S2와 일치).
2. **fail-safe 방향**: 정지 신호 **읽기 실패 = 차단측**(신규 주문 막음)이 안전. 무결성 가드가
   이미 fail-closed 선례.
3. **flatten-only는 별개 축**: "신규 진입 차단"과 "청산 허용"을 분리해야 함 — graceful halt(둘 다
   차단)와 flatten-only(진입만 차단)는 다른 모드. §1 표의 BUY-편중이 이 분리의 근거.
4. **trading-critical 경계**: E15 구현은 `app/risk/`·주문 경로 접촉 → `/risk-assessment` 필수
   (master_completion_plan E15 게이트). 본 카탈로그는 설계 입력(read-only)까지만.

## §4. 검증 (Green 재현)

- §1 12행이 실재 가드 전수 — F-24가 예시한 7종(일일 주문수·명목·무결성·PnL브레이크·노출%·
  쿨다운·stale) 모두 앵커와 함께 포함.
- §2가 갭(심볼 집중도·연속손실 halt) + flatten/graceful halt 부재를 표로 명기.
- §3이 E15 설계 입력으로 명시 인용됨 → master_completion_plan §4 E13-F24 Green 충족.
