# 대회 리셋(자산 초기화) 대응 — 로그 저장 설계 + 초기화 리스크 해결설계안 (2026-07-06)

새 모의투자 대회 개시로 **자산이 초기화**되고 계좌 시그니처가 회전된 상황에서,
(1) 대회 단위 로그를 어떻게 저장·보존·구분할지, (2) 초기화로 생길 수 있는 문제와 해결책을 설계한다.

선행 문서: `mock_account_rotation_review_20260706.md` (not included in this source snapshot)
(자격증명 회전 리스크·토큰 캐시 설계 A/B/C), [`runbooks/credential_rotation.md`](runbooks/credential_rotation.md).
이 문서는 그 후속 — **자산·이력 관점**의 리셋을 다룬다.

---

## 1. 현행 로그 구조 (근거 확인 완료)

계좌 상태·거래 로그는 전부 **계좌 시그니처**(sha256(env·CANO·상품코드·key·secret)[:16],
`app/auth/account_scope.py:40-54`)로 키잉된다. 시그니처가 바뀌면 새 파일 세트가 시작되고
구 파일은 그대로 남는다.

**시그니처-스코프 아티팩트 8패밀리** (`app/dashboard/account_discovery.py:39-48` `_SIGNATURE_SOURCES`):

| 위치 | 파일 | 파티션 |
|------|------|--------|
| `data/` | `runtime_state_{sig}.json` | — |
| `data/` | `cycle_snapshots_{sig}.jsonl` | — |
| `data/` | `performance_snapshots_{sig}.jsonl` | — |
| `logs/` | `orders_{sig}.jsonl` | — |
| `logs/` | `performance_summary_{sig}.jsonl` | — |
| `logs/` | `cycle_stats_{sig}_{YYYYMMDD}.jsonl` | 일별 |
| `logs/` | `candidate_outcomes_{sig}_{YYYYMMDD}.jsonl` | 일별 |
| `logs/` | `backtest_signals_{sig}_{YYYYMMDD}.jsonl` | 일별 |

계정-무관 파일(`data/live_snapshot.json`, `data/runtime/market_data_quality.json` 등)은 시장
데이터라 리셋과 무관. `data/account_scope_meta.json`은 **last/previous 단일 슬롯**만 보유
(`account_scope.py:223-247`) — 시그니처 이력이 2세대를 넘으면 유실된다.

## 2. 로그 저장 설계 (대회 단위 보존·구분)

### 문제 정의

- **P1**: 시그니처는 불투명 해시 — 파일만 보고 "어느 대회 계좌인지" 사람이 알 수 없다.
  meta가 단일 슬롯이라 다다음 대회부터는 매핑 자체가 사라진다.
- **P2**: 은퇴 시그니처의 8패밀리 파일이 `data/`·`logs/`에 영구 잔존 — 디스커버리
  (`discover_accounts`)가 디스크 스캔 기반이라 멀티계좌 대시보드에 죽은 계좌가 계속 나타나고,
  일별 파티션 파일은 대회가 거듭될수록 누적된다.
- **P3**: 대회 간 성과 비교(직전 대회 equity curve vs 이번 대회)를 하려면 시그니처↔대회
  매핑과 파일 위치가 안정적이어야 하는데 현재는 관행에 의존한다.

### D1 — 계좌 스코프 원장 (append-only ledger) 【P1·P3 해결, 소형 코드】

상태: ✅ 구현 완료(2026-07-06). `sync_account_scope_meta()`가 스코프 변경 시
`data/account_scope_history.jsonl`에 append-only 기록을 남긴다.

> **알려진 한계(감사 확인)**: 원장은 D1 배포 이후의 스코프 변경만 기록한다. meta는
> last_* 단일 슬롯이라 과거 시그니처를 소급 복원할 수 없으므로, D1 이전 회전(2026-07-06
> 회전 포함)의 은퇴 시그니처는 D2를 `--signature` 명시로 사용한다(런북 3c 갱신됨).
> 다음 회전부터는 `--retired`가 정상 동작한다.

