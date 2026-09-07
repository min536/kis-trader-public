# 모닝 브리핑 + T+2 예수금 표면화 위임 계획서 — 2026-07-18

프로파일: **신규 모듈 TDD** (AST 게이트 해당 없음 · 핀+스위트 게이트 적용)

선행 트랙 산출물 소비: `docs/disclosure_sentinel_plan_20260718.md`(공시 이벤트 JSONL), `docs/pnl_attribution_postmortem_plan_20260718.md`(어트리뷰션 빌더). **이 계획서는 두 트랙 머지 후 실행.**

## §0 배경 / 목표 / 비목표

**배경.** 증권사 RA의 아침 브리핑 직무 에이전트화 — 개장 전 Slack 한 통에 시스템 전 상태 종합. 기존 부품이 거의 다 있고(레짐 픽 아티팩트·brake 상태·공시 로그·어트리뷰션), 조립 계층만 없다. T+2 예수금 갭은 이미 영속돼 있어(`cash_krw` vs `orderable_cash_krw`, cycle_snapshots 실측 키) 순수 읽기 표면화로 충분.

**목표.**
1. `app/notifications/morning_briefing.py` — 디스크의 기존 아티팩트만 조립하는 순수 빌더 + 렌더러. **네트워크 호출 0** (브로커/DART/외부 API 일절 없음).
2. CLI `app/tools/morning_briefing_report.py` (+ launchd plist **example 파일만** — 로드는 운영자).
3. slack_bot에 `briefing` 온디맨드 명령 추가.

**비목표 (명시적 제외).**
- 미국장/환율 실시간 조회 — 네트워크 0 원칙. 레짐 섹션은 `morning_regime_<date>.json` 아티팩트(있으면)로 대체.
- T+2 임계 알림 — v1은 표시만(노이즈 회피). 알림 규칙은 운용 데이터 관찰 후.
- 대시보드 표출, 엔진 런타임 wiring — 무접촉.
- launchd 잡 로드/실행 — example 파일 작성까지만 (운영자 게이트).

## §1 실행 원칙 (가드)

- tdd_guard: 테스트 1개씩 stub-first red→green.
- **금지 파일**: `app/execution/`, `app/risk/`, `app/strategy/`, `app/auth/settings.py`, `app/main.py`, `app/pipeline/order_gate.py|buy_lane.py|sell_lane.py`, `.env`, `.token_cache.json`. launchd `launchctl` 실행 금지(example 파일 작성만).
- 테스트에서 실 `data/`·`research/` 접근 금지 — tmp_path 합성 픽스처. 빌더는 값 입력 순수 함수.
- slack_bot 기존 라우트/테스트 문자 불변(순수 가산).
- 코드 커밋(feat)과 문서 커밋(docs) 분리. 진행표 실제 SHA 기입.

## §2 구조지도 (인용 검증용)

**섹션 소스 (전부 디스크 read-only):**
- 계좌 상태: `data/cycle_snapshots_<account>.jsonl` 마지막 줄 — 실측 키 `equity_krw, cash_krw, orderable_cash_krw, intraday_pnl_pct, current_regime, daily_pnl_brake_state, timestamp, account_signature, masked_account_display, environment`. **T+2 갭 = cash_krw − orderable_cash_krw** (의미 근거: `app/portfolio/schema.py:188-190` dnca_tot_amt/prvs_rcdl_excc_amt 파싱, `app/portfolio/equity_state.py:90,133-138` T+2 미결제 semantics).
- 브레이크 표시: `app/risk/pnl_brake.py:15 daily_pnl_brake_display_status(brake_state) -> str` — **이 함수를 import해 사용** (재구현 금지; risk 모듈 read-only 호출은 허용, 수정 금지).
- 전일 어트리뷰션: `app/reporting/pnl_attribution.py`의 `build_daily_attribution`/`render_attribution_lines` (B트랙 산출물) 재사용.
- 공시: `<state_dir>/disclosure_events.jsonl` (A트랙 산출물; state_dir env `DISCLOSURE_SENTINEL_STATE_DIR` or `data`) tail 최근 24h.
- 레짐 픽: `morning_regime_<date>.json` (`app/tools/morning_regime_pick.py:88` 명명; 출력 디렉토리는 CLI 인자 — 브리핑은 env `MORNING_REGIME_ARTIFACT_DIR` 지정 시만 읽고, 미지정/부재 시 섹션 생략).
- JSONL 헬퍼: `app/core/jsonl.py:47,67`; 파일 tail은 데이터 파일 전체-읽기 금지 규칙에 맞춰 마지막 N줄 bounded read.

