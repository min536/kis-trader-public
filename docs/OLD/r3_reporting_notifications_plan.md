# R3: 리포팅/알림 구획 슬리밍 계획 (2026-06-11)

대상: `app/reporting/` + `app/notifications/` — 17파일 7,877줄. 상위 6파일이 6,243줄(79%):
runtime_status_snapshot.py 1,320 / performance.py 1,312 / slack_bot.py 974 / console.py 949 /
slack.py 944 / cycle_snapshots.py 744.

방식: R1/R2와 동일 — 구조 지도 → 슬라이스 계획(이 문서) → 슬라이스당 TDD 실행 + 검증 + 1커밋.

## 1. 목표와 비목표

- **목표**: 1,300줄급 파일 2개를 응집 단위로 분할하고, 순수 포맷터/빌더를 테스트 가능한
  전용 모듈로 추출. verbatim move + facade(legacy 이름 바인딩) 보존.
- **비목표**: 동작 변경, 출력 형식 변경, 중복 로직 *통합*(아래 §5 — 차등 하니스가 필요한
  별도 작업), 트레이딩-크리티컬 함수 이동.

## 2. 클러스터 지도 (요약)

| 파일 | 주요 클러스터 (라인) |
|------|---------------------|
| slack.py | 상수/채널 env(35–191) · sanitize(272–310+민감상수 118–162) · 순수 포맷터(313–397) · 알림 텍스트 빌더(400–626) · HTTP 전송 `_post_message_urllib`(639–660) · `SlackNotifier`(663–909) |
| runtime_status_snapshot.py | 스냅샷 빌드/읽기쓰기(169–388) · 포맷 헬퍼(391–445) · positions 읽기/렌더(448–602) · render_* 리플라이(605–862) · **orders-today 수명주기(865–1248, 384줄)** · smoke/CLI(1251–1316) |
| performance.py | 수학·통계(41–67) · 포맷(70–101) · 주문레코드 반복/기간(104–166) · 스냅샷 히스토리/equity basis(169–392) · **계좌/PnL 상태(480–733, 리스크 급전)** · 리포트 빌더(758–1104, 단일 347줄 함수) · 콘솔 라인(1116–1246) |
| slack_bot.py | 명령 파싱(135–199) · **백테스트 서브프로세스 제어(206–573, ~370줄)** · 권한(623–694) · dotenv/env 헬퍼(714–767) · socket-mode 러너(770–952) |
| cycle_snapshots.py | 직렬화기 9종(14–364) · `build_cycle_snapshot`(367–690, 단일 324줄) · persist/load/replay(693–744) |
| console.py | print_* 24종 — Day1에 main.py에서 추출된 랜딩 존. 변경 없음 |

## 3. 트레이딩-크리티컬 결합 (R3 제외 근거)

- `performance.py` 480–733 (`_build_account_state`, `build_account_state_payload`,
  `select_risk_managed_current_equity_krw`, `build_daily_pnl_state`,
  `build_current_drawdown_state`): **`app/risk/pnl_brake.py`가 `build_daily_pnl_state`를
  직접 소비** — 브레이크 판단 입력. 이 클러스터와 그 전용 의존(스냅샷 히스토리 일부)은
  R3에서 이동 금지, R6(/risk-assessment 게이트)에서 다룬다.
- `console.py`는 `app.execution.calculate_sell_position_sizing` / `app.strategy.sell_decision`을
  import하지만 **표시 계산 전용**(주문 경로 아님). 그래도 R3에서는 무변경.

## 4. 슬라이스 계획 (리스크 오름차순, 슬라이스당 1커밋)

