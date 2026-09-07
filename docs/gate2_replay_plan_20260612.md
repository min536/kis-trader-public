# Gate2 v2 + Minute Replay 구현 계획 (2026-06-12)

> 근거: `docs/0611plan.md` §11 즉시 과제 중 **데이터 다운로드(Stage 1 수집)를 제외한 전부**.
> 절대 제약: 런타임 무변경 (score_v1 그대로), shadow/연구 전용. 트레이딩-크리티컬 표면
> (`app/execution/`, `app/risk/`, `app/strategy/`, `app/auth/`, `app/main.py`, `scripts/`) 무수정.
> broker/order API 호출 금지. `.env`/`.token_cache.json` 접근 금지.

## 1. 설계 결정

- 신규 코드는 전부 **`app/research/` leaf 패키지** — 어떤 런타임 모듈도 이 패키지를 import하지 않는다
  (역방향 import 금지: research → 런타임은 허용 안 함. research는 stdlib + pandas/pyarrow만).
- **어댑터는 dict-in/dict-out 순수 함수**: `score_components: dict[str, float]`를 입력으로 받고
  scanner를 import하지 않는다 → 워크트리 분기점(R8 이전)과 무관하게 동작.
- BrokerSimulator도 settings/costs를 import하지 않고 명시적 비용 파라미터를 받는다
  (런타임 연결은 통합 후 runner 단계).
- 정규화 캡은 고정 상수가 아니라 **artifact(JSON)의 일부** — 0611plan §4의
  "condition score normalization"이 탐색 대상이기 때문.

## 2. v1 score_components 실측 범위 (어댑터 근거)

`calculate_selection_score`(app/scanner/scoring.py) + service.py 합류 컴포넌트:

| v1 키 | 범위 | 근거 |
|------|------|------|
| `*_strength_score` 7종 (intraday_pullback/rebound_from_low/controlled_down/gap_down_open/range_recovery/live_volume_rank/live_volume_power_rank) | 0~100 | `_threshold_strength`/`_range_strength`/이진 0·100 |
| `trend_alignment_score` | 0~0.35 | technical.py 0.10+0.15+0.10 |
| `macd_momentum_score` | 0~0.20 | technical.py 0.10+0.10 |
| `cost_quality_score` | 0~0.35 | service.py `* 0.35` |
| `mean_reversion_bonus` | 0~약 0.30 | mean_reversion.py |
| `velocity_bonus` / `velocity_penalty` | 0~약 0.20 | price_dynamics.py |
| `diversification_bonus` | 0 또는 0.15 | portfolio_risk.py:152 |
| `portfolio_correlation_penalty` | 0~약 0.37 | portfolio_risk.py:150 |
| `variance_increase_penalty` | 0~(소프트 무한, 실측 ≤0.5) | portfolio_risk.py:151 |

## 3. 슬라이스 사양

### Track G — `app/research/gate2/` (실행자 1, 슬라이스 직렬 G1→G4)

**G1 — `schema.py`** (커밋: `feat(research): add gate2 score_v2 schema and weights artifact (G1)`)
- `CONDITION_SCORE_NAMES: tuple[str, ...]` — 0611plan §3의 15종 정확히:
  `pullback_strength_score, rebound_strength_score, controlled_down_quality_score,
  gap_down_quality_score, range_recovery_score, trend_alignment_score, macd_momentum_score,
  volume_rank_score, volume_power_score, cost_quality_score, mean_reversion_score,
  price_velocity_score, liquidity_score, volatility_risk_score, portfolio_diversification_score`
- `@dataclass(frozen=True) ScoreV2Artifact`: `version: str`, `weights: dict[str, float]`,
  `buy_threshold: float`, `normalization_caps: dict[str, float]`.
- `validate_artifact(artifact)` → 위반 시 ValueError: weights 키셋 == 15종 정확히,
  각 weight 0.0~1.0, buy_threshold 0~100, caps 양수.
