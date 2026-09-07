# R7: backtester 구획 슬리밍 계획 (2026-06-12)

> **실행 위임 문서** (R5와 동일 체계). 구조 지도·슬라이스 경계·TDD 사양은 상위 모델(Fable)이 확정했고,
> TDD 실행은 하위 모델(Claude sonnet) 실행자가 수행한다. **트랙 2개를 워크트리 격리로 병렬 위임**하고,
> 완료 후 상위 모델이 메인 브랜치에 cherry-pick + 통합 검증 게이트를 실행한다.
> 판단이 필요한 상황은 전부 §8 에스컬레이션 — 임의 판단 금지.
> BASE = `c412c9c` (각 워크트리의 분기점이자 모든 AST 비교 기준).

## 1. 목표와 비목표

- **목표**: `backtester/engine_backtest/` 850줄+ 2개 파일(parity.py 1,017줄, runner.py 908줄)에서
  순수 클러스터를 verbatim 이동 + facade로 분리. 무커버 분석/샘플링 로직에 직접 행위 테스트 확보.
- **비목표**: 동작 변경 일절 금지. 800줄 미만 파일(parity_score_pipeline 686, cli 582 등)은 비대상.
  `__init__.py` 공개 API(`run_backtest`/`BacktestResult`/`build_parity_report` 등)는 전부 기존 경로 유지.
- **리스크 등급: 전 슬라이스 LOW.** backtester는 오프라인 리서치 인프라 — 브로커 호출 없음,
  트레이딩-크리티컬 표면(`app/execution|risk|strategy/`) 아님(읽기 import만 존재, 해당 모듈 무수정).

## 2. 구조 지도

### 2.1 타깃 인벤토리

| 파일 | 줄수 | 기존 테스트 | 외부 소비자 (facade 보존 필수) |
|------|------|------------|------------|
| runner.py | 908 | test_backtest_runner.py(281줄, run_backtest 실통합), test_ai_runner_integration.py(_run_buy_pass + **runner 네임스페이스 patch**) | `BacktestResult`: metrics/report/evaluator/experiments.runner/`__init__`/테스트 4종 · `DayRecord`: parity, parity_score_pipeline, test_backtest_metrics, test_ai_runner_integration · `run_backtest`: cli(함수내 import 3곳)/`__init__`/experiments · `_run_buy_pass`/`_run_sell_pass`: parity, parity_score_pipeline · `collect_buy_pass_samples`: parity_score_pipeline |
| parity.py | 1,017 | test_engine_backtest_parity.py(232줄 — **parity 네임스페이스에 6심볼 mock.patch**: `_candidate_outcomes_path`/`_cycle_stats_path`/`_cycle_snapshots_path`/`_orders_path`/`_load_signal_rows`/`_run_proxy_replay`) | `build_parity_report`: `__init__`/cli/parity_batch · **private 10종을 parity_score_pipeline이 직접 import**: `_build_provider_from_signal_rows`, `_cycle_snapshots_path`, `_filter_snapshots_for_day`, `_load_signal_rows`, `_normalize_date`, `_pick_latest_symbol_rows`, `_read_jsonl`, `_resolve_initial_cash`, `_seed_portfolio_from_snapshot`, `_select_seed_snapshot` |

### 2.2 교차-patch 제약 (슬라이스 경계를 결정한 하드 제약)

1. **`_run_buy_pass`/`_run_sell_pass`/`_emit_approved_feature_records`는 runner.py 이동 금지.**
   test_ai_runner_integration.py가 `patch.object(runner_mod, "evaluate_buy_decision"/"calculate_selection_score"/"calculate_position_sizing")`로
   runner 모듈 전역을 교체한 뒤 `_run_buy_pass`를 직접 호출한다. 이동 시 함수가 새 모듈 전역에서
   심볼을 해석해 patch가 무효 → 기존 테스트 red.
2. **parity의 patch 6심볼은 "호출자 잔존" 조건으로만 이동 가능.** 전부 `build_parity_report`(잔존)가
   호출하므로, 이동해도 mock.patch가 parity 모듈 attr(facade 바인딩)를 교체하면 잔존 호출자에 유효.
   단 **이동 함수끼리 patch 대상을 내부 호출하는 경로가 생기면 안 됨** — `_run_proxy_replay`는
   다른 이동 함수가 호출하지 않음(검증 완료).
