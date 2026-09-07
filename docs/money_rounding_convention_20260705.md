# 금액 라운딩 규약 (F-20, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E13
> 백로그 F-20** 산출물. 금액 연산이 float 전면(Decimal 1파일)인데 **라운딩 규약이 문서화되지
> 않아** 기준이 없었다(블루프린트 §16.1 F-20). 본 문서가 규약을 성문화하고, 경계 테스트
> (`tests/test_money_rounding_convention.py`)가 현행 동작을 **고정**한다. **전면 Decimal 전환은
> 비권고** — KRW 정수 시장은 위험이 낮고, US 소수(BP-1/BP-5)만 규약을 명시하면 된다.

---

## §1. 실측된 현행 규약 (코드 근거)

| 대상 | 규약 | 근거 |
|------|------|------|
| **KRW 수수료/세금/슬리피지** | `int(round(notional * bps / 10_000))` — **half-to-even(banker's)** 후 정수화 | `app/core/costs.py:39` `_calc_bps_amount` |
| **KRW net PnL** | `gross_krw - int(cost)` — 정수 차감 | `costs.py:133` |
| **퍼센트/bps 리포트값** | `round(x, 2)` — 소수 2자리 half-to-even | `costs.py:110,153-156,195` |
| **US 소수 가격** | `Decimal` 사용 | `app/overseas_stock/market_data.py`(유일 Decimal 파일) |

Python `round()`는 **round-half-to-even**(banker's)이다: `round(2.5)=2`, `round(7.5)=8`,
`round(0.5)=0`. `int(round(x))`는 이미 정수값인 float를 절사(음수 없음 — notional≥0 clamp).

## §2. 규약 (성문화)

1. **KRW 금액은 정수.** bps 기반 금액은 `int(round(notional * bps / 10_000))` — half-to-even.
   단일 거래의 sub-won 반올림은 무의미하나, **누적 bps에서 편향 방지**를 위해 half-to-even 유지
   (half-up로 바꾸면 상방 편향 — 금지).
2. **퍼센트/bps 표시값은 `round(x, 2)`** — 표시·비교용, 계산 중간값은 반올림하지 않는다.
3. **US 소수 가격은 Decimal 또는 명시적 tick 라운딩.** float 누적 금지 — BP-1/BP-5에서 US
   가격이 왕복하면 **최종 KRW 환산 직전 1회만** 라운딩(중간 라운딩 누적 금지).
4. **환산 순서 고정**: 가격(소수) → 명목(곱) → 라운딩(1회) → 정수 KRW. 라운딩을 여러 번 하면
   오차가 축적된다(F-20 핵심 위험).
5. **전면 Decimal 전환 비권고** — 현행 float+`int(round())`로 KRW는 안전. US 경로만 위 §3/§4 준수.

## §3. 경계 테스트 (규약 고정)

`tests/test_money_rounding_convention.py`가 현행 banker's rounding을 **경계값에서 고정**한다.
규약이 바뀌면(예: half-up 전환) 이 테스트가 실패해 잡는다. 고정 케이스:

- `calc_buy_fee(10_000, 2.5) == 2` (2.5 → even 2, 하방)
- `calc_buy_fee(10_000, 7.5) == 8` (7.5 → even 8, 상방)
- `calc_buy_fee(10_000, 0.5) == 0` (0.5 → even 0)
- `calc_buy_fee(10_000, 1.5) == 2` (1.5 → even 2)

## §4. 검증 (Green 재현)

- §1 규약 = `costs.py` 실측 라인 인용. Decimal 파일 = grep 1건 재현.
- 경계 테스트 green = 현행 half-to-even 고정(규약 이탈 감지선).
- master_completion_plan §4 F-20 Green(규약 문서 + 경계 테스트 green) 충족. **동작 무변경**
  (특성화 테스트 — 신규 라운딩 코드 없음).