- `load_artifact(path)` / `save_artifact(artifact, path)` JSON 라운드트립.
- `default_artifact()` → version `"score_v2_w0"`, buy_threshold `60.0`,
  weights: 7종 strength 계열(pullback/rebound/controlled_down/gap_down/range_recovery/volume_rank/volume_power) `1.0`,
  `trend_alignment_score/macd_momentum_score/cost_quality_score/mean_reversion_score/price_velocity_score/volatility_risk_score/portfolio_diversification_score` `0.5`, `liquidity_score` `0.25`,
  normalization_caps: `trend_alignment=0.35, macd_momentum=0.20, cost_quality=0.35,
  mean_reversion=0.30, velocity_bonus=0.20, velocity_penalty=0.20, diversification_bonus=0.15,
  portfolio_correlation_penalty=0.37, variance_increase_penalty=0.50`.
- 테스트 `tests/test_gate2_schema.py`: 15종 이름 리터럴 전체 동등성, default 값 리터럴 핀,
  validate 위반 케이스(누락/초과 키, 범위 밖), load/save 라운드트립(tmp_path).

**G2 — `score_v2.py`** (커밋: `feat(research): add weighted_gate2_score pure function (G2)`)
- `@dataclass(frozen=True) Gate2ScoreResult`: `final_score: float`,
  `contributions: dict[str, float]`, `missing: tuple[str, ...]`.
- `weighted_gate2_score(scores: Mapping[str, float | None], weights: Mapping[str, float]) -> Gate2ScoreResult`
  - 공식(0611plan §3): `final = sum(score_i/100 * weight_i * 100) = sum(score_i * weight_i)` —
    None/누락 score는 기여 0 + `missing`에 기록. 재정규화 없음(결정적).
  - 검증: scores 키가 weights 키의 부분집합이 아니면 ValueError, score가 0~100 밖이면 ValueError.
  - `passes_threshold(result, artifact)` 헬퍼: `final_score >= buy_threshold`.
- 테스트 `tests/test_gate2_score_v2.py`: 손 계산 리터럴 2케이스 이상(전 contributions dict 동등성),
  None/누락 처리, 범위 위반 ValueError, threshold 경계.

**G3 — `adapter.py`** (커밋: `feat(research): map score_v1 components to v2 condition scores (G3)`)
- `condition_scores_from_v1(score_components: Mapping[str, float], caps: Mapping[str, float]) -> dict[str, float | None]`
  매핑(§2 근거, `_clip = min(max(x,0),100)`):
  - 직통 7종: `pullback←intraday_pullback_strength_score`, `rebound←rebound_from_low_strength_score`,
    `controlled_down_quality←controlled_down_strength_score`, `gap_down_quality←gap_down_open_strength_score`,
    `range_recovery←range_recovery_strength_score`, `volume_rank←live_volume_rank_strength_score`,
    `volume_power←live_volume_power_rank_strength_score` — `_clip(v)`.
  - 캡 정규화 4종: `trend_alignment`, `macd_momentum`, `cost_quality`(←`cost_quality_score`),
    `mean_reversion`(←`mean_reversion_bonus`) — `_clip(v / cap * 100)`.
  - 양극 2종(50=중립): `price_velocity = _clip(50 + (velocity_bonus/cap_b - velocity_penalty/cap_p) * 50)`,
    `portfolio_diversification = _clip(50 + (diversification_bonus/cap_b - portfolio_correlation_penalty/cap_p) * 50)`.
  - 위험 역변환: `volatility_risk = 100 - _clip(variance_increase_penalty / cap * 100)` (100=저위험).
  - `liquidity_score`: v1에 원천 없음 → 항상 `None` (replay feature 단계에서 도입 예정 주석).
  - 입력 키 부재 시 해당 condition score는 `None` (0 아님 — 관측 불가와 0점 구분).
- 테스트 `tests/test_gate2_adapter.py`: 손 입력 dict → 15종 전체 출력 dict 동등성(리터럴) 3케이스
  (풍부/부분 결측/전부 결측), 클립 경계, 양극 중립 50 확인.