3. `collect_buy_pass_samples`/`_max_positions`는 patch하는 테스트 없음(전수 grep) → 이동 가능.
   `_max_positions`는 잔존 `_run_buy_pass`도 사용 → runner가 facade 바인딩으로 사용(무해).

### 2.3 의존 방향 (전 구간 단방향)

신규 모듈은 자신의 원본을 절대 import하지 않는다.
`records.py`/`sampling.py` ← runner.py(facade) ← parity/metrics/report/cli.
`parity_summary.py` ← `parity_proxy.py`(파서 3종 사용) ← parity.py(facade).
`parity_proxy.py` → runner(DayRecord/_run_buy_pass/_run_sell_pass)는 기존 facade 경유로 안정.

## 3. 슬라이스 계획 — 트랙 2개 병렬, 트랙 내 순차

**Track A = runner.py** (워크트리 1, A1→A2 순차 2커밋) / **Track B = parity.py** (워크트리 2, B1→B2 순차 2커밋).
두 트랙은 파일이 겹치지 않아 병렬 실행 가능. B2가 runner에서 import하는 심볼은 facade로 안정적이라
Track A 결과와 무관.

### A1 — 결과 dataclass → `backtester/engine_backtest/records.py` (캘리브레이션)

- **이동 심볼 (2종, verbatim)**: `DayRecord`(39-101), `BacktestResult`(104-120)
- **신규 모듈 import**: `from __future__ import annotations`, `dataclass, field`, `date`,
  `from backtester.engine_backtest.portfolio import ClosedTrade`
- **facade**: runner.py에 `from backtester.engine_backtest.records import BacktestResult, DayRecord`
- **스테일 import 정리(같은 커밋)**: runner의 `dataclass, field` import 제거,
  `from ...portfolio import BacktestPortfolio, ClosedTrade`에서 `ClosedTrade` 제거(이동 후 미사용)
- **행위 테스트** (`tests/test_backtest_records.py`): 핀 2종(assertIs) +
  `DayRecord.to_dict()` 기본값/리스트 채운 2케이스 전체 dict 동등성(손으로 쓴 리터럴) +
  `BacktestResult.total_return_pct`(정상 + `initial_cash=0`→0.0) + `n_trades`(실제 ClosedTrade 인스턴스 사용, mock 금지)

### A2 — 샘플링 → `backtester/engine_backtest/sampling.py`

- **이동 심볼 (2종, verbatim)**: `collect_buy_pass_samples`(682-877, 내부 `_base_sample` 포함 통째),
  `_max_positions`(882-908)
- **신규 모듈 import**: `from __future__ import annotations`, `date`, `Any`,
  `calculate_position_sizing`(app.execution.position_sizing), `evaluate_buy_decision`(app.strategy.buy_decision),
  `calculate_selection_score`(app.scanner.service),
  `BacktestDataProvider`, `BacktestPortfolio`
- **facade**: runner.py에 `from backtester.engine_backtest.sampling import collect_buy_pass_samples, _max_positions`
  — 잔존 `_run_buy_pass`가 `_max_positions`를 facade 바인딩으로 호출(테스트 patch 없음 — 안전 확인 완료)
- **폐쇄 근거**: collect_buy_pass_samples는 DayRecord/logger/_BUY_RULE_NAMES/evaluate_sell_decision 미사용(검증 완료)
- **행위 테스트** (`tests/test_backtest_sampling.py`): 핀 2종 + `_max_positions` 직접 어서션
  (exposure 한도, budget 한도, 둘 중 타이트한 쪽, `exposure_pct<=0`→10, portfolio_value 미지정 폴백) +
  `collect_buy_pass_samples` **전략 함수 호출 전 결정 분기만** 전체 dict 동등성:
  max_positions 도달→`[]`, `already_bought_today`/`already_holding`/`missing_snapshot` stage 3종 —
  settings는 SimpleNamespace(`buy_min_score`/`qty`/`buy_max_account_exposure_pct`/`buy_max_budget_per_trade_krw`만 주입)로
  기대 dict를 손으로 완전 도출. rule 이후 stage는 기존 test_engine_backtest_score_pipeline가 실통합 커버.

### B1 — parity 요약/분류 → `backtester/engine_backtest/parity_summary.py`

