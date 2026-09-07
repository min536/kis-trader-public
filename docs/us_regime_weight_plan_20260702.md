# Track M — 미국장 유사일 레짐 조건부 가중치 상세 구현 계획 (2026-07-02)

> **상태(2026-07-04): M-D1~M-R1 4슬라이스 코드+테스트 전부 완료** (Opus 위임 + Fable 검토, 브랜치 58
> 커밋 `30530de`/`fb47d6b`/`82fd9f2`/`f960f4a`; 신규 테스트 34개, full suite 3081 passed).
> 남은 것은 **운영자 실행**뿐: ① US 일봉 CSV 적재+캘린더 빌드(`app.tools.ingest_us_daily`) —
> 본체 D1 변환 산출물 필요 ② 라이브러리 빌드(`app.tools.build_regime_library`) — 본체 ④ V1
> records 필요 ③ 08:30 launchd 등록(`app.tools.morning_regime_pick`). 에이전트는 실데이터
> 게이트를 실행하지 않는다.

> **실행 위임 문서** (r4 형식). tdd_guard: 테스트 1개씩, red 확인 후 구현. 판단 필요 시 §8 에스컬레이션.
> **선행 조건**: [`docs/gate2_minute_backtest_plan_20260702.md`](gate2_minute_backtest_plan_20260702.md)
> **V1 완료**(records parquet + Stage A/B 하니스). M-D1·M-F1은 **코드+테스트에 한해** 본체 D1과 병행
> 가능(합성 픽스처 사용); 실데이터 `--build-calendar` 실행은 본체 D1의 11GB 변환(운영자)이 만든
> `data/toss_minute_parquet_backfill`이 있어야 한다(리뷰 #10). M-C1부터 V1 의존.
> 실행 공통 규칙은 본체 계획 §1을 그대로 상속(브로커 API 금지·data/ 원본 불변·research leaf 등).

## 0. 요구와 설계 결정

**요구**: 매일 08:30 KST에 전일(직전 완료) 미국장을 분석 → 과거에서 차트 변화가 비슷한 미국장 날들을
찾고 → 그 날들의 D+1 국장만 백테스터로 평가해 → 당일 gate2 가중치를 개장 전에 선택한다.

**핵심 결정 — 아침에는 탐색하지 않고 분류만 한다.** 유사일 D+1 표본(~20일) 위 free search는
과최적화가 보장됨. ① 오프라인: 미국장 피처 공간을 레짐으로 군집화 → 레짐별 조건부 탐색·OOS 검증을
통과한 artifact만 **레짐 라이브러리** 등재. ② 아침 잡: 전일 미국장 → 피처 → 레짐 판정 → 라이브러리
lookup. 신뢰도 미달·미등재 레짐이면 **baseline 유지**(코드로 강제).

**적용 경계**: 이 트랙의 산출물은 추천 JSON + Slack 통지까지(**shadow**). 런타임 자동 반영은 범위 밖
(기존 autotuner 승인 흐름 / Stage-5 게이트 재사용, 별도 결정).

## 1. 신규 패키지 구조

```
app/research/us_regime/
  __init__.py
  ingest.py        # M-D1: US 일봉 적재(소스 어댑터: csv 우선)
  calendar_map.py  # M-D1: KR거래일 ← 직전 완료 US세션 매핑 테이블
  features.py      # M-F1: 세션 피처 벡터 + expanding z-score
  similarity.py    # M-F1: k-NN 유사일
  regimes.py       # M-C1: k-means(레짐) + 조건부 탐색 + 라이브러리 빌드
app/tools/
  ingest_us_daily.py        # M-D1 CLI
  build_regime_library.py   # M-C1 CLI
  morning_regime_pick.py    # M-R1 CLI
```

의존: stdlib + pandas/pyarrow/numpy만(sklearn 가정 금지 — k-means/실루엣은 numpy 직접 구현, seed 고정).
records/아티팩트는 본체 계획 산출물을 읽기 전용으로 소비.

## 2. M-D1 — US 일봉 적재 + KR거래일 매핑

**파일**: `ingest.py`, `calendar_map.py`, `app/tools/ingest_us_daily.py`,
`tests/test_us_regime_ingest.py`, `tests/test_us_calendar_map.py`.

- 대상 자산(설정화, 기본): `("SPX", "NDX", "VIX", "SOX")` — 지수 심볼 라벨은 임의 문자열(소스 CSV의
  컬럼 계약만 고정). 기간: 2016-10-01~ (피처 워밍업 위해 토스 커버리지보다 3개월 앞).
- `ingest.py`:
  - `REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")` (volume은 지수면 0 허용).
  - `def import_us_daily_csv(csv_path: Path, *, asset: str, out_root: Path) -> int` —
    검증(날짜 오름차순·중복 금지·OHLC 정합 low<=open,close<=high) 후
    `out_root/<asset>.parquet` 기록, 행 수 반환. 위반 시 ValueError(행 번호 포함).
  - `def load_us_daily(out_root: Path, asset: str) -> "pd.DataFrame"`.
  - 소스 어댑터 확장점: `fetch_us_daily(source: str, ...)`는 `source=="csv"`만 구현,
    `"kis_overseas"`/`"toss_us"`는 `NotImplementedError` + §8-① 에스컬레이션 주석
    (현 `app/overseas_stock/market_data.py`에는 현재가 계열 API만 존재 — 기간별시세 TR 신규 필요 확인됨).
- `calendar_map.py`:
  - KR 거래일 정본 = **토스 parquet의 date 파티션 목록**(`data/toss_minute_parquet_backfill`)
    — `def kr_trading_days(parquet_root) -> tuple[date, ...]`. parquet_root 부재/파티션 0개면
    `FileNotFoundError("run docs/gate2_minute_backtest_plan D1 conversion first")` — 폴백 없음
    (조용한 빈 캘린더 금지).
  - US 세션일 정본 = 적재된 US parquet의 date 컬럼.
  - `def build_kr_to_us_map(kr_days, us_days) -> dict[date, date]` — **KR거래일 K ← K의 08:30 KST
    이전에 완료된 마지막 US 세션일**. 구현 규칙(시간대 계산을 단순화한 보수 규칙, 주석으로 명기):
    US 세션일 D(동부 날짜)의 완료 시각은 KST로 D+1 새벽 → `map[K] = max{D ∈ us_days : D < K}`
    (날짜 비교, D는 K 이전 달력일). US 연휴로 D가 밀리면 그 값을 그대로 사용하되
    `gap_days = (K - D).days`를 함께 기록. 제외 임계(리뷰 #12): **gap_days > 5** —
    US 목·금 연휴 + 주말 → KR 월요일(gap 5)은 유효 매핑으로 **유지**하고, 6 이상만 저신뢰로 제외.
    제외된 KR 일수는 매핑 빌드 로그에 카운트로 출력.
  - `def to_frame(mapping) -> pd.DataFrame` — 컬럼 `(kr_day, us_day, gap_days)`;
    `results/gate2_backtest/kr_us_calendar.parquet` 저장은 CLI에서.
- CLI `ingest_us_daily.py`: `--csv <path> --asset SPX --out-root data/us_daily` 반복 호출형 +
  `--build-calendar` 플래그(매핑 parquet 생성). 실데이터 적재 실행은 운영자.
- TDD(1개씩): ① CSV 검증 위반(역순 날짜) ValueError ② parquet 라운드트립 ③ 매핑: 금요일 US → 월요일
  KR(주말 스킵, gap 3) ④ KR 연휴(합성으로 KR 날 제거) → 다음 KR 날이 같은 US 세션을 가리킴
  ⑤ 경계: US 목·금 연휴 → KR 월요일 = gap 5 **유지**, gap 6 제외 ⑥ parquet_root 부재 FileNotFoundError.
- 커밋: `feat(research): US daily ingest + KR-trading-day to last-US-session map (M-D1)`

## 3. M-F1 — 세션 피처 + 유사일 검색

**파일**: `features.py`, `similarity.py`, `tests/test_us_regime_features.py`.

- `features.py`:
  - 자산 a의 세션일 D 피처(전일 = 직전 US 세션일):
    `gap_pct = open/prev_close - 1`, `intraday_ret = close/open - 1`, `day_ret = close/prev_close - 1`,
    `close_pos = (close-low)/(high-low)` (high==low면 0.5), `range_pct = (high-low)/prev_close`,
    `rv5 = stdev(직전 5 세션 day_ret)`. VIX 자산은 `level`과 `day_ret`만.
  - `def build_feature_frame(us_daily_by_asset: Mapping[str, pd.DataFrame],
    assets: tuple[str, ...]) -> pd.DataFrame` — index=us_day, 컬럼=`{asset}_{feature}` 평탄화.
    워밍업(피처 계산 불가 초기 행)은 드롭.
  - `def expanding_zscore(frame: pd.DataFrame, *, min_obs: int = 60) -> pd.DataFrame` —
    각 컬럼을 **해당 행 이전까지의**(shift(1) 후) expanding mean/std로 정규화. min_obs 미만 행 드롭.
    std==0 구간은 0. **look-ahead 금지의 단일 관문 — 당일 값이 자기 통계에 포함되지 않음을 테스트로 단언.**
- `similarity.py`:
  - `def find_similar_us_days(z: pd.DataFrame, target_day: date, k: int = 20,
    max_age_years: float | None = 8.0) -> list[tuple[date, float]]` — target 행과 그 **이전** 행들 간
    유클리드 거리 오름차순 상위 k(연령 초과 제외). target 이후 날짜가 후보에 포함되면 버그(테스트 단언).
- TDD: ① 피처 수기 검증(작은 OHLC 표) ② expanding_zscore가 당일 값을 통계에서 제외
  ③ 유사일이 미래를 포함하지 않음 ④ high==low 경계.
- 커밋: `feat(research): US session features + leak-free similar-day search (M-F1)`

## 4. M-C1 — 레짐 라이브러리 (오프라인, V1 산출물 재사용)

**파일**: `regimes.py`, `app/tools/build_regime_library.py`, `tests/test_us_regimes.py`.

- `regimes.py`:
  - `def kmeans(z_matrix: np.ndarray, k: int, *, seed: int = 20260702, iters: int = 100)
    -> tuple[np.ndarray, np.ndarray]` — Lloyd, 초기중심 = seed RNG 표본, (centroids, labels).
  - `def silhouette(z_matrix, labels) -> float` — 표준 정의 numpy 구현(전 표본 평균).
  - `def choose_k(z_matrix, *, k_range=(3, 4, 5, 6, 7), seed) -> int` — 실루엣 최대.
  - `def conditional_kr_days(regime_us_days: set[date], kr_us_map: pd.DataFrame) -> tuple[date, ...]`
    — `kr_us_map.us_day ∈ regime_us_days`인 kr_day 목록.
  - `def evaluate_regime(records_frame, kr_days, base_artifact, candidates, *, min_days: int = 40,
    min_selected_train: int = 30) -> dict` — kr_days 미달 시 `{"enrolled": False, "reason": "min_days"}`.
    시간순 70/30 분할(일 단위) → train에서 `candidate_objective_v2(mode="dd_adjusted",
    min_selected=min_selected_train)` 최대 후보 선택 → test에서 그 후보와 **baseline을 같은 지표로
    병기** → 후보 test 성적 > baseline test 성적일 때만 `enrolled: True`.
    **가드 상호작용(리뷰 #13)**: test 슬라이스는 최소 12일 수준(40일×30%)이므로 고정 30선택 요구는
    과함 — test 쪽 최소 선택 수는 `min_selected_test = max(10, test_day_count)`로 **일수 비례**
    적용하고, 미달 시 `{"enrolled": False, "reason": "min_selected_test"}`. TDD에 이 상호작용
    케이스(총 40일·test 12일·선택 11건 → 탈락) 포함.
    (records_frame은 R1 parquet를 **kr_day 필터를 pyarrow 단계에서 적용해** 로드 — 전 기간 프레임
    상주 금지; EvaluationRecord 재구성 헬퍼 `records_from_frame(frame)` 포함, 본체 W2 함수 재사용.
    본체 2차 리뷰 F-3 상속: 후보 생성은 `frozen_keys=("portfolio_diversification_score",
    "volatility_risk_score")` 동결 그대로 사용 — 레짐 탐색 차원도 13개.)
  - `def build_regime_library(...) -> dict` — 스키마:
    `{"version": "regime_lib_v1", "created_utc": ..., "assets": [...], "feature_columns": [...],
    "scaler": {"mean": {...}, "std": {...}, "as_of": "..."}, "k": int, "centroids": [[...]],
    "max_assign_distance": float(훈련 표본 소속거리 p95), "regimes": [{"regime_id": int,
    "n_us_days": int, "n_kr_days": int, "artifact": {...}|null, "enrolled": bool, "reason": str,
    "candidate_test": {...}, "baseline_test": {...}}], "fallback": "baseline"}`.
    scaler는 **라이브러리 생성 시점 고정 통계**(아침 잡이 재사용 — 아침에 expanding 재계산 금지).
- CLI `build_regime_library.py`: 입력(US parquet root, 캘린더 parquet, records parquet 경로들,
  base artifact 경로) → `app/gate2/artifacts/regime_library.json` + 사람용 요약
  `results/gate2_backtest/regime_library_report.md`(레짐별 등재/탈락 사유·baseline 대비 표 필수).
- TDD: ① kmeans seed 재현성 ② choose_k(분리 잘 된 합성 3군집 → k=3) ③ min_days 가드
  ④ baseline 미개선 → enrolled False ⑤ 라이브러리 JSON 스키마 라운드트립.
- 커밋: `feat(research): US-regime library with conditional gate2 search + guards (M-C1)`

## 5. M-R1 — 아침 파이프라인 (분류·추천 전용, shadow)

**파일**: `app/tools/morning_regime_pick.py`, `app/notifications/slack.py`(이벤트 타입 1행 추가),
`tests/test_morning_regime_pick.py`.

- CLI 흐름(`--date` 기본 = 오늘 KST, `--us-root data/us_daily`, `--library app/gate2/artifacts/
  regime_library.json`, `--out results/gate2_backtest/`):
  ① 라이브러리 로드 ② 대상 US 세션일 = 캘린더 매핑 규칙과 동일(`max{D < today}`); US parquet에 그 날이
  없으면 **fallback("us_data_missing")** ③ 피처 계산 → 라이브러리 `scaler`로 정규화(재계산 금지)
  ④ 최근접 centroid; 거리 > `max_assign_distance` → **fallback("low_confidence")**; 레짐
  `enrolled==False` → **fallback("regime_not_enrolled")** ⑤ 산출 JSON
  `morning_regime_<date>.json`: `{date, us_session_day, regime_id|null, assign_distance,
  artifact_version("baseline"|"regime_<id>"), artifact: {...}, fallback_reason|null,
  data_as_of, generated_at}` ⑥ Slack 통지 1회(운영자 채널) — 실패는 삼키고 JSON은 남긴다.
- Slack(리뷰 #11 — 선례 정정): **`AUTOTUNER_EVENT_TYPE` 패턴을 따른다**(slack.py:108-109, 123).
  `order_gate_blocked`는 BOTTLENECKS 채널 튜플 방식이라 여기 쓰면 운영자 채널 단언과 모순.
  추가는 3행: `MORNING_REGIME_EVENT_TYPE = "morning_regime_pick"`,
  `EVENT_CHANNEL_ENV_BY_TYPE[MORNING_REGIME_EVENT_TYPE] = OPERATOR_CHANNEL_ENV`,
  `EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE[MORNING_REGIME_EVENT_TYPE] = (OPERATOR_CHANNEL_ENV,)`.
- **런타임 자동 적용 금지** — 이 CLI는 파일과 통지만 만든다. 08:30 launchd 등록은 운영자.
- TDD: ① fallback 3케이스(us_data_missing/low_confidence/regime_not_enrolled) 각각 JSON의
  fallback_reason과 artifact_version=="baseline" ② 정상 판정 케이스(합성 라이브러리) ③ Slack 스파이
  1회 호출·실패 삼킴 ④ 이벤트 타입 라우팅(operator 채널) 단언.
- 커밋: `feat(research): morning US-regime classification + shadow artifact pick (M-R1)`

## 6. 실행 순서

M-D1 → M-F1 → (V1 완료 대기) → M-C1 → M-R1. 운영자 실행 2회: US CSV 적재, 라이브러리 빌드.

## 7. 리스크 (라이브러리 리포트에 전문 수록)

- 조건부 소표본 — min_days=40·min_selected=30 가드, 레짐 수 k≤7 상한.
- **가설 자체 검증**: 레짐별 표에 무조건-baseline 대비 개선폭 병기; 전 레짐 미개선이면 Track M
  미채택이 결론(그 자체가 유효한 결과).
- 08:30 데이터 확정성(벤더 지연/정정) — `data_as_of` 기록, us_data_missing fallback.
- 서머타임·휴장 매핑은 날짜 단위 보수 규칙(§2)로 흡수 — 분 단위 정밀 시간대 계산은 비목표.
- 지수 데이터는 생존편향 무관(지수 자체), 다만 SOX 등 구성 변화는 존재 — 피처가 수익률 기반이라 영향 제한.

## 8. 에스컬레이션

- ① US 데이터 소스를 CSV 외(KIS 기간별시세 TR 신설/토스 US)로 확장하려면 중단·보고(신규 API 작업은
  별도 승인). ② sklearn 등 신규 의존성 추가 금지 — numpy 구현이 막히면 보고. ③ 라이브러리/추천의
  런타임 자동 반영 요구가 생기면 중단(트레이딩-크리티컬 게이트 필요). ④ 기존 테스트 충돌 시 보고.
- records parquet 스키마가 본체 R1과 다르게 나오면(컬럼 불일치) 임의 변환 말고 보고.
