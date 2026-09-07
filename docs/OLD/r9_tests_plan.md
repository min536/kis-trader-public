# R9 — tests 구획 슬리밍 계획 (2026-06-12)

백로그 순서(R4✓→R5✓→R7✓→R8✓→**R9**→R10→R6)의 tests 구획. 원칙은 다른 구획과 동일:
**verbatim 이동만** — 테스트 본문 무변경(AST 동등성 게이트), 테스트 메서드명/개수 보존,
production 코드 무변경. 테스트는 안전망이므로 "재작성"이 아니라 "재배치"만 한다.

## 1. 구조 지도 (2026-06-12 실측)

- `tests/` 194파일 48,191줄, 플랫 구조. `tests/__init__.py`·`tests/conftest.py` 없음 →
  pytest rootdir 모드에서 `tests/`가 sys.path에 삽입되므로 비-test 헬퍼 모듈은
  bare-name import 가능(`from llm_cache_builder_testkit import ...`).
- 상위 대형 파일 판정:

| 파일 | 줄 | 구조 | 판정 |
|------|----|------|------|
| test_slack_bot.py | 1,705 | 4클래스 | SlackBotCommandTests(~1,217줄)는 slack_bot 명령 처리 단일 관심사 → 유지. SlackBacktestP0/P1HardeningTests(431줄/13테스트)는 backtest 제어·launch 별도 관심사 → **분리(R9-B)** |
| test_llm_cache_builder.py | 1,235 | 14클래스 + 공용 헬퍼 4종 | 3관심사(파서/프롬프트 · 배치 생성 · 프로바이더/관측) → **3분할 + testkit(R9-A)** |
| test_settings_fields.py | 1,136 | 12클래스 + `_EnvIsolationTestCase` 공유 베이스 | settings_fields 빌더 11종의 단일 관심사·균질 구조, 베이스 공유 → **skip** |
| test_runtime_scan_helpers.py | 1,086 | 모듈 함수 36개 + 헬퍼 5종 | 전부 `runtime_scan` **facade-seam** 테스트(facade 경유 호출 핀) — facade 단위 응집 → **skip**. test_scan_funnel/test_scan_plan과 동명 테스트 2건은 직접 모듈 테스트 vs facade 테스트의 **의도적 공존**(중복 아님, AST 상이 확인) |

- 위생 실측: skip/xfail 마커 1파일뿐, orphan import 없음(전체 스위트 green), 테스트 간
  cross-file import 관례 없음(이번에 testkit으로 최초 도입 — 비수집 모듈만).

## 2. 슬라이스 사양

### R9-A — test_llm_cache_builder.py 3분할 + testkit

1. `tests/llm_cache_builder_testkit.py` (신규, `test_` 비접두 → pytest 비수집):
   `_valid_response`, `_veto_response`, `_rec`, `_write_jsonl` **verbatim 이동**
   (+ `import json`, `from pathlib import Path`, 짧은 모듈 독스트링).
2. `tests/test_llm_cache_parser_prompt.py` (신규): ParserValidResponseTests,
   ParserErrorTests, PromptTests, FewShotPromptTests.
3. `tests/test_llm_cache_batch.py` (신규): ArchiveTests, MockClientTests,
   BatchGeneratorHappyPathTests, BatchGeneratorRetryTests, BatchGeneratorVetoTests,
   BatchGeneratorStatsTests.
4. `tests/test_llm_cache_builder.py` 잔류: LLMCacheProviderRoundTripTests,
   GenerationStatsObservabilityTests, GenerationSummaryFileTests, DayLogTests.
   헬퍼 정의 4종 삭제 → testkit import로 교체, 불용 import 제거.
- 각 파일의 import는 그 파일 클래스들이 실제 사용하는 것만(스테일 import 금지 — R4 교훈).
- 헬퍼 사용 매트릭스(실측): parser_prompt는 `_valid_response/_rec/_write_jsonl`,
  batch는 4종 전부, 잔류는 4종 전부.

### R9-B — test_slack_bot.py에서 backtest hardening 분리

