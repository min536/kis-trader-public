---
Purpose: open-trading-api를 kis-trader에 안전하게 연결하기 위한 단계별 통합 계획
Read when: 외부 KIS 샘플/백테스터/open-trading-api 기능을 kis-trader에서 재사용하려 할 때
Do not use for: app.main 실행, 세션 스크립트 실행, broker/order API 호출, 보호 credential 파일 점검
Status: SHORT-TERM IMPLEMENTED
---

# open-trading-api Integration Plan

## 현재 구현 상태

단기 연결면은 구현되어 있다.

- 모듈: `app/integrations/open_trading_api/`
- 상태 확인 CLI: `python3 -m app.tools.open_trading_api_status --json`
- root 설정: `OPEN_TRADING_API_ROOT` 환경변수만 사용
- 허용 subprocess command:
  - `repo_head`: `git rev-parse HEAD`
  - `repo_root`: `git rev-parse --show-toplevel`
- subprocess 안전 경계:
  - `cwd`는 검증된 `OPEN_TRADING_API_ROOT`
  - `shell=False`
  - 기본 timeout 5초
  - stdout/stderr 기본 8KB 상한
  - 전달 env는 `PATH`, locale, temp 관련 값으로 제한
- artifact reader:
  - JSON object reader
  - CSV dict-row reader
  - 기본 파일 크기 상한 64KB
  - relative path만 허용
  - root 밖 escape와 보호 파일 경로 차단
  - required key/column schema 확인
- autotuner 영향: 이번 단기 adapter는 autotuner runtime/evidence 경로에 직접 배선하지 않는다. 외부 결과를
  autotuner evidence로 붙이는 작업은 이 adapter를 사용해 shadow-only로 별도 진행한다.

### 중기 진행 상태 (2026-06-07)

- **경계 일관성 (Phase B):** 백테스터 REST 호출(`run_proposal_backtest → :8002`)의 **URL 정책을 어댑터가 소유**한다.
  `is_loopback_url`이 loopback(localhost/127.0.0.1/::1) http/https만 허용하고, `_call_bt_api`는 비-loopback URL을
  **네트워크 접근 전에 거부**한다(SSRF/원격 오발송 가드). 호출 자체는 그대로(응답 bound는 강제하지 않음 — full_output 보존).
  결정 근거: 백테스터 응답은 정당하게 클 수 있어 hard 응답 bound는 부적절. 대신 "어댑터가 정책(URL allowlist)을
  통과시키고, 소비자는 검증된 필드만 신뢰"하는 방식으로 경계를 닫았다.
- **관측성/감사 로그 (Phase C):** `build_command_audit_record`(어떤 command를 어떤 commit/path에서 실행했는지 pure 레코드)
  + `append_audit_record`(caller 지정 JSONL append). `open_trading_api_status --audit-log PATH`로 opt-in 배선.
- **운영자 handoff (Phase D):** `docs/runbook_open_trading_api.md` (not included in this source snapshot) — 설정/상태확인/백테스트/
  screen→suggest/실패복구/검증 체크리스트.
- **남은 것:** 장기(submodule pin)는 사람 결정 대기.

## 결정

`open-trading-api`를 `kis-trader` 안으로 직접 병합하지 않는다.

가장 좋은 장기 방향은 `kis-trader`를 운영/주문/리스크/승인/감사의 중심으로 유지하고,
`open-trading-api`는 외부 연구/백테스트/샘플 도구로 격리해서 호출하는 것이다.

```text
kis-trader
  운영, 주문, 리스크, 세션, 승인, Slack, 보안 게이트

open-trading-api
  외부 백테스터, 공식 샘플, API 연구, 데이터/전략 실험

연결 경계
  subprocess + artifact(JSON/CSV) + allowlist + timeout
```

이 구조는 의존성 충돌과 credential 노출 위험을 줄이고, 나중에 sibling repo에서 git submodule로 옮겨도
`kis-trader` 내부 호출 경로를 크게 바꾸지 않게 만든다.

