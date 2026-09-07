# BP-10 fill-side slippage — 설계 + S1 순수계산 (E7, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E7** /
> 블루프린트 §11 BP-10. 현행 submit-side 슬리피지 측정의 **반쪽 해소**. **트랙**: read-only
> reporting leaf — trading-critical **아님**. 브로커 체결조회 호출은 **운영자 게이트**(EOD
> reconciliation); 본 항목의 S1은 그 데이터가 주어졌을 때의 **순수 계산**만 담당.

---

## §0. 문제 정의 (실측)

`app/reporting/slippage_measure.py`는 **submit-side만** 측정한다: `(quote_at_submit -
reference_price_krw)/reference_price_krw` bps. 그런데 docstring이 명시하듯 **오늘 두 값은 동일**
(결정~제출 사이 재견적 없음, order-cash ack에 체결가 없음) → 실측 슬리피지가 **fill 소스가 없어
0**이다. 인프라는 맞지만 숫자가 안 나온다.

**fill-side**는 나머지 반쪽: **실체결가(fill_price) vs 기준가**의 갭. 실체결가는 **EOD
reconciliation**(장마감 후 브로커 체결조회)에서만 온다 — 그 호출은 운영자. 본 설계는 그
체결가가 레코드에 실렸을 때의 계산·집계·배선을 준비한다.

## §1. 슬라이스 (Red→Green)

| 슬라이스 | 내용 | Red 지점 | Green 지점 |
|----------|------|----------|-----------|
| **S1** (본 항목) | 순수 계산 모듈 `app/reporting/fill_slippage.py` | 합성 체결/제출 쌍 fixture 테스트가 모듈 부재로 실패 | `_slippage_bps` 동일 부호규약(adverse=+)으로 fill-side 집계 green + 결측/불량 fill degrade-safe green + 기존 SlippageSideSummary 재사용 |
| S2 (9월) | EOD 배선 (dormant-hook) | flag/hook off 시 기존 EOD 산출물 무변경 green | 테스트된 capability 커밋 + 라이브 호출은 운영자 1-liner (W6/W7 선례) |

## §2. S1 설계 (순수 계산)

- **모듈**: `app/reporting/fill_slippage.py`. `build_fill_slippage_summary(records, *, report_date,
  policy_buy_bps, policy_sell_bps) -> SlippageReportSummary`.
- **입력 레코드**: submit-side와 동일한 order-log 레코드지만 **`fill_price_krw`**(실체결가, EOD
  reconciliation이 부착)를 읽는다. reference는 동일 `reference_price_krw`(W2 필드).
- **부호 규약 재사용**: submit-side와 **완전히 같은** `(exec - ref)/ref*10000`, adverse=positive
  (buy는 기준가 위 체결이 +, sell은 기준가 아래 체결이 +). `-0.0 → 0.0` 정규화 포함.
- **degrade-safe**: `fill_price_krw` 결측/불량(≤0/비유한/비숫자)은 raise 없이 `missing_reference_count`로
  집계(submit-side `_coerce_price` 패턴 재사용). 비-Mapping 레코드 스킵.
- **재사용**: `SlippageSideSummary`/`SlippageReportSummary`/`format_slippage_report`
  (`slippage_report.py`) 그대로 — fill-side는 같은 리포트 형태에 다른 소스.
- **경계**: I/O·브로커 호출 0. bounded order-log read는 (S2의) CLI 소관. 런타임 모듈 import 0.

## §3. 게이트 판정

- **GREEN(S1)**: 합성 fixture 수계산 일치 + degrade-safe + 전체 스위트 무회귀 + 위생 grep(런타임
  import 신설 0).
- **RED**: ① 체결조회 API 호출이 에이전트 경로(S1)에 들어오는 설계가 되는 순간 → 설계 수정
  (운영자 게이트 침범). ② fill_price 부호 규약이 submit-side와 어긋나면 → 규약 통일 강제.

## §4. 위임·검증

①불요(reporting leaf) → ②프로파일 "신규 모듈 TDD" (본 문서) → ③S1 순수함수 test-first →
④completion-audit `--hygiene app/reporting/fill_slippage.py --run-suite` → ⑤3-스켑틱
math(bps 산술)/robustness(불량 fill)/consumer(리포트 소비 가능성).
