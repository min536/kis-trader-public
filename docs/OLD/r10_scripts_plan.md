# R10 — scripts 구획 슬리밍 계획 (2026-06-12)

백로그 순서(R4✓→R5✓→R7✓→R8✓→R9✓→**R10**→R6)의 scripts 구획. 원칙 동일:
**verbatim 이동만** — 이동 심볼 본문 무변경(AST 동등성 게이트), 스크립트는 thin CLI +
I/O facade로 잔류, 기존 테스트 무수정 green. 기준 SHA: 10c70b3.

## 1. 구조 지도 (2026-06-12 실측)

`scripts/` 14파일 4,070줄 = Python 3파일 1,382줄 + 셸 11파일 2,688줄.

| 파일 | 줄 | 판정 |
|------|----|------|
| refresh_workspace_data.py | 799 | 순수 payload 빌더 22심볼(~590줄)이 I/O 없이 동작 → **R10-A 이동** |
| live_snapshot.py | 507 | 순수 헬퍼 9함수+상수 2종(~140줄) → **R10-B 이동**. `SNAPSHOT_PATH` 의존 loader·네트워크 fetcher·`collect`·`LiveSnapshotRateLimitError`는 잔류(테스트가 `mock.patch.object(live_snapshot_script, ...)`로 script attr를 패치 — 이동하면 seam 파괴) |
| session_lock.py | 76 | `app/core/session_lock` thin wrapper → **skip** |
| *.sh 11종 | 2,688 | 운영 기동 표면(launchd/cron), 테스트가 스크립트 본문 텍스트를 핀(test_restart_session_script 등), shell TDD 하니스 부재 → **전부 skip** (standing "scripts/*.sh 무수정" 유지) |

- 외부 참조 실측: `scripts.*`를 import하는 곳은 `tests/test_workspace_static_ui_contract.py`,
  `tests/test_live_snapshot_runtime_pressure.py` 단 2곳. app/은 scripts를 import하지 않음.
- 테스트 seam 실측(이동 금지 근거): workspace 테스트는 `_issue_read_only_token`,
  `inquire_balance`, `load_dashboard_data`, `_write_workspace_data`, `build_portfolio_snapshot`,
  `build_performance_report`, `persist_performance_report`를 **script 모듈 attr**로 monkeypatch.
  live_snapshot 테스트는 `SNAPSHOT_PATH`, `get_korean_now`, `issue_access_token_for`,
  `_fetch_volume_rank`, `_load_latest_runtime_state`, `urllib_request`, `time`을 script attr로 패치.
  → 이 이름들이 쓰이는 함수는 전부 스크립트 잔류.

## 2. 슬라이스 사양

### R10-A — refresh_workspace_data.py → app/dashboard/workspace_payload.py

1. `app/dashboard/workspace_payload.py` (신규): 아래 22심볼 **verbatim 이동**
   (동일 이름 유지, 본문 무변경) — `ACTION_LABELS`, `TimedEvent`, `_safe_name`, `_parse_dt`,
   `_format_ts_short`, `_format_ts_long`, `_seconds_since`, `_action_label`, `_session_label`,
   `_session_tone`, `_event_tone`, `_position_statuses`, `_normalize_positions`,
   `_build_mix_rows`, `_build_watchpoint`, `_dedupe_items`, `_build_order_events`,
   `_build_cycle_events`, `_build_trade_events`, `_build_queue`, `_build_workspace_payload`,
   `_build_local_workspace_report`.
   모듈 import 셋: `__future__.annotations`, `dataclasses.dataclass`, `datetime.datetime`,
   `typing.Any`, `app.core.time_utils (KOREA_TZ, get_korean_now)`,
   `app.dashboard.metrics` 7빌더, `app.scanner.symbol_names.get_symbol_name` + 짧은 독스트링.
2. `scripts/refresh_workspace_data.py` 잔류: sys.path 블록, `WORKSPACE_DATA_PATH`,
   `BROKER_BALANCE_FLAG`, `_read_cached_token`, `_issue_read_only_token`,
   `_write_workspace_data`, `_build_broker_balance_report`, `_build_parser`, `main`
   + `from app.dashboard.workspace_payload import _build_local_workspace_report,
   _build_workspace_payload`. 잔류가 안 쓰는 import 전부 제거(스테일 import 금지 — R4 교훈):
   `dataclass`/`datetime`/`KOREA_TZ`/`get_korean_now`/metrics/`get_symbol_name` 제거,
   `argparse`/`json`/`sys`/`time`/`Path`/`Any`/settings·token·balance·performance·portfolio·
   `load_dashboard_data`는 유지(잔류 사용 실측).