- **이동 심볼 (17종, verbatim)**: `_normalize_date`(40-46), `_parse_ts`(49-56), `_bool`(59-64),
  `_int`(67-73), `_float`(76-82), `_sum_count_dicts`(85-96), `_collect_unique_list_values`(99-109),
  `_last_dict_value`(112-117), `_max_count_entry`(120-128), `_build_rule_failure_summary`(131-163),
  `_build_buy_diagnostics`(166-246), `_enrich_engine_summary`(249-268), `_unique_symbols`(356-363),
  `_summarize_live_reference`(366-432), `_summarize_engine_report`(712-768), `_overlap`(771-782),
  `_classify_parity`(785-859)
- **신규 모듈 import**: `from __future__ import annotations`, `json`, `Counter`, `date, datetime`, `Path`, `Any`
- **facade**: parity.py에 17종 전부 재수출 — parity_score_pipeline이 `_normalize_date` 직접 import,
  잔존 `build_parity_report`가 6종 사용
- **스테일 import 정리(같은 커밋)**: parity의 `from collections import Counter` 제거(이동 후 잔존 무사용)
- **행위 테스트** (`tests/test_parity_summary.py`): 핀 17종 + 소형 파서 직접 어서션
  (`_normalize_date` 8자리/ISO 2건, `_parse_ts` Z-suffix/invalid, `_bool`/`_int`/`_float` 경계,
  `_sum_count_dicts`/`_collect_unique_list_values`/`_last_dict_value`/`_max_count_entry`/`_overlap`(jaccard 손계산)) +
  `_build_rule_failure_summary` 반환 3-튜플 전체 동등성 1건 +
  `_build_buy_diagnostics` 대표 분기 3건(capacity_blocked/rule_blocked(dominant 포함)/executed_buy) 전체 dict 동등성 +
  `_classify_parity` 2건(high 도달 + proxy 모드 medium 강등 분기) 레벨/노트 어서션 +
  `_summarize_live_reference` 최소 rows 픽스처 전체 dict 동등성 +
  `_summarize_engine_report` tmp_path에 손으로 쓴 report json → 전체 dict 동등성

### B2 — parity proxy replay → `backtester/engine_backtest/parity_proxy.py`

- **이동 심볼 (7종, verbatim)**: `_pick_latest_symbol_rows`(470-486), `_estimate_prev_close`(489-494),
  `_build_provider_from_signal_rows`(497-554), `_select_seed_snapshot`(557-566),
  `_seed_portfolio_from_snapshot`(569-599), `_resolve_initial_cash`(602-620), `_run_proxy_replay`(623-709)
- **신규 모듈 import**: `from __future__ import annotations`, `date, datetime`, `Any`,
  `BacktestDataProvider`, `BacktestPortfolio`,
  `from backtester.engine_backtest.runner import DayRecord, _run_buy_pass, _run_sell_pass`,
  `make_settings`, `make_backtest_state, reset_daily_state`,
  `from backtester.engine_backtest.parity_summary import _float, _int, _parse_ts`
- **facade**: parity.py에 7종 전부 재수출 — parity_score_pipeline이 5종 직접 import
  (`_build_provider_from_signal_rows`/`_pick_latest_symbol_rows`/`_resolve_initial_cash`/`_seed_portfolio_from_snapshot`/`_select_seed_snapshot`),
  기존 테스트가 `parity._run_proxy_replay`를 patch(호출자 잔존이라 유효 — §2.2-2)
- **잔존**: `_DEFAULT_SESSION`, `_KNOWN_APPROXIMATIONS`, `_NOT_YET_MODELED`, `_read_jsonl`, `_read_csv`,
  경로 5종, `_session_matches`, `_filter_snapshots_for_day`, `_load_signal_rows`, `build_parity_report`.
  1,017 → ~380줄
- **스테일 import 정리(같은 커밋)**: parity의 `from datetime import date, datetime`에서 `date` 제거
  (잔존은 `datetime.now()`만 사용 — AST 스캔으로 확인 후)
