# 코드베이스 건강 감사 + 해결 설계안 (2026-07-16)

방법: 이 세션의 실측(도구 결과)만 근거로 사용. 추측 항목 없음. 각 발견에
증거 → 리스크 → 해결 설계를 기록. 우선순위 P0(즉시)~P3(핸드오프/위생).

## §0 발견 요약

| # | 발견 | 심각도 | 상태 |
|---|------|--------|------|
| 1 | **94파일 미커밋** 대시보드-v2 트랙 (53M+36U+5D, deps 변경 포함) | **P0** | 개방 |
| 2 | `com.kis-trader.dashboard-v2` launchd 잡이 **plist 없이 ad-hoc submit** + KeepAlive | **P1** | 개방 |
| 3 | 런타임(`dashboard_control.py`)이 `workspace/claude-design/` 디자인 샌드박스에 의존 | **P1** | 개방 |
| 4 | `logs/` **2.6GB** (100MB+ 파일 3개) — 보존정책 문서만 존재, 집행 없음 | **P2** | 개방 |
| 5 | 회전 계좌 equity=0 대시보드 갭 (기존 발견, 운영자 게이트) | P2 | 개방 |
| 6 | slack-bot plist의 dead env `SLACK_BACKTEST_ACCOUNT` 잔존 | P3 | 핸드오프 대기 |
| 7 | `main` 브랜치가 58보다 **276커밋** 뒤 (0 ahead) — 기준선 부재 | P3 | 개방 |
| — | ~~세션 잡 중복~~ → **해소 실측** (`com.kistrades.regularsession` launchctl에서 소멸) | ✅ | 종결 |
| — | ~~테스트 격리 누수~~ → **해소 실측** (`tests/conftest.py` tracked, G1/G2/G3 가드 구현) | ✅ | 종결 |

## §1 P0 — 94파일 미커밋 대시보드-v2 트랙

**증거:** `git status` 집계 53 M / 36 ?? / 5 D. 삭제 5는 streamlit 대시보드
일소(`app/dashboard/{charts,panels,streamlit_app,theme}.py`, `tests/test_dashboard_panels.py`),
untracked 36에 앱 모듈 6(`v2_site_payload.py`, `token_cache_identity.py` 등)·테스트 16·
설계문서 9 포함. `requirements.txt`/`constraints.txt`에서 `streamlit`·`plotly` 제거가
**미커밋 diff로만** 존재.

**리스크:** ① 전체 스위트 그린(3423)이 이 더티 상태에 결합 — 어떤 checkout/reset도
스위트를 깨거나 작업을 소실시킴. ② 기준선 카운트 오염(이번 트랙에서 +2 미설명 델타의
원인으로 실측됨). ③ deps 제거가 커밋 없이 환경에만 반영되면 재구축 시 불일치.

**해결 설계 (S0-스타일 편입, 도메인별 커밋 분할):**
1. `feat(dashboard): v2 사이트 페이로드/서버/옵션` — untracked 앱 모듈+대응 테스트.
2. `refactor(dashboard): streamlit 대시보드 제거` — 삭제 5 + 관련 M(테스트 수정 포함).
3. `build(deps): streamlit/plotly 제거` — requirements/constraints (커밋 2와 같은 PR 단위).
4. `feat(tools/auth/...)`: 나머지 untracked 모듈 그룹별 (census/rotation/token 등).
5. `docs:` 설계문서 9종 일괄.
- **게이트:** 각 커밋 후 관련 테스트, 전부 끝난 뒤 **클린 트리에서** 전체 스위트
  (지금까지 한 번도 클린 트리 스위트가 실행된 적 없음이 핵심 리스크).
- 위임 시 `/delegation-plan` 계획서 필수(5슬라이스). 이 트랙은 다른 세션 소유
  작업일 수 있으므로 **편입 전 운영자 확인 1회**(어느 세션 산출물인지) 권장.

## §2 P1 — dashboard-v2 잡: plist 없는 ad-hoc submit + KeepAlive

**증거:** `launchctl print gui/501/com.kis-trader.dashboard-v2` → `path = (submitted by
launchctl[72859])`, `program = /usr/bin/env`, `properties = keepalive | inferred program`.
`~/Library/LaunchAgents/*dashboard*` 파일 **없음**. PID 72860이 4173 서빙 중.

**리스크:** ① 재부팅/로그아웃 시 잡 자체가 증발(선언 없음) — "어제는 됐는데" 류 재발.
② **KeepAlive라서 `kill 72860`은 무효** — launchd가 즉시 재스폰. 앞선 안내(“kill 후
슬랙에서 dashboard”)는 이 잡에는 틀린 처방이었음. 정지는
`launchctl remove com.kis-trader.dashboard-v2`. ③ Slack `dashboard` 명령과 이중
소유 — 프로브(T1)가 충돌은 안전 처리하지만 소유권 정책이 없음.

