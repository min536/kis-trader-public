# 테스트 격리 누출 — 실 logs/·data/ 오염 근본 해결 설계안 (2026-07-07)

관련: `docs/daily_error_triage_design_20260707.md`(E5·E6 트리아지 — 이 문서가 그 근본원인 확정),
`docs/dashboard_account_routing_design_20260707.md` §8-1(4743403 레드헤링 정정),
메모리 `rotated-account-equity-gap`.

## 0. 결론 요약 — 근본원인 확정 (재현됨)

어제/오늘의 "비활성 시그니처에 성과·주문 기록", "활성 계좌 주문로그 이상 성장",
20:13 크로스라이트 — 이 전부의 근본원인은 **pytest 스위트가 실제 `logs/`·`data/`에 쓴다**는
테스트 격리 누출이다. 운영자가 20:11~20:20에 아무것도 실행하지 않았음을 확인 → 그 시간대
유일 실행은 **리뷰 턴의 전체 스위트(20:11~20:13, 97초)**. 결정적 재현으로 확정했다.

**중대성**: 오염 대상에 **활성(라이브) 계좌 `9763e304`의 주문 로그**가 포함된다 — 테스트가
운영 중인 계좌의 실주문 이력 파일에 append하고 있었다.

## 1. 결정적 증거 (실측)

**재현 1 (원 사건)**: `logs/orders_mock_acct_4743403….jsonl` 등 mtime = **2026-07-07 20:13:17**
= 스위트 실행 창. 레코드 액션 `skipped_buy_scan_cadence`·`rate_limit_detected_sell_watch`
(세션형 실행만 내는 이벤트).

**재현 2·3 (직접)**: 실파일 mtime 스냅샷 → 전체 스위트 실행 → diff. 두 번 모두 아래 실파일이
생성/수정됨:
```
logs/orders_mock_acct_fedcba9876543210.jsonl          ← 활성(라이브) 계좌!
logs/orders_mock_acct_4743403ee24897a8.jsonl
logs/performance_summary_mock_acct_4743403ee24897a8.jsonl
data/performance_snapshots_mock_acct_4743403ee24897a8.jsonl
```
(3297 passed, 100.5s — 통과하면서 오염.)

**순서 의존성**: 5개 의심 파일을 개별 실행하면 **전부 clean**. 전체 스위트에서만 누출 →
**order-dependent 오염**: 한 테스트가 `os.environ` KIS 크레덴셜을 정리 없이 변경(직접
`os.environ[...]=`; monkeypatch 아님 — 최소 8개 파일 실측: `test_multi_account_preflight`,
`test_buy_scan_quote_account`, `test_lane_scheduler_runtime`, `test_auth_settings` 등), 이후
다른 테스트의 실경로 라이터(`log_order_event`→`get_order_log_path`)가 그 크레덴셜에서 파생된
시그니처로 **실파일에 append**.

**격리 부재 확정**: `tests/conftest.py` **부재** — 실 상태 디렉터리를 tmp로 돌리는 전역 autouse
픽스처가 없다. 경로 리졸버(`app/auth/account_scope.py:92-97`)는 `PROJECT_ROOT / "logs"|"data"`를
**env override 이음새 없이** 하드코딩.

## 2. 재해석 — 대시보드/회전 조사의 레드헤링 해소

- `mock_acct_4743403ee24897a8`은 **실계좌가 아니라 테스트 산출물 시그니처**다. 대시보드 계좌
  셀렉터에 뜬 "다른 계좌", "비활성 시그니처에 오늘 11:18 성과 기록"(→ 오전 스위트 실행)은
  전부 이 누출. `dashboard_account_routing_design_20260707.md` §8-1의 "회전 중 account-scope
  해석 불일치 정황"은 **철회**한다.
- 단, **활성 계좌 equity=0의 상류 원인(성과 파일 부재 + 스냅샷 `equity_krw=None`)은 그대로
  유효**하다 — 그건 라이브 세션이 쓴 실데이터이지 테스트 누출이 아니다(9763e304 cycle_snapshots는
  라이브 산출). 즉 equity=0은 여전히 트레이딩-크리티컬 상류 이슈(잔고 조회 미충전), 4743403
  단서만 테스트 오염으로 분리된다.