- **행위 테스트** (`tests/test_parity_proxy.py`): 핀 7종 +
  `_estimate_prev_close`(정상/분모≈0→0/음수 결과→0) + `_pick_latest_symbol_rows`(최신 ts 승자/가격 결측 제외/심볼 정렬) +
  `_resolve_initial_cash`(fallback 우선/seed `cash_krw`/holdings 키/기본 10_000_000) +
  `_select_seed_snapshot`(positions 보유 우선/빈 리스트→None) +
  `_seed_portfolio_from_snapshot`(실 BacktestPortfolio에 시드 후 positions 상태 어서션, qty<=0 스킵 분기) +
  `_build_provider_from_signal_rows`(records 변환 + prices dict 동등성 + seed 보유 종목 추가 분기) —
  전부 손으로 쓴 픽스처. `_run_proxy_replay`는 결정적 필드만(mode/initial_cash/seeded_position_count/키 집합) 어서션하는
  소형 실통합 1건(make_settings 사용, 브로커/파일 IO 없음 — rows 인자로 주입)

## 4. TDD 실행 절차 (슬라이스 공통 — R5 §4와 동일)

1. 핀 테스트 1개 → red(ImportError) → 빈 모듈 생성 → AttributeError.
2. 스텁 추가 (일괄 거부 시 1개씩).
3. 행위 테스트 함수별 1개씩 red → **verbatim 채움 (의미가 동일해도 문자 그대로 복사 — 삼항식→if 등 동치 재작성 금지)**.
   NameError red 근거로 import 추가.
4. 원본 로컬 정의 → facade import 교체. **죽은 코드 포함 어떤 잔존 심볼도 삭제 금지.**
5. 기존 테스트 무수정 green 확인 (red면 §8-7).
6. §6 검증 프로토콜 실행.
7. **원본 스테일 import 정리**: 이동분만 쓰던 import를 AST 스캔으로 확인 후 같은 커밋에서 제거
   (각 슬라이스 명세에 사전 분석된 목록 있음 — 그 외 발견 시 §8-6). facade 재수출 import는 의도적 — 제거 금지.
8. 전부 green이면 경로 지정 커밋 (슬라이스당 1커밋).

## 5. 병렬 실행/통합 프로토콜 (R7 신규 — 워크트리 격리)

- 실행자 2명(sonnet)이 각각 **자기 git worktree**(BASE c412c9c에서 분기)에서 트랙을 수행.
  워크트리에는 `.venv`가 없으므로 **반드시 `$KIS_TRADER_ROOT/.venv/bin/python -m pytest`** 사용.
- 워크트리는 NOT-mine 수정분이 없는 클린 체크아웃 — 그래도 NOT-mine 경로는 무접촉 원칙 유지.
- 완료 보고: 슬라이스별 커밋 해시 + 워크트리 경로. 상위 모델이 메인 브랜치에 A1→A2→B1→B2 순서로
  cherry-pick 후 **통합 게이트**(AST 전수 + 위생 + 테스트 품질 + 전체 스위트, 4에이전트) 실행.
- 워크트리 내 전체 스위트 기준선: BASE c412c9c 기준 **1,933 passed + 245 subtests** + 자기 슬라이스 신규 테스트.

## 6. 슬라이스 공통 검증 프로토콜 (결정적)

(1) AST verbatim — Python heredoc(`$KIS_TRADER_ROOT/.venv/bin/python - <<'EOF'`)으로
`git show c412c9c:<원본>` 대비: 이동분 동일(`ast.dump(include_attributes=False)`), 잔존분 무변경,
사라짐 0, 임의추가 0. (2) DAG — 신규 모듈에 원본 모듈명 문자열 없음. (3) facade 동일성 one-liner 전수.
(4) 전체 스위트 green. (5) 경로 지정 `git add` + 커밋 메시지
`refactor(backtester): extract <클러스터> to <모듈> (R7-<슬라이스>)` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## 7. 안전 제약 (위반 시 즉시 중단 — R5와 동일)

- `.env`/`.token_cache.json` 접근 금지 (Bash 점 리터럴 regex는 trading_guard 오탐 — Python heredoc 사용).
- `data/`, `logs/`, `results/`, `archive/` 전체 read 금지. `app.main`/`run_session.sh` 실행 금지, 브로커 API 호출 금지.
- `app/execution|risk|strategy|auth/`, `app/main.py`, `scripts/*.sh` 무수정 (import만 허용).
- NOT-mine 워킹트리 파일 수정/커밋 금지 (R5 §7 목록과 동일).
- 코드 커밋과 docs 커밋 분리. 자기 슬라이스의 3개 파일(신규 모듈/원본/신규 테스트) 외 수정 금지.

