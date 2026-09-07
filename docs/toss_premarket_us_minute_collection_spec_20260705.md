# 경로 B 수집 스펙 — Toss API 장전 당일 미장 분봉 (E4, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E4** 산출물.
> BP-1 US weight-search의 **경로 B**(네이티브 US `EvaluationRecord` 생산) 데이터 수집 파이프라인
> 설계 스펙 **only**. 코드·수집 실행·스케줄은 이 문서에 없다 — **수집 실행/스케줄은 운영자
> 게이트**(master_completion_plan T2 **OB**). 경로 A(레짐 조건화)는 [us_weight_search_plan_20260704.md](us_weight_search_plan_20260704.md)
> 소관이며 본 스펙과 독립적으로 진행된다.
>
> **왜 경로 B인가**: BP-1 S0가 "네이티브 US 레코드 설계 불가"로 RED였던 근본 원인은 **15조건
> 스코어가 전부 분봉 마이크로구조 파생인데 US 분봉 원천이 없어서**였다(일봉 프록시로는 산출
> 불가). 경로 B는 그 데이터 공백을 Toss API 장전 당일 미장 분봉 수집으로 메운다. 운영자가
> 2026-07-05에 "경로 A 우선, B는 향후"로 결정했다 — 본 스펙은 B 착수 시점의 착수 재료다.

---

## §0. 스코프 경계 (불변)

- **크레덴셜 경계**: Toss 수집기는 **KIS와 무관한 별도 자격증명**을 쓴다. `BUY_SCAN_QUOTE_KIS_ENV`
  (live quote-token 발급 게이트)를 **읽지도 설정하지도 않는다** — gate2 CLI 3종이 이 env
  unset을 강제하는 것과 동일 정신. Toss 수집 경로는 KIS live 주문/시세 경로와 물리적으로 분리.
- **런타임 무간섭**: 수집기는 오프라인 배치다. `app/main.py`·파이프라인·주문 경로를 import
  하지 않는다(위생 grep 경계). 산출물은 parquet 파일뿐.
- **읽기 전용 산출**: 수집은 외부 데이터를 읽어 로컬 parquet로 적재. 어떤 주문·브로커 호출도 없음.

---

## §1. 15조건 스코어 산출 가능성 매핑 (실측 근거 — 이 스펙의 핵심)

`app/gate2/adapter.py::condition_scores_from_v1`(:13)과 `app/gate2/schema.py`
`CONDITION_SCORE_NAMES`(:11-26)를 실측한 결과. **분봉 OHLCV 단일 심볼**만으로 산출 가능한지 분류:

| # | 스코어 | v1 소스(adapter) | 단일심볼 분봉으로 산출? | 추가 필요 원천 |
|---|--------|-----------------|:----------------------:|----------------|
| 1 | pullback_strength | intraday_pullback_strength | ✅ | — (당일 분봉 경로) |
| 2 | rebound_strength | rebound_from_low_strength | ✅ | — |
| 3 | controlled_down_quality | controlled_down_strength | ✅ | — |
| 4 | gap_down_quality | gap_down_open_strength | ⚠️ | **전일 종가**(일봉 1행) 필요 |
| 5 | range_recovery | range_recovery_strength | ✅ | — |
| 6 | trend_alignment | trend_alignment(cap-norm) | ⚠️ | **다일 MA 히스토리**(일봉 N행) |
| 7 | macd_momentum | macd_momentum(cap-norm) | ⚠️ | 충분한 분봉 or 일봉 EMA 워밍업 |
| 8 | **volume_rank** | live_volume_rank_strength | ❌ | **유니버스 횡단면**(전 심볼 동시 분봉) |
| 9 | **volume_power** | live_volume_power_rank_strength | ❌ | **유니버스 횡단면** |
| 10 | **cost_quality** | cost_quality(cap-norm) | ❌ | **스프레드/비용 원천**(분봉 OHLCV엔 L1 호가 없음) |
| 11 | mean_reversion | mean_reversion_bonus(cap-norm) | ✅ | — |
| 12 | price_velocity | velocity_bonus/penalty(bipolar) | ✅ | — |
| 13 | **liquidity** | **없음(항상 None)** | ❌ | **feature stage에서 신규 정의 필요**(adapter :90 주석) |
| 14 | volatility_risk | variance_increase_penalty(inverse) | ✅ | — |
| 15 | **portfolio_diversification** | diversification_bonus/corr_penalty | ❌ | **포트폴리오 상태**(리플레이 보유·상관) |