## 3. 왜 심각한가 (blast radius)

1. **라이브 데이터 오염**: 활성 계좌 주문로그에 테스트 레코드 append → 주문 카운트/무결성
   왜곡. 오전에 고친 order-log-too-large 무결성 가드가 다루는 바로 그 파일을 테스트가 재오염.
2. **조사 오도**: 팬텀 시그니처가 대시보드·회전 조사를 며칠간 오도(위 §2).
3. **비결정성**: order-dependent라 CI/로컬 결과가 실행 순서에 의존 — 잠재적 flaky.
4. **정책 위반**: 테스트가 `data/logs/results/archive` 실경로에 쓰는 것 자체가 리포 정책 위반.

## 4. 해결 설계 (2026-07-08 설계 검토로 개정 — 개정 사유는 §4-1)

### 4-1. 설계 검토에서 잡은 초안 결함 4건

| # | 초안 결함 | 실측 근거 | 개정 |
|---|----------|----------|------|
| P1 | G1 "모듈별 `PROJECT_ROOT` 몽키패치"는 **import 시점에 굳는 파생 상수를 못 돌린다** | `ACCOUNT_SCOPE_META_FILE`/`ACCOUNT_SCOPE_HISTORY_FILE`(account_scope.py:15-16), `DATA_DEFAULT_DIR`/`LOGS_DEFAULT_DIR`(archive_account_scope·multi_account_preflight·account_discovery), `QUALITY_ARTIFACT_PATH`(quality_sentinel.py:41), `_ML_DIR`(build_research_snapshot.py:47) — **10개+ 상수, 자체 PROJECT_ROOT 보유 모듈 9개, settings에서 import 13개** | 함수 이음새(G3)를 선택→**필수·선행**으로 승격; G1은 G3 위에서 env 1개로 구현 |
| P2 | 전역 autouse 리다이렉트를 한 번에 켜면 실경로를 정당하게 읽는 테스트(리포 파일 계약 테스트 등)가 무더기로 깨질 수 있음 | 스위트 3,300개 — 영향 범위 미열거 상태였음 | **검출(G2 report 모드) → 범인 테스트 직접 수정 → 전역 가드** 순서로 뒤집고, `@pytest.mark.uses_real_state_paths` 에스케이프 마커 추가 |
| P3 | G4 "팬텀 파일 삭제"는 파괴적이고, 후보 열거도 4743403 하나뿐이었음 | `655a7ee9…`(7/5 19:25)·`367314552…`(7/4 09:25)도 equity 1.09M·스위트 실행 창 mtime — 동류 의심 | 삭제 → 기존 `app/tools/archive_account_scope.py`로 **이동(비파괴)**; 후보는 census로 분류 후 운영자 승인 |
| P4 | G4 "활성 로그 트림 검토"는 라이브 주문로그를 편집하는 위험 대비 이득이 없음 | 오염 영향은 관측성뿐(cycle_error 일일 카운트 인플레이션 — 오늘 9건 중 테스트 주입분 포함), 매수 카운트류는 당일 한정이라 익일 자연 소멸 | **라이브 로그 불변 유지** + 오염 창(16:07·20:13·재현 2회) 문서화로 대체. 트림 옵션 삭제 |

### G3 — 상태-루트 함수 이음새 (필수·선행, 소형 TDD)

`app/auth/account_scope.py`에 `state_root() -> Path` 도입: `os.environ["KIS_STATE_ROOT"]`
우선, 기본 `PROJECT_ROOT`. 경로 리졸버(`_data_path_for_signature`/`_log_path_for_signature`)와
§4-1 P1의 파생 상수들을 **호출 시점 함수**(`account_scope_meta_file()` 등)로 전환하거나
`state_root()` 경유로 재계산. 기본값 현행 유지 = 무회귀. `account_scope.py`는 트레이딩-크리티컬
표면 목록 밖이나 인접 — 회귀 핀(기존 경로 문자열 동등성 테스트) 필수.

