# 주문로그/스냅샷 라인 비대 → 매수 전면 차단(fail-closed) 해결 설계안 — 2026-07-07

## 0. 증상 (이 세션 실측)

2026-07-07 신규 대회 계좌(`mock_acct_fedcba9876543210`) 세션에서 **당일 체결 0건**.
43사이클 집계: `executed_order_count` 합 0, `buy_non_execution_reason` = `order_guard_blocked` 19건.
주문로그 tail 실측: `action: "blocked_buy_order_log_untrusted"`,
`reason: "주문 로그를 신뢰할 수 없어 리스크 가드를 fail-closed로 차단합니다."`,
`order_log_error_code: "order_log_too_large"`.

- 주문로그 `logs/orders_mock_acct_fedcba9876543210.jsonl`: 최근 라인 **1,260,523~1,312,531 bytes**,
  하루 만에 파일 63.8MB.
- 라인 분해: 1,279,305B 중 `raw_response.selection_details.candidates` = **1,272,925B (99.5%)**
  — 후보 103종목 × ~12.4KB(종목당 feature_map/feature_vector/score_components 등 전체 평가 payload).
- cycle_snapshots `data/cycle_snapshots_mock_acct_fedcba9876543210.jsonl`: 최근 30라인 중
  15라인이 **2,485,719~3,258,621 bytes** (같은 `selection_details` 벌크 + 부가 payload).

## 1. 근본 원인 사슬 (코드 인용)

```
serialize_selection_details()가 후보 전종목의 전체 평가 payload를 candidates로 직렬화
  (app/scanner/presentation.py:99-159 — feature_map·feature_vector·score_components 등 ~30필드/종목)
    │  신규 계좌 조건에서 deep_eval_count 94~103 (cycle_stats 실측; shallow_shortlist_size=0)
    │  → candidates ≈ 1.27MB
    ├─ Sink A: buy_flow의 log_order_event 19/22개 호출부가 raw_response에 통째 내장
    │    (app/execution/buy_flow.py — `"selection_details": selection_details` 19곳)
    │    → orders_*.jsonl 라인 1.26~1.31MB
    │    → 판독: iter_lines_bounded의 줄당 캡 DEFAULT_LOCAL_LINE_MAX_BYTES=1MB 초과
    │      (app/core/file_read_limits.py:9,70-73) → LocalReadLimitError
    │    → strict 판독 변환: OrderLogReadError("order_log_too_large")
    │      (app/core/order_log.py:82-87)
    │    → 가드 fail-closed: evaluate_order_log_integrity가 매수 전체 차단
    │      (app/risk/guards.py:60-64,195-213 — 일일 주문 수/명목 한도 계산 불능 → 차단)
    │    → 자기치유 없음: 계좌별 append-only 파일에 오염 라인 영구 잔존, 매 사이클 재실패.
    │      차단 기록 자체도 full candidates를 재내장(1.28MB) → 자기재생산.
    └─ Sink B: finalize가 ctx.selection_details를 스냅샷에 그대로 전달
         (app/runtime/cycle_phases/finalize.py:475 → app/reporting/cycle_snapshots.py:377)
         → snapshot 라인 2.5~3.3MB
         → 판독: read_jsonl_objects가 첫 초과 라인에서 **파일 전체 드롭**
           (app/core/jsonl.py:79-80 — `return [], [오류]`)
         → math_models 가격 히스토리·분석 도구가 조용히 빈 결과 수신 (무알림 데이터 손실)
```

**관측성 공백**: 두 실패 모두 알림 없음. cycle_stats 분류기(`app/reporting/runtime_snapshots.py:326`)가
이 상태를 범용 `order_guard_blocked`로 뭉뚱그려 운영자가 물어보기 전까지 발견 불가.

**왜 이제 터졌나** — 검증됨: candidates 크기 ∝ deep_eval_count이고 오늘 94~103(캡의 1.27배).
구계좌 주문로그 마지막 라인은 5,264B. 미검증 가설(구계좌 장중 라인 크기는 로그 전체읽기 금지로
표본 불가): 구계좌는 축적 데이터로 shortlist가 deep-eval을 소수로 제한했거나 후보 payload가
피처 확장 이전이라 캡 미만이었음. **W0/W1 인베리언트는 트리거와 무관하게 이 결함 클래스를
제거하므로 가설 확정은 불요.**

## 2. 설계 원칙

1. **Writer가 reader의 계약을 강제한다** — strict 판독기가 못 읽는 라인은 애초에 쓰지 않는다.
2. **주문로그는 주문 감사 로그다** — 스캔 아카이브가 아니다. 벌크의 정본은 이미 따로 있다
   (스냅샷의 `scanner_candidates_top` top-N 요약: `cycle_serializers.py:171`; 연구용 피처는
   `signal_outcomes_*.csv` 전용 데이터셋).