## 단기

목표: `open-trading-api`를 sibling repo로 둔 상태에서, `kis-trader`가 안전하게 "읽기/연구용 외부 도구"로
호출할 수 있는 최소 연결면을 만든다.

- 위치: `$OPEN_TRADING_API_ROOT`
- 설정: `OPEN_TRADING_API_ROOT` 환경변수로만 경로를 받는다.
- 호출: `shell=False`, 명시적 command allowlist, 제한된 env, `cwd=OPEN_TRADING_API_ROOT`, timeout.
- 입출력: stdout 텍스트를 운영 로직에 직접 믿지 않고 JSON/CSV artifact를 검증해서 소비한다.
- 금지: `.env`, `kis_devlp.yaml`, token cache, broker/order API, session script, app.main 실행.
- 기본: 연결 기능은 read-only/mock-first이며, live 경로에는 연결하지 않는다.

## 중기

목표: `app/integrations/open_trading_api/`에 얇은 어댑터를 두고, autotuner/backtester가 이 어댑터만 통과하게 한다.

- `OpenTradingApiRoot`/path resolver: 경로 존재, repo fingerprint, 보호 파일 접근 금지 검사.
- command runner: 허용된 명령만 실행하고, timeout/exit code/stdout size/stderr size를 제한한다.
- artifact reader: JSON/CSV schema, 파일 크기, 상대 경로 escape 여부를 검증한다.
- evidence bridge: autotuner 제안의 evidence에 외부 백테스트 결과를 붙이되, proxy evidence로만 취급한다.
- observability: 어떤 명령을 어떤 commit/path에서 실행했는지 감사 로그로 남긴다.
- tests: broker/API 없이 fixture repo와 fake command로만 검증한다.

## 장기

목표: 통합면이 충분히 안정되면 `open-trading-api`를 재현 가능한 외부 dependency로 pin한다.

- 후보 1: `external/open-trading-api` git submodule.
- 후보 2: 필요한 일부 스키마/샘플만 문서화해서 수동 동기화.
- 비추천: 전체 subtree/vendor copy. 현재 폴더 규모와 `.venv`, Lean workspace, credential성 설정 파일 때문에
  `kis-trader`의 운영 repo를 무겁고 위험하게 만든다.
- 원칙: pin된 commit, 명시적 update 절차, protected file allowlist, offline/mock validation을 유지한다.

## 단기 실행 계획

이 섹션은 구현된 단기 계약의 운영 체크리스트로 유지한다.

### 1. 현재 상태 고정

산출물:
- `open-trading-api` sibling repo 위치와 용도 기록.
- 현재 `kis-trader`가 외부 repo를 직접 참조하는 곳이 있는지 inventory.

안전 조건:
- `app.main`, session script, broker/order API 실행 금지.
- `open-trading-api/kis_devlp.yaml`, `.env*`, token/cache 파일 읽기 금지.

검증:
- `git status --short`
- `rg "open-trading-api|OPEN_TRADING_API|run_proposal_backtest|localhost:8002" app tests docs`

### 2. 연결 계약 정의

산출물:
- 허용할 첫 command 목록 1-2개만 정의.
- 각 command의 입력, 출력 artifact, timeout, 최대 stdout/stderr 크기, 실패 시 동작을 문서화.

권장 첫 범위:
- read-only status/inventory command.
- fixture 또는 mock artifact를 생성하는 offline command.

금지:
- live API key가 필요한 data fetch.
- 주문, 계좌, broker session, server start를 동반하는 command.

검증:
- 계약 문서 리뷰.
- command별로 "broker/API 호출 없음" 근거 기록.

### 3. 최소 어댑터 설계