| 슬라이스 | 대상 → 랜딩 존 | 규모 | 리스크 |
|---------|----------------|------|--------|
| S1 | slack.py sanitize 클러스터(민감 상수+`_is_sensitive_key`+`_sensitive_env_values`+`sanitize_text`+`_sanitize_details`) → `app/notifications/sanitize.py` | ~90줄 | 낮음 (순수) |
| S2 | slack.py 순수 포맷터+알림 텍스트 빌더(313–626 + 사용 상수, `_get_tag_registry`) → `app/notifications/slack_format.py` (sanitize는 S1 모듈에서 import) | ~360줄 | 낮음 (순수 텍스트) |
| S3 | runtime_status_snapshot.py orders-today 클러스터(865–1248: `_read_today_order_events`…`render_orders_today_reply`) → `app/notifications/orders_today.py` | ~384줄 | 낮음 (order log 읽기만) |
| S4 | runtime_status_snapshot.py 포맷 헬퍼(391–445)+positions 읽기/렌더(448–602) → `app/notifications/snapshot_render.py` | ~210줄 | 낮음 |
| S5 | slack_bot.py 백테스트 서브프로세스 제어(206–573) → `app/notifications/backtest_control.py` | ~370줄 | 중간 (프로세스 수명주기) |
| S6 | performance.py 순수 수학/포맷/기간 헬퍼(41–166) → `app/reporting/performance_metrics.py` | ~125줄 | 낮음 |
| S7 | performance.py 리포트 계층(`_build_trade_quality_summary`, `_build_integrity_warnings`, `build_performance_report`, `persist_performance_report`, `build_performance_console_lines`) → `app/reporting/performance_report.py` — 의존 헬퍼는 S6 모듈로 하향, 순환 import 금지(검증: import DAG) | ~600줄 | 중간 |
| S8 | cycle_snapshots.py 직렬화기 9종(14–364) → `app/reporting/cycle_serializers.py` | ~350줄 | 중낮음 |

제외(명시): §3 리스크-급전 클러스터(R6), `SlackNotifier`/전송 계층(응집 클래스),
slack_bot.py dotenv 헬퍼+socket-mode 러너(env 파일 문자열 — 가드·보안상 제자리), console.py.

완료 시 추정: runtime_status_snapshot 1,320→~720, performance 1,312→~590,
slack.py 944→~500, slack_bot 974→~600, cycle_snapshots 744→~390. 이동 ~2,490줄.

## 4.5 진행 상황 (2026-06-11)

| 슬라이스 | 상태 | 커밋 | 결과 |
|---------|------|------|------|
| S1 | **완료** | ea3d465 | sanitize.py 105줄 신설 (함수 4 verbatim + 상수/정규식 7 값동일), slack.py 944→864 |
| S2 | **완료** | ec90daf | slack_format.py 379줄 신설 (함수 14 verbatim + 상수 7 값동일), slack.py 864→526 — 전송 계층만 잔존. facade 동일성 핀(assertIs 14종) |
| S3 | **완료** | 1d5dbb6 | orders_today.py 413줄 + snapshot_shared.py 37줄 신설 (함수 19 verbatim + 상수 5 값동일), runtime_status_snapshot.py 1,320→975. facade 동일성 핀(assertIs 24종) |
| S4 | **완료** | 1d124a9 | snapshot_render.py 306줄 신설 (함수 11 + 상수 13, AST verbatim 확인), _parse_timestamp·RuntimeSnapshotReadResult는 snapshot_shared로 하향(37→60줄). runtime_status_snapshot.py 975→690 — S3의 중복 facade import 블록 4개 상단 통합 + 스테일 import 5종 제거. facade 핀(assertIs 26종) + 신규 행위 테스트 7종(기존 암묵 커버 헬퍼 직접 커버) |
| S5 | **완료** | 0861fcd | backtest_control.py 476줄 신설 (함수 15 + dataclass 2 + 상수 9, AST verbatim 확인). slack_bot.py 974→569 — 명령 파싱·권한·dotenv·socket 러너만 잔존. facade 핀(assertIs 26종) + 모니터 post-실패 로깅 행위 테스트(except 경로의 logging import를 테스트로 견인). **교차 patch 4곳(launch_backtest_pipeline ×3, _terminate_backtest_process_group ×1)을 이동 전에 양쪽 모듈 동시 패치로 전환** — 패치 미적중 시 실 pid killpg 오발사 위험을 모든 중간 상태에서 차단한 뒤 이동 |
| S6 | **완료** | 5664f72 | performance_metrics.py 신설 (수학 4 + 포맷 5 + 주문레코드 2 + 기간 5 = 16종, AST verbatim 확인). facade 핀 16종 + 신규 직접 행위 테스트 15종(기존 간접 커버만 있던 순수 헬퍼). §5-4 확인: _format_signed_krw/_format_signed_pct는 core/formatters의 format_signed_*와 문자 단위 동일 — 통합은 차등 하니스 후속 커밋으로 위임 |
| S7 | **완료(범위 조정)** | 5bde3fb | performance_report.py 신설 (persist+_save 2종 + trade quality + integrity warnings + console lines = 6종 verbatim). **build_performance_report는 잔존** — 리스크 클러스터(_build_account_state, select_risk_managed_current_equity_krw)를 직접 호출하므로 이동 시 순환 import 또는 R6 위반. import DAG: metrics ← report ← performance 단방향. 신규 행위 테스트: save happy/OSError, persist 위임, trade-quality 집계(기존엔 zero-path만 커버), console 섹션 렌더 전체 |
| S8 | **완료** | 1e083e8 | cycle_serializers.py 362줄 신설 (직렬화기 9종 verbatim). cycle_snapshots.py 744→406 — build_cycle_snapshot(324줄 컴포저)·persist/load/replay 잔존. facade 핀 9종 + 신규 직접 행위 테스트 8종(직렬화기들은 기존에 None-경로 외 커버 전무: full-dict 동등성 패턴) |

