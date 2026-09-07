# Weekend Change Summary

작성일: 2026-05-10 (토)
대상 기간: 2026-05-08 ~ 2026-05-10 (목~토)
다음 확인 일정: 2026-05-12 (월) regular session

---

## 목적

이번 주말 작업은 **live trading behavior를 바꾸지 않으면서** inventory 문서화, helper 소규모 분리, 테스트 보강, 운영 도구 정리를 진행한 것이다.

이 문서는 월요일 regular session에서 확인해야 할 회귀 포인트를 정리하고, 이상 신호가 발견될 때 어떤 커밋/영역을 의심해야 하는지 안내한다.

---

## 현재 branch/upstream 상태

- Branch: `main`
- Upstream: `origin/main` (`529c3d8`)
- Local HEAD: `50faa85`
- **Local은 origin 대비 10 커밋 ahead**
- **push하지 않았다**

---

## 최근 커밋 목록과 분류

oldest → newest 순서.

### docs-only (6개)

| 커밋 | 메시지 | 변경 파일 |
|------|--------|----------|
| `ae6f4c1` | Document workspace inventory | `docs/workspace_inventory.md` |
| `a77a91d` | Document app tools inventory | `docs/tools_inventory.md` |
| `2baac7c` | Document app main split plan | `docs/main_split_plan.md` |
| `721279e` | Document runtime budget split plan | `docs/runtime_budget_split_plan.md` |
| `e132e4a` | Document regular session observation checklist | `docs/regular_session_observation_checklist.md` |
| `31b5d04` | Document postrun audit commands and full154 fetch investigation | `docs/postrun_audit_commands.md`, `docs/run_full154_fetch_inventory.md` |

### production code 변경 — helper extraction (2개)

| 커밋 | 메시지 | 변경 파일 |
|------|--------|----------|
| `cfc3491` | Extract main runtime Slack helpers | `app/notifications/main_runtime_hooks.py` (신규), `app/main.py` (shim 유지) |
| `7b9905a` | Extract runtime budget remaining helpers | `app/core/runtime_budget.py` (신규), `app/main.py` (shim 유지) |

### tests-only (3개)

| 커밋 | 메시지 | 변경 파일 |
|------|--------|----------|
| `748c67e` | Add unit tests for extracted helper modules | `tests/test_main_runtime_hooks.py`, `tests/test_runtime_budget_helpers.py` |
| `50faa85` | Add runtime status snapshot unit tests | `tests/test_runtime_status_snapshot.py` |

---

## production code 변경 요약

총 3개 파일, 약 72 줄 변경 (17 insertions / 26 deletions in `app/main.py`, + 신규 2 파일).

### app/notifications/main_runtime_hooks.py (신규, 29줄)

`app/main.py`에서 3개 순수 helper를 분리:

- `slack_event_type_for_order_action()` — Slack 이벤트 타입 매핑
- `slack_session_status_text()` — session status 텍스트 추출
- `market_session_status_payload()` — market session payload 구성

`app/main.py`에는 기존 함수명으로 compatibility shim이 남아있다.

### app/core/runtime_budget.py (신규, 26줄)

`app/main.py`에서 2개 순수 helper를 분리:

- `api_budget_backoff_remaining_seconds()` — EGW00201 backoff 잔여 초
- `api_budget_transient_backoff_remaining_seconds()` — transient backoff 잔여 초

`app/main.py`에는 기존 함수명으로 compatibility shim이 남아있다.

### app/main.py (43줄 변경: +17, -26)

위 두 extraction에 의한 변경만 존재한다.

- 기존 helper 본체를 제거하고 새 모듈을 import
- 기존 함수명을 compatibility shim으로 유지
- **trading behavior, 주문 순서, API budget mutation, scheduler cadence는 변경하지 않았다**

---

## tests-only 변경 요약

총 3개 테스트 파일 추가, 812줄, 93개 테스트 케이스.

| 파일 | 테스트 수 | 대상 |
|------|---------|------|
| `tests/test_main_runtime_hooks.py` | 19 | `main_runtime_hooks.py` 3개 함수 |
| `tests/test_runtime_budget_helpers.py` | 16 | `runtime_budget.py` 2개 함수 |
| `tests/test_runtime_status_snapshot.py` | 39 | `runtime_status_snapshot.py` build/write/read/render |

추가 실행 검증:
- 기존 테스트 `test_slack_bot.py` (34), `test_slack_notifier.py` (31), `test_main_runtime_hooks.py` (19) 전체 통과
- 기존 7개 테스트 파일 97 passed 확인