**G4 — `shadow_report.py`** (커밋: `feat(research): add score_v1 vs v2 shadow comparison report (G4)`)
- 입력: `rows: list[dict]` (각각 `symbol`, `score_v1: float`, `passed_count: int`,
  `score_components: dict`), `artifact: ScoreV2Artifact`.
- `build_shadow_comparison(rows, artifact) -> list[dict]`: 행마다
  `symbol, score_v1, score_v2, rank_v1, rank_v2, rank_delta, would_pass_v2, missing` —
  rank_v1은 v1 선정 규칙(passed_count desc, score desc, symbol asc =
  `build_analysis_sort_key`와 동일 튜플), rank_v2는 score_v2 desc, symbol asc.
- `summarize_shadow_comparison(comp) -> dict`: `top1_agreement: bool`, `top3_overlap: int`,
  `v2_pass_count: int`, `mean_abs_rank_delta: float`.
- `build_shadow_console_lines(comp, summary) -> list[str]` (포맷은 손 리터럴로 테스트 핀).
- 테스트 `tests/test_gate2_shadow_report.py`: 손 rows 4종목 → 비교/요약/콘솔 전체 동등성.

### Track R — `app/research/replay/` + `app/research/data/` (실행자 1, 직렬 R1→R4)

**R1 — `minute_provider.py`** (커밋: `feat(research): add historical minute data provider (R1)`)
- `@dataclass(frozen=True) MinuteBar`: `symbol: str, ts: datetime, open: float, high: float,
  low: float, close: float, volume: int`.
- `@dataclass(frozen=True) MinuteObservation`: `symbol, ts`(관측 시각 T), `bar_ts`(최신 bar 시각),
  `open/high/low/close/volume`(해당 bar), `day_open, day_high, day_low, day_cum_volume`
  (그 날 T 이하 bar들만 집계).
- `HistoricalMinuteDataProvider(bars: Iterable[MinuteBar])`: 심볼별 ts 정렬 저장,
  중복 ts ValueError.
  - `observable_snapshot(symbol, t: datetime) -> MinuteObservation | None` —
    **ts ≤ t** bar만 사용(같은 날), 없으면 None. 미래 누수 금지.
  - `universe_at(t) -> tuple[str, ...]` — 그 날 t 이하 bar가 있는 심볼 정렬 튜플.
  - `volume_rank_at(t, top_n) -> tuple[str, ...]` — t 이하 당일 누적 거래량 내림차순
    (동률은 심볼 오름차순). live rank proxy(0611plan §5: T 시점 데이터만).
- 테스트 `tests/test_replay_minute_provider.py`: 손 bar 3심볼×수분 → snapshot 전체 필드 리터럴,
  경계(t가 bar 사이/이전/이후), **누수 테스트**(t 이후 bar 추가 전후 출력 불변),
  universe/volume_rank 리터럴, 중복 ts ValueError.

**R2 — `broker_sim.py`** (커밋: `feat(research): add broker fill simulator with cost model (R2)`)
- `@dataclass(frozen=True) SimCostParams`: `buy_fee_bps, sell_fee_bps, sell_tax_bps,
  buy_slippage_bps, sell_slippage_bps` (float, 기본값 없음 — 명시 주입).
- `@dataclass(frozen=True) SimFill`: `symbol, side("BUY"/"SELL"), qty, ref_price, fill_price,
  fee, tax, ts, cash_after`.
- `BrokerSimulator(initial_cash: float, costs: SimCostParams)`:
  - `buy(symbol, qty, ref_price, ts) -> SimFill | None` — fill_price = ref*(1+slip/1e4),
    총비용 = fill*qty + fee; **현금 부족이면 None(하드 거절, 음수 현금 불가)**.
  - `sell(symbol, qty, ref_price, ts) -> SimFill | None` — 보유 수량 부족이면 None;
    수취 = fill*qty − fee − tax.
  - `positions: dict[str, SimPosition(qty, avg_price)]`, `cash`, `realized_pnl`,
    `equity(mark_prices: Mapping[str, float]) -> float`, `record_equity(ts, mark_prices)`,
    `equity_curve: list[tuple[ts, float]]`, `fills: list[SimFill]`.