`data/account_scope_history.jsonl` 신설. `sync_account_scope_meta()` 확장:

- 스코프 변경 감지 시(기존 `account_scope_changed` 분기) 한 줄 append:
  ```json
  {"signature": "mock_acct_ab12…", "environment": "mock",
   "masked_display": "1234***56-01", "label": "mock-contest-2026H2",
   "first_seen_at": "...", "retired_previous": "mock_acct_9f8e…"}
  ```
- `label`은 신규 env `KIS_ACCOUNT_LABEL`(선택, 미설정 시 빈 값) — 대회명을 사람이 지정.
  시크릿 아님 → `.env`가 아닌 세션 env 파일/launchd plist 어느 쪽이든 가능.
- 원문 계좌번호·키는 **기록하지 않는다** (masked_display만 — 기존 관용구 유지).
- 쓰기 실패는 기존 meta와 동일하게 swallow (`OSError → pass`) — 세션 기동을 막지 않는다.
- 수용 테스트: 변경 시 1줄 append / 무변경 시 append 없음 / 원장 파손(비JSON 줄) 시에도
  총함수성 유지 / label env 반영.

### D2 — 은퇴 시그니처 아카이브 도구 【P2 해결, 신규 tool】

상태: ✅ 구현 완료(2026-07-06). `app/tools/archive_account_scope.py` (읽기+이동만,
브로커 API 없음):

- 입력: `--signature <sig>` (반복) 또는 `--retired`(원장 기준 현재-활성 아닌 전부).
  **현재 활성 시그니처(`get_account_signature()`)는 어떤 경우에도 제외** — 가드 필수.
- 동작: 8패밀리 전부(일별 파티션 glob 포함)를
  `archive/accounts/<retired_date>_<label>_<sig>/`로 **이동** + `MANIFEST.json` 생성
  (파일 목록·바이트·기간(파일명 날짜 min/max)·이동 시각·label).
- **기본 dry-run** — 이동 대상 목록만 출력, `--apply` 플래그를 줘야 실이동.
  파일 이동은 파괴적 조작이므로 실행은 운영자 게이트(에이전트는 dry-run까지만).
- 효과: `discover_accounts`가 스캔하는 `data/`·`logs/`에서 은퇴 파일이 사라져 대시보드
  노이즈 소멸; 분석은 아카이브 경로로 계속 가능(§D3).
- 수용 테스트: `tests/test_archive_account_scope_tool.py` — tmp 픽스처로 8패밀리 이동·
  활성-시그니처 거부·dry-run 무이동·manifest 스키마.

### D3 — 분석 경로의 아카이브 지원 【P3 해결, 선택】

`dashboard_paths_for_signature(signature, data_dir=…, logs_dir=…)`가 이미 디렉터리 주입을
받으므로 (`account_scope.py:97-118`), quant 분석·대시보드에서 아카이브된 대회는
`archive/accounts/<…>/` 를 data/logs 루트로 넘기면 된다. 코드 변경은 선택사항
(원장 label→시그니처→경로 resolve 헬퍼 하나 정도), 우선은 **문서/런북 규약**으로 충분.

### D4 — 런북 편입 【절차】

`runbooks/credential_rotation.md`에 회전 절차 후속 단계로 추가:
"구 대회 시그니처 아카이브(D2 dry-run → 운영자 --apply)" + "KIS_ACCOUNT_LABEL 지정".
(이번 세션에서 런북에 사전 항목으로 반영 — 도구 완성 전에는 수동 mv 가이드.)

### 보존 정책 (기본값 제안)

- 아카이브는 **삭제하지 않는다** — 대회 단위 성과 비교가 이 프로젝트의 연구 자산.
- 일별 파티션 로그만 아카이브 시점에 `gzip` 압축 옵션(`--compress`) — 선택.

## 3. 초기화(자산 리셋) 리스크 레지스터 + 해결

### RR-1 · 절대 KRW 캡이 새 시드 자본에 비례하지 않음 — **Medium** (실측 근거)