3. **fail-closed 시맨틱은 유지한다** — 가드(`evaluate_order_log_integrity`)는 옳았다. 입력을
   고치지, 가드를 무디게 하지 않는다.

**소비자 전수조사 (이번 세션 grep 실측)** — 축약해도 되는 근거:
- `orders_*.jsonl`의 `candidates`를 읽는 소비자: **0곳**.
- `cycle_snapshots`의 `candidates`를 읽는 소비자 2곳: `app/math_models/history.py:130`,
  `app/tools/analyze_technical_feature_activation.py:241` — 둘 다
  `symbol` + `market_snapshot.{current_price,open_price,low_price,prev_day_change_pct}`만 소비.
- 주문로그 `raw_response`의 실소비 필드(성과 리포터): `position_sizing`, `order_plan`,
  `sell_plan`, `sell_strategy_details`, `reference_price_krw`, `fill_price_krw`
  (`performance_report.py:68-86`, `fill_slippage.py:107-115`, `performance_metrics.py:84-86`,
  `performance.py:556-560`) — 전부 보존 대상.

## 3. 수정안 (W0 근본 + W1/W2 인베리언트 + R1 복구 + O1 관측성)

### W0 · candidates 직렬화를 compact 스키마로 (근본 수정)

**대상**: `app/scanner/presentation.py::serialize_selection_details`

candidates 항목을 화이트리스트 필드로 축약 생성 (`candidates_schema: "compact_v1"` 마커 추가):

- **보존** (소비자 계약): `symbol`, `name`, `candidate`, `passed_count`, `score`,
  `market_snapshot` 4필드, 비용 요약(`expected_cost_bps`, `net_edge_bps`, `cost_block_reason`),
  `score_summary`(문자열 1개).
- **제거** (소비자 0, 종목당 ~11KB): `feature_map`, `feature_vector`, `feature_summaries`,
  `score_components`, `score_highlights/penalties`, math/포트폴리오 요약 문자열군.
- 예상 효과: 종목당 ~12.4KB → ~0.4KB; 103종목 candidates ≈ **40KB** (캡의 4%).
  두 sink 모두 자동 정상화 — **buy_flow 19개 호출부 무수정** (트레이딩 크리티컬 표면 비접촉).
- 선택 종목 1개의 전체 상세는 이미 `strategy_details`/`selected_buy_candidate`로 별도 보존됨.

### W1 · 주문로그 라인 인베리언트 (마지막 방어선)

**대상**: `app/core/order_log.py::log_order_event`

직렬화 후 `len(line) > ORDER_LOG_LINE_SOFT_MAX_BYTES`(신설 상수, 기본 256_000)이면:
1. `raw_response`를 요약으로 대체: `{"truncated": true, "original_bytes": N,
   "oversize_keys": {키: 바이트}, ...보존 필드(§2의 실소비 필드는 유지)}`
2. stderr `[warn]` 1줄. 어떤 경우에도 **판독 캡을 넘는 라인을 append하지 않는다.**
W0가 정상 경로, W1 발동은 "새 벌크 유입" 신호로 남는다.

### W2 · 스냅샷 라인 인베리언트

**대상**: 스냅샷 append 지점 (`build_cycle_snapshot` 산출물을 쓰는 writer)

동일 바이트 가드(soft cap 900KB): 초과 시 `selection_details.candidates` 드롭 +
`selection_details_truncated: true` 마커. `scanner_candidates_top`(top-5 요약)은 항상 보존.

### R1 · 판독 헤드룸 — 기존 오염 라인 무해화 (복구 경로, 파일 수술 없음)

- `app/core/order_log.py::_read_log_lines_cached`: `iter_lines_bounded(...,
  max_line_bytes=ORDER_LOG_READER_LINE_MAX_BYTES)`(신설, 기본 8_000_000).
- `app/core/jsonl.py::read_jsonl_objects`: `max_line_bytes` 파라미터 추가(기본 현행 유지),
  cycle_snapshots 판독 경로만 8MB 지정.
- 효과: 오늘 쌓인 1.3MB 주문로그 라인 ~48개·3.3MB 스냅샷 라인 ~15개가 **재시작 즉시 판독 가능**
  → 매수 차단 해제 + 히스토리 복원. fail-closed 시맨틱 불변(진짜 malformed·stat 실패·8MB 초과는
  여전히 차단) — 한도만 상향, 예산 과소계상 없음.
- 대안 기각: 오염 라인 재작성 도구 — 라이브 append 파일 수술은 레이스·감사추적 훼손 위험이
  헤드룸 상향보다 크다. 총량 캡(250MB) 대비 63.8MB라 여유 있음.

