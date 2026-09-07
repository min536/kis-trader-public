# 수동 매매 정합성(Manual broker trades reconciliation) 설계 (2026-07-04)

> **실행 위임 문서** (r4 형식). 출처 요구: todo §16 (not included in this source snapshot) "추가 TODO — 수동 매매 정합성 반영".
> tdd_guard: 테스트 1개씩, red 확인 후 구현. risk-analyst 게이트 조건(§6)은 기계적 검증 가능 요건.

## 0. 현행 실사 (2026-07-04 코드 기준 — 재확인 불요)

- `app/core/reconciliation.py`: drift 분류 4종(`unexpected_new_position`/`unexpected_missing_position`/
  `unexpected_quantity_difference`/`sell_intent_exceeds_actual_qty`) — 로컬 주문 흔적 있으면 억제.
  `sync_reconciliation_state()`가 ① pending SELL intent를 **이미 자동 축소**(actual 감소분 차감→clamp→
  0 이하 pop; **무음, 감사 흔적 없음**) ② missing/qty-감소 이벤트에 `record_symbol_exit(
  exit_reason="manual_or_reconciled")` 기록(→ `app/strategy/reentry.py`의 fallback cooldown 분기
  `blocked_churn_risk`로 흡수 — **이미 안전한 재진입 처리 존재**) ③ baseline을 actual로 갱신(이벤트는
  drift 발생당 자연 1회성).
- 호출: `app/runtime/cycle_phases/account_snapshot.py:266` — `_sync_reconciliation_state`는 main에서
  exact-name kw-only 주입(테스트 lambda 대체: `tests/test_account_snapshot_phase.py:71`).
- 스냅샷: `cycle_snapshots.py:436-437`이 `last_reconciliation_summary/events`를 그대로 embed.
- Slack 통지 **없음**. `app/tools/preopen_operator_brief.py`(build_brief)에 수동매매 섹션 **없음**
  (runtime_state를 읽지 않음 — health/budget/core 합성만).

**갭 = todo §16 미충족분**: ① 이벤트가 "오류" 명명뿐, "외부 수동 체결 가능성" 분류 없음 ② intent
축소가 무음(감사 로그 없음) ③ 운영자 통지 없음 ④ 다음 세션 전 brief 표시 없음.

## 1. 설계 원칙

- **관측 강화만, 산술 불변.** 기존 축소 산술·record_symbol_exit 호출·이벤트 type 문자열·기존 dict
  키는 전부 불변(additive only). 로컬 상태 자동 "보정" 신규 추가 없음(todo 주의사항 준수).
- 브로커/주문 API 호출 금지. 통지 실패는 삼킨다(운영 경로 보호).
- 신규 state 키는 bounded. main.py는 import+call-site 배선만(net-new def 금지).

## 2. R-a — 분류 강화 + 의심 원장 (`app/core/reconciliation.py`)

- 각 이벤트 dict에 **additive** 필드 2개: `detected_at`(KST iso), `classification`:
  - `unexpected_new_position` → `"suspected_manual_buy"`
  - `unexpected_missing_position` → `"suspected_manual_sell"`
  - `unexpected_quantity_difference`: actual<expected → `"suspected_manual_partial_sell"`,
    actual>expected → `"suspected_manual_buy"`
  - `sell_intent_exceeds_actual_qty` → `"unexplained"`
- `sync_reconciliation_state()`에서 suspected_manual_* 이벤트 발생 시
  `state["manual_trade_suspects_by_symbol"][symbol]` upsert:
  `{first_detected_at, last_detected_at, last_event_type, classification, expected_qty, actual_qty,
  occurrences}`. drift 해소된 심볼(이번 sync 이벤트 없음 + suspects에 존재 + resolved_at 없음)은
  `resolved_at` 기록(엔트리 유지). 총 엔트리 **30개 상한 FIFO**(first_detected_at 오름차순 drop).
- `record_symbol_exit` 호출 로직 **불변** — 특히 suspected_manual_buy에 exit 기록 추가 금지.
- TDD: ① new_position 이벤트에 classification/detected_at 부여 ② qty-감소 → partial_sell 분류
  ③ suspects upsert(occurrences 증가) ④ drift 해소 → resolved_at ⑤ 31번째 심볼 → 최고령 drop
  ⑥ 기존 이벤트 키/type 불변 단언(스냅샷 호환).

