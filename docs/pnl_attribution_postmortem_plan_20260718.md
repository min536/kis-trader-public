# PnL 어트리뷰션 + 트레이드 포스트모템 위임 계획서 — 2026-07-18

프로파일: **신규 모듈 TDD** (AST 게이트 해당 없음 · 핀+스위트 게이트 적용)

## §0 배경 / 목표 / 비목표

**배경.** 성과 리포트는 있으나 "오늘 왜 벌었/잃었나"(귀속 분해)와 "청산 트레이드 리뷰"(포스트모템)가 없다 — 운용역·성과평가 직무의 에이전트화. 실측 확인(2026-07-18): ① 트레이드 저널은 영속 파일이 아니라 대시보드가 주문 로그에서 즉석 파생(`app/dashboard/normalizers.py:781 _build_trade_journal`), ② `app/reporting/fill_slippage.py:80 build_fill_slippage_summary`는 정의만 있고 런타임 호출처 0 — 이 트랙이 채택한다.

**목표.**
1. `app/reporting/pnl_attribution.py` — 일 단위 계좌 손익을 심볼별 실현/미실현 기여 + 슬리피지 비용 + 레짐/브레이크 컨텍스트로 분해하는 순수 빌더 + 렌더러.
2. `app/reporting/trade_postmortem.py` — 주 단위 청산 트레이드 리뷰(보유시간·청산사유 분포·심볼별 반복 성과·승률/평균손익) 순수 빌더 + 렌더러.
3. 각각 CLI(`app/tools/`) + Slack 발송 옵션(SUMMARY 채널). 전부 **파일 읽기 전용** — 브로커/네트워크 호출 없음.

**비목표 (명시적 제외).**
- MFE/MAE(분봉 경로 조인) — 분봉 파케이 조인은 리서치 배치 규모라 v1 제외.
- 엔진 런타임 wiring(사이클 내 호출) — CLI/스케줄 실행 전용. `app/main.py`·cycle_phases 무접촉.
- 진입 시그널 코호트 분석 — 주문 로그에 시그널 스냅샷이 영속되는지 미확인이므로 "있으면 표기, 없으면 생략" 수준까지만.

## §1 실행 원칙 (가드)

- tdd_guard: 테스트 1개씩 stub-first red→green.
- **금지 파일**: `app/execution/`, `app/risk/`, `app/strategy/`, `app/auth/settings.py`, `app/main.py`, `app/pipeline/order_gate.py|buy_lane.py|sell_lane.py`, `.env`, `.token_cache.json`.
- 테스트에서 실 `data/` 읽기/쓰기 금지 — 픽스처는 전부 `tmp_path` 합성 JSONL. 빌더는 경로가 아니라 **파싱된 리스트/딕트를 인자로** 받는 순수 함수로 설계(경로 해석은 CLI 계층만).
- 실 데이터 파일을 스모크로 볼 때도 head/tail 일부만 (전체 cat 금지).
- 코드 커밋(feat)과 문서 커밋(docs) 분리. 진행표에 실제 SHA 기입.

## §2 구조지도 (인용 검증용)

**주문 로그:** 경로 리졸버 `app/auth/account_scope.py:206 get_order_log_path(settings=None) -> Path`. 대시보드 소비 선례: `app/dashboard/data_loader.py:290`(경로), `:313-314 _safe_read_jsonl`, `:345 _normalize_orders(order_records)`, `:348 trade_journal 파생`. 정규화 선례 `app/dashboard/normalizers.py:781 _build_trade_journal(orders)` — **매수/매도 페어링·필드 명세는 이 함수를 읽고 동일 필드를 소비**할 것 (재발명 금지, 단 dashboard 모듈 import 금지 — reporting 계층은 자체 경량 페어링 구현).

**사이클 스냅샷:** `data/cycle_snapshots_<account>.jsonl` (실측 키: `cash_krw, orderable_cash_krw, equity_krw, intraday_pnl_baseline_krw, intraday_pnl_pct, current_regime, regime_state, daily_pnl_brake_state, executed_order_count, timestamp, account_signature, environment`).