### G2 — 실경로 쓰기 검출 안전망 (G3와 병행 가능, 소형)

`tests/conftest.py`(신설)의 autouse 픽스처: 테스트 전후 실 `logs/`·`data/` 디렉터리 목록+mtime
셋을 비교해 생성/수정 발생 시 해당 테스트를 특정. **1단계 report 모드**(경고 수집 → 세션 말미
요약)로 범인 전수 열거 → 범인 테스트 직접 수정 → **2단계 fail 모드** 전환(이후 신규 누출은
그 자리에서 실패). 순서 의존이라 bisect가 못 짚은 문제를 per-test 경계로 해소.
비용: ~170파일 stat × 2 × 3,300테스트 ≈ 1.1M syscall — 수초 수준(수용); 초과 시 세션-스코프
지문+이분 탐색 폴백.

### G1 — 전역 격리 (G3 이음새 위에서, 소형)

conftest autouse에서 `KIS_STATE_ROOT=tmp_path` 설정(+`monkeypatch.setenv`) — G3 덕에 env
1개로 전 라이터가 tmp로 간다(몽키패치 열거 불필요). 추가로 KIS 크레덴셜 env 스냅샷→복원
(직접 `os.environ` 변경 테스트 — 실측 8개 파일 — 의 뒤 테스트 전파 차단). 실경로가 정말
필요한 테스트는 `@pytest.mark.uses_real_state_paths`로 옵트아웃(G2 감시는 유지).

### G4 — 오염 산출물 정리 (운영자 게이트, 비파괴)

- census(`account_artifact_census.py`)로 팬텀 후보 분류: {활성 9763e304, 직전 5ff8bd, 레거시
  12345678} **밖의** 시그니처(4743403·655a7ee9·367314552 등) → 레코드 tail 샘플로 테스트
  산출물 확인 → `archive_account_scope.py`로 **아카이브 이동**(삭제 아님). 운영자 승인 후 실행.
- 활성 9763e304 주문로그: **불변 유지**(§4-1 P4). 오염 창 기록: 07-07 16:07·20:13(스위트
  2회) + 07-07 재현 2회. cycle_error 일일 카운트가 그만큼 인플레이션됨을 리포트 해석 시 참작.

## 5. 검증 전략

- **최종 수용 기준**: 이 문서의 결정적 재현(실파일 mtime 스냅샷 → 전체 스위트 → diff)이
  **빈 집합** — 이를 G2 fail 모드의 세션 지문 테스트로 승격해 상시 회귀 가드화.
- G3: 기존 경로 동등성 핀(기본값에서 현행 경로와 바이트 동일) + `KIS_STATE_ROOT` 설정 시
  리다이렉트 확인.
- G2: 일부러 실경로에 쓰는 합성 테스트가 fail 모드에서 잡히는지(양성 검출) + report 모드
  요약 형식.
- 전체 스위트 그린 유지 + 오염 0.

## 6. 슬라이스·순서 (구현 시 /delegation-plan 대상)

| 순서 | 항목 | 규모 | 비고 |
|------|------|------|------|
| 1 | G2 report 모드 (conftest 신설 + 범인 전수 열거) | 소형 | 즉시 가시화, 무회귀 |
| 2 | G3 `state_root()` 이음새 + 파생 상수 함수화 + 경로 동등성 핀 | 소형 TDD | P1 해소의 본체 |
| 3 | 범인 테스트 직접 수정 (G2 report 결과 기반) + G1 전역 env 격리 | 소형~중형 | 마커 옵트아웃 포함 |
| 4 | G2 fail 모드 전환 + 수용 기준 재현 = 빈 diff 확인 | 소형 | 상시 가드 확립 |
| 5 | G4 팬텀 아카이브 이동 | **운영자** | 비파괴, census 분류 첨부 |

## 7. 이 발견이 바꾸는 것