---

## docs-only 변경 요약

총 7개 문서 파일, 약 1,456줄.

| 파일 | 내용 |
|------|------|
| `docs/workspace_inventory.md` | 프로젝트 전체 디렉토리/파일 구조 |
| `docs/tools_inventory.md` | `app/tools/` 모듈 사용법/인자 목록 |
| `docs/main_split_plan.md` | `app/main.py` split 순서/금지 범위 |
| `docs/runtime_budget_split_plan.md` | runtime budget helper 분리 계획 |
| `docs/regular_session_observation_checklist.md` | regular session 관찰 항목 |
| `docs/postrun_audit_commands.md` | postrun audit 명령 실행 순서 |
| `docs/run_full154_fetch_inventory.md` | `run_full154_fetch.sh` 깨진 참조 조사 |

---

## 월요일 regular session에서 특히 확인할 항목

### Slack helper extraction 관련 (`cfc3491`)

- [ ] Slack 주문 알림이 정상적으로 발송되는지
- [ ] 이벤트 타입 매핑이 변경 전과 동일한지 (submitted→order_submitted, succeeded→order_accepted, failed→order_rejected)
- [ ] market session status payload에 session, order_allowed, reason, buy_block_action, sell_block_action 키가 모두 존재하는지
- [ ] Slack runtime status snapshot이 정상 갱신되는지

### runtime budget remaining helper 관련 (`7b9905a`)

- [ ] `last_budget_status`에 `backoff_remaining_seconds`가 포함되는지
- [ ] `last_budget_status`에 `transient_backoff_remaining_seconds`가 포함되는지
- [ ] remaining seconds 값이 음수가 되지 않는지
- [ ] EGW00201 backoff 발생 시 남은 초가 감소하면서 정상 복구되는지
- [ ] transient backoff 발생 시 scheduler가 예상대로 skip하는지

### runtime status snapshot 관련

- [ ] Slackbot `status` 명령이 세션/주문 카운터를 정상 표시하는지
- [ ] Slackbot `health` 명령이 rate-limit/PnL/backoff 상태를 정상 표시하는지
- [ ] Slackbot `positions` 명령이 보유 종목을 정상 표시하는지
- [ ] Slackbot `orders today` 명령이 로컬 주문 로그 기준으로 정상 표시하는지
- [ ] Slackbot `bottlenecks` 명령이 병목 카운터를 정상 표시하는지

### postrun audit 관련

- [ ] regular session 종료 후 `scripts/check_eod.sh mock_12345678_01` 실행 가능한지
- [ ] `postrun_diagnostics` 개별 실행 가능한지
- [ ] `eod_health_check` 개별 실행 가능한지

---

## 이상 신호가 나오면 의심할 커밋/영역

| 이상 신호 | 의심 커밋 | 영역 |
|----------|---------|------|
| Slack 주문 알림 누락/이벤트 타입 변경 | `cfc3491` | `app/notifications/main_runtime_hooks.py` shim |
| session status payload 키 누락 | `cfc3491` | `market_session_status_payload()` |
| `backoff_remaining_seconds` 누락/음수 | `7b9905a` | `app/core/runtime_budget.py` |
| `transient_backoff_remaining_seconds` 누락/음수 | `7b9905a` | `app/core/runtime_budget.py` |
| import error, AttributeError | `cfc3491` 또는 `7b9905a` | shim import 경로 |
| Slackbot 명령 응답 깨짐 | 관련 없음 (이번 작업에서 `slack_bot.py` 미수정) | `runtime_status_snapshot.py` |

---

## 이상 없을 때 push 판단 기준

다음 조건을 모두 만족하면 push한다:

1. regular session 동안 Slack 알림/Slackbot 명령이 정상 동작
2. EGW00201 backoff remaining seconds가 정상 표시
3. import/attribute 에러 없음
4. postrun audit 도구 실행 가능
5. bottleneck 경고 빈도가 이전 세션과 비슷
6. 전체 테스트 green 재확인:

```bash
PYTHONPATH=. pytest tests/ -v --tb=short
```

---

## 원칙

- 월요일 regular session 관찰이 끝나기 전에 추가 refactor를 하지 않는다.
- 이 문서를 커밋한 후 push는 하지 않는다.
- 관찰 결과에 따라 rollback이 필요하면 `git revert` 대상은 `cfc3491` (Slack helper)과 `7b9905a` (budget helper)이다.
- docs-only, tests-only 커밋은 rollback 대상이 아니다.