**성과 스냅샷:** `data/performance_snapshots_<account>.jsonl` (존재 실측; 스키마는 실행자가 head 1줄로 확인 후 소비 필드를 테스트 픽스처에 고정).

**청산 사유:** `data/runtime_state_<account>.json`의 `last_exit_reason_by_symbol`, `last_exit_price_by_symbol`, `last_exit_qty_by_symbol`, `last_exit_at_by_symbol` (실측 키 확인).

**슬리피지:** `app/reporting/fill_slippage.py:80 build_fill_slippage_summary` — 입력 계약은 함수 시그니처/독스트링 확인 후 채택. 호출처 0 실측이므로 시그니처 변경 금지(채택만).

**JSONL 헬퍼:** `app/core/jsonl.py:47 decode_jsonl_objects`, `:67 read_jsonl_objects`, `:14 SNAPSHOT_READ_LINE_MAX_BYTES`.

**Slack 등록 관습:** `app/notifications/slack.py:109-119` 선례(상수+dict 2줄). SUMMARY 채널 env는 `:58 SUMMARY_CHANNEL_ENV`.

**CLI 선례:** `app/tools/morning_regime_pick.py` (argparse, 오프라인 계약, notify 실패 삼킴 `:248-251`).

## §3 슬라이스 사양

### S1 — `app/reporting/pnl_attribution.py`

- `build_daily_attribution(*, orders: list[dict], cycle_tail: list[dict], exit_state: dict, target_date: str) -> dict`
  - 반환: `{"date", "account_signature", "realized_by_symbol": {sym: krw}, "unrealized_delta_krw", "slippage_summary", "regime_timeline": [(regime, count)], "brake_state_last", "intraday_pnl_pct_last", "totals": {"realized_krw", "trades_closed", "trades_opened"}}`
  - realized: target_date의 매도 주문을 주문 로그에서 페어링(같은 심볼 직전 매수 평균가 기준; 로그에 평균단가/실현손익 필드가 이미 있으면 그 필드 우선 — S1 첫 테스트 전에 실행자가 주문 로그 head 3줄로 필드 확정).
  - slippage_summary: `build_fill_slippage_summary` 채택 호출(입력 부족 시 `None` — never raises).
  - 계약: **never raises** — 입력 결손은 해당 섹션 `None`/빈으로 강등.
- `render_attribution_lines(report: dict) -> list[str]` — 콘솔/Slack 공용 한국어 라인.

**행위 테스트 (`tests/test_pnl_attribution.py`):**
1. `test_attribution_realized_by_symbol_from_paired_orders`
2. `test_attribution_uses_recorded_realized_fields_when_present`
3. `test_attribution_regime_timeline_and_brake_from_cycle_tail`
4. `test_attribution_never_raises_on_empty_inputs`
5. `test_attribution_slippage_section_optional`
6. `test_render_attribution_lines_korean_sections`

### S2 — CLI `app/tools/pnl_attribution_report.py`

- argparse: `--account <signature|all>`, `--date <YYYY-MM-DD>`(기본 오늘 KST), `--slack`(발송), `--json-out <path>`.
- 경로 해석: `data/` 아래 계정별 파일 glob(§2 명명 규칙), 주문 로그는 `get_order_log_path()`. 읽기는 `read_jsonl_objects` tail 위주(전일 분량이면 마지막 N줄로 충분 — N 기본 2000, env `PNL_ATTRIBUTION_TAIL_LINES`).
- Slack: `app/notifications/slack.py`에 `PNL_ATTRIBUTION_EVENT_TYPE = "pnl_attribution"` → `SUMMARY_CHANNEL_ENV` 등록(2줄). 발송 실패는 삼키고 exit 0, 리포트 JSON은 남긴다(`morning_regime_pick.py:248-251` 선례).