**Slack 등록 관습:** `app/notifications/slack.py:109-119` 선례. OPERATOR env `:53`. (주의: `:115 MORNING_REGIME_EVENT_TYPE = "morning_regime_pick"`이 이미 존재 — 새 타입은 `"morning_briefing"`으로 충돌 없음.)

**slack_bot 명령 라우터:** `app/notifications/slack_bot.py:106` 명령 allowlist 튜플, `:130` 채널 제한 set, `:155` alias map, `:161 parse_command`, `:174 render_command_reply` 분기. 선례 분기: `:198 positions`, `:204 orders`.

**CLI/plist 선례:** `app/tools/morning_regime_pick.py`(argparse+notify 삼킴 `:248-251`), `launchd/com.kis-trader.session.plist.example`(example 관습).

## §3 슬라이스 사양

### S1 — `app/notifications/morning_briefing.py`

- `build_morning_briefing(*, accounts: list[dict], attribution: dict | None, disclosures: list[dict], regime_pick: dict | None, now: datetime) -> dict`
  - `accounts` 원소 = 계정별 최신 cycle 스냅샷 dict(원키 그대로). 반환 구조:
    `{"generated_at", "accounts": [{"display", "environment", "equity_krw", "cash_krw", "orderable_cash_krw", "t2_pending_krw", "t2_pending_pct_of_cash", "intraday_pnl_pct", "regime", "brake_display", "snapshot_age_minutes", "stale": bool}], "yesterday_attribution": {...}|None, "disclosures_24h": [...], "regime_pick": {...}|None}`
  - `t2_pending_krw = max(0, cash_krw − orderable_cash_krw)`; `brake_display`는 `daily_pnl_brake_display_status` 호출; `stale`은 snapshot timestamp가 12h 초과.
  - 계약: never raises — 섹션 결손은 None/빈 강등.
- `render_briefing_lines(briefing: dict) -> list[str]` — 한국어 섹션: `[계좌]`(T+2 잠김 표기 포함: `T+2 미결제 X원 (현금의 Y%)`), `[브레이크]`, `[전일 귀속]`, `[공시 24h]`, `[레짐 픽]`. 결손 섹션은 `— 없음` 1줄.

**행위 테스트 (`tests/test_morning_briefing.py`):**
1. `test_briefing_accounts_section_with_t2_gap`
2. `test_briefing_t2_gap_never_negative`
3. `test_briefing_marks_stale_snapshot`
4. `test_briefing_brake_display_uses_risk_helper`
5. `test_briefing_sections_degrade_to_none_on_missing_inputs`
6. `test_render_briefing_lines_korean_sections_and_placeholders`

### S2 — CLI + Slack 등록 + plist example

- `app/tools/morning_briefing_report.py`: argparse `--slack`, `--json-out`, `--data-dir`(기본 `data`, env `KIS_DATA_DIR` 오버라이드 가능하면 그 관습 따름 — 없으면 인자만). 계정 열거: `cycle_snapshots_*.jsonl` glob → 각 마지막 줄. 전일 어트리뷰션: B트랙 빌더 재사용(입력 결손 시 None). 공시/레짐 픽: §2 규칙.
- `app/notifications/slack.py`: `MORNING_BRIEFING_EVENT_TYPE = "morning_briefing"` → `OPERATOR_CHANNEL_ENV` (2줄).
- `launchd/com.kis-trader.morning-briefing.plist.example`: 평일 08:35 KST, `.venv/bin/python -m app.tools.morning_briefing_report --slack` — **example 파일만, 로드 금지.**

**행위 테스트 (`tests/test_morning_briefing_report_cli.py`):**
1. `test_cli_composes_briefing_from_tmp_data_dir`
2. `test_cli_slack_optional_and_failure_swallowed`
3. `test_morning_briefing_event_routes_to_operator_channel`
4. `test_plist_example_exists_and_references_cli` (파일 존재+핵심 문자열)