- 슬라이스마다 전체 스위트 100% green 후 커밋 (S3 종료 시점: 1,772 passed, 162 subtests;
  S4 종료 시점: 1,787 passed, 188 subtests; S8 종료 시점: 1,822 passed, 245 subtests
  — 수치에는 병렬 보조 작업의 미트래킹 테스트 3파일 포함).
- **R3 분할 작업 완료** (S1~S8). 이동 합계 ~2,400줄: slack.py 944→526,
  runtime_status_snapshot 1,320→690, slack_bot 974→569, performance 1,312→약 990
  (build_performance_report 잔존으로 계획치 590보다 큼 — 사유는 S7 행),
  cycle_snapshots 744→406. 잔여 후속: §5 중복 통합(차등 하니스),
  성과 리포트 본체(build_performance_report)는 R6에서 리스크 클러스터와 함께 재검토.
- 검증 패턴 확립: AST verbatim 기계 비교 + 상수 값-동일성 + undefined-name 스캔 +
  **facade 동일성 핀 테스트(assertIs)** — 로컬 사본 shadowing이 남으면 red가 되어
  제거 단계까지 테스트 주도로 강제된다.
- S3에서 공유 프리미티브 모듈 `snapshot_shared.py` 신설 (계획의 S3/S4 경계 보완):
  `_safe_*` 3종과 DEFAULT_SNAPSHOT_PATH/STALE, _SIDE_LABELS는 orders·positions
  양쪽이 쓰므로 하위 모듈로 내려 순환 import 차단. S4는 여기서 import할 것.
- ~~runtime_status_snapshot.py에 중복 facade import 블록 4개가 남아 있음~~ → S4에서
  상단 import로 통합 완료. 스테일 import(deque/dataclass/iter_lines_bounded/
  KOREA_TZ/sanitize_text)도 함께 제거 — 외부 소비자 AST 스캔으로 무참조 확인 후.
- S4 실행 노트: TDD 가드는 "identity 핀 red → 본문 제거" 직행을 거부한다.
  통과한 순서는 ① 핀 테스트 red(AttributeError) → ② 스텁 생성 → ③ identity red →
  ④ 원본에서 로컬 정의를 *같은 이름의 import 바인딩으로 교체*(단순 삭제로 인식되면
  거부됨) → ⑤ 행위 테스트 red(NotImplementedError)로 함수별 verbatim 채움
  (한 함수씩; 다분기 함수는 fake-then-triangulate 강제) → ⑥ 커버리지 없는 이동
  함수는 신규 행위 테스트로 red를 만들어 채움. 최종 AST 비교로 verbatim 수렴 검증.