## 8. 에스컬레이션 (보고 후 정지 — R5와 동일 + 1)

1. 폐쇄 위반 발견. 2. 가드 같은 편집 3회+ 거부. 3. 스위트에서 슬라이스 무관 실패. 4. 코드가 문서와 다름(코드가 진실).
5. NOT-mine 접촉 필요. 6. verbatim DIFF가 명시 import로 해소 안 됨. 7. 기존 테스트 red(facade/patch 호환성 신호).
8. **워크트리 환경 문제**(venv/훅/pytest 플러그인 오작동)는 우회 시도 말고 보고.

## 9. 진행 상황

| 슬라이스 | 상태 | 커밋 (메인 브랜치) | 비고 |
|----------|------|------|------|
| A1 records | 완료 | be3dad3 (워크트리 d99faf3 cherry-pick) | Track A. runner 908→825줄 |
| A2 sampling | 완료 | a8320fc (워크트리 12ccfd0 cherry-pick) | Track A 실행자가 세션 한도로 중단 → 같은 워크트리에서 이어하기 실행자가 완성. runner 825→598줄 |
| B1 parity_summary | 완료 | e50bb16 (워크트리 a04abb3 cherry-pick) | Track B. parity 1,017→내부 분할 |
| B2 parity_proxy | 완료 | 8745bf8 (워크트리 31cb429 cherry-pick) | Track B. parity 최종 385줄 |

상위 모델 결정적 재검증(2026-06-12, cherry-pick 직후): parity BASE 38 top-level = 잔존 14 + summary 17 + proxy 7
정확 분할, runner BASE 10 = 잔존 6 + records 2 + sampling 2 — 사라짐 0, AST 불일치 0, 임의추가 0,
facade 동일성 28/28, parity_score_pipeline private 10종 생존, 패키지 `__all__` 정상, DAG 클린.
타깃 테스트(backtester 8파일) 114 passed.

통합 게이트 결과(2026-06-12, R5-S6와 일괄, 테스트품질+위생 2에이전트 + 상위 모델 AST/스위트 직접):
records/sampling/parity_proxy 테스트 3파일 PASS. test_parity_summary는 `_build_buy_diagnostics` 3케이스
부분검증(major)·`_summarize_live_reference` 서브키 누락(minor) 적발 → 수정 커밋 379b8ce로 10키/8서브키
전체 dict 동등성 강화. 위생: 커밋 격리·NOT-mine·트레이딩 표면·DAG·trailer 전부 PASS("facade 재수출 import
미사용" 지적은 확립된 패턴으로 비결함 판정). 최종 전체 스위트 **2,014 passed + 245 subtests, 실패 0**.
**R7 전 슬라이스(A1·A2·B1·B2) 완료·게이트 통과.**

### 병렬 실행 실측 교훈 (차기 계획서에 반영할 것)

1. **tdd_guard 상태는 워크트리 간 공유**: setup.cfg의 `tdd_guard_project_root`가 메인 절대경로 고정이라
   병렬 실행자들의 pytest 결과가 같은 상태 파일에 기록됨 → 서로의 red/green을 보고 가드가 편집을
   오거부(R5-S6 1차 실행자가 이로 인해 ESC-2 정지). 대응: 트랙 직렬화, 또는 실행자 프롬프트에
   "가드가 무관 실패를 이유로 거부 시 자기 타깃 red를 fresh 재수립 직후 재시도" 레시피 명시(A2 이어하기에서 유효 확인).
2. **워크트리 분기점은 지정 BASE가 아닐 수 있음**: 두 워크트리 모두 c412c9c가 아닌 0535de8(main)에서
   분기됨. AST 검증을 `git show <BASE>:<path>`로 수행시키는 설계 덕분에 무사했고, 통합 전
   `git diff <분기점> <BASE> -- <대상경로>` 공집합 확인으로 cherry-pick 안전성을 입증함 — 이 확인을 표준 절차로.
3. **실행자 세션 한도 중단 대응**: 작업물(커밋+미커밋)이 워크트리에 보존되므로, 같은 워크트리를
   가리키는 이어하기 실행자(fresh 프롬프트 + 부분 산출물 명세)로 재개하면 됨 — A2에서 47 tool uses로 완료.