- 테스트 `tests/test_replay_broker_sim.py`: 손 계산 리터럴(수수료/세금/슬리피지 bps 산식 전체
  동등성), 현금 부족 거절, 평단 갱신, realized/equity 곡선 리터럴.

**R3 — `sizing.py`** (커밋: `feat(research): add research sizing curve for score_v2 (R3)`)
- `@dataclass(frozen=True) SizingTier`: `min_score: float, multiplier: float`.
- `DEFAULT_SIZING_TIERS` (0611plan §3 리터럴): `[(60.0, 0.5), (70.0, 1.0), (85.0, 1.25)]`.
- `score_to_budget_multiplier(score, tiers=DEFAULT) -> float` — 최저 tier 미만 0.0,
  구간별 계단식. `apply_budget_caps(budget, *, cash, max_budget) -> float` — min 하드캡.
- 테스트 `tests/test_replay_sizing.py`: 경계 리터럴 전수(59.99/60/69.99/70/85/100), 캡 동작.

**R4 — `app/research/data/minute_csv_to_parquet.py`** (커밋: `feat(research): add minute CSV to parquet converter with quality report (R4)`)
- 입력 레이아웃(0611plan §8): `raw_dir/date=YYYY-MM-DD/symbol=XXXXXX_YYYYMMDD.csv`,
  필요 컬럼 `datetime, open, high, low, close, volume` (헤더 대소문자 허용).
- `convert_minute_csv_dir(raw_dir, out_dir) -> list[DayFileQuality]`:
  심볼·일자별 정렬, 중복 datetime 제거(첫 행 유지, flag), date 파티션 parquet 저장
  (`out_dir/date=YYYY-MM-DD/part.parquet`, pyarrow).
- `@dataclass(frozen=True) DayFileQuality`: `date, symbol, rows, first_time, last_time,
  flags: tuple[str, ...]` — flags(§8 기준): `late_open`(first > 09:05),
  `early_close`(last < 15:30), `duplicate_ts`, `non_monotonic`. **381행 불변식 사용 금지.**
- `build_quality_console_lines(reports) -> list[str]`.
- 테스트 `tests/test_minute_csv_to_parquet.py`: tmp_path에 손 CSV 생성 → parquet 라운드트립
  전체 동등성(pandas 비교), 각 flag 발화 케이스, 정렬/중복 처리.
  **`tests/fixtures/` 디렉토리 사용 금지(타 작업 소유) — tmp_path만.**

## 4. 실행 프로토콜 (R8과 동일 + 추가)

- 트랙 G/R 파일 완전 비중첩 → 워크트리 2병렬, 트랙 내 슬라이스 직렬.
- **순수 신규 파일만 생성** — 기존 파일 수정 금지(원본 소스 어디도 건드리지 않음).
  `app/research/__init__.py`, `app/research/gate2/__init__.py` 등 패키지 init 포함(빈 파일 OK,
  Track G가 `app/research/__init__.py` 생성, Track R은 자기 하위 패키지 init만).
- pytest 절대경로 `$KIS_TRADER_ROOT/.venv/bin/python -m pytest`.
- tdd_guard 공유 상태: 무관 실패 사유 거부 시 자기 테스트 파일 재실행으로 fresh red 후 즉시 재시도.
  일괄 테스트 작성 거부됨 — 1테스트(또는 1클래스)씩 Edit→pytest 사이클.
- 커밋: 슬라이스당 1개, 경로 지정 add만. NOT-mine 파일 절대 미포함.
- 의미 동일 재작성 금지 사양 아님(신규 코드) — 단 **계획 명시 API/리터럴은 정확히** 구현.
- 메인 트리($KIS_TRADER_ROOT) 파일 수정 금지. `.claude/` 설정 무수정.