### O1 · 관측성 (이번 장애가 조용히 지나간 것 자체가 결함)

- `order_log_integrity` FAIL 감지 시 **세션-dedup 1회 알림** (Slack/Telegram — `app/main.py:382-401`
  `account.blocked` 관용구 재사용). 발화 지점은 트레이딩 크리티컬 밖(리포팅 층에서 guard_results 소비).
- `runtime_snapshots.py` funnel 분류기에 `order_log_untrusted` 전용 버킷 추가
  (현행: `order_guard_blocked`에 합산돼 식별 불가).
- (P2) `read_jsonl_objects`의 errors를 대시보드 `data_quality`에 표면화.

### G1 · (P2) 파일 총량 가드

startup sanity(`runtime_validation.py`)에 orders/snapshots 파일 200MB 초과 WARN —
250MB 캡 도달 전 D2 아카이브 도구(`app/tools/archive_account_scope.py`) 유도.

## 4. TDD 슬라이스 (각각 소형·독립; RED→GREEN)

| # | 내용 | 대상 | 핀 테스트 (신설 `tests/test_order_log_line_limits.py` 외) |
|---|------|------|------|
| S1 | W0 compact 직렬화 | `presentation.py` | 화이트리스트 필드만 존재·소비자 5필드 보존·103후보 직렬화 ≤ 200KB·schema 마커 (`tests/test_scanner_presentation*.py` 기존 파일 확장) |
| S2 | R1 판독 헤드룸 | `order_log.py`, `jsonl.py` | 1.3MB 라인 픽스처 strict 카운트 성공 / 9MB 라인은 여전히 `order_log_too_large` / `read_jsonl_objects` 파라미터 |
| S3 | W1 주문로그 인베리언트 | `order_log.py` | 합성 2MB raw_response → 기록 라인 ≤ soft cap·truncated 마커·§2 실소비 필드 보존·strict 재판독 성공 |
| S4 | W2 스냅샷 인베리언트 | 스냅샷 writer | 동일 패턴 + `scanner_candidates_top` 보존 |
| S5 | O1 알림·버킷 | `runtime_snapshots.py` | dedup 1회 발화·`order_log_untrusted` 버킷 분류 |

- 완료 기준: 전체 스위트 green + `/completion-audit`(핀 커버리지·스테일 import).
- 게이트: 코드가 리스크 가드의 **입력**(주문로그)과 스캐너 직렬화를 바꾸므로 통합 직전
  `/adversarial-verify` — 특히 "compact가 성과 리포터·math 히스토리 소비 필드를 하나라도
  깨뜨리는가"를 렌즈로.

## 5. 롤아웃 (운영자 게이트 포함)

1. **[에이전트]** S1~S5 TDD 구현 + 전체 스위트 + adversarial-verify.
2. **[운영자]** 세션 재시작 — `launchctl kickstart -k gui/501/com.kis-trader.session` 단발
   (pkill+이중 kickstart 금지 — stale-lock 레이스).
3. **[운영자·검증]** 재시작 첫 매수 사이클에서 콘솔 `order_log_integrity: PASS` +
   `blocked_buy_order_log_untrusted` 미발생 확인. cycle_stats에서 `order_guard_blocked` 소멸 확인.
4. 기존 오염 라인은 R1 헤드룸으로 흡수 — 파일은 건드리지 않는다.
5. **재시작 전까지는 매수 차단이 지속됨** — 장중이면 조속 배포 권고. (SELL은 영향권 분석 필요:
   sell 가드도 동일 주문로그를 strict로 읽으므로 sell_daily_max 계산 실패 시 동일 차단 —
   S2 픽스처에 sell 카운트 케이스 포함.)

## 6. 리스크 레지스터

| # | 리스크 | 가능성/영향 | 완화 |
|---|--------|------------|------|
| R-A | compact가 미지의 candidates 소비자를 깨뜨림 | Low/Med | 전수 grep 소비자 2곳뿐(§2)·소비 필드 화이트리스트 보존·S1 핀 테스트 |
| R-B | 8MB 헤드룸이 라인 비대 재발을 은폐 | Med/Low | W1/W2 인베리언트가 애초에 못 쓰게 함 + truncated 마커·stderr warn으로 가시화 |
| R-C | 재시작 전 매수 공백 지속 | 확정/Med | §5-5 조속 배포; 차단은 안전 방향(fail-closed)이라 금전 리스크 없음 |
| R-D | strict 완화로 일일 예산 과소계상 | — | 시맨틱 불변(한도 상향만); malformed·초과는 여전히 차단 |
| R-E | 연구 데이터셋(피처) 손실 | Low/Low | full 피처의 정본은 `signal_outcomes_*.csv`(별도 파이프라인, `export_signal_dataset.py`는 `staged_scan`만 소비 — grep 실측) |
| R-F | 트레이딩 크리티컬 접촉 | — | buy_flow/guards 무수정 설계(W0가 상류에서 해결). 그래도 가드 입력 변경이므로 `/risk-assessment` 1회 권장 |