### S3 — slack_bot `briefing` 명령

- `:106` allowlist에 `"briefing"` 추가, `:130` 채널 제한 set에 추가, help 텍스트 1줄, `render_command_reply`에 분기 — CLI와 동일 조립 경로 호출해 reply 텍스트 반환(발송 아님, 봇 reply). 기존 분기 문자 불변.

**행위 테스트:** `tests/test_slack_bot.py`에 2건 — `test_parse_command_briefing`, `test_render_briefing_command_reply_smoke`(조립 실패 시 오류 문구 reply, 예외 전파 금지).

### S4 — 마무리

- 전체 스위트 green → feat 커밋 → 진행표 SHA 기입 → docs 커밋.

## §4 계획-단계 체크리스트 판정

1. **구조지도 인용** — §2 전 항목 file:line 실측(선행 2트랙 산출물은 해당 계획서 §3 사양 참조). PASS
2. **패치-서피스** — slack_bot 기존 테스트 patch 대상 불변(가산 분기만). PASS
3. **trading-critical 접촉** — 없음. `app/risk/pnl_brake.py`는 **import 호출만**(수정 금지 명문). PASS
4. **import 도출** — stdlib+기존 모듈. notifications→reporting(B빌더)·notifications→risk(표시 헬퍼) 방향 import는 기존 선례 확인 후(순환 시 CLI 계층으로 조립 이동). PASS(조건부)
5. **테스트 격리** — tmp_path 합성 데이터, 실 data/ 금지, launchctl 금지. PASS

## §5 에스컬레이션

- `notifications → reporting` import가 순환을 만들면 조립을 CLI 계층(`app/tools/`)으로 옮기고 notifications 빌더는 값 입력만 받게 유지 — 그래도 안 풀리면 중단 보고.
- slack_bot 기존 테스트 red 유발 시 기존 테스트 수정 금지, 중단 보고.

## §6 진행표

| 슬라이스 | 상태 | 커밋 | 증거 |
|---|---|---|---|
| S1 브리핑 빌더 | DONE | f6a0119 | `tests/test_morning_briefing.py` 6건 green — build_morning_briefing/render_briefing_lines(순수·never raises), T+2=max(0,cash−orderable), brake=risk 헬퍼 |
| S2 CLI+Slack+plist | DONE | f6a0119 | `tests/test_morning_briefing_report_cli.py` 4건 green — CLI 조립(tmp data-dir), slack `morning_briefing`→OPERATOR, plist example valid XML |
| S3 봇 briefing 명령 | DONE | f6a0119 | `tests/test_slack_bot.py::BriefingCommandTests` 2건 green; slack_bot 60/60 무회귀(기존 라우트 불변) |
| S4 마무리 | DONE | f6a0119 | 전체 스위트 `3503 passed, 2 skipped, 726 subtests` (109s); 핀 12건 전부 포함 |

(실행자: 각 슬라이스 완료 시 상태 DONE + 실제 커밋 SHA + 증거 기입. SHA 없는 DONE은 감사 FAIL.)

**이탈 기록.** §2/S2가 slack 등록을 "(2줄)"로 명시했으나, OPERATOR 라우팅 이벤트는
`EVENT_CHANNEL_ENV_OPTIONS_BY_TYPE` 등록이 없으면 `resolve_channel_env_vars`가
`()`를 반환해 라우팅이 실패한다 — `disclosure_alert` 선례(`slack.py:146`)대로 3번째
줄(OPTIONS)을 추가함. 그 외 이탈 없음. §5(순환)·에스컬레이션 미발동
(notifications→reporting/risk 단방향, 순환 없음).

### §6.1 핀 테스트 목록

test_briefing_accounts_section_with_t2_gap, test_briefing_t2_gap_never_negative, test_briefing_marks_stale_snapshot, test_briefing_brake_display_uses_risk_helper, test_briefing_sections_degrade_to_none_on_missing_inputs, test_render_briefing_lines_korean_sections_and_placeholders, test_cli_composes_briefing_from_tmp_data_dir, test_cli_slack_optional_and_failure_swallowed, test_morning_briefing_event_routes_to_operator_channel, test_plist_example_exists_and_references_cli, test_parse_command_briefing, test_render_briefing_command_reply_smoke
