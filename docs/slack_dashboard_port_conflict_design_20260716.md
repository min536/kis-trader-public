# Slack `dashboard` 포트 충돌 진단·응답 설계안 (2026-07-16)

## §0 증상

2026-07-16 14:57 슬랙 `@project-signalor dashboard` 2회(14:57:07, 14:57:30) 모두
기동 직후 사망. 응답: "Dashboard server exited right after launch (포트 충돌 등으로
추정)" + `OSError: [Errno 48] Address already in use` 트레이스백 tail.

## §1 원인 분석 (실측 인과 사슬)

1. **07-15 17:21** — 슬랙 `dashboard` 기동 **성공**(`slack_dashboard_20260715_172143.log`:
   "Serving … on http://127.0.0.1:4173" + 콘솔 HTML/CSS/JSX 실사용 트래픽 17:29:43까지).
2. **07-15 17:29→17:30** — 그 인스턴스 종료 직후 **17:30:37 슬랙 밖 경로**로
   `server.py`가 재기동됨 → **PID 72860**, 현재까지 생존. 슬랙 경로가 아니라는 증거:
   `reports/slack_dashboard/`에 17:30 타임스탬프 로그 부재(슬랙 launch는 반드시
   타임스탬프 로그를 만든다). 후보: `.claude/launch.json` `kis-v2-local` 프리뷰 서버
   (대시보드 v2 디자인 작업 세션) 또는 터미널 직접 실행. PPID=1(부모 종료로 고아화).
3. **07-16 14:57** — 슬랙 `dashboard`의 멱등 체크는 **pid파일(슬랙이 스폰한 pid)만
   추적** → 외부 기동 72860을 인지 못 함 → 스폰 강행 → `bind()` EADDRINUSE 즉사 ×2.
4. post-spawn liveness 체크(구현 시 설계된 방어층)가 즉사를 감지해 로그 tail을
   응답에 첨부 — **감지는 설계대로 작동**. 갭은 진단(누가 점유?)과 안내(그래서 뭘
   해야?)의 부재.

**핵심 갭 한 줄:** 멱등의 단위가 "슬랙이 아는 pid"인데, 충돌의 단위는 "포트"다.

**현재 상태(실측):** 72860은 지금도 정상 서빙 중 —
`GET / → 302`, `GET /api/health → {"ok": true, "service": "kis-trader-v2-site", …}`.
즉 14:57의 실패는 "대시보드를 못 켠 것"이 아니라 **이미 떠 있는 대시보드를 두고
중복 기동을 시도**한 것.

## §2 기각 가설

- **H1 슬랙 인스턴스 잔존 + pid파일 유실** — 기각. 17:21 인스턴스는 17:29 이후
  로그가 없고(정상 소멸), 17:30 기동은 슬랙 로그 부재로 슬랙 경로가 아님.
- **H3 pid 재활용/start-marker 오판** — 기각. pid파일은 오늘 크래시한 15295를
  정확히 기록(`15295\nThu Jul 16 14:57:30`), 마커 로직 정상.
- **H4 더블-스타트 레이스** — 기각. 두 시도는 23초 간격 순차였고 둘 다 외부
  점유(72860)에 막힘 — 상호 레이스 아님.

## §3 설계

### T1 — 기동 전 포트 프로브 + 대시보드 판별 (핵심)

`launch_dashboard_server()` 스폰 전에 `http://{host}:{port}/api/health`를 짧은
타임아웃(~1s)으로 프로브해 3분기:

| 프로브 결과 | 판정 | 응답 |
|-------------|------|------|
| 연결 거부/타임아웃 | 포트 빈 상태 | 기존 스폰 경로 진행 |
| 200 + `"service": "kis-trader-v2-site"` | **이미 대시보드가 떠 있음(외부 기동)** | ✅ "이미 서빙 중" + url + "슬랙 밖에서 기동된 인스턴스라 `dashboard stop`으로는 중지되지 않습니다" |
| 응답 있으나 시그니처 불일치 | 타 서비스가 포트 점유 | ⚠️ 점유 프로세스 요약(`lsof -i :{port}` 1줄) + "`SLACK_DASHBOARD_PORT` env 또는 `port=N`으로 다른 포트 지정" 안내 |

- 판별 키는 `/api/health`의 `service` 필드(실측 존재 확인) — HTML 302 같은 모호한
  신호가 아니라 서비스 자기선언으로 판정.
- 프로브~스폰 사이 레이스는 기존 post-spawn liveness 체크가 최종 방어(이중 안전,
  기존 계층 유지).
- 신설 status 값: `already_serving_external`, `port_occupied_foreign`.

### T2 — `dashboard port=N` 토큰 (운영 편의)

`backtest days=N`과 동일한 토큰 패턴으로 `dashboard [port=N]`(1024..65535 검증)
지원 → 일회성 포트 변경을 env 수정 없이. url·서브프로세스 `PORT` 주입에 반영.
HELP_TEXT 갱신: `dashboard [port=N]`.

### T3 — stop 안전 경계 명문화 (동작 변경 없음)

`dashboard stop`은 **pid파일이 추적하는 슬랙-스폰 인스턴스만** 중지(기존 동작
유지). 외부 기동 인스턴스에 SIGTERM을 보내는 자동화는 **비목표** — 소유권이
불명한 호스트 프로세스를 봇이 죽이지 않는다. T1의 already_serving_external 응답에
이 경계를 문구로 명시.

## §4 비목표 (검토 후 기각)

- **자동 포트 폴백**(4173 점유 시 4174 자동 선택) — URL이 조용히 바뀌는 surprise가
  운영 혼란(북마크·안내 불일치)을 만든다. 명시적 `port=N`으로 대체.
- **외부 인스턴스 자동 kill/adopt** — 봇이 소유하지 않은 프로세스의 생명주기 개입은
  호스트-프로세스 안전 원칙 위반. 진단·안내까지만.

## §5 테스트 계획 (TDD, 신규 ~6)

1. 프로브 연결거부 → 기존 스폰 경로(레거시 동작 핀).
2. 프로브 200 + kt 시그니처 → `already_serving_external`, 스폰 없음(popen 미호출 단언).
3. 프로브 응답 + 시그니처 불일치 → `port_occupied_foreign`, 스폰 없음.
4. `port=4180` 토큰 → 서브프로세스 env `PORT=4180` + url 반영.
5. `port=80`(범위 밖)/`port=abc` → 사용법 안내.
6. HELP_TEXT `[port=N]` 핀.
- 기존 `tests/test_dashboard_control.py` 8핀 무파손. 프로브는 주입식
  `probe_fn`(기본 urllib, 테스트는 fake)으로 실네트워크 없이.

## §6 즉시 조치 (구현 전, 지금 쓸 수 있는 것)

- **지금 대시보드가 필요하면**: 이미 http://127.0.0.1:4173 이 정상 서빙 중(72860).
  새로 켤 필요 없음.
- 72860을 정리하고 슬랙 관리 하에 두고 싶으면(운영자): `kill 72860` 후 슬랙에서
  `dashboard` — 이후는 `dashboard status`/`stop`으로 관리됨.