상대(%) 파라미터는 자동 스케일되지만, 절대액 3종은 아니다
(`app/auth/settings_fields.py:256-264, 722-728`; 기본값 기준):

| 파라미터 | 기본값 | 성격 |
|----------|--------|------|
| `BUY_MAX_BUDGET_PER_TRADE_KRW` | 1,000,000 | 건당 매수 상한 (절대) |
| `BUY_DAILY_MAX_NOTIONAL_KRW` | 500,000 | 일일 매수 총액 상한 (절대) |
| `SELL_DAILY_MAX_NOTIONAL_KRW` | 7,000,000 | 일일 매도 총액 상한 (절대) |
| `BUY_MAX_ACCOUNT_EXPOSURE_PCT` | 10% | 상대 — 자동 스케일 ✅ |

새 대회 시드가 구 대회와 다르면(예: 1억→3억) 절대 캡의 **상대적 의미가 왜곡**된다 —
과소면 전략 스로틀링(매수 기회 상실), 과대면 리스크 예산 초과. 오류로 터지지 않고
**조용히 캘리브레이션이 틀어지는** 유형이라 감지가 어렵다.

**해결 설계**:
- (즉시, 절차) 런북 체크리스트에 "새 시드 자본 확인 → 절대 캡 3종을 목표 비율로 재산출"
  추가 — 이번 세션 반영.
- (코드, 소형) **capital-scale report**: ✅ 구현 완료(2026-07-06). 첫 계좌 스냅샷 확보 시점(잔고 조회 후)에
  `절대캡/총자산` 비율을 1회 출력하고 권장 밴드 이탈 시 warning.
  - 배치: startup sanity가 아니라 **첫 스냅샷 후 리포트** — equity는 런타임에만 알 수 있고,
    startup sanity는 네트워크 이전 단계이므로 프레임이 다르다.
  - 랜딩 존: `app/reporting/capital_scale_report.py` 순수 함수 +
    account_snapshot phase 배선 — main.py 비대화 없음(boundary 준수).
  - 권장 밴드(제안, 운영자 조정 가능): 건당 0.5~3% / 일일매수 0.3~5% / 일일매도 ≤30%.
  - 수용 테스트: `tests/test_capital_scale_report.py`, `tests/test_account_snapshot_phase.py` —
    밴드 내 무경고/이탈 경고/총자산 0·결측 시 무판정(총함수성), 세션 내 1회 출력.
  - 출력 억제 키는 sha256(시그니처+캡 3종)이고 runtime_state에 영속되므로, 실제 의미는
    "**계좌·캡 구성이 바뀔 때마다 1회**"(재기동만으로는 재출력 안 됨 — 의도된 저소음 동작,
    equity 변동은 키에서 제외). 감사 시 문서 문구보다 강한 억제로 확인됨(2026-07-06).

### RR-2 · 시그니처↔대회 매핑 유실 — **Medium** → §2 D1 원장으로 해결.

### RR-3 · 은퇴 파일 잔존·대시보드 노이즈·디스크 누적 — **Low** → §2 D2 아카이브로 해결.

### RR-4 · 분석 도구의 하드코딩 초기자본 — **Low**

`app/tools/shadow_watch.py:145` — 백테스트 서버 페이로드에 `initial_capital: 100_000_000`
하드코딩. 새 대회 시드가 1억이 아니면 **shadow 비교의 자본 베이스가 실계좌와 어긋난다**
(주문 경로 무관, 분석 왜곡만). 해결: `--initial-capital` CLI 인자/env로 파라미터화, 기본값은
현행 유지 — 백로그(분석 도구라 우선순위 낮음).

### RR-5 · 자동으로 안전한 것 (조치 불필요 — 근거)

