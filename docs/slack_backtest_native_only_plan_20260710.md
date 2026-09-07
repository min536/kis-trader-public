# Slack backtest native 단일화(레거시 :8002 해치 제거) 실행 계획 (2026-07-10)

> **프로파일:** 신규 모듈 TDD 변형 — 제거 리팩터링 (verbatim 이동 없음 → AST 게이트 해당 없음)
> **실행 위임:** opus / **게이트:** 상위 모델(본 세션, completion-audit)
> **BASE SHA:** `452ced0` (branch 58)
> **배경/근본원인:** `docs/todo_20260710.md` §D — launchd plist가 `SLACK_BACKTEST_PIPELINE_SCRIPT`로 레거시(:8002 사이드카) 스크립트를 핀, `.env`(`DOTENV_DEFAULT_KEYS`)도 같은 키를 로드하는 2중 유입. env로는 스크립트를 바꿀 수 없게 해치 자체를 제거한다.

## §0. 목표 / 비목표

- 목표:
  1. `launch_backtest_pipeline`이 env와 무관하게 **항상** `DEFAULT_BACKTEST_PIPELINE_SCRIPT`(native)를 실행 — env에 레거시 경로를 넣어도 argv[0]=native임을 회귀 테스트로 고정.
  2. Slack backtest 표면에서 `SLACK_BACKTEST_PIPELINE_SCRIPT`/`OPEN_TRADING_API_ROOT`/`BT_URL` 처리 코드 제거 (`grep -rn "SLACK_BACKTEST_PIPELINE_SCRIPT" app tests` == 0).
  3. `scripts/open_trading_api_pipeline.sh` + 전용 테스트 파일 은퇴(git rm).
  4. 전체 스위트 green (기준선 3391 passed, 1 skipped).
- 비목표 (재량 확장 금지):
  - `app/integrations/open_trading_api/` 어댑터·연구도구(`run_proposal_backtest`, `shadow_watch`, `update_baselines.sh`, `run_research_loop.sh`)의 :8002 사용 — 건드리지 않는다.
  - `reports/open_trading_api/` 디렉토리명 변경, `DEFAULT_BACKTEST_REPORTS_DIR` 변경 — 하지 않는다.
  - 배포 plist·`.env` 수정 — 절대 금지(보호 파일/운영자 영역).
  - `scripts/native_backtest_pipeline.sh`의 동작 변경 — 헤더 주석 1곳 외 금지.

## §1. 실행 공통 규칙

- 전체 스위트 green 후 커밋: `.venv/bin/python -m pytest -q -p no:tdd_guard_pytest` (기준선 3391 passed, 1 skipped).
- **in-repo 실행(워크트리 미사용).** 워킹트리에 이 작업과 무관한 대량 pending 변경이 있다 — `git add`는 **아래 스코프 파일의 명시 경로만**. `git add -A`/`-u` 절대 금지.
- 커밋은 2개: C1=S1+S2(코드+테스트 이전), C2=S3(레거시 파일 은퇴). 각 커밋 전 전체 스위트 green. 커밋 메시지에 슬라이스 ID 포함, 말미에 `Co-Authored-By: Claude Opus <noreply@anthropic.com>`.
- 죽은 코드도 삭제 금지 — 계획서 명시 삭제분(§3~§5의 정확한 대상) 외에는 BASE의 어떤 라인도 지우지 않는다.
- 계획서에 명시된 테스트는 전부 작성 — 1건이라도 생략하면 미완.
- 에스컬레이션(§에스컬레이션) 외 판단이 필요하면 **중단하고 보고** — 자기 판단으로 메꾸지 않는다.

## §2. 확정 구조지도 (실행자는 재조사 불필요 — 인용 검증만)