- S5~S8 실행 노트(추가 레시피): (a) 교차 patch(이동 함수가 내부에서 해석하는 이름을
  원본 모듈에 patch하는 테스트)는 **이동 시작 전에 양쪽 모듈 동시 patch로 전환** —
  특히 부작용 함수(killpg/subprocess 기동)는 중간 상태에서 패치 미적중이 실 부작용이
  된다. (b) 가드가 binding-교체를 거부하면 순서를 뒤집어 *신규 모듈 fill 먼저*
  (직접 행위 테스트로 red 생성) → 좁힌 identity red 출력 직후 교체 재시도.
  (c) 다분기/대형 dict 함수는 테스트 어서션을 **전체 라인/전체 dict 동등성**으로
  강화하면 "최소 구현 = verbatim 본문"이 되어 증분 강제를 우회하지 않고 수렴한다.
  (d) zero/None fake가 기존 스위트를 전부 통과하면 그 함수는 실질 무커버였다는
  신호 — 반드시 ⑥의 직접 테스트를 추가하고 채울 것 (S6 포맷터, S7 trade-quality,
  S8 직렬화기 전부 해당).

## 5. 중복 발견 (통합은 후속 작업 — R3에서는 분할만)

1. positions 빌더 2계열: `_build_positions_snapshot`/`_normalize_performance_position`
   (runtime_status_snapshot) vs `_serialize_positions`(cycle_snapshots) — ~395줄 평행 로직.
2. 주문 레코드 리더 2계열: `_read_today_order_events`(runtime_status_snapshot) vs
   `reporting/daily_summary.py`의 일별 필터 — 패턴 중복 ~225줄.
3. rate-limit 라벨: `_rate_limit_source_detail`(runtime_status_snapshot:712) vs
   `_format_rate_limit_source`(slack.py:383) — 동일 상수 변형.
4. `performance.py` 포맷터(70–101) vs `app/core/formatters.py` — S6에서 동일성 확인 후
   동일하면 후속 커밋으로 위임(이동 먼저, 통합은 별도).

통합은 차등(differential) 하니스로 동등성 입증 가능할 때만 — R2-3 방식.

## 6. 슬라이스 공통 검증 프로토콜

1. **TDD 순서**: 신규 모듈 대상 테스트(red) → 모듈 생성(verbatim move) → 원본에
   facade 바인딩(`_name = name`) + import 교체 → wide-edit으로 본체 블록 제거.
2. **patch 표면**: 테스트는 모듈 단위 import + `mock.patch.object(모듈별칭, "이름")`
   패턴이 다수 — 슬라이스 착수 시 해당 모듈의 patch.object 이름 목록을 추출해
   facade로 전부 보존 (예: slack_bot은 runtime_status_snapshot에서 render_* 5종
   + 상수 4종 import — S3/S4 후에도 동일 경로 유효해야 함).
3. **verbatim 기계 비교**: AST 함수 추출 + 문자열 비교로 이동 함수 무변경 확인.
4. **전체 스위트 100% green** 후 커밋. 코드/문서 커밋 분리.
5. **import DAG 검사**: 신규 모듈이 원본 모듈을 역-import하지 않을 것
   (sanitize ← slack_format ← slack 단방향).

## 7. 외부 소비자 (facade 보존 대상)

- `app/main.py` ← performance(리포트 5종 + realized summary), console(print_* 21종),
  runtime_status_snapshot(빌드/쓰기 2종), cycle_snapshots(빌드/persist 2종)
- `app/risk/pnl_brake.py` ← performance.`build_daily_pnl_state` (**무변경**)
- `app/notifications/slack_bot.py` ← runtime_status_snapshot(render_* 5종 + 상수 4종),
  slack(`sanitize_text` + 채널 상수)
- `app/notifications/runtime_status_snapshot.py` ← slack.`sanitize_text`
- `app/market_data/benchmark.py` ← performance.`BenchmarkSnapshot`
- `app/reporting/__init__.py` ← performance·cycle_snapshots 재수출(공개 API)
- 테스트 15파일이 두 패키지를 모듈 단위 참조 (patch.object 별칭 기반)