**행위 테스트 (`tests/test_pnl_attribution_report_cli.py`):**
1. `test_cli_builds_report_from_tmp_data_dir`
2. `test_cli_slack_send_optional_and_failure_swallowed`
3. `test_cli_json_out_writes_report`
4. `test_pnl_attribution_event_routes_to_summary_channel`

### S3 — `app/reporting/trade_postmortem.py`

- `build_weekly_postmortem(*, orders: list[dict], exit_state: dict, now: datetime, window_days: int = 7) -> dict`
  - 반환: `{"window": {...}, "closed_trades": [{symbol, entry_at, exit_at, hold_minutes, pnl_krw, pnl_pct, exit_reason}], "exit_reason_distribution": {reason: count}, "per_symbol": {sym: {"trades", "wins", "net_krw"}}, "summary": {"win_rate", "avg_win_krw", "avg_loss_krw", "profit_factor"}}`
  - exit_reason은 주문 로그 레코드에 있으면 우선, 없으면 `exit_state`의 `last_exit_reason_by_symbol` 폴백(최근 청산만 매칭 가능함을 필드 `reason_source`로 명시).
  - 계약: never raises.
- `render_postmortem_lines(report: dict) -> list[str]`.

**행위 테스트 (`tests/test_trade_postmortem.py`):**
1. `test_postmortem_pairs_closed_trades_within_window`
2. `test_postmortem_exit_reason_distribution_with_fallback`
3. `test_postmortem_per_symbol_repeat_performance`
4. `test_postmortem_summary_win_rate_and_profit_factor`
5. `test_postmortem_never_raises_on_empty_inputs`
6. `test_render_postmortem_lines_korean_sections`

### S4 — CLI `app/tools/trade_postmortem_report.py`

- argparse: `--account`, `--days`(기본 7), `--slack`, `--json-out`. Slack: `TRADE_POSTMORTEM_EVENT_TYPE = "trade_postmortem"` → `SUMMARY_CHANNEL_ENV`.

**행위 테스트 (`tests/test_trade_postmortem_report_cli.py`):**
1. `test_cli_builds_weekly_report_from_tmp_data_dir`
2. `test_cli_days_option_bounds_window`
3. `test_trade_postmortem_event_routes_to_summary_channel`

### S5 — 마무리

- 전체 스위트 green → feat 커밋(코드 전체) → 진행표 SHA 기입 → docs 커밋(계획서만).

## §4 계획-단계 체크리스트 판정

1. **구조지도 인용** — §2 전 항목 file:line 실측. 단 주문 로그/성과 스냅샷 레코드 필드는 실행자가 head로 확정 후 픽스처 고정(계획서에 필드명을 넘겨짚지 않음). PASS(조건부 위임 명시)
2. **패치-서피스** — main.py 무접촉, 기존 함수 시그니처 변경 없음(`build_fill_slippage_summary` 채택만). PASS
3. **trading-critical 접촉** — 없음(reporting/tools 계층). PASS
4. **import 도출** — stdlib + 기존 앱 모듈만, dashboard 패키지 import 금지(계층 역전 방지). PASS
5. **테스트 격리** — 빌더는 값 입력 순수 함수, CLI 테스트는 tmp_path 합성 데이터 + env로 data-dir 오버라이드. PASS

## §5 에스컬레이션

- 주문 로그에 실현손익/평균단가 필드가 전혀 없어 페어링 정확도가 보장 안 되면 — 근사 페어링으로 임의 진행하지 말고 "필드 부재" 보고와 함께 `reason_source`/`pairing_basis` 명시 강등 구현까지만.
- `build_fill_slippage_summary` 입력 계약이 주문 로그와 호환 불가면 슬리피지 섹션 `None` 강등으로 진행(시그니처 개조 금지).

