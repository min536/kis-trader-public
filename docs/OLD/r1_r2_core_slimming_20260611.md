# R1 코어 슬리밍 + R2 1차 슬라이스 — 증거 기록 (2026-06-11)

전체 리팩토링 구획 계획(R1~R10)의 앞부분(R1 런타임 코어 잔여 → R2 설정/인증)을 실행한 기록.
브랜치: `refactor/r1-core-slimming` (base: `refactor/day1-main-slimming`, PR #56 위에 스택).

## 커밋 목록

| 커밋 | 구획 | 내용 |
|------|------|------|
| 1f6faef | R1-1 | slack/bottleneck alert hooks → `app/notifications/runtime_alerts.py` |
| 1e2aa65 | R1-2 | benchmark snapshot resolver → `app/market_data/benchmark.py` |
| 77e2e7a | R1-3 | `build_sell_analysis` → `app/strategy/sell_decision.py` |
| 0c0791d | R1-4 | sell selftest 4종 → `app/runtime/sell_selftest.py` (+분기별 신규 테스트 8건) |
| 02376d9 | fix | 검증 단계에서 발견한 preview 분기 `print_cycle_conclusion` 누락 복원 |
| c962d7f | R2-1 | 순수 env 파서 7종 → `app/auth/env_parsing.py` (facade 보존) |
| 2ef9a39 | fix | sell guard selftest가 cooldown-skip 분기를 실제로 타도록 수정 (아래 결함 항목 해소) |
| 70e2491 | R2-2 | 런타임 검증 클러스터(파라미터 검증/startup sanity 리포트 + env-file 상태) → `app/auth/runtime_validation.py` (+신규 테스트 11건) |
| 4c30df9 | R2-3 | `get_settings()` 필드그룹 단위 분해 → `app/auth/settings_fields.py` 빌더 11종 (+신규 테스트 26건/서브테스트 80건) |

## 라인 수 변화

| 파일 | 이전 | 이후 | 증감 |
|------|------|------|------|
| `app/main.py` | 4,211 | 3,780 | −431 (−10.2%) |
| `app/auth/settings.py` | 1,947 | 483 | −1,464 (R2-1 −42, R2-2 −202, R2-3 −1,220) |

(Day 1 기준 누계: `app/main.py` 5,775 → 3,780, −34.5%)

## 이동 방식 (seam 보존 원칙)

- **verbatim move + 레거시 언더스코어 바인딩 유지**: 이동한 함수는 새 모듈에서 canonical
  이름으로 정의하고, `main.py`/`settings.py`는 `_name = name` 형태로 기존 이름을 그대로
  바인딩한다. run_cycle 호출부와 characterization 테스트의 setattr/patch seam이 모두 유지됨
  (예: `tests/test_buy_order_flow_characterization.py`의 `"_print_test_mode"` dict stub).
- **TDD-guard 순서**: green 상태에서 신규 모듈 대상 테스트 작성(red) → 모듈 생성(green) →
  본체 교체. 모든 신규 모듈은 생성 전에 해당 테스트가 먼저 존재.
- **R2-2 모듈 상태 공유**: `_INITIAL_ENV`/`_LOADED_ENV_FILE_VALUES`는 settings.py에서
  runtime_validation 모듈의 **동일 객체**로 바인딩 (load_env_file 변이 공유).
  `load_env_file(PROJECT_ROOT / ...)` 호출 지점은 settings.py 내 기존 위치 그대로라
  import-시점 env 로딩 순서(INITIAL_ENV 캡처 → env 파일 setdefault) 불변.
- **R2-3은 verbatim move가 아닌 재구조화**: `get_settings()`의 read→parse→validate
  본문을 필드그룹 빌더 11종으로 분해 (각 빌더가 자기 그룹의 read/parse/invariant를
  소유). 동등성은 텍스트 비교가 아니라 **차등(differential) 하니스**로 입증 —
  HEAD 구현을 별도 모듈로 로드해 동일 프로세스에서 신구 `get_settings()`를
  같은 env로 호출·비교. credential 해석 블록은 `get_settings()` 안에 그대로
  (라인 단위 보존 확인).
- **R2-3 문서화된 동작 변화 (의도적, 테스트로 핀 고정)**: 자격증명 누락 검증이
  필드그룹 파싱 오류보다 **먼저** 보고됨. 기존에는 read 블록의 telegram bool
  eager 파싱이 credential 검증보다 앞서 raise 할 수 있었음
  (`GetSettingsErrorPrecedenceTests`). 그 외 다중 오류 env 조합에서 첫 예외의
  순서가 그룹 단위로 재배열될 수 있으나, 단일 오류 86케이스 전수에서 예외
  타입·메시지 동일 확인.

## 검증 증거 (검토 단계)

1. **전체 테스트**: `1,764 passed, 114 subtests` (시작 시점 1,708 → +56 신규 테스트, 전부 green).
2. **verbatim 기계 비교** (AST 함수 추출 + alias 정규화 후 문자열 비교, git 원본 대조):
   - R1 이동 10개 함수: **10/10 verbatim 일치**.
   - R2-1 이동 7개 파서: **7/7 verbatim 일치**.
   - R2-2 이동 10개 함수 + 8개 상수: **10/10 + 8/8 verbatim 일치**.
   - 이 비교가 실제 회귀 1건을 적발 → `run_sell_test_cycle` preview 분기의
     `print_cycle_conclusion(side="SELL", ...)` 누락(146 vs 140줄). 02376d9로 복원 후 10/10.
3. **R2-3 차등 동등성 하니스**: HEAD `get_settings` vs 분해본 —
   **값 매트릭스 18케이스** (그룹별 오버라이드 전체 + legacy env 폴백 5조합 +
   core gate 폴백) 모두 Settings 필드 dict 동일, **단일 오류 86케이스** 모두
   예외 타입·메시지 동일. **PASS (불일치 0)**.
4. **트레이딩-크리티컬 무수정 확인**: `_resolve_kis_env`, `_infer_kis_env_from_base_url`,
   `_resolve_scoped_kis_value`, `validate_kis_base_url`, `_classify_kis_base_url_host`,
   `classify_kis_base_url_env`, `get_token_cache_path`, `get_slack_notify_order_submitted`
   — 기준 커밋 대비 **byte-identical 8/8** (R2-3 이후 재검증). `get_settings` 내부
   credential 해석 블록도 라인 단위 보존.
5. **patch-target 스윕**: tests 전체에서 `app.main`에 patch/setattr/참조되는 이름 98개 →
   전부 존재 (누락 0). R2-3 이후 `app.auth.settings`에 참조되는 이름 28개(테스트 patch +
   app 전체 import 포함)도 전부 존재.
6. **정적 점검**: 변경 파일(main.py + settings.py + 신규 모듈 6종) AST 기반 undefined-name
   검사 — 깨끗함.

## 검토 중 발견한 기존 결함 (→ 2ef9a39로 해소)

- `run_sell_guard_selftest`는 배너에서 "두 번째 동일 SELL 신호는 cooldown skip 이어야 합니다"
  라고 선언하지만, 두 가지 잠복 결함으로 cooldown-skip 분기를 영원히 타지 않았다:
  1. 시나리오 트리거 `stop_loss`가 `SELL_EMERGENCY_TRIGGERS`에 포함되어 cooldown 검사를 우회
     → 2차 검증도 항상 PASS.
  2. signature를 `build_sell_attempt_signature`(qty 포함 audit 포맷)로 기록했는데, 가드의
     cooldown 비교는 qty-free `build_sell_cooldown_key`를 사용 → 포맷 불일치로 어차피 매칭 불가.
- **수정 (2ef9a39)**: production sell flow(`app/execution/sell_flow.py`)와 동일하게
  `build_sell_cooldown_key`를 기록하고, 2차 가드 호출에만 `emergency_triggers=frozenset()`을
  전달해 cooldown 분기를 강제. production 기본값(`SELL_EMERGENCY_TRIGGERS`)은 무변경.
  특성 테스트는 2차 검증 COOLDOWN_SKIP + `blocked_sell_cooldown` 로그를 핀 고정
  (`tests/test_sell_selftest.py`).

## 남은 백로그

- ~~**R1-5**: `run_cycle`(~2,900줄) 분해 설계 문서~~ — 완료:
  [`docs/r1_5_run_cycle_decomposition_plan.md`](r1_5_run_cycle_decomposition_plan.md)
  (phase 지도 + 10슬라이스 실행 계획, 트레이딩-크리티컬 S9/S10은 /risk-assessment 게이트).
  슬라이스 실행은 별도 작업.
- **R2 후속 슬라이스**: ~~`build_runtime_parameter_validation_report`/`build_startup_sanity_report`
  추출~~ (70e2491 완료), ~~`get_settings()`(~1,300줄) 필드그룹 단위 분해~~ (4c30df9 완료 —
  **R2 구획 종료**, settings.py 483줄). `_resolve_kis_env` 계열은 계속 무수정 원칙.
- R3 이후 구획 (실행 순서 — R6 트레이딩-크리티컬은 의도적으로 맨 뒤):
  **R3** 리포팅/알림 (`app/reporting/` + `app/notifications/`, ~7.9k줄 14파일 —
  최대 파일: runtime_status_snapshot.py 1,320 / performance.py 1,312) →
  **R4** 대시보드 → **R5** tools → **R7** backtester → **R8** 소형 패키지 →
  **R9** tests → **R10** scripts → **R6** 트레이딩-크리티컬
  (`app/execution|risk|strategy/`, /risk-assessment 게이트, 마지막).
  각 구획은 착수 시 R1-5처럼 구조 지도 → 슬라이스 계획 → TDD 실행 순으로 진행.
