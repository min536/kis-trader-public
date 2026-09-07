# Gate2 가중치 튜닝용 분봉 백테스터 — 상세 구현 계획 (2026-07-02, r2)

> **실행 위임 문서** (r4 형식). 구조 지도·심(seam) 검증·슬라이스 경계·TDD 사양까지 상위 모델이
> 코드 실사로 확정했고, 실행(실패 테스트 → 최소 구현 → 검증)은 하위 모델이 수행한다.
> tdd_guard: **테스트 1개씩, red 확인 후 구현**. 판단이 필요하면 §10 에스컬레이션 — 임의 판단 금지.
>
> 선행: [`docs/gate2_replay_plan_20260612.md`](gate2_replay_plan_20260612.md) §7 보류분 ①②의 재개.
> 자매 문서: [`docs/us_regime_weight_plan_20260702.md`](us_regime_weight_plan_20260702.md) (Track M —
> 미국장 유사일 레짐. **본 문서 V1 완료가 선행 조건**).

## 0. 목표 / 비목표

- **목표**: 토스 분봉(200종목, 2017-01-02~2025-06-16, 11GB)으로 실운영 봇을 재현하는 리플레이
  백테스터를 완성하고, gate2 v2 가중치(`ScoreV2Artifact`)를 walk-forward 탐색해 OOS 수익 최대
  후보를 선정한다. 런타임 무변경(score_v1 유지). 산출물 = artifact JSON + 리포트.
- **비목표**: 런타임 score_v2 전환(별도 게이트), 토스 fetcher 개선, 초 단위 시뮬, 미국주식 트랙.

## 1. 실행 공통 규칙 (전 슬라이스)