- `daily_error_triage_design_20260707.md` **F2(계정 스코프 단일-라이터)**: E5의 "세션형 실행이
  스코프를 바꿈" 가설(H1/H3)은 **기각**, H2(다른 프로세스=테스트 스위트)로 확정. F2의 라이터
  스탬프(pid·시그니처)는 여전히 가치 있으나(재발 진단), 스코프 동결의 긴급도는 낮아짐.
- `dashboard_account_routing_design_20260707.md` §8-1: 4743403 회전 정황 철회(위 §2).
- **cycle_error 9건**(E6)의 원인 문자열 미기록은 별개로 유효 — F2-3(관측성 배선) 유지.

## 8. 구현 완료 (2026-07-09, TDD)

| 슬라이스 | 상태 | 산출물 / 근거 |
|----------|------|---------------|
| G3 state_root 이음새 | ✅ | `account_scope.state_root()`+`KIS_STATE_ROOT` env; `_data/_log_path_for_signature`·`dashboard_paths`·`get_partitioned_log_path` 경유 라우팅. `tests/test_account_scope_state_root.py` 12 passed(기본값 바이트동일=무회귀 + 리다이렉트). |
| G2 conftest 감시망 | ✅ | `tests/conftest.py` autouse: 리다이렉트(`KIS_STATE_ROOT=tmp`)+KIS_* env 스냅샷/복원+실경로 쓰기 감시+`uses_real_state_paths` 마커. `tests/test_conftest_isolation_guard.py` 5 passed(센서 검출+서브프로세스 양성검출 실발화 확인). |
| G1 전역 격리 | ✅ | G3 이음새 위 env 1개 리다이렉트로 in-process 전 writer가 tmp로. report 모드 누출리포트 빈집합. |
| G4 팬텀 아카이브 | 운영자 | census 분류 후 `archive_account_scope.py` 이동(비파괴). |

### 8-1. 라이브 세션 거짓양성 — watcher 개선 (중대 정정)

fail 모드 전체 스위트에서 2개 테스트(`test_restart_session_script`, `test_weight_search_cli`)가
활성 계좌 `9763e304`의 실 state 파일(cycle_snapshots/runtime_state/cycle_stats/account_scope_meta)
기록으로 간헐 오류. **근본원인: 동시 실행 중인 운영자 라이브 세션**(`ps aux`로 PID 74438,
`app.main`, 9:08 기동, `app_main_9763e304.lock` 보유 확정). watcher가 per-test 전후 스냅샷으로
라이브 세션의 매-사이클 정상 write를 그 순간 실행 중이던 테스트에 **오귀속**한 거짓양성이었다
(테스트 단독/2개 통과, order·타이밍 의존이 증거).

- **정정**: 스위트 실행 중 활성계좌 실파일 변경은 **테스트 누출이 아니라 라이브 세션 정상 동작**.
  §1 결정적 재현이 잡았던 `orders_9763e304` 오염도 상당 부분 동시 라이브 세션 소산일 개연 —
  다만 §1의 팬텀 시그니처(4743403 등) 오염은 여전히 테스트 산출(라이브 세션은 활성 계좌만 씀).
- **watcher 개선**: `_live_owned_signatures()`가 `app_main_<sig>.lock`을 `fcntl.flock(LOCK_NB)`로
  프로브(held=라이브 소유; 락 제거/우회 없음 — 운영자 게이트 준수)해, 라이브-보유 시그니처 파일 +
  전역 scope 파일을 감시에서 제외. 비-라이브(테스트/팬텀) 시그니처 누출은 계속 fail.
- **최종 수용 (quiescent 아님, 라이브 세션 동시 실행 하에서도)**: 전체 스위트 3359 passed·0 fail;
  독립 mtime diff 결과 **변경된 실파일 전부 라이브 세션(9763e304)+scope 전역 = 비-라이브 시그니처
  테스트 누출 0**. 즉 in-process redirect가 실제로 모든 테스트 writer를 tmp로 격리함을 확인.