### 결론 (경로 B가 반드시 수집·정의해야 하는 것)

1. **분봉을 후보뿐 아니라 US 유니버스 전체로 수집**해야 한다 — #8/#9(volume_rank/power)가
   그 순간의 **횡단면 랭크**이기 때문. 단일 심볼 분봉만 모으면 두 스코어는 산출 불가.
2. **전일 종가 + 다일 MA용 일봉 히스토리**가 동반돼야 한다(#4/#6/#7) — 이건 **O7-2
   `ingest_us_daily`**가 이미 담당(경로 A와 공유 자산). 경로 B는 일봉 위에 분봉을 얹는다.
3. **cost_quality(#10)**: Toss 분봉은 OHLCV라 L1 호가 스프레드가 없다. 세 옵션 —
   (a) 비용 상수 가정(문서화), (b) 분봉 고저 스프레드 프록시, (c) 이 조건을 중립충전으로
   고정(경로 A가 US artifact에서 이미 하는 방식). **방법은 운영자 확정 대기**(임의 결정 금지).
4. **liquidity(#13)**: v1에 소스가 없어 항상 None. 분봉 turnover(가격×거래량)로 **신규 정의**를
   feature stage에서 도입해야 실효. 정의식은 별도 슬라이스(경로 B S-feature).
5. **portfolio_diversification(#15)**: 포트폴리오 상태 파생 → 수집 대상 아님. **리플레이
   시뮬레이션 시점**에 산출(경로 A/B 공통 replay 하니스 책임). 수집 스펙 밖.

---

## §2. 수집 대상 (스코프)

| 항목 | 스펙 |
|------|------|
| 심볼 집합 | **US 유니버스 전체**(횡단면 랭크 요건). 후보만 수집 금지. 유니버스 정의는 `config/` symbol master의 US 셋 — 실목록은 착수 시 실사 |
| 봉 간격 | 1분봉(OHLCV). gate2 국내 파이프라인(`build_backfill_parquet` D1)과 동일 입도 |
| 세션 범위 | **당일 미장(pre-market) + 정규장 초반** — 스코어가 "장전~장초" 신호 기반. 정확한 창(예: 04:00–10:00 ET)은 Toss 데이터 제공 범위 실사 후 확정 |
| 수집 시각 | 한국 시각 장전(운영자 스케줄) — 당일 미장 분봉을 **당일 아침**에. launchd 등록은 운영자(OB) |

---

## §3. 시간대 / DST 처리 (US 자산 최대 함정)

- **타임스탬프는 UTC 저장 + ET 파생 컬럼 별도**. naive `datetime.now()` 금지(F-21 교훈 —
  US 자산에서 자정·DST 경계가 커진다). `app/core/time_utils.py` 경로 사용.
- **DST 전환일**(3월 2주·11월 1주)에 ET 벽시계-UTC 오프셋이 -4/-5h로 바뀐다 → 파티션 키를
  **ET 거래일(trade_date_et)** 로 잡아야 미장 창이 흔들리지 않음. UTC로만 파티션하면 DST 경계에
  하루가 갈라진다.
- **홀드아웃 경계 정합**: 경로 A/기존 KR 홀드아웃(2025-04-01~06-16)과 1:1 대응되는 US ET
  구간을 S0에서 확정(leak 검사의 기준). 대응 불가 시 RED 보고(임의 진행 금지).

---

## §4. 저장 스키마 (분봉 parquet 파티션)

- **포맷**: parquet, gate2 flat-parquet 규약 준수 — 신규 로더 재작성 금지, **`app.tools.parquet_utils`
  단일 경로**로 진입([tools_parquet_ingest_guide_20260705.md](tools_parquet_ingest_guide_20260705.md) = E17).
- **파티션**: `trade_date_et` / `symbol`. 컬럼: `ts_utc, ts_et, symbol, open, high, low, close, volume`
  (+ 수집 메타 `source="toss", ingested_at_utc`). 결측 분봉은 행 부재로 표현(0-충전 금지 —
  volume 0과 결측 구분).
- **품질 게이트**(D1 선례 재사용): 심볼×거래일 커버리지 표, 결측 분봉 비율, 중복 타임스탬프 0,
  OHLC 정합(low≤open,close≤high) — `build_backfill_parquet`의 품질 게이트 패턴을 US로 이식.

---

## §5. Rate limit / 안전

- Toss API rate limit은 **착수 시 실측**(문서 추정 금지). 수집기는 자체 로컬 스로틀 +
  지수 백오프. 유니버스 전체×미장 분봉은 대량이므로 **일자별 재개 가능한 체크포인트**
  (부분 수집 후 이어받기) 필수 — 11GB 배치(gate2 ①) 교훈.
- 실패 격리: 심볼 1개 실패가 배치 전체를 중단시키지 않음(수집 로그에 결측 심볼 기록 →
  §4 커버리지 게이트가 잡음).
- **크레덴셜**: Toss 자격증명은 `~/.config/` 또는 별도 env — `.env`/KIS 토큰 캐시와 분리.
  본 스펙은 자격증명 값을 다루지 않음(운영자 배치).

---

## §6. 파이프라인 위상 (경로 B 전체, 참고 — 착수 시 delegation-plan으로 분해)

```
[운영자 스케줄]  Toss 분봉 수집(당일 미장, 유니버스 전체)  → data/us_minute/*.parquet   ← OB(운영자)
[에이전트]       + O7-2 ingest_us_daily(전일 종가·MA 히스토리)  → data/us_daily/*.parquet
[에이전트]       feature stage: 분봉+일봉 → EvaluationRecord (15조건, liquidity 신규정의)
[에이전트]       기존 walk-forward 탐색 하니스(weight_search.py) 재사용
[운영자 게이트]  artifact 채택 결정
```

경로 A와의 관계: **경로 A가 먼저**(레짐 조건화, 실데이터는 O7-2만 의존). 경로 B는 A의 결과가
rank-neutral을 못 풀거나 네이티브 정밀도가 필요할 때 **후속 승격** — 두 경로는 배타 아님.

---

## §7. Red/Green (E4 = 문서 슬라이스)

- **Red**: 문서 — 코드 없음(스펙 단계).
- **Green** (master_completion_plan §4 기준): 본 문서에 ① 수집 대상(심볼=유니버스 전체·세션
  시간대 **DST 처리**) ② 저장 스키마(분봉 parquet 파티션) ③ rate limit ④ 크레덴셜 경계
  (`BUY_SCAN_QUOTE_KIS_ENV` 불간섭) ⑤ **15조건 스코어 산출 가능성 매핑** 전부 명기 —
  §1~§6이 충족. **+ 운영자 승인**(OB 착수 결정)이 최종 Green.

## §8. 운영자 결정 대기 항목 (착수 전 확정 필요)

1. cost_quality 처리 방법 (§1-3: 상수 가정 / 고저 프록시 / 중립충전 중 택1).
2. 미장 세션 창 (Toss 제공 범위 실사 후 — 예: 04:00–10:00 ET).
3. US 홀드아웃 구간 (KR 2025-04-01~06-16 대응 ET 구간, §3).
4. OB 착수 시점 (경로 A 결과 리뷰 후 승격 여부).