- `.venv/bin/python -m pytest` (Python 3.13). 브로커/토스/KIS API 호출 금지(수집 완료 상태).
- **환경 가드(리뷰 발견 #4)**: `scan_target_symbols`는 분기 이전에 `build_buy_scan_quote_context`
  (service.py:649)를 무조건 호출하며, `BUY_SCAN_QUOTE_KIS_ENV`가 설정돼 있으면
  `issue_access_token_for`(quote_account.py:186-199)로 **네트워크+실자격 토큰 발급**이 일어난다.
  리플레이/레코드 프로세스는 반드시 `BUY_SCAN_QUOTE_KIS_ENV`를 **unset/빈값**으로 실행
  (→ `execution_account` 모드, 무네트워크; `allow_inline_quote_fetch=False`+전량 프리페치로
  `fetch_buy_scan_price` 미도달). F2-c와 각 CLI에 monkeypatch.delenv/os.environ 가드 + 테스트 단언 필수.
- `app.main`/`run_session.sh` 실행 금지. `.env`/`.token_cache.json` 접근 금지.
- `data/` 원본 수정·삭제 금지, 전체 cat/read 금지 — 스트리밍(per-date)·head/tail만.
- 신규 코드는 `app/research/`·`app/tools/`만. **런타임 모듈이 research를 import하게 만들지 않는다.**
  research → 런타임 **순수 계산 모듈** import는 허용(scanner/service·math_models·market_data.schema·
  strategy.sell_decision·portfolio.schema). settings 전역(`get_settings`)·broker 모듈 import 금지.
- 각 슬라이스: 신규 테스트 red 확인 → 구현 → green → 슬라이스 파일 세트 100% → 커밋 1개(메시지 지정됨).
- 전체 스위트 기준선: **2,813 passed + 596 subtests, 0 failed** (2026-07-02). 슬라이스 종료마다 전체 1회.

## 2. 확정된 구조 지도 (실사 근거 — 실행자는 재조사 불필요, 인용만 검증)

### 2.1 데이터

- 원본: `data/toss_minute_raw_backfill/date=YYYY-MM-DD/symbol=XXXXXX_YYYYMMDD.csv`
  (2,072 date 디렉터리 + `_state/`(200종목 JSON) + `fetch_manifest.jsonl`).
  CSV 헤더 `datetime,open,high,low,close,volume`, 08시대 프리장 행 포함, volume=0 행 존재.
- 변환기: `app/research/ingest/minute_csv_to_parquet.py::convert_minute_csv_dir(raw_dir, out_dir)`
  (+ `build_quality_console_lines`). 커버리지: `app/research/coverage/data_coverage.py::
  scan_partitioned_cache(root)`, `build_coverage_report(...)`, `coverage_report_to_dict(...)`.

### 2.2 리플레이 기존 자산 (`app/research/replay/`)

- `minute_provider.py`: `MinuteBar(symbol, ts, open, high, low, close, volume)`(frozen),
  `MinuteObservation(symbol, ts, bar_ts, open, high, low, close, volume, day_open, day_high,
  day_low, day_cum_volume)`, `HistoricalMinuteDataProvider(bars)` —
  `observable_snapshot(symbol, t)`(ts<=t·당일만 보장), `universe_at(t)`, `volume_rank_at(t, top_n)`.
- `broker_sim.py`: `SimCostParams`, `BrokerSimulator(initial_cash, costs)` — `buy/sell/equity/
  record_equity`, `SimFill`, `SimPosition`. `sizing.py`: `score_to_budget_multiplier`, `apply_budget_caps`.

### 2.3 gate2 자산

- 런타임 승격 완료: `app/gate2/schema.py`(`ScoreV2Artifact`, `CONDITION_SCORE_NAMES` 15종,
  `default_artifact()`), `score_v2.py::weighted_gate2_score(scores, weights)`,
  `adapter.py::condition_scores_from_v1(score_components, caps)`.
- 패키지 주의(리뷰 #6): gate2는 **두 위치**가 정상 — 순수 스코어러는 `app/gate2/`(런타임 승격본),
  탐색 하니스는 `app/research/gate2/`. `app/research/gate2/schema.py`는 `app/gate2/schema.py`의
  재수출 shim이라 `ScoreV2Artifact`는 **동일 클래스**(이중 클래스 위험 없음). 이 분리를 "정리"하려
  들지 말 것.
- 탐색 하니스: `app/research/gate2/weight_search.py` —
  `EvaluationRecord(symbol, ts: str, scores: dict, forward_return_bps: float)`,
  `build_walk_forward_windows(records, train_size, test_size, step=None)` (ts 단위 윈도),
  `evaluate_candidate(records, artifact)`, `candidate_objective(evaluation, min_selected=1)`
  (= mean_selected_return_bps), `generate_coordinate_candidates(base, scales, thresholds)`,
  `walk_forward_search(records, candidates, train_size, test_size, step=None, min_selected=1)`.

### 2.4 런타임 스캔 경로와 심(seam) — **이 계획의 신뢰 핵심**

- 진입점: `app/scanner/service.py:622` `scan_target_symbols(*, settings, token, portfolio_snapshot=None,
  symbols=None, layer_by_symbol=None, price_data_by_symbol=None, allow_inline_quote_fetch=True)`
  → `SymbolAnalysisResult` 튜플(`.score_components` 포함). 상위 선정:
  `app/scanner/presentation.py:26` `select_top_candidate(results, *, min_price_krw=0)`.
- payload 파서: `app/market_data/schema.py:45` `build_market_snapshot(output)` — 읽는 키:
  `stck_shrn_iscd, stck_prpr, stck_oprc, stck_hgpr, stck_lwpr, prdy_ctrt`. **부수 채널**: 내부에서
  `app.market_data.live_snapshot.get_live_snapshot_signal(symbol)`을 호출해 `live_volume_rank/
  live_fluctuation_rank/live_volume_power_rank/combined_rank`를 스냅샷에 주입.
- **심 A — live 랭크** (⚠ 리뷰 확정 형태 — 아래가 정본): `app/market_data/live_snapshot.py:28`
  `resolve_snapshot_data_dir()`이 `LIVE_SNAPSHOT_DIR_ENV` 환경변수로 디렉터리 오버라이드 가능(C-4 P1).
  `SNAPSHOT_PATH = dir / "live_snapshot.json"`. **JSON 형태(live_snapshot.py:179-183, 276-353 실사)**:
  `source_symbols`는 심볼 키 dict가 **아니라** 순서 리스트 dict —
  `{"volume_rank": [sym,...], "fluctuation_rank": [...], "volume_power_rank": [...]}` 이고
  **랭크 = 리스트 내 1-base 위치**(`_build_rank_map`). `combined_rank`는 **최상위 `top_symbols`
  리스트**에서 온다. 최상위 필수 필드: `updated_at`(ISO-8601, `datetime.fromisoformat` 파싱 가능),
  `ttl_seconds`, `top_symbols`. `_fresh_snapshot`(:276-292)이 벽시계 나이 게이트를 통과해야 하므로
  writer는 `updated_at=datetime.now().isoformat()`으로 기록해 age>ttl의 worker-health/PID 분기
  (:218-244)에 절대 들어가지 않게 한다. **이 심은 부차가 아니라 탐색 대상 15조건 중
  `volume_rank_score`/`volume_power_score` 2개의 원천**(scoring.py:238-306 →
  adapter.py:85-86) — 형태가 틀리면 에러 없이 조용히 오염되므로 F2-a 계약 테스트는 게이트다.
- **심 B — 가격 이력**: `app/math_models/history.py` — `get_recent_symbol_price_history(symbol,
  limit=60)`은 `_cycle_snapshots_file()`( = `app/auth/account_scope.py:178
  get_cycle_snapshots_path()`)의 JSONL을 `_build_symbol_histories(limit, file_marker)`(lru_cache,
  mtime_ns 무효화)로 파싱한다. 소비자: `math_models/{technical, mean_reversion, price_dynamics,
  indicator_quality, portfolio_risk}` 전부 이 단일 함수 경유(portfolio_risk.py:73,112 확인).
  → 리플레이 심은 **한 지점**: `app.math_models.history._cycle_snapshots_file`을 리플레이 임시
  JSONL 경로로 patch(`unittest.mock.patch`, 연구 코드 한정 — §10-①의 사전 승인 항목).
  **주의(리뷰 #5)**: `_store_observation`(history.py:38)은 `_build_symbol_histories` **내부의
  중첩 함수**라 patch/직접 참조 불가 — 레코드 형태는 감싸는 루프(history.py:67-212)를 읽어 확정.
  가장 단순한 경로는 `observed_market_snapshots` 리스트(소스 `observed_cycle`): 레코드 최상위
  `timestamp` + `observed_market_snapshots: [{symbol, current_price, open_price, low_price,
  prev_day_change_pct}, ...]` 형태를 미러. mtime 갱신으로 lru_cache가 일 단위로 무효화된다.
- 매도 룰: `app/strategy/sell_decision.py::build_sell_analysis(symbol, holding_qty, average_cost,
  market_snapshot, portfolio_snapshot, settings)` — 순수(스냅샷 입력). 포트폴리오 스냅샷:
  `app/portfolio/schema.py::build_portfolio_snapshot(balance_data)` — 입력은 KIS 잔고 응답 모양
  (`output1`: `pdno/hldg_qty/pchs_avg_pric/prpr/evlu_amt/...`, `output2`: 예수금 —
  `tests/test_lane_scheduler_no_hooks.py::_balance_response`가 최소 형태의 정본).
- 런타임 score_components 기록(패리티 근거): `app/reporting/cycle_serializers.py:73,215`.
- settings 최소 필드 정본: `tests/test_lane_scheduler_no_hooks.py::_Settings`의 defaults dict —
  리플레이 `replay_settings()`는 이를 복제한 뒤 `config/regular_session.env` 값으로 핀.

### 2.5 사이클 정합 규칙 (S1에서 코드화)

실운영: 매도 감시 30s·매수 스캔 60s·SELL 우선·사이클당 매수 1건. 리플레이: t를 1분 tick으로 진행,
매 tick 매도 감시(1분 근사) → 60초 cadence(=매 tick)로 매수 스캔 → 체결은 **다음 분봉 open** +
`SimCostParams`. 장중 필터 09:00~15:30(KST) 상수 `SESSION_OPEN/CLOSE`.

## 3. 슬라이스 D1 — 백필 parquet 변환 + 품질 게이트

**파일**: 신규 `app/research/coverage/quality.py`, 신규 `app/tools/build_backfill_parquet.py`,
신규 `tests/test_backfill_quality.py`. (변환 로직은 기존 `convert_minute_csv_dir` 재사용 — 재구현 금지.)

- `quality.py` 공개 API:
  - `detect_price_jumps(daily_closes: list[tuple[str, float]], *, threshold_pct: float = 30.0)
    -> list[dict]` — 인접 거래일 |close 변화율| ≥ threshold → `{symbol?, date, prev_close, close,
    change_pct}` (비조정 액면분할/증자 후보).
  - `intraday_gap_ratio(bars_minutes: list[str], *, session_open="09:00", session_close="15:30")
    -> float` — 장중 기대 분(391) 대비 결측률.
  - `halt_spans(bars: list[tuple[str, int]]) -> list[tuple[str, str]]` — volume=0 연속 ≥ 30분 구간.
  - `build_quality_report(parquet_root, *, symbols, dates) -> dict` — 종목별
    `{jump_flags, gap_ratio_p95, halt_days}` + `exclude_recommended: list[str]`
    (기준: jump_flag ≥ 1 또는 gap_ratio_p95 > 0.2).
- CLI `build_backfill_parquet.py`: `--raw-dir data/toss_minute_raw_backfill
  --out-dir data/toss_minute_parquet_backfill --report results/gate2_backtest/quality_report.json`
  → 변환(기존 함수) 후 품질 리포트 JSON 기록. `_state`/`fetch_manifest.jsonl`은 스킵.
  **11GB 실변환 실행은 운영자** — 이 슬라이스는 코드+테스트만.
- TDD(순서대로 1개씩): ① 점프 탐지 경계(+29.9% 미탐/+30.0% 탐지, 음의 갭 동일) ② gap_ratio(391분
  만봉=0.0, 반결측=~0.5) ③ halt_spans(29분 무탐/30분 탐지) ④ 합성 CSV 2종목×2일 → CLI가 parquet과
  리포트 JSON을 산출(exclude 권고 포함).
- 커밋: `feat(research): backfill parquet CLI + minute-data quality gate (D1)`

## 4. 슬라이스 D2 — parquet provider + 일봉 롤업 (look-ahead 차단)

**파일**: 신규 `app/research/replay/parquet_provider.py`, `tests/test_parquet_provider.py`.

- `class ParquetMinuteProvider`:
  - `__init__(self, parquet_root: Path, *, symbols: tuple[str, ...] | None = None,
    session_open: str = "09:00", session_close: str = "15:30")`.
  - `load_window(self, start_date: date, end_date: date) -> None` — date-partition만 로드(pyarrow),
    장중 필터 적용, 내부에 `HistoricalMinuteDataProvider`(기존)를 구성해 위임. 슬라이딩 윈도 ≤ 5거래일
    상주(메모리 예산), 윈도 밖 접근은 ValueError.
  - `observable_snapshot / universe_at / volume_rank_at` — 기존 provider로 위임(재구현 금지).
  - `prev_close(self, symbol: str, day: date) -> float | None` — **day 이전** 마지막 거래일 종가.
  - `daily_history(self, symbol: str, day: date, n: int) -> list[dict]` — day **이전** n거래일의
    일봉 롤업 `{date, open, high, low, close, volume}` (분봉 집계). day 당일 데이터 포함 금지.
  - 일봉 롤업은 로드 시 1회 사전 계산·캐시(전 기간 일봉은 종목당 ~2,000행 — 전체 상주 허용).
- TDD: ① 합성 parquet(3종목×3일) prev_close 정확성·첫날 None ② daily_history에 당일 미포함
  (look-ahead 차단을 명시적으로 단언) ③ 장중 필터로 08시대 행 제외 ④ 윈도 밖 접근 ValueError.
- 커밋: `feat(research): parquet minute provider with daily rollups (D2)`

## 5. 슬라이스 F1 — KIS payload 합성기

**파일**: 신규 `app/research/replay/payload_synth.py`, `tests/test_payload_synth.py`.

- `def synth_quote_payload(obs: MinuteObservation, *, prev_close: float | None) -> dict | None`:
  - prev_close가 None/0 → None(스캔 제외).
  - 반환: `{"rt_cd": "0", "output": {"stck_shrn_iscd": obs.symbol, "stck_prpr": str(int(obs.close)),
    "stck_oprc": str(int(obs.day_open)), "stck_hgpr": str(int(obs.day_high)),
    "stck_lwpr": str(int(obs.day_low)), "prdy_ctrt": f"{(obs.close/prev_close-1)*100:.2f}"}}`.
  - **왕복 계약 테스트**: 합성 payload를 실제 `build_market_snapshot(payload["output"])`에 넣어
    `current_price/open_price/high_price/low_price/prev_day_change_pct`가 관측값과 일치함을 단언
    (파서 키가 바뀌면 이 테스트가 깨진다 — 그것이 목적). 이때 live 신호는 없는 환경이므로
    `live_snapshot_available is False`도 단언.
- TDD: ① 왕복 동등성 ② prev_close None → None ③ prdy_ctrt 부호(하락일 음수).
- 커밋: `feat(research): synthesize KIS quote payloads from minute observations (F1)`

## 6. 슬라이스 F2 — 스캐너 리플레이 구동기 + 패리티 게이트 ★최중요

**파일**: 신규 `app/research/replay/scan_driver.py`, `tests/test_scan_driver.py`,
신규 `tests/test_replay_parity.py`(게이트).

- **F2-a — live 랭크 심** (⚠ load-bearing — §2.4 심 A의 확정 형태를 그대로 구현):
  `class ReplayLiveSnapshotWriter(tmp_dir)` — `write(t, ordered_symbols: dict[str, list[str]])` 가
  `tmp_dir/live_snapshot.json`을 기록. JSON 최상위: `updated_at=datetime.now().isoformat()`,
  `ttl_seconds`(넉넉히 3600), `top_symbols`(combined 순위 리스트),
  `source_symbols={"volume_rank": [...], "fluctuation_rank": [...], "volume_power_rank": [...]}`
  (**랭크 = 리스트 1-base 위치**). 리스트 값: `volume_rank_at(t, 200)` 순서를 세 리스트와
  `top_symbols`에 동일 적용(프록시 한계는 §9 명시).
  **리다이렉트 방식(3차 리뷰 — F1 실행 실사 확정)**: `SNAPSHOT_PATH`는 **import 시 1회 해석**
  (live_snapshot.py:36)되고 `load_live_snapshot`이 호출 시 모듈 전역으로 읽으므로(:296),
  warm 프로세스에서 `monkeypatch.setenv`만으로는 리다이렉트되지 **않는다** — 방치 시 실기기
  `data/live_snapshot.json`을 조용히 읽는 오염 경로. 따라서 테스트·드라이버 공통으로 **양쪽 병행**:
  ① `LIVE_SNAPSHOT_DIR_ENV`를 tmp로 setenv(의도 문서화·fresh-import 대비) +
  ② `live_snapshot.SNAPSHOT_PATH`를 `tmp/live_snapshot.json`으로 redirect
  (테스트는 `monkeypatch.setattr`, 드라이버는 `unittest.mock.patch.object`를 드라이버 수명 동안
  유지하는 컨텍스트 매니저 — `tests/test_payload_synth.py::_isolate_live_snapshot`이 선례).
  계약 테스트(게이트): 위 병행 리다이렉트 → writer 기록 → 실제
  `get_live_snapshot_signal("005930")`이 기대 랭크(리스트 위치)를 반환하고, 리스트에 없는 심볼은
  None/무랭크 처리됨을 단언. 이 심이 틀리면 `volume_rank_score`/`volume_power_score`가 무에러로
  오염된다 — 계약 테스트 통과 없이는 F2-c 진행 금지.
- **F2-b — 가격 이력 심** (⚠ 2차 리뷰 F-1 반영 — **시간 스케일이 본질**):
  런타임 이력은 일봉이 아니라 **사이클(≈1분) 단위 관측 시계열**이다 — `_build_symbol_histories`는
  `records[-limit:]`(limit=**레코드 수**; technical 3000, mean_reversion/price_dynamics 30~90)를
  파싱하고, 장중 런타임 레코드는 60초 간격으로 쌓인다. 따라서 ReplayHistoryWriter는 일봉 롤업이
  아니라 **리플레이 tick(1분)마다 그 tick의 관측 레코드를 append**한다:
  레코드 = 최상위 `timestamp`(ISO, tick 시각) + `observed_market_snapshots: [{symbol,
  current_price, open_price, low_price, prev_day_change_pct}, ...]` (history.py:182-212 실사 —
  이 소스는 **flat dict**가 맞음; scanner_candidates_top류는 nested라 쓰지 않는다).
  - 기록 대상 심볼: 런타임 밀도를 모사해 **그 tick의 gate1 통과 상위 N(기본 40) + sim 보유 심볼**만
    (전 200종목 기록 금지 — 런타임보다 조밀한 이력은 fidelity 델타; §5에 명시, 패리티 ±5로 정량화).
  - 파일 수명: 날 시작 시 전일 파일의 **tail 3000레코드로 reseed**(technical limit 커버), 이후 append.
  - 성능 노트: append마다 mtime 변경 → lru_cache((limit, marker) 키, maxsize=8) 무효화로 tick당
    재파싱 발생, limit별(3000/30/90) 엔트리 분리로 다중 파싱. `history_refresh_ticks` 파라미터
    (기본 1)로 갱신 주기를 조절 가능하게 하고, 프로파일 결과 tick당 비용이 과하면 §10-⑤로 에스컬레이션.
  계약 테스트: `mock.patch("app.math_models.history._cycle_snapshots_file",
  return_value=tmp_jsonl)` + 분 단위 레코드 3개 append 후 실제
  `get_recent_symbol_price_history("005930")`이 **분 간격 시계열**을 시간순 반환; reseed 후
  전일 tail이 유지됨을 단언.
- **F2-c — 구동기**: `def run_scan_at(*, t: datetime, provider: ParquetMinuteProvider,
  sim: BrokerSimulator, settings, history_writer, live_writer) -> tuple[SymbolAnalysisResult, ...]`
  ① universe = `provider.universe_at(t)` ② payload dict 합성(F1) ③ live_writer.write(t, 랭크)
  ④ history_writer는 **스캔 후(post-scan)에 이번 tick 관측을 append**(F2-b per-tick 규칙과 정합 —
  스캔 시점 이력은 t-1까지만 보이는 런타임 의미론 유지; 일 경계에서는 reseed) ⑤ sim 보유 →
  `_balance_response` 모양 dict →
  `build_portfolio_snapshot` ⑥ **실제** `scan_target_symbols(settings=..., token="replay",
  portfolio_snapshot=..., symbols=universe, price_data_by_symbol=payloads,
  allow_inline_quote_fetch=False)` 호출·반환.
  - `replay_settings()` 팩토리 동봉: `tests/test_lane_scheduler_no_hooks.py::_Settings`의 defaults를
    **복제**(테스트 헬퍼 import 금지 — research가 tests를 import하지 않는다; `_balance_response`
    모양도 동일하게 research 코드에 재작성) + `config/regular_session.env` 값 핀(핀 대상 키와 값을
    코드 상수로 명기).
  - **환경 가드(§1 리뷰 #4)**: `run_scan_at` 진입부에서 `os.environ.get("BUY_SCAN_QUOTE_KIS_ENV")`가
    truthy면 `RuntimeError("replay requires BUY_SCAN_QUOTE_KIS_ENV unset")` — 테스트로 단언.
- **F2-d — 패리티 게이트** (`tests/test_replay_parity.py`):
  최근 실거래일 1일을 골라 실계정 cycle snapshots JSONL에서 **장중(09:00~15:30 KST) 사이클이면서
  `scanner_candidates_top`이 비어 있지 않은 행**을 앞에서부터 M(≤50)개 수집(리뷰 #3: 파일 끝은
  장외 사이클이라 tail은 빈 후보만 잡힘; 스트리밍으로 조건 행만 취하고 전체 상주 금지).
  후보별 컴포넌트 위치: `scanner_candidates_top[i].score_components`. 같은 시각 리플레이 재구성
  값과 대조.
  허용 오차: strength 7종·gap/range류 **정확 일치**, 이력 의존류(trend/macd/mean_reversion/
  velocity)·portfolio류 **±5 이내 또는 불일치 사유 표**. 픽스처 경로(2차 리뷰 F-4):
  계정 스코프 해석(`get_cycle_snapshots_path()` — base_url 필요)을 테스트 환경에서 재현하지 말고,
  **운영자가 env `GATE2_PARITY_SNAPSHOT_PATH`로 실제 JSONL 경로를 지정**;
  `@pytest.mark.skipif(not os.getenv("GATE2_PARITY_SNAPSHOT_PATH"))`로 CI에서는 스킵.
  운영자 머신에서 1회 실행해 결과 표를 이 문서 아래 "패리티 결과" 절에 기록하는 것까지가
  **R1 착수 조건**.
- TDD 순서: F2-a 계약 → F2-b 계약 → F2-c(합성 시나리오: 눌림목 상승 종목이 candidate=True로 선정)
  → F2-d. 각 1개씩.
- 커밋: `feat(research): replay scan driver over real scanner with store seams (F2)`

## 7. 슬라이스 R1 — EvaluationRecord runner / W2 — 탐색 강화 / S1 — 전체 리플레이 / V1 — 선정

### R1 (파일: `app/research/replay/record_runner.py`, `app/tools/build_gate2_records.py`,
`tests/test_record_runner.py`)

- `def build_records(*, provider, dates: list[date], horizons_min=(10, 30, 60),
  cost_roundtrip_bps: float, settings) -> list[EvaluationRecord]`:
  매 거래일 09:01~15:30을 1분 tick으로 진행(빈 sim으로 — 레코드 단계는 포트폴리오 무상태.
  **결과(2차 리뷰 F-3)**: 15조건 중 `portfolio_diversification_score`/`volatility_risk_score`는
  빈 포트폴리오에서 항상 중립/None → 레코드 기반 Stage A에서 이 2개의 가중치 신호는 무의미하다.
  Stage A 후보 생성기는 이 2개 키를 **baseline 값으로 동결**(탐색 차원 13개)하고, 두 조건의
  차별화 평가는 포트폴리오 상태가 실재하는 Stage B(S1)에서만 이뤄진다 — W2·M-C1에 동일 적용),
  각 tick에서 `run_scan_at` → **gate1 통과 후보 전원**에 대해
  `scores = condition_scores_from_v1(result.score_components, default_artifact().normalization_caps)`.
  라벨(리뷰 #8 — 방향 명시): `entry = t+1분 분봉의 open`, `exit = t+1분+h 분봉의 open`,
  `forward_return_bps = (exit / entry - 1) * 10_000 - cost_roundtrip_bps`.
  exit 시점이 장마감 이후면 당일 마지막 분봉 close를 exit으로 절단하고 `truncated=True` 기록.
  **볼륨 추정(리뷰 #9)**: 후보 밀도에 따라 시간당 수만 행 — horizon당 수천만 행까지 가능.
  월 단위 chunk parquet로 쓰고, 하류(W2/M-C1)는 **필요 날짜로 parquet 필터 후** DataFrame화
  (전 기간 프레임 상주 금지 — pyarrow filter/predicate 사용).
- 출력: `results/gate2_backtest/records_<start>_<end>_h<h>.parquet`
  (컬럼: symbol, ts, 15개 score 컬럼, forward_return_bps, truncated).
- CLI: 기간·심볼 수·horizon 인자, 월 단위 chunk 스트리밍, 진행 로그 10일마다.
- TDD: ① 2종목×1일 합성으로 레코드 수 = tick×후보 수 ② 라벨 수기 검증(비용 차감 포함)
  ③ 마감 절단 케이스 ④ parquet 라운드트립.
- 커밋: `feat(research): gate2 evaluation record runner + CLI (R1)`

### W2 (파일: `app/research/gate2/weight_search.py` 확장, `tests/test_weight_search_v2.py` 신규)

- 추가(기존 함수 시그니처 불변 — 신규 함수로):
  - `build_daily_walk_forward_windows(records, train_days, test_days, step_days=None)` — ts의
    날짜부(YYYY-MM-DD) 단위 윈도(기존 ts-단위 빌더는 그대로 둠).
  - `candidate_objective_v2(evaluation, *, mode: str, min_selected: int = 30, dd_lambda: float = 0.5)`
    — mode ∈ {"mean", "total", "dd_adjusted"}; dd_adjusted = total - λ·(선택 수익 시퀀스의 누적합
    최대 낙폭 프록시). min_selected 미달 → -inf.
  - `generate_random_candidates(base, n, seed, *, weight_low=0.0, weight_high=1.5,
    thresholds=(55, 60, 65), frozen_keys=("portfolio_diversification_score",
    "volatility_risk_score"))` — seed 고정 재현. **frozen_keys는 base 값으로 고정**(2차 리뷰 F-3:
    레코드 단계에서 포트폴리오 조건 2종은 무신호 — Stage A 탐색 차원은 13개).
    coordinate 후보 사용 시에도 동일 키를 스킵.
- TDD: ① 일 단위 윈도 경계(훈련/시험 날짜 겹침 없음) ② random seed 재현성 ③ dd_adjusted 수기 검증
  ④ frozen_keys가 모든 후보에서 base와 동일함.
- 커밋: `feat(research): daily windows + objective/candidate extensions for weight search (W2)`

### S1 (파일: `app/research/replay/sim_runner.py`, `tests/test_sim_runner.py`)

- `def run_portfolio_replay(*, artifact: ScoreV2Artifact, provider, dates, settings,
  costs: SimCostParams, initial_cash: float) -> SimReplayReport`:
  §2.5 규칙 그대로 — 매 tick: ① 보유 종목별 관측 스냅샷 → `build_sell_analysis` → 트리거 시
  다음 분봉 open으로 `sim.sell`(SELL 우선) ② 매수: `run_scan_at` → gate1 후보에
  `weighted_gate2_score` ≥ threshold 인 상위 1종목(동점 시 심볼 오름차순) →
  `score_to_budget_multiplier`+`apply_budget_caps` 사이징 → 다음 분봉 open으로 `sim.buy`
  (사이클당 매수 1건) ③ `sim.record_equity`. 장마감 시 강제 청산 없음(오버나이트 보유 = 실운영 동일).
- **매수 자격 의미론(S1 구현 확정 — Fable 검토 수용)**: ① 대상은 `result.candidate is True`
  (v1 매수 판정 불변 — §0 "런타임 무변경/score_v1 유지"와 정합; gate2는 v1 후보 위의 추가
  임계/재랭킹 계층이며 v1이 기각한 종목을 구제하지 않는다. Stage B 결과는 이 보수적 계층
  해석으로 읽을 것) ② 보유 중 심볼은 매수 선정에서 스킵(실운영 buy_flow의 보유/재매수 가드
  미러 — 스킵 없으면 rebuy_cooldown=0 설정에서 매 tick 동일 종목 피라미딩 퇴화).
- `SimReplayReport`: total_return_pct, mdd_pct, trade_count, win_rate(**트레이드별 실현손익
  기준** — 심볼 누적 아님), equity_curve(일 단위 다운샘플),
  per_symbol_pnl 상위/하위 5. JSON 직렬화 `to_dict()`.
- 보수 모드 옵션 `intrabar_mode="stop_first"`: 한 분봉에서 손절가·익절가 동시 관통 시 손절 우선 판정.
- TDD: ① 손절 트리거 수기 시나리오(다음 분봉 open 체결·비용 반영 equity 수기 일치) ② 사이클당
  매수 1건 제약 ③ stop_first 동시 관통 케이스 ④ report to_dict 스키마.
- 커밋: `feat(research): full portfolio minute replay for finalist artifacts (S1)`

### V1 (파일: `app/tools/run_gate2_weight_search.py`, `tests/test_weight_search_cli.py`)

- 오케스트레이션: ① records parquet 로드(기간 인자) ② **튜닝 2023-01-02~2025-03-31 /
  홀드아웃 2025-04-01~2025-06-16(탐색 절대 미사용, 상수로 명기)** ③ Stage A:
  coordinate+random(기본 n=64, seed=20260702) × `build_daily_walk_forward_windows(60, 20)` ×
  objective dd_adjusted ④ 상위 5 artifact → Stage B `run_portfolio_replay`(2023+) ⑤ 홀드아웃
  성적(Stage A 레코드 평가 + Stage B 리플레이 둘 다) ⑥ `results/gate2_backtest/final_report.md` +
  선정 artifact `app/gate2/artifacts/score_v2_w1_candidate.json` 저장(**적용은 운영자**).
- 리포트 필수: baseline(`default_artifact`) 동일 지표 병기, 윈도별 승률, 선택 수 분포, §9 리스크 전문 수록,
  "홀드아웃에서 baseline 이하이면 채택 불가" 판정 줄.
- TDD: 합성 records로 CLI end-to-end(파일 산출·홀드아웃 분리 단언 — 홀드아웃 날짜가 탐색 입력에
  포함되면 실패하는 단언 포함).
- 커밋: `feat(research): gate2 weight search orchestration CLI + final report (V1)`

## 8. 실행 순서·병행

D1 → D2 → F1 → F2(**패리티 게이트 통과가 R1 착수 조건**) → R1 → W2 → S1 → V1.
W2는 R1과 논리 독립이나 tdd_guard 직렬 권장. 운영자 배치 2회: D1 변환(11GB), R1 레코드 생산.

## 9. 리스크·한계 (final_report에 전문 수록)

생존편향(현 200종목 고정 → 튜닝은 2023+ 중심) / 비조정 가격(D1 게이트로 제외) / 분 내부 경로 미상
(S1 stop_first 보수 모드) / 매도 감시 30s→1분 근사 / 체결=다음 분봉 open+고정 bps(호가·부분체결 없음) /
volume rank = 200종목 내 프록시(전시장 아님; power/fluctuation 랭크도 동일 프록시) / 운영 노이즈
(rate-limit 백오프·부분평가·잔고 실패) 미모델 → 리플레이는 실물 대비 낙관 / 과최적화 방어 =
walk-forward + 홀드아웃 + baseline 판정. **목적은 후보 간 상대 비교** — 절대 수익 예측 아님.

## 10. 에스컬레이션 규칙

- ① `mock.patch` 심 2개는 **사전 승인**: ⓐ `history._cycle_snapshots_file`,
  ⓑ live 랭크 = `live_snapshot.SNAPSHOT_PATH` 경로 상수 redirect + `LIVE_SNAPSHOT_DIR_ENV`
  setenv **병행**(3차 리뷰: env 단독은 import-시점 해석 때문에 warm 프로세스에서 무효 —
  live_snapshot.py:36·:296 실사; 함수 `get_live_snapshot_signal` patch는 차선 폴백).
  그 외 런타임 모듈 patch가 필요해 보이면 중단·보고. 런타임 모듈 **수정**은 전면 금지(발견 즉시 중단).
- ② 패리티 게이트에서 허용 오차 초과 컴포넌트 → 임의 보정 금지, 표로 보고 후 대기.
- ③ `scan_target_symbols`가 명시된 인자만으로 replay 불가한 추가 부수 채널을 갖고 있음이 드러나면
  (예: 또 다른 전역 저장소) 중단·보고.
- ④ 기존 테스트와 충돌 시 수정 말고 보고. ⑤ 성능이 예산(레코드 생산 월 chunk 당 ≤ 30분 목표) 초과 시
  최적화 임의 착수 말고 측정치와 함께 보고.

## 패리티 결과 (F2-d 실행 후 기입)

_기입: 2026-07-03. 운영자 실행 2026-07-03 00:35(F2-d 1회) — source `data/cycle_snapshots_mock_acct_0123456789abcdef.jsonl`, 대상일 2026-06-30. 전체 표는 `results/gate2_backtest/parity_table.txt`(433줄)._

| 항목 | 값 |
|------|-----|
| compared_candidates | 599 |
| exact_mismatches (하드 게이트) | **0 — PASS** |
| tolerance_exceeded (±5, 허용표) | 429 |
| 429건 카테고리 분포 (전수 집계) | `live_volume_power_rank_strength_score` 235건 / `live_volume_rank_strength_score` 194건 — 두 카테고리가 전부이며 모두 live=100.0 vs replay=0.0 |

±5 초과분은 전수가 live-rank 2개 컴포넌트로, 리플레이가 store-free로 재구성할 수 없는 범주(F2-d 설계의 허용표 대상)다. §10-② 원칙대로 보정 없이 표로만 기록한다. exact-match 범주(패턴 강도 등 순수함수 스코어)는 599개 후보 전체에서 불일치 0건.