## 3. R-b — pending intent 축소 감사 흔적 (`app/core/reconciliation.py`)

- 기존 축소 산술 **한 줄도 변경 금지**. 축소/해제 발생 시 별도 리스트
  `report["intent_adjustments"]` + `state["last_intent_adjustments"]`에
  `{symbol, before_qty, after_qty, cause, detected_at}` append —
  cause ∈ `"position_gone"`(pop by current_qty<=0) / `"position_shrunk"`(감소분 차감) /
  `"clamped_to_actual"`(min clamp) / `"drained"`(차감 결과 0 pop). 변화 없으면 기록 없음.
- **[게이트 C4, 필수]** `state["last_intent_adjustments"]`는 **상한 50 FIFO**
  (`recent_orders[-50:]` 선례, runtime_state.py:460) — 51번째 append 시 최고령 drop.
  근거: `save_runtime_state`는 전체 dict를 dump하고 `load_runtime_state`는 250MB
  `read_text_bounded` 한도 초과 시 **전체 상태를 무음 리셋**(runtime_state.py:371-372) —
  unbounded append는 381MB 인시던트의 재판. report 쪽 리스트는 state에 저장되지 않는 경우에만
  무상한 허용.
- `print_reconciliation_report()`에 adjustments 있으면 1줄/건 출력(최대 5건, 기존 형식 준수).
- TDD: ① 수동 매도로 intent 축소 → adjustment 1건(before/after 정확) ② 변화 없음 → 빈 리스트
  ③ **기존 `tests/test_reconciliation_helpers.py` 전부 무수정 green**(산술 불변의 증거)
  ④ **[C4]** 51번째 adjustment → 최고령 drop, `len(state["last_intent_adjustments"]) <= 50`.

## 4. R-c — Slack 운영자 통지 (신규 `app/notifications/reconciliation_alerts.py` + slack.py 3행 + 배선)

- `slack.py`: `RECONCILIATION_EVENT_TYPE = "reconciliation_drift"` + operator 채널 라우팅
  (AUTOTUNER_EVENT_TYPE 3행 패턴, slack.py:108-109,123 준거 — kis-ops 선례 그대로).
- `reconciliation_alerts.py`: `def maybe_notify_manual_trade_suspects(report, *, notify) -> bool` —
  suspected_manual_* classification 이벤트만 취합해 **메시지 1건**(심볼별 1줄: 유형·expected→actual),
  이벤트 없으면 no-op(False). `account_alerts.py`의 문체/구조 준거.
  (자연 1회성이라 latch 불요 — baseline 갱신으로 다음 cycle 재발화 없음.)
- **[게이트 C5a, 필수]** 함수 **본문 전체**(메시지 조립 포함, notify 호출만이 아님)를 단일
  `try/except Exception → return False`로 감싼다. 근거: 호출 지점이 account_snapshot.py 외곽
  try(L110-411) 내부 — 예외가 새면 정규 모드에서는 사이클 재raise, scan_only에서는 141행
  진단 폴백을 오발동시킨다. malformed report(키 누락·비-dict 이벤트)도 예외 없이 no-op.
- 배선: `account_snapshot.py`의 phase 함수에 **kw-only param `_notify_reconciliation=None`**
  (None → no-op) 추가, `ctx.reconciliation_report` 대입 직후 호출. main.py는 import 1행 +
  기존 call site에 `_notify_reconciliation=...` 1행(기존 함수 내부 수정 — boundary 준수).
  기존 phase 테스트는 param 미전달 → no-op으로 무수정 green이어야 한다.
- **[게이트 C5b, 필수]** 호출부는 `if _notify_reconciliation is not None:` 가드 형태만 허용.
  phase 모듈은 notify 협력자를 **import하지 않는다**(kw param 전용 — `_sync_reconciliation_state`
  선례, blocklist 규율).