## 5. 검증 게이트 (상위 모델)

1. API 표면 검증: 계획 §3의 클래스/함수/시그니처/기본값 리터럴 전수 존재.
2. leaf 검증: `app/research/` 내 모듈이 app.* 중 research 외부를 import하지 않음 +
   런타임 코드가 research를 import하지 않음(grep).
3. 게이트 2에이전트(read-only): 테스트 품질(비동어반복·전체 동등성·mock 금지) + 위생
   (커밋 격리, NOT-mine, 트레이딩-크리티컬/가드 무수정).
4. 메인 트리 전체 스위트(기준 2,166 passed + 320 subtests + 신규).

## 6. 에스컬레이션

- 가드 4회 연속 거부 → STOP·부분 산출물 보고. 세션 한도 → 워크트리 보존·이어하기.
- 사양 불명/충돌 발견 → 임의 설계하지 말고 STOP·보고.

## 7. 진행표 (2026-06-12 완료)

| 슬라이스 | 상태 | 커밋 (메인 브랜치) |
|---------|------|------|
| G1 schema | 완료 | dbb00f7 |
| G2 score_v2 | 완료 | 2fe26bb |
| G3 adapter | 완료 | 035ee77 |
| G4 shadow_report | 완료 | 9c852f7 |
| R1 minute_provider | 완료 | 15cf65f |
| R2 broker_sim | 완료 | 8dfab5c |
| R3 sizing | 완료 | 6099db5 |
| R4 csv→parquet | 완료 | e1b3b38 (+891642d: `app/research/data`→`ingest` rename) |
| 문서: API budget 프로파일 설계 | 완료 | c367ff8 (`docs/api_budget_high_throughput_profile_20260612.md`) |

### 게이트 결과

1. **상위 모델 결정적 검증**: Track G/R API 표면이 §3 리터럴 전수 일치(15종 이름·기본 weights·캡 9종·
   어댑터 4유형 매핑·sizing 경계·체결 산식), R1 미래 누수 가드 실증, leaf 격리(research 외부
   `app.*` import 0 / 런타임의 research import 0), init 4종 0바이트.
2. **테스트 품질 게이트** (opus, read-only): 8/8 CLEAN, 결함 0. R2/R4의 "사양 산식 표현식 기대값"
   deviation은 비동어반복으로 판정(부호/연산순서/베이스 오류를 잡는 독립 산출 — pass).
3. **위생 게이트** (opus, read-only): 7체크 전부 CLEAN — 런타임 무영향(변경 20파일 전부
   app/research/ + 신규 테스트), 커밋 격리, NOT-mine 무오염, 트레이딩-크리티컬/가드/.gitignore
   무수정, ingest rename 후 gitignore 비매칭 확인, 스테일 import 0.
4. **전체 스위트**: 2,211 passed + 348 subtests, 100% green (기준 2,166+320 대비 +45/+28 전부 신규).

### 실행 기록

- 워크트리 2병렬(opus). 두 트랙 모두 완주 — tdd_guard 교차 오염은 양쪽 다 fresh-red 레시피로
  회복(에스컬레이션 0). R4의 `app/research/data/`가 루트 `.gitignore` `data/`에 매칭되는 문제를
  실행자가 force-add로 우회 → 통합 시 `app/research/ingest/`로 rename해 해소(891642d).
- `.gitignore` `/data/` 앵커링은 `archive/data`, `.claude/tdd-guard/data`를 노출시켜 기각.

### 나중 todo (2026-06-12 사용자 결정 — 착수하지 않음)

- ① 분봉 데이터 다운로드(0611plan Stage 1 수집) — 사용자가 직접 시점 결정.
- ② replay 통합 runner(provider+sim+gate2 배선, 100종목×20일 prototype) — 데이터 확보 후.
- ③ runtime shadow 기록(Stage 5; score_v1 불변, would_select만 기록) — 트레이딩-크리티컬
  게이트(/risk-assessment) 필요, runner 이후.