**해결 설계:** 소유권을 하나로 정한다. 권고 = **launchd 상시 서빙을 정본**으로:
1. `launchd/com.kis-trader.dashboard-v2.plist.example` 신설(리포 관례 준수 — 예시
   파일과 이름 일치가 곧 "정본" 신호; HOST=127.0.0.1 명시, KeepAlive true,
   로그 reports/launchd/).
2. 운영자: ad-hoc 잡 remove → plist 설치·bootstrap (1회 핸드오프).
3. Slack `dashboard`는 T1 프로브 덕에 자연히 "상태/URL 안내자"가 됨(이미 서빙 중이면
   already_serving_external 응답) — 코드 변경 불요. HELP_TEXT 문구만
   "(상시 서버가 있으면 URL 안내)"로 1줄 보강 (선택).

## §3 P1 — 런타임 → workspace/claude-design 커플링

**증거:** `dashboard_control.py:39` `DEFAULT_DASHBOARD_SERVER_SCRIPT =
PROJECT_ROOT / "workspace" / "claude-design" / "kis-trader-v2" / "server.py"`.
launch.json·(ad-hoc launchd 잡)도 같은 경로 실행. `tests/test_v2_site_server.py`가
untracked로 이미 존재(§1과 결합).

**리스크:** `workspace/`는 디자인/실험 관례 영역 — 리팩토링·정리 대상이 되기 쉬운
경로에 Slack 명령·launchd 잡·프리뷰 3소비자가 걸려 있음. 경로 이동 한 번에 셋 다 파손.

**해결 설계:** `server.py`를 `scripts/dashboard_v2_server.py`(엔트리) +
`app/dashboard/v2_site_server.py`(로직, 기존 untracked 테스트와 정합)로 승격.
`DEFAULT_DASHBOARD_SERVER_SCRIPT`·launch.json·plist.example 경로 갱신. §1 편입과
같은 트랙에서 실행(순서: 편입 → 승격). 정적 자산(`project/*.jsx` 등)은 우선
workspace에 두고 서버가 절대경로로 서빙(자산 이동은 후속).

## §4 P2 — logs/ 2.6GB 보존정책 미집행

**증거:** `du -sh logs/` = 2.6G. 상위: rotated 계좌 orders 109MB, performance_summary
100MB×2. `docs/log_data_retention_plan.md`는 존재하나 수정본이 미커밋 상태(§1)이고
집행 장치 없음.

**해결 설계:** ① retention 집행 스크립트 `app/tools/enforce_log_retention.py`
(dry-run 기본, rotated-계좌 파일은 `archive/`로 이동+gzip, 활성 계좌는 보존일수 정책) —
plan 문서를 사양으로 TDD. ② 주간 launchd 잡 example(장외 시간, 세션 잡과 비중첩).
③ **가드 준수**: 도구는 파일 이동/압축만, 내용 read 없음. 트레이딩 세션 실행 중
차단(세션 락 감지 시 skip).

## §5 P2·P3 — 기존 개방 항목 (재확인분)

- **회전 계좌 equity=0** (P2): 메모리 `rotated-account-equity-gap` — 성과파일 부재+
  스냅샷 None, 트레이딩-크리티컬 상류라 운영자 게이트. census 도구로 진단 후 착수
  판단 필요(이번 감사에서 재실측하지 않음 — 기존 발견 유지).
- **slack-bot plist dead env** (P3): `SLACK_BACKTEST_ACCOUNT` 잔존 **실측 확인**(grep=1).
  핸드오프 명령은 `docs/session_no_run_incident_20260715.md` §해결 2)에 이미 준비됨.
- **main 276커밋 뒤** (P3): 릴리스·리뷰 기준선이 없음. 해결: 58→main 주기 머지
  정책(예: 트랙 완결 시점 스냅숏 머지). 강제 아님 — 단일 운영자 리포에선 선택.
- **S3 cap→floor 의미 반전** (P3, 재고지): `SLACK_BACKTEST_MAX_RUNTIME_SEC`는 이제
  하한. 운영 env에 상한 의도로 설정된 값이 있으면 재검토.

## §6 실행 순서 제안

1. **§1 편입** (P0 — 소유 세션 확인 후 5슬라이스, delegation-plan) → 클린트리 스위트.
2. **§3 서버 승격** (§1 직후 같은 트랙).
3. **§2 plist 정식화** (docs+example 커밋 + 운영자 핸드오프 1회).
4. **§4 retention 도구** (독립 트랙, TDD).
5. §5는 각 핸드오프/게이트 시점에 처리.

## §7 비목표

- 트레이딩-크리티컬 표면(`app/execution|risk|strategy`, pipeline 게이트류) 리팩토링 —
  이번 감사 스코프 밖(전용 `/risk-assessment` 트랙으로만).
- `workspace/` 정적 자산의 전면 이동(§3에서 서버만 승격, 자산은 후속).
- main 브랜치 정책 강제 — 운영자 결정 사항.