- TDD: ① suspected 이벤트 2심볼 → notify 1회·메시지에 2줄 ② 이벤트 없음/unexplained만 → no-op
  ③ notify 예외 삼킴 + **[C5a]** 메시지 조립 전 예외(malformed report)도 삼킴 ④ 이벤트 타입
  operator 채널 라우팅 단언 ⑤ phase 주입: None이면 호출 안 됨, 스파이 주입 시 report 전달 확인
  ⑥ **[C5b]** 예외 던지는 스파이 주입 → phase가 정상 반환(scan_only 폴백 미진입).

## 5. R-d — preopen operator brief 섹션 (`app/tools/preopen_operator_brief.py`)

- runtime_state **read-only 로드**(계정 스코프드 로더 — `app/runtime_state.py`의 기존 load 함수를
  재사용; 실제 함수명은 구현 시 확인, 신규 저장 경로 발명 금지). 실패/부재 시 섹션은 "정보 없음".
- `build_brief()` 반환에 `"manual_trades"` 키: suspects 중 `last_detected_at`이 최근 2 KR
  달력일 내인 엔트리(resolved 포함, resolved는 표시 구분) + `last_intent_adjustments`.
- `print_terminal()`에 섹션 추가: 의심 있으면 `_warn`(심볼|분류|expected→actual|resolved 여부),
  없으면 `_dim("수동 매매 의심 없음")`. `--json` 자동 포함.
- **[게이트 C8]** "state 로드 불가(정보 없음)"와 "의심 없음"은 **서로 다른 문자열**로 출력
  (상태 리셋/부재를 '수동매매 없음'으로 오독 금지) — 두 분기 문자열 상이함을 테스트로 단언.
- TDD: ① suspects 있는 합성 state → 섹션 내용 ② 없음 → dim 문구 ③ state 파일 부재 → "정보 없음"
  (예외 없음) ④ **[C8]** ②·③ 분기 문자열 상이.

## 6. Risk-gate 검증 조건 (기계적 — 2026-07-04 risk-analyst 게이트 판정 PROCEED-WITH-CONDITIONS 반영)

1. `tests/test_reconciliation_helpers.py` **무수정** 전부 green (축소 산술·exit 기록 계약 불변;
   게이트 사전 baseline: reconciliation+account_snapshot 34 passed 확인됨).
2. 이벤트 dict의 기존 키·type 문자열 diff 없음 (additive 필드만) — cycle_snapshots 소비자 호환.
   **+[C2 확장]** additive 필드가 `save_runtime_state`/`load_runtime_state` 라운드트립에서
   보존됨을 테스트로 단언(`_normalize_state` L329-330 passthrough 가드).
3. `record_symbol_exit` 호출부 diff 없음 (신규 호출 0 — grep으로 단언 가능해야 함).
4. suspects 상한 30 FIFO 테스트 + **[C4]** `last_intent_adjustments` 상한 50 FIFO 테스트
   존재·green (unbounded state 성장 금지 — 250MB 무음 리셋 방지).
5. **[C5a]** 통지 헬퍼 본문 전체 try/except(조립 예외 포함) 테스트 존재·green;
   `reconciliation_alerts.py`에 브로커/주문 API import 없음.
   **[C5b]** 호출부 None 가드 + 예외 스파이 phase 테스트(scan_only 폴백 미진입) 존재·green.
6. `account_snapshot.py` 주입 param 기본 None no-op — `tests/test_account_snapshot_phase.py`
   무수정 green + None→미호출/스파이→report 전달 테스트 추가.
7. `app/main.py` net-new top-level def/class 0 (import + kw 배선만; trading_guard 통과).
   phase 모듈에 main-patched 이름 신규 import 0 — 패치표면 이중 grep(multiline patch.object +
   `"app.main.X"` 점표기) 확인.
8. **[C8]** brief의 "정보 없음" vs "의심 없음" 문자열 상이 테스트 green.
9. `.venv/bin/python -m pytest` full green.

## 7. 에스컬레이션

- runtime_state 로더가 계정 스코프 경로를 노출하지 않으면 중단·보고(경로 발명 금지).
- 기존 reconciliation 테스트와 스펙 충돌 시 산술 변경 대신 중단·보고.
- Slack 채널 상수/라우팅 구조가 §4 준거와 다르면 중단·보고.