| 항목 | 근거 | 판정 |
|------|------|------|
| PnL 브레이크·레짐 임계 | `PnlBrakeRegimeFields` 전 필드 `_pct` (`settings_fields.py:1404-1414`), `pnl_brake.py:158` op_pnl_pct 비교 | %기반 — 새 자본에 자동 스케일 ✅ |
| 드로다운 앵커 | `regime.py:49` current_drawdown_pct — 상태 파일 fresh 시작이라 피크=새 시드 | 새 계좌 기준 정상 리셋 ✅ |
| 일일 주문 한도 계정 | `orders_{new_sig}.jsonl` 신규 파일에서 집계 | 리셋이 곧 정답 (빈 계좌=빈 이력) ✅ |
| 포지션 사이징 | 계좌 스냅샷(API 실시간) 기반 | 스테일 자본 참조 없음 ✅ |
| 첫 기동 로그 | `account scope changed` 1회 (main.py:667-690) | 기대 동작, 런북 §4 반영됨 ✅ |

## 4. 구현 우선순위

| 순위 | 항목 | 규모 | 실행 주체 |
|------|------|------|----------|
| 1 | 런북 체크리스트 갱신 (RR-1 절대 캡 재산출 + D4 아카이브 단계) | 문서 | ✅ 이번 세션 |
| 2 | D1 원장 (`sync_account_scope_meta` 확장 + `KIS_ACCOUNT_LABEL`) | 소형 TDD | ✅ 이번 세션 |
| 3 | RR-1 capital-scale report (`app/reporting/` 순수 모듈) | 소형 TDD | ✅ 이번 세션 |
| 4 | D2 아카이브 도구 (dry-run 기본) | 중형 TDD | ✅ 이번 세션 (`--apply`는 운영자) |
| 5 | RR-4 shadow_watch 파라미터화 / D3 resolve 헬퍼 | 백로그 | — |

2~4는 각각 단일 구획이므로 착수 시 `/delegation-plan` 규율(2슬라이스+면 계획서) 적용 판단.

## 5. 설계안-구현 정합 검토 (2회, 2026-07-06) — 리셋 + 토큰/회전 영역

이 검토는 `mock_account_rotation_review` (not included in this source snapshot)(토큰 A/B/C)와
본 문서(D1·RR-1·D2)를 함께 다룬다.

**1차 — 결정적**: `completion_audit.py`(reset 플랜) FAIL이나 지적 전량 오탐 —
#3 "미구현 test_account_snapshot_phase·test_archive_account_scope_tool"는 백틱 파일명을 함수핀으로
오파싱(두 파일 실재 확인), #5 스테일 import는 `__future__ annotations` 오탐(pyflakes 독립검사
미사용 import 0). 대상 테스트 실행: 36 passed, 3 skipped(토큰 rotation self-activating), 전체
스위트 3235 PASS.

**2차 — 적대적(주장 대조)**:
- **토큰 A**: `app/auth/token.py` read/edit 하네스 재차단 재확인(이번 세션 3회 거부) → 배선은
  운영자 게이트 확정. 순수 모듈 `token_cache_identity.py` + self-activating 통합테스트 3개가
  **정확한 사유로 skip**(프로브가 미배선 감지) — 배선 시 자동 PASS 전환 설계 유지 확인.
- **B (CANO 형식)**: `runtime_validation` 검사 + 기존 sanity 무회귀(속성부재 skip) — 테스트 PASS.
- **D1 원장**: `sync_account_scope_meta`가 `account_scope_changed`에서만 append(무변경 시 무기록);
  "D1 이전 회전 미기록→--retired 공백" 한계를 §D1 캐비엇 + 런북 3c `--signature` 경로로 정직 반영.
- **RR-1 capital-scale**: `account_snapshot` phase 배선 + once-per-config(sha256 키 runtime_state 영속)
  — 재기동만으론 무재출력. 테스트 PASS.
- **D2 아카이브**: `ActiveSignatureArchiveError` 활성-시그니처 거부 + `--apply` 필수(기본 dry-run) +
  목적지 충돌 `FileExistsError` — 파괴적 조작 3중 가드 코드 확인, 테스트 PASS.
- 잔여: 토큰 A 배선·mock 캐시 삭제는 운영자 조치(코드 아님). 판정: **설계 정합 PASS**
  (에이전트 완결 가능분 전량 구현·검증, 운영자 게이트 항목만 미결).