## §6 진행표

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| S1 어트리뷰션 빌더 | DONE | ff6d02d | `app/reporting/pnl_attribution.py` + `tests/test_pnl_attribution.py` 6테스트 red→green (기록 net_pnl_krw 우선·직전매수 페어링·미실현 델타·레짐 RLE·slippage 채택·한국어 렌더러) |
| S2 어트리뷰션 CLI | DONE | ff6d02d | `app/tools/pnl_attribution_report.py` + `tests/test_pnl_attribution_report_cli.py` 4테스트 (KIS_STATE_ROOT tmp 오버라이드·notify 실패 삼킴·--json-out·SUMMARY 라우팅); slack.py 등록 2줄 |
| S3 포스트모템 빌더 | DONE | ff6d02d | `app/reporting/trade_postmortem.py` + `tests/test_trade_postmortem.py` 6테스트 (윈도우 페어링·exit_state 최근청산 폴백 reason_source·심볼별·승률/PF·never-raises·렌더러) |
| S4 포스트모템 CLI | DONE | ff6d02d | `app/tools/trade_postmortem_report.py` + `tests/test_trade_postmortem_report_cli.py` 3테스트 (--days 윈도우 바운드·SUMMARY 라우팅) |
| S5 마무리 | DONE | ff6d02d | 전체 스위트 `.venv/bin/python -m pytest -q` → 3491 passed, 2 skipped, 726 subtests (2026-07-18); 코드 전체 단일 feat 커밋 |

**§2 조건부 위임 실측 확정 (2026-07-18, head/tail 샘플만):** 주문 로그 top-level `timestamp/cycle_id/symbol/symbol_name/qty/order_type/confirm_buy/market_open/action/result/reason/environment/account_signature/account_environment/masked_account_display/pid/raw_response`; SELL(`sell_order_succeeded`) `raw_response.sell_strategy_details.details`에 `average_cost/current_price/gross_pnl_krw/net_pnl_krw/gross_pnl_pct/net_pnl_pct/pnl_pct/effective_pnl_basis/cost_estimate{...}` 기록 실현손익 존재 → 기록 필드 우선 채택(§5 에스컬레이션 불발동). `raw_response.trigger`·`sell_strategy_details.triggered_rule_name`이 청산 사유. BUY(`order_succeeded`) `raw_response`: `order_response/position_sizing/quote_at_submit/reference_price_krw/selection_details/strategy_details`. 성과 스냅샷 head 1줄 키: `account_environment/account_signature/benchmark_*/cash_*/equity_basis/holdings_market_value_krw/operating_equity_krw/positions_count/raw_balance_total_evaluation_amount_krw/timestamp/total_cost_basis_krw/total_equity_krw/total_unrealized_pnl_krw/trading_date/unrealized_gross_pnl_krw/unrealized_net_pnl_krw` (v1 빌더는 cycle_snapshots의 equity/baseline로 충분해 미소비). exit_state 4키는 `app/runtime_state.py:123-126` 실측 일치.

(실행자: 각 슬라이스 완료 시 상태 DONE + 실제 커밋 SHA + 증거를 기입. SHA 없는 DONE은 감사 FAIL.)

### §6.1 핀 테스트 목록

test_attribution_realized_by_symbol_from_paired_orders, test_attribution_uses_recorded_realized_fields_when_present, test_attribution_regime_timeline_and_brake_from_cycle_tail, test_attribution_never_raises_on_empty_inputs, test_attribution_slippage_section_optional, test_render_attribution_lines_korean_sections, test_cli_builds_report_from_tmp_data_dir, test_cli_slack_send_optional_and_failure_swallowed, test_cli_json_out_writes_report, test_pnl_attribution_event_routes_to_summary_channel, test_postmortem_pairs_closed_trades_within_window, test_postmortem_exit_reason_distribution_with_fallback, test_postmortem_per_symbol_repeat_performance, test_postmortem_summary_win_rate_and_profit_factor, test_postmortem_never_raises_on_empty_inputs, test_render_postmortem_lines_korean_sections, test_cli_builds_weekly_report_from_tmp_data_dir, test_cli_days_option_bounds_window, test_trade_postmortem_event_routes_to_summary_channel