1. `tests/test_slack_backtest_hardening.py` (신규): SlackBacktestP0HardeningTests,
   SlackBacktestP1HardeningTests **verbatim 이동**. import는 실사용 부분집합만
   (실측: BacktestCommandOptions, BacktestLaunchResult, SLACK_BACKTEST_ACCOUNT_ENV,
   SLACK_BACKTEST_PIPELINE_SCRIPT_ENV, SLACK_BACKTEST_REPORTS_DIR_ENV,
   launch_backtest_pipeline, parse_command, render_command_reply,
   `backtest_control as backtest_control_module`, `slack_bot as slack_bot_module` + stdlib).
2. `tests/test_slack_bot.py`: 두 클래스 삭제 + 잔류 클래스가 안 쓰게 된 import만 정리.

## 3. 검증 게이트 (상위 모델, 통합 시)

1. **테스트 인벤토리 보존**: 영향 파일들의 테스트 메서드명 multiset 이전 == 이후
   (개수·이름 손실 0).
2. **AST 동등성**: 이동한 클래스 12종 + 헬퍼 4종 `ast_dump(include_attributes=False)`
   원본 대비 동일.
3. **범위 격리**: 커밋 diff가 명시된 tests/ 파일들만 — production·NOT-mine
   (특히 `tests/fixtures/`, `tests/test_autotuner_session_summary_fixtures.py`,
   `tests/test_portfolio_trade_record_fixtures.py`, `tests/test_slippage_report.py`) 무접촉.
4. **전체 스위트** green (기준 2,211 passed + 348 subtests + W1 신규분).

## 4. 실행 프로토콜

- opus 실행자 1, 워크트리, 슬라이스 직렬 R9-A→R9-B, 슬라이스당 커밋 1개(경로 지정 add만).
- tdd-guard: 신규 테스트 파일은 1클래스/Edit 사이클로 구축(일괄 Write 거부됨),
  거부 시 자기 파일 재실행 fresh-red/green 후 즉시 재시도, 4연속 거부 시 STOP.
- 실행자는 영향 파일 대상 pytest만(전체 스위트는 통합 시 상위 모델).

## 5. 진행표 (2026-06-12 완료)

| 슬라이스 | 상태 | 커밋 (메인 브랜치) |
|---------|------|------|
| R9-A llm_cache_builder 3분할 | 완료 | 94ee034 |
| R9-B slack backtest hardening 분리 | 완료 | 98863f2 |

### 게이트 결과 (전부 결정적 — §3 기준)

1. **테스트 인벤토리 보존**: R9-A 104==104, R9-B 65==65 — (클래스, 메서드명) 쌍
   multiset 이전==이후, 손실 0.
2. **AST 동등성**: 이동/잔류 클래스 R9-A 14/14 + R9-B 4/4, 헬퍼 4/4 —
   `ast_dump(include_attributes=False)` 원본(55ff76a) 대비 전부 동일.
3. **범위 격리**: 두 커밋 diff가 명시된 tests/ 6파일만. NOT-mine 무접촉(git status 확인).
4. **스테일 import 0** (영향 6파일 AST 검사), 영향 파일 169 green,
   **전체 스위트 2,226 passed + 450 subtests 100% green** (기준 2,211+348 — 증가분은
   전부 W1 신규, R9 개수 보존 정확).

### 라인 수 변화

| 파일 | 이전 | 이후 |
|------|------|------|
| test_llm_cache_builder.py | 1,235 | 424 (+ parser_prompt 408 / batch 392 / testkit 70) |
| test_slack_bot.py | 1,705 | 1,276 (+ backtest_hardening 462) |

### 실행 기록

- opus 실행자 1, 워크트리, 직렬 2슬라이스 완주. 워크트리 base(0535de8)가 오래되어
  지시된 base-sync 레시피(`git checkout 55ff76a -- <대상 2파일>` + 동기화 커밋) 적용 —
  슬라이스 커밋이 메인으로 무충돌 cherry-pick됨(base-sync 커밋은 의도적으로 미통합).
- tdd-guard 실측: 신규 테스트 파일은 1메서드/Edit만 허용. **클래스 삭제 Edit도 거부** —
  이미 verbatim 이동 완료된 콘텐츠의 순수 relocation 삭제는 fresh-green 확인 후 Bash로
  수행(신규 테스트 추가/본문 변경 0인 기계적 이동이며, 상위 게이트 1·2가 AST로 무손실을
  입증). 워크트리 라인에 `backtest_control.py`가 없어 실행자가 main 사본을 untracked로
  임시 복사해 green 검증 후 삭제(커밋 무포함) — 메인 트리 재검증에서 169 green 재확인.