**코드:**
- `app/notifications/backtest_control.py:35` — `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV = "SLACK_BACKTEST_PIPELINE_SCRIPT"` (제거 대상)
- `app/notifications/backtest_control.py:41-44` — S5a-3 주석 + `DEFAULT_BACKTEST_PIPELINE_SCRIPT = PROJECT_ROOT / "scripts" / "native_backtest_pipeline.sh"` (주석의 "Rollback: …" 문구를 해치-제거 사실로 갱신; 상수는 유지)
- `app/notifications/backtest_control.py:284-286` — `script = Path(str(source.get(SLACK_BACKTEST_PIPELINE_SCRIPT_ENV) or DEFAULT_BACKTEST_PIPELINE_SCRIPT))` → env 조회 제거, **호출 시점에 모듈 전역을 읽는 형태 유지**: `script = Path(str(DEFAULT_BACKTEST_PIPELINE_SCRIPT))`
- `app/notifications/backtest_control.py:248-264` — `_build_backtest_subprocess_env` allowlist에서 `"OPEN_TRADING_API_ROOT"`, `"BT_URL"` 2줄 제거 (나머지 키 유지)
- `app/notifications/slack_bot.py:22` — facade import의 `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV,` 1줄 제거 (같은 블록의 다른 이름은 전부 잔존 실사용 — 유지)
- `app/notifications/slack_bot.py:85` — `DOTENV_DEFAULT_KEYS`에서 `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV,` 제거
- `app/notifications/slack_bot.py:88` — 같은 frozenset에서 `"OPEN_TRADING_API_ROOT",` 제거

**테스트 (patch/import seam):**
- `tests/test_backtest_control.py:20` — `_CONTROL_NAMES` 튜플의 `"SLACK_BACKTEST_PIPELINE_SCRIPT_ENV",` 제거
- `tests/test_backtest_control.py:187-222` — **미커밋 워킹트리 클래스** `DefaultPipelineScriptTests` (S5a-3 잔류물, HEAD에 없음): `test_default_script_is_native_pipeline`(187-194) 그대로 유지, `test_env_override_still_allows_rollback`(196-222) → §3의 회귀 테스트로 교체. C1 커밋에 이 클래스가 통째로 처음 들어간다 — 커밋 메시지에 "S5a-3 미커밋 default=native 테스트를 본 커밋에 흡수" 명시.
- `tests/test_slack_bot.py:40` — import 블록의 `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV,` 제거
- `tests/test_slack_bot.py:341-363` — `test_socket_mode_env_loads_backtest_runtime_defaults`: fixture(349행)의 `OPEN_TRADING_API_ROOT=…` 줄은 유지하되 단언(361행)을 `self.assertNotIn("OPEN_TRADING_API_ROOT", env)`로 교체 (dotenv 기본 로드에서 빠졌음을 고정)
- `tests/test_slack_bot.py:1051-1102` — `test_launch_backtest_pipeline_starts_background_script_with_sanitized_env`: env 주입(1072행) → `mock.patch.object(backtest_control, "DEFAULT_BACKTEST_PIPELINE_SCRIPT", script)`로 이전; 1073행 `"OPEN_TRADING_API_ROOT": …` 입력은 유지하고 1101행 단언을 `self.assertNotIn("OPEN_TRADING_API_ROOT", kwargs["env"])`로 교체; argv[0] 단언(1087행)은 `str(script)` 그대로.
- `tests/test_slack_backtest_hardening.py:24` — import의 `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV,` 제거
- `tests/test_slack_backtest_hardening.py:421-446` — `test_launch_writes_pid_file_with_start_marker`: env의 `SLACK_BACKTEST_PIPELINE_SCRIPT_ENV: "/bin/true"`(429행) → `mock.patch.object(backtest_control_module, "DEFAULT_BACKTEST_PIPELINE_SCRIPT", Path("/bin/true"))` (파일 상단 18행에 `backtest_control_module` alias 기존재, 16행에 `mock` 기존재)
- **patch 대상 주의:** `launch_backtest_pipeline`은 `backtest_control` 모듈 전역을 읽는다. patch는 반드시 `backtest_control`(canonical)에 — `slack_bot`의 재바인딩(slack_bot.py:17)에 하면 무효.