weight/threshold search 하니스(Stage 4)는 2026-06-12 사용자 지시로 **진행** — §8 참조.

## 8. Stage 4 — weight/threshold search 하니스 (W1, 2026-06-12 착수)

데이터 없이도 동작하는 **walk-forward 탐색 하니스**를 leaf 모듈로 추가한다. 입력은
"평가 레코드"(심볼·시각·v2 condition scores·전방 수익률)의 시퀀스 — 나중에 replay
runner(②)가 이 레코드를 생산하면 그대로 꽂힌다. 지금은 합성 데이터 테스트로 검증.

### W1 사양 — `app/research/gate2/weight_search.py` (+ `tests/test_gate2_weight_search.py`)

leaf 불변식: stdlib만, 형제 import 허용(`app.research.gate2.schema`, `...score_v2`).

**데이터 모델 (frozen dataclass):**
- `EvaluationRecord(symbol: str, ts: str, scores: dict, forward_return_bps: float)` —
  `scores`는 name→float|None, 키는 CONDITION_SCORE_NAMES의 부분집합.
- `WalkForwardWindow(train_ts: tuple[str, ...], test_ts: tuple[str, ...])`
- `CandidateEvaluation(version: str, selected_count: int, mean_selected_return_bps: float, hit_rate: float, total_return_bps: float)`
- `WindowResult(window_index: int, best_version: str, train_eval: CandidateEvaluation, test_eval: CandidateEvaluation)`
- `WalkForwardReport(window_results: tuple[WindowResult, ...], version_win_counts: dict[str, int], overall_best_version: str, mean_test_return_bps: float)`

**함수:**
- `build_walk_forward_windows(records, train_size, test_size, step=None) -> list[WalkForwardWindow]`
  — 레코드의 **고유 ts를 오름차순 정렬** 후 인덱스 롤링: `i=0, step, 2*step, …`에 대해
  train=`ts[i:i+train_size]`, test=`ts[i+train_size:i+train_size+test_size]`.
  **완전한 윈도우만**(두 구간 모두 요청 길이일 때) 포함. `step` 기본값 `test_size`.
  train_size/test_size/step < 1 → ValueError. test는 train 직후 구간 — 미래 누수 구조적 차단.
- `evaluate_candidate(records, artifact) -> CandidateEvaluation` — 각 레코드에
  `weighted_gate2_score(record.scores, artifact.weights)` 적용,
  `final_score >= artifact.buy_threshold`면 선택. `version=artifact.version`,
  `mean_selected_return_bps`/`hit_rate`(forward_return_bps > 0 비율)는 선택 0건이면 0.0,
  `total_return_bps`=선택 레코드 합.
- `candidate_objective(evaluation, min_selected=1) -> float` —
  `selected_count < min_selected`면 `float("-inf")`, 아니면 `mean_selected_return_bps`.
- `generate_coordinate_candidates(base, scales=(0.5, 1.5), thresholds=(55.0, 60.0, 65.0)) -> list[ScoreV2Artifact]`
  — 순서 고정: 먼저 각 threshold(입력 순)에 대해 base 가중치 그대로
  `version=f"base@thr{threshold:g}"`; 이어서 CONDITION_SCORE_NAMES 순서로 각 name,
  각 scale(입력 순, `scale == 1.0` skip), 각 threshold(입력 순)에 대해
  `weights[name] = min(1.0, max(0.0, base.weights[name] * scale))`인 변형
  `version=f"{name}x{scale:g}@thr{threshold:g}"`. caps는 base 복사.
  각 후보는 `validate_artifact` 통과 후 반환.