## 7. 이번에 고치지 않는 것 (명시적 비범위)

- `evaluate_order_log_integrity`의 fail-closed 정책 완화 — 유지(설계 §2-3).
- 주문로그 일자별 분할/로테이션 — 일일 카운트 판독 경로(`get_order_log_read_paths`) 재설계
  수반, 총량 가드(G1)로 충분.
- 기존 63.8MB 파일 축소 수술 — R1로 무해화, 아카이브는 대회 종료 시 D2 도구로.
- G1(총량 WARN)·(P2) data_quality 표면화 — 후속(설계 P2 표기). 이번 구현 범위는 S1~S5.

## 8. 구현 완료 (2026-07-07, TDD RED→GREEN)

| # | 슬라이스 | 대상 파일 | 핀 테스트 | 상태 |
|---|---------|----------|----------|------|
| S1 | W0 compact 직렬화 | `app/scanner/presentation.py` | `test_scanner_presentation.py::SerializeSelectionDetailsTests` (compact exact·bulk부재·소비자계약·120종목<200KB) | ✅ |
| S2 | R1 판독 헤드룸 | `app/core/order_log.py`·`jsonl.py`·`reporting/cycle_snapshots.py`·`math_models/history.py` | `test_order_log_line_limits.py` (OrderLogReadHeadroom×2·JsonlReadHeadroom×2·SnapshotReaderHeadroom×1) | ✅ |
| S3 | W1 주문로그 인베리언트 | `app/core/order_log.py` | `test_order_log_line_limits.py::OrderLogWriteInvariantTests`×2 | ✅ |
| S4 | W2 스냅샷 인베리언트 | `app/reporting/cycle_snapshots.py` | `test_order_log_line_limits.py::SnapshotWriteInvariantTests`×2 | ✅ |
| S5 | O1 관측성 | `reporting/runtime_snapshots.py`·`notifications/account_alerts.py`·`runtime/cycle_phases/finalize.py` | funnel버킷×3·emitter×3·finalize배선×1 | ✅ |

- **핵심 상수**: `ORDER_LOG_READER_LINE_MAX_BYTES=8MB`(판독 헤드룸)·`ORDER_LOG_LINE_SOFT_MAX_BYTES=256KB`(주문로그 write)·`SNAPSHOT_READ_LINE_MAX_BYTES=8MB`(스냅샷 판독)·`SNAPSHOT_LINE_SOFT_MAX_BYTES=900KB`(스냅샷 write). W0 실측: candidate당 12,382B→409B(103종목 ~42KB, 캡의 4%).
- **트레이딩 크리티컬 무접촉**: `app/risk/guards.py`·`app/execution/buy_flow.py`·`sell_flow.py` 무수정 — W0가 상류(직렬화)에서 근본 해결, O1 알림은 리포팅 층(finalize)에서 발화.
- **검증**: 전체 스위트 `3258 passed, 1 skipped, 719 subtests`(착수 전 3239 대비 +19 신규, 신규 실패·스킵 0). 적대검증(R-A): `selection_details.candidates` 소비자 전수 2곳(`analyze_technical_feature_activation.py:243`·`math_models/history.py:130`) 모두 `symbol`+`market_snapshot`만 소비 — compact 보존 필드와 정합. 리치 소비자(ML/버킷 분석)는 `scanner_candidates_top`(미변경, top-5·~60KB) 경로. 스테일 import 0·8파일 py_compile OK.

### 8-1. 운영자 롤아웃 (필수 — 현재 매수 차단 지속 중)

코드는 배포됐으나 **라이브 세션(PID 79667)은 구 코드로 가동 중** — 재시작 전까지 오늘 쌓인
1.3MB 오염 라인 때문에 매수 차단이 계속된다. 재시작 시:
1. `launchctl kickstart -k gui/501/com.kis-trader.session` (단발 — pkill+이중 kickstart 금지, stale-lock 레이스).
2. 재시작 즉시 R1 헤드룸이 기존 오염 라인(주문로그 ~48개·스냅샷 ~15개)을 판독 → 매수 차단 해제.
3. 이후 신규 라인은 W0 compact(~42KB)로 기록 → 재발 없음.
4. 검증: 콘솔 `order_log_integrity: PASS`·`blocked_buy_order_log_untrusted` 미발생, cycle_stats에서 `order_guard_blocked` 소멸.
- 파일 수술 불요(R1로 무해화). 커밋은 코드/문서 분리.
