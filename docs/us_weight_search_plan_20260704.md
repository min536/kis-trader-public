# BP-1 US weight-search — 위임 계획서 + S0 데이터 원천 실사 (2026-07-04)

정본: master_blueprint_20260704.md (not included in this source snapshot) §4. 프로파일: **신규 모듈 TDD**
(단 S0는 상위 직접 — 리스크 전부가 데이터 원천에 있음). 트랙: research-only(`/risk-assessment` 불요).

## ✅ 운영자 결정 (2026-07-04) — 경로 A 채택, 경로 B는 향후

- **지금 채택: 경로 A (regime-conditioning)** — `app/research/us_regime/` 자산(US 일봉→레짐
  클러스터링 + `find_similar_us_days`/`conditional_kr_days`로 US레짐↔KR일 매핑) 위에 weight
  search를 얹는다. 네이티브 US 레코드 없이 **기존 KR gate2 레코드를 US 레짐으로 조건화**한다.
  → S0 RED 해제(A 한정): S1은 "US레짐 조건부 KR 탐색" 축으로 **재설계 후 착수 가능**.
- **향후 추가: 경로 B (네이티브 US 레코드)** — **Toss API로 장전에 당일 미장 분봉을 수집**하여
  US 관측당 15 조건 스코어를 산출하는 파이프라인을 나중에 추가한다. B가 확보되면 A와 병행/대체
  가능. **B의 데이터 수집(장전 Toss 미장 분봉)은 운영자 게이트** — 수집 스케줄·크레덴셜은 운영자.
  ⚠️ 수집 경로가 라이브 시세 크레덴셜을 건드리면 `BUY_SCAN_QUOTE_KIS_ENV` 하자 경계를 준수해야
  하며, weight search CLI는 그 env가 설정되면 실행을 거부한다(자체 가드).
- 아래 §S0 결론(RED)은 **경로 미결정 시점의 기록**이며, 위 결정으로 A 축은 해제됨 — 이력 보존용.

## ⛔ S0 결론: RED 게이트 — 현 데이터로 "네이티브 US 레코드" 설계는 불가. 운영자 결정 필요.

블루프린트 §4는 US 자체 `EvaluationRecord`를 만들어 기존 walk-forward 하니스에 태우는 설계다.
S0 실사 결과, 그 전제(**US 관측당 15 gate2 조건 스코어 + forward_return_bps**)가 현 데이터에서
성립하지 않는다. 임의 진행(S1 착수) 금지 — 아래 세 발견을 운영자에게 보고하고 방향을 확정받는다.

### 실사 근거 (이 세션 실측)

1. **EvaluationRecord는 15 조건 스코어를 요구** — `app/gate2/schema.py::CONDITION_SCORE_NAMES`
   (15개): `pullback_strength_score`, `rebound_strength_score`, `controlled_down_quality_score`,
   `gap_down_quality_score`, `range_recovery_score`, `trend_alignment_score`, `macd_momentum_score`,
   `volume_rank_score`, `volume_power_score`, `cost_quality_score`, `mean_reversion_score`,
   `price_velocity_score`, `liquidity_score`, `volatility_risk_score`, `portfolio_diversification_score`.
   이들은 **분봉 미시구조 파생**(거래량 랭크·가격 속도·유동성 등) — 일봉으로 계산 불가.
2. **US는 일봉 원천만 존재** — `app/research/us_regime/ingest.py`는 US 지수 일봉(OHLC+volume)을
   parquet로 적재. KR의 분봉 replay(`app/research/replay/record_runner.py`,
   `forward_return_bps=(exit/entry-1)*1e4 - cost`)에 대응하는 US 분봉 인프라가 **없음**.
3. **원천 데이터 미적재 + US 레코드 0건** — `data/us_daily/` parquet **0개**(운영자 게이트 O7-2
   "US CSV 적재" 미실행), `data|results`에서 US record/eval 파일 glob **0건**.
4. **기존 US regime 접근은 네이티브가 아님** — `app/research/us_regime/`는 15 조건 스코어를
   만들지 않고, US 일봉으로 regime 클러스터링(k-means+silhouette) 후 `find_similar_us_days`/
   `conditional_kr_days`로 **US 레짐↔KR일을 매핑**한다(KR 레코드를 US 레짐으로 조건화). 즉
   "US 레짐으로 KR 탐색을 조건화"이지 "네이티브 US gate2 레코드 생산"이 아니다.

### 운영자 결정 사항 (셋 중 택 — 에이전트 임의 진행 금지)

| 경로 | 내용 | 비용/리스크 | 에이전트 후속 |
|------|------|-----------|--------------|
| **A. regime-conditioning 채택(재설계)** | 네이티브 US 레코드 포기, `us_regime`의 US레짐→KR일 매핑 위에 weight search | 저비용(자산 존재), 단 "US 자체 신호"가 아닌 "US레짐 조건부 KR" | 계획서를 이 축으로 재작성 후 S1~ |
| **B. US 분봉 수집 투자** | US 분봉 원천 확보→15 조건 스코어 산출 파이프라인 신설 | 고비용·장기, **데이터 수집은 운영자 게이트** | 수집 스펙 브리프만, 코드는 데이터 확보 후 |
| **C. 현행 artifact 유지** | `score_v2_us_w0.json`(rank-neutral) 그대로, BP-1 보류 | 무변경, threshold-60 무력 지속 | BP-1 종료·§17 잔여(BP-11)도 재검토 |

**RED 규약 준수**: 블루프린트 §4 RED ①("US 레코드 원천이 없거나 일봉 프록시뿐 → 프록시 수용
여부는 운영자 결정, 임의 진행 금지")에 정확히 해당. 홀드아웃(KR 2025-04-01~06-16) 대응 US
구간 확정은 경로 확정 후로 이연.

## 슬라이스(경로 A/B 확정 시 유효) — 현재는 **BLOCKED**

블루프린트 §4 S1~S6(us_records.py 빌더 → 중립충전 처리 → CLI → leak 테스트 → 실데이터 → OOS)는
**운영자가 A 또는 B를 택한 뒤에만** 착수. S0가 GREEN(원천 확정)이 되기 전 S1 스폰 금지.

## 검증 (§3 인스턴스, 경로 확정 후)

①불요 → ②본 계획서 재작성(택한 경로) → ③S1~S4 opus, S0는 상위 직접(완료) →
④`completion_audit.py --hygiene app/research/gate2/us_records.py --run-suite` →
⑤3-스켑틱 math/robustness/completeness + `json.dumps(allow_nan=False)` 적대 테스트(inf/NaN 게이트).