- `walk_forward_search(records, candidates, train_size, test_size, step=None, min_selected=1) -> WalkForwardReport`
  — candidates 빈 리스트 또는 완전한 윈도우 0개 → ValueError. 윈도우마다 train_ts에
  속한 레코드만으로 각 후보 평가, `(objective, selected_count)` 최대 후보 선택
  (동률이면 **candidates 입력 순서상 앞선 후보** — strict-greater 비교로 자연 구현;
  전 후보 -inf여도 같은 규칙으로 첫 후보). 선택된 후보를 test_ts 레코드로 평가해
  `WindowResult` 기록. **test 데이터는 후보 선택에 절대 불관여.**
  `version_win_counts`: 1회 이상 우승한 version만, candidates 입력 순서로.
  `overall_best_version`: 최다 우승 version(동률은 입력 순서상 앞선 것).
  `mean_test_return_bps`: 윈도우별 `test_eval.mean_selected_return_bps`의 산술 평균.
- `build_search_console_lines(report) -> list[str]` — 윈도우당
  `f"window={window_index} best={best_version} train_sel={..} train_mean={..:.2f} test_sel={..} test_mean={..:.2f} test_hit={..:.2f}"`,
  마지막 줄 `f"overall_best={..} wins={win}/{len(window_results)} mean_test_return_bps={..:.2f}"`
  (win = `version_win_counts[overall_best_version]`).

**테스트 의무 케이스:** 윈도우 롤링 리터럴 전체 동등성(중복/비정렬 ts 입력 포함),
사이즈/step ValueError, evaluate_candidate 5필드 전체 동등성 + 0건 선택 + None score 기여 0,
objective 경계(min_selected), 후보 생성 개수 산식·순서·클램프(1.0×1.5→1.0)·version 리터럴,
walk_forward_search 전체 리포트 동등성, **train 우승 후보 ≠ test 우승 후보인 시나리오로
누수 부재 실증**, 동률 tie-break, 전 후보 -inf, 콘솔 라인 리터럴, ValueError 2종.
mock/patch 금지(순수 함수), `tests/fixtures/` 사용 금지.

### W1 진행표 (2026-06-12 완료)

| 슬라이스 | 상태 | 커밋 (메인 브랜치) |
|---------|------|------|
| W1 weight_search | 완료 | e491a9f (+0b16b64: 게이트 후속 — version_win_counts 입력순 핀 테스트) |

### W1 게이트 결과

1. **상위 모델 결정적 검증**: 함수 6종 시그니처/기본값, frozen dataclass 5종 필드가 §8
   리터럴 전수 일치(AST). 동작 스팟 체크 — 후보 93개 산식, version 포맷
   (`base@thr55`, `pullback_strength_scorex0.5@thr55`), 클램프(1.0×1.5→1.0,
   liquidity 0.25×0.5=0.125), 윈도우 롤링, 누수 프로브(train 우승 후보가 test 열세여도
   선택 유지), 콘솔 라인 리터럴 — 전부 일치. leaf 격리: stdlib+형제 import만,
   런타임의 research import 0.
2. **테스트 품질 게이트** (opus, read-only): 7/7 CLEAN, 결함 0. 비동어반복(기대값 전부
   손계산 리터럴), 전체 동등성, mock 0, 누수 실증 테스트의 구조적 유효성(test 구간
   strictly-better 후보 존재) 독립 재계산으로 확인. 유일한 비결함 지적(win_counts 삽입
   순서가 dict 동등성으로 핀 안 됨)은 0b16b64로 보강.
3. 전체 스위트: 2,226 passed + 450 subtests 100% green (W1 신규분 +15/+102 포함).
4. 실행 기록: opus 워크트리 1실행자. tdd-guard가 일괄 구현을 "over-implementation"으로
   거부 → `walk_forward_search`를 private 헬퍼 4종(stub-then-fill)으로 분해해 수용됨
   (공개 API/동작 무변경 — 게이트 7번 항목으로 검증). 가드 거부 다수는 병렬 워크트리의
   상태 오염 — fresh-red 레시피로 전부 회복, 에스컬레이션 0.