산출물:
- `app/integrations/open_trading_api/` 설계 초안.
- public 함수 후보:
  - `resolve_open_trading_api_root(env) -> Path | None`
  - `run_allowed_open_trading_api_command(command_id, *, root, timeout) -> CommandResult`
  - `read_open_trading_api_artifact(path, *, max_bytes, schema) -> dict`

설계 원칙:
- root가 없으면 기능은 no-op 또는 명확한 unavailable 결과.
- command id는 enum/allowlist로만 선택.
- shell 문자열 입력 금지.
- 운영 설정 변경 없이 artifact만 반환.

검증:
- 단위 테스트 계획 작성.
- fake repo/fake command만으로 테스트 가능해야 한다.

### 4. TDD로 read-only adapter 구현

산출물:
- adapter 코드.
- fixture 기반 tests.

테스트 순서:
- `OPEN_TRADING_API_ROOT` 미설정이면 unavailable/no-op.
- root가 없거나 repo fingerprint가 맞지 않으면 reject.
- allowlist에 없는 command는 reject.
- command는 `shell=False`, 제한 env, timeout으로 실행된다.
- artifact path traversal은 reject.
- oversized/malformed artifact는 reject.

검증:
- `.venv/bin/python -m pytest tests/test_open_trading_api_integration.py -q`
- `git diff --check`

### 5. 백테스터/autotuner 연결은 shadow-only로 시작

**진행 상태 (2026-06-07): 후보-제안 측 고리는 연결됨 (shadow-only).**
`app/tools/autotuner_operational_backtest.py`가 local session summary와 cadence candidates를 읽어
autotuner-domain eval(`changes[].parameter`/`to_value`)을 만들고, `app/autotuner/screening.py` +
`app/tools/autotuner_screen.py`가 이를 `screening.json`으로 집계하여
`autotuner_suggest → plan → propose → approve → runtime`로 잇는다. verdict는 eval 자체값(조작 불가),
백테스터(:8002)를 직접 호출하지 않고 local artifact만 *집계*한다. evidence 측(외부 결과를 proposal evidence로
붙이는 `evidence_sources.load_backtest_evidence`)은 이미 존재하며 proxy로만 취급한다. `run_proposal_backtest`
eval은 strategy/research-domain이라 evidence로는 쓸 수 있지만 cadence screening producer로 직접 쓰지 않는다.

산출물:
- 외부 결과를 autotuner evidence에 붙이는 shadow path.
- 결과에는 `source=open-trading-api`, `proxy_evidence=true`, `commit_or_path`, `generated_at`을 기록.

안전 조건:
- runtime override 적용 경로와 분리.
- human approval 전에는 어떤 runtime setting도 바꾸지 않음.
- live env에서는 no-op.

검증:
- mock artifact로 proposal evidence가 생성되는지 확인.
- approved runtime override와 별개의 read-only evidence path임을 테스트.

### 6. 운영자 handoff

산출물:
- 운영자 체크리스트.
- 실패/복구 절차.

체크리스트:
- `OPEN_TRADING_API_ROOT`가 sibling repo를 가리키는지 확인.
- protected config 파일을 복사하거나 열지 않는다.
- 첫 실행은 read-only/mock artifact command만 허용한다.
- 생성 artifact는 수동 검토 후에만 autotuner evidence로 사용한다.

검증:
- safe test command 결과.
- full test 또는 관련 테스트 통과.
- 다음 정규 세션 전에 live 경로와 연결되지 않았음을 확인.

## 성공 기준

- `kis-trader`의 운영 안정성과 보안 경계가 흐려지지 않는다.
- `open-trading-api` 의존성, venv, Lean workspace, credential성 파일이 `kis-trader` repo로 들어오지 않는다.
- 외부 도구 호출은 항상 allowlist, timeout, bounded output, artifact validation을 통과한다.
- 연결 실패는 거래 차단이나 설정 변경이 아니라 "외부 evidence unavailable"로 끝난다.
- autotuner가 외부 백테스트를 사용할 때도 결과는 proxy evidence로만 취급된다.