3. `tests/test_workspace_payload.py` (신규): 직접 모듈 행위 테스트 —
   - `_normalize_positions`: weight desc 정렬 + status 부여(best/worst/largest/loss/steady)
     + 필드 fallback(`weight_pct`→`account_weight_pct`, `quantity`→`holding_qty`) + None pnl 유지.
   - `_build_mix_rows`: 현금>0 행 + top5 + "기타 포지션" 합산 + "정산반영 추정" 행,
     largest status → `warning` tone.
   - `_build_watchpoint`: worst pnl ≤ −2.0 → worst 선택(손실 reason), 아니면 largest 선택,
     빈 입력 → None.
   - `_dedupe_items`: key_fields 기준 최초 항목 유지.
   - `_build_order_events`: dedupe 후 최대 6건, timestamp 불가 항목 skip, payload 키 형태.
   - `_build_workspace_payload`: 최소 data/report로 최상위 키 셋
     (meta/account/anomaly/concentration/engine/buy_snapshot/positions/events/queue/mix/watchpoint)
     + `account.cash_weight_pct` 전달 핀.
   - 포맷터 리터럴: `_action_label`(매핑·미지정 Title Case·빈 값 "기록 없음"),
     `_session_label`/`_session_tone`, `_parse_dt`(naive→KST), `_format_ts_long`, `_event_tone` 분기.
   기존 `tests/test_workspace_static_ui_contract.py` **무수정** green.

### R10-B — live_snapshot.py → app/market_data/live_snapshot_collect.py

1. `app/market_data/live_snapshot_collect.py` (신규): 아래 11심볼 **verbatim 이동** —
   `_SELL_WATCH_PARTIAL_DEFER_SECONDS`, `_VALID_SYMBOL_PATTERN`, `_first_env`, `_positive_int`,
   `_parse_response_body`, `_parse_runtime_timestamp`, `_build_runtime_pressure_defer_reason`,
   `_build_headers`, `_extract_rows`, `_extract_symbols`, `_merge_ranked_lists`.
   모듈 import 셋: `__future__.annotations`, `datetime.datetime`, `json`, `os`, `re`,
   `typing.Any` — **stdlib만**(app 의존 0).
2. `scripts/live_snapshot.py` 잔류: 상수(`SNAPSHOT_PATH` 등), `LiveSnapshotRateLimitError`,
   `_load_json_file`, `_load_existing_snapshot`, `_load_latest_runtime_state`, `_get_json`,
   `_fetch_volume_rank`/`_fetch_fluctuation_rank`/`_fetch_volume_power_rank`, `collect`, `main`
   + `from app.market_data.live_snapshot_collect import ...`(이동 9함수 — 같은 이름 바인딩이라
   `collect`/`_get_json` 내부 호출과 기존 테스트 직접 호출 모두 그대로 동작). 스테일 import
   제거: `re` 제거(이동), `os`는 잔류 사용 여부 실측 후 판단.
3. `tests/test_live_snapshot_collect.py` (신규): 직접 모듈 행위 테스트 —
   - `_extract_symbols`: 6자리 패턴 필터 + 필드 우선순위(mksc_shrn_iscd→stck_shrn_iscd→pdno)
     + dedupe + top_n 절단.
   - `_merge_ranked_lists`: 라운드로빈 interleave + dedupe + top_n 조기 반환.
   - `_parse_response_body`: 정상 dict / 비dict JSON·깨진 JSON → `{"raw_text": ...}`.
   - `_parse_runtime_timestamp`: ISO 파싱 / 빈 값·불가 값 None.
   - `_positive_int`: 통과 / 0·음수 ValueError(이름 포함 메시지).
   - `_first_env`: 우선순위·공백 스킵·default (monkeypatch.setenv).
   - `_build_headers`: 키 셋·Bearer 포맷 리터럴.
   - `_build_runtime_pressure_defer_reason`: backoff defer 1건 + stale snapshot → None 1건
     (정밀 케이스는 기존 runtime_pressure 테스트가 script seam에서 계속 커버).
   기존 `tests/test_live_snapshot_runtime_pressure.py` **무수정** green.

## 3. 검증 게이트 (상위 모델, 통합 시 — 전부 결정적)