**은퇴 대상 (S3):**
- `scripts/open_trading_api_pipeline.sh` (git rm)
- `tests/test_open_trading_api_pipeline_script.py` (245줄, git rm — 스크립트 전용 e2e 테스트)
- `scripts/native_backtest_pipeline.sh:4` — 헤더 주석 "Drop-in replacement for open_trading_api_pipeline.sh …" → "Native minute-replay pipeline for the Slack backtest command (legacy open-trading-api sidecar wrapper retired 2026-07-10, docs/todo_20260710.md §D) …" 취지로 1~2줄 재작성 (배너 echo "native backtest pipeline"(60행)은 절대 변경 금지 — `tests/test_native_backtest_pipeline_script.py:11`이 스크립트를 실행 검증)

**참조 잔존 확인(변경 금지, 참고):** `tests/test_backtest_start_reply_native.py:5`(주석 참조만), docs/*.md의 역사 기록 — 건드리지 않는다.

## §3. S1 — env 해치 제거 + 회귀 테스트 (P0)

- 행위 테스트 사양 (tdd_guard: stub-first, red 확인 후 fill):
  - `tests/test_backtest_control.py::DefaultPipelineScriptTests::test_env_pin_cannot_override_native_script` (196-222행 교체): env에 `"SLACK_BACKTEST_PIPELINE_SCRIPT": "scripts/open_trading_api_pipeline.sh"` + `SLACK_BACKTEST_ACCOUNT`/`SLACK_BACKTEST_REPORTS_DIR`(tmp) 구성, fake popen으로 argv 캡처 → `captured["argv"][0] == str(backtest_control.DEFAULT_BACKTEST_PIPELINE_SCRIPT)` 단언 (현행 코드에서 red — 이 버그의 회귀 테스트).
- 코드: §2의 backtest_control.py 3곳 (35행 상수 제거, 284-286행 env 조회 제거, 41-44행 주석 갱신).
- 주의: S1 코드 변경 직후 `tests/test_slack_bot.py:1051`·`tests/test_slack_backtest_hardening.py:421`이 red가 된다 — 정상. S2에서 이전 후 C1로 함께 커밋.

## §4. S2 — facade/dotenv/allowlist 정리 + 테스트 이전

- 코드: §2의 slack_bot.py 3곳(22·85·88행), backtest_control.py `_build_backtest_subprocess_env` 2키 제거.
- 테스트 이전: §2의 test_slack_bot.py 3곳(40·341-363·1051-1102), test_slack_backtest_hardening.py 2곳(24·421-446), test_backtest_control.py `_CONTROL_NAMES`(20행).
- 스테일 import 검사: 편집 후 `grep -rn "SLACK_BACKTEST_PIPELINE_SCRIPT" app tests` 결과 0건 (`refs.count == 0` 기준).
- **C1 커밋**: S1+S2 전체, 스코프 6파일 명시 경로 add, 전체 스위트 green 확인 후.

## §5. S3 — 레거시 파일 은퇴

- `git rm scripts/open_trading_api_pipeline.sh tests/test_open_trading_api_pipeline_script.py` (tdd_guard는 Edit/Write만 게이트 — 파일 삭제는 git rm으로).
- `scripts/native_backtest_pipeline.sh` 헤더 주석 갱신(§2 명세).
- 참조 스윕: `grep -rn "open_trading_api_pipeline" app tests scripts` 결과 0건 확인 (docs/·.claude/는 제외 — 역사 기록 유지).
- **C2 커밋**: 삭제 2파일 + native 스크립트 1파일, 전체 스위트 green 확인 후.

## §실행 순서·병행

- 직렬 단일 트랙: S1(red 테스트→코드) → S2(이전·정리) → C1 커밋 → S3 → C2 커밋. 병행 없음, 워크트리 없음.

## §리스크 (계획 단계에서 못 박은 판단)

- `DEFAULT_BACKTEST_PIPELINE_SCRIPT`를 함수 기본 인자·지역 스냅샷으로 옮기지 않는다 — 호출 시점 모듈 전역 조회가 테스트 patch seam이다.
- 워킹트리의 무관 pending 변경(README, docs, dashboard 삭제 등 다수)은 절대 add/commit/revert하지 않는다. 스코프 밖 파일이 dirty해도 무시.
- `tests/test_backtest_control.py`의 미커밋 `DefaultPipelineScriptTests`는 이 수정과 동주제라 **의도적으로 흡수**한다(§2). 그 외 미커밋 하크는 이 파일에 없다(+38줄 전부가 이 클래스임을 diff로 확인함).
- `.env`·`.token_cache.json`·plist는 읽기/수정 금지. `data/`·`logs/`·`results/`·`archive/` 전체 read 금지.
- 트레이딩-크리티컬 표면(`app/execution|risk|strategy`, `app/auth/settings.py`, `app/main.py`, `app/pipeline/*`) 접촉 없음 — 스코프 밖 발견 시 STOP.

## §에스컬레이션 규칙

- 가드가 무관 실패 이유로 편집 거부: red 재수립 후 같은 편집 1회 재시도 → 재실패 시 STOP·보고.
- 계획서와 실코드 불일치(인용 라인이 다른 내용): 수정하지 말고 STOP·보고.
- no-progress 600초: STOP.

## §진행표 (실행자가 슬라이스 완료마다 갱신 — completion-audit 입력)

| 슬라이스 | 상태 | 커밋 | 비고 |
|----------|------|------|------|
| S1 | 완료 | 26016d6 | red 재현(argv[0]=legacy) 확인 후 green |
| S2 | 완료 | 26016d6 | S1과 단일 커밋(계획대로), 스위트 3392 passed |
| S3 | 완료 | 711dc03 | 스위트 3386 passed (−6=삭제 테스트 파일) |

**실행 기록·이탈 (2026-07-10):**
- 실행자: 계획서는 opus 위임용이었으나 Agent 스폰이 계정 세션 한도로 즉시
  종료(resets 8:40pm) → **상위 세션이 동일 계획서로 직접 실행** (TDD 절차 동일).
  completion-audit은 위임 부재로 생략 — 각 게이트(red→green·grep 스윕·전체
  스위트·커밋 stat)를 본 세션 도구 결과로 직접 검증.
- grep 게이트 기준 정밀화: §4의 "0건"은 실측상 "코드 소비처 0건"으로 판정 —
  잔존 3건은 설명 주석 2 + 회귀 테스트의 의도적 poison env 리터럴(키가
  무시됨을 증명하는 입력)이라 제거 대상이 아님.
- 계획 외 발견: `scripts/native_backtest_pipeline.sh`와
  `tests/test_native_backtest_pipeline_script.py`가 **untracked**였음(S5a-3이
  코드 기본값만 커밋하고 스크립트·테스트를 미커밋) → C2(711dc03)에 정식 편입.
  직전까지 fresh checkout은 Slack backtest가 존재하지 않는 스크립트를 가리키는
  상태였다.

## §계획-단계 체크리스트 판정 (plan-stage-checklist.md 5항목)

| # | 항목 | 판정 |
|---|------|------|
| 1 | 이동 후 원본 스테일 import 정리 단계 명시 | 반영 §4 (`grep == 0` 게이트) + §5 참조 스윕 |
| 2 | facade import를 "잔류 실사용"에서 도출 | 반영 §2 — slack_bot.py:22에서 제거하는 1개 이름만 실사용 소멸, 나머지는 DOTENV_DEFAULT_KEYS 등 잔존 실사용 확인 완료 |
| 3 | 이동 금지·patch 부작용·순환 import 사전 판정 | 반영 §2 "patch 대상 주의" + §리스크(호출 시점 조회 유지) — 이동 심볼 없음, 순환 import 해당 없음 |
| 4 | 패치-서피스 grep 2패턴 실행 | 해당 없음(app.main 비접촉) — 대체 grep 실행: `SLACK_BACKTEST_PIPELINE_SCRIPT` 전 사용처 6파일 확정(§2), `DEFAULT_BACKTEST_PIPELINE_SCRIPT` 사용처 4곳 확정, patch.object 기존 사용 패턴 hardening:18 alias 확인 |
| 5 | 워크트리 의존 형제 모듈 복원 절차 | 해당 없음 — in-repo 실행(§1), 워크트리 미사용 |