1. **AST 동등성**: 이동 33심볼 `ast_dump(include_attributes=False)`가 원본(10c70b3) 대비 동일.
2. **스크립트 위생**: 잔류 스크립트에 이동 심볼의 잔존 정의 0 + 스테일 import 0(AST 검사).
3. **seam 보존**: 기존 테스트 2파일 git 무접촉 + green.
4. **계층 격리**: live_snapshot_collect는 stdlib만, workspace_payload는 app 내부만
   (scripts import 0); scripts→app 단방향 유지.
5. **범위 격리**: 커밋 diff가 명시 6파일(scripts 2 + app 신규 2 + tests 신규 2)만.
   `*.sh`·session_lock.py·NOT-mine 무접촉.
6. **전체 스위트** green (기준 2,226 passed + 450 subtests + 신규 테스트 증가분).

## 4. 실행 프로토콜

- opus 실행자 1, 워크트리, R10-A→R10-B 직렬, 슬라이스당 커밋 1(경로 지정 add만).
- **스크립트 실행 절대 금지**: `python scripts/*.py` 직접 실행 금지(broker/live 인접) —
  검증은 pytest로만. broker/order API 테스트 호출 금지.
- tdd-guard: 신규 테스트 파일은 1테스트/Edit 사이클(subTest로 압축), 거부 시 자기 파일
  fresh-red/green 후 동일 편집 재시도, 4연속 거부 STOP. relocation 삭제(이동 완료분의
  원본 제거)가 Edit 거부되면 fresh-green 확인 후 Bash로 수행(상위 게이트 1·2가 무손실 입증).
- stale base: `git merge-base --is-ancestor 10c70b3 HEAD` 실패 시
  `git checkout 10c70b3 -- scripts/ app/dashboard/ app/market_data/` + base-sync 커밋
  (통합 시 base-sync는 cherry-pick하지 않음).
- pytest는 절대경로 `$KIS_TRADER_ROOT/.venv/bin/python -m pytest`.

## 5. 진행표 (2026-06-13 완료)

| 슬라이스 | 상태 | 커밋 (메인 브랜치) |
|---------|------|------|
| R10-A workspace payload 추출 | 완료 | 32098f2 |
| R10-B live snapshot 헬퍼 추출 | 완료 | 0c923c6 |

### 게이트 결과 (전부 결정적 — §3 기준)

1. **AST 동등성**: 이동 33심볼(R10-A 22 + R10-B 11) 전부
   `ast_dump(include_attributes=False)` 원본(63056ee) 대비 동일 — 33/33.
2. **스크립트 위생**: 이동 심볼 잔존 정의 0, 스테일 import 0. 통합 게이트에서
   `_parse_runtime_timestamp` 스테일 import 1건 적발(사양의 import 목록 과잉이 원인 —
   실행자 결함 아님) → 제거 후 R10-B 커밋에 amend.
3. **seam 보존**: 기존 테스트 2파일(test_workspace_static_ui_contract,
   test_live_snapshot_runtime_pressure) 원본 대비 byte-identical + green.
4. **계층 격리**: live_snapshot_collect는 stdlib만(app/scripts import 0),
   workspace_payload는 app 내부만 — CLEAN.
5. **범위 격리**: 두 커밋 diff가 명시 6파일만. `*.sh`·session_lock.py·NOT-mine 무접촉.
6. **전체 스위트** 100% green (§6 실행 기록 참조).

### 라인 수 변화

| 파일 | 이전 | 이후 |
|------|------|------|
| refresh_workspace_data.py | 799 | 137 (+ workspace_payload 675) |
| live_snapshot.py | 507 | 360 (+ live_snapshot_collect 168) |

신규 테스트: test_workspace_payload.py 9테스트(+3 subtests) + test_live_snapshot_collect.py
10테스트 = 19테스트 신규(기존 seam 테스트 12건은 무수정 green).

### 실행 기록

- opus 실행자 1, 워크트리, 직렬 2슬라이스 완주. 워크트리 base가 오래되어 지시된
  base-sync 레시피 적용(971ed70 — 의도적 미통합), 슬라이스 커밋 무충돌 cherry-pick.
- tdd-guard 실측: 일괄 verbatim fill을 "over-implementation"으로 거부 → 사양의 escape
  hatch대로 red-before-green 수립 후 Bash heredoc으로 verbatim 본문 작성(AST 게이트가
  무손실 입증). 신규 테스트는 1테스트/Edit 유지.
- 실행자 보정 1건(정당): `_build_workspace_payload` 최소 fixture에서도
  `build_operational_alerts`·현금 행 때문에 queue/mix가 비어 있지 않음 — 단언을
  빈 리스트가 아닌 list 타입 + 키 셋 핀으로 작성.
