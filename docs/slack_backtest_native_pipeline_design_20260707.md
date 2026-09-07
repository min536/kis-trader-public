# 설계안 1 — Slack 백테스트 파이프라인 네이티브 전환 (open-trading-api 결박 해제) (2026-07-07)

관련: `docs/backtester_redesign_20260706.md`(M0~M4 은퇴 계획 — 이 문서는 그 **Slack 경로 확장**),
`docs/gate2_minute_backtest_plan_20260702.md`, 메모리 `gate2-replay-research-track`.

## 0. 결론 요약

Slack `backtest` 명령은 여전히 외부 사이드카(`~/open-trading-api`, REST `:8002`)를 기동하려다
실패한다. 사이드카 은퇴는 이미 결정·진행 중(M0 발효, M1·M3 완료)이므로, **Slack 파이프라인
스크립트를 네이티브 분봉 리플레이 파이프라인으로 교체**한다. 데이터는 우리가 수집한
KIS+Toss 2017~2026 분봉 parquet(`_workspace/gate2/paths.json` 매니페스트), 엔진은 M1로 완성된
`app/research/replay/backtest_api.run_backtest`(실전 스캔/매도 코드 그대로 리플레이)를 쓴다.
서버 기동·uv/npm·:8002 의존이 전부 사라진다.

## 1. 현상 (실측 2026-07-07)

- `reports/open_trading_api/slack_backtest_20260707_125920.log`: `curl: (7) Failed to connect to
  localhost port 8002` 반복 → `ERROR: backtester did not become healthy within 90s` → exit 1 (89s).
- `reports/open_trading_api/backtester.log`(tail): 사이드카 `start.sh`가
  `line 28: uv: command not found`, `line 43: npm: command not found` — **백엔드 자체가 안 뜸**.
- 직접 원인: Slack 봇(launchd `com.kis-trader.slack-bot`)의 최소 PATH 환경에서 사이드카가
  요구하는 uv/npm이 없음. `backtest_control._build_backtest_subprocess_env()`
  (`app/notifications/backtest_control.py:245`)는 PATH를 봇 환경에서 그대로 물려준다.
- 근본 원인: 외부 사이드카 결합 그 자체. PATH를 고쳐도 Lean DSL 재구현 괴리·서버 운영 부담은
  남는다(재설계 문서 §2). 운영자 방침도 "open-trading-api를 보는 게 아니라 수집된 KIS/Toss
  2017~2026 분봉 데이터를 활용"으로 확정.

## 2. 현행 체인 (실측 앵커)

| 단계 | 실체 |
|------|------|
| Slack 명령 파싱/기동 | `app/notifications/backtest_control.py` — `launch_backtest_pipeline()` argv 계약: `--account` (+`--date`/`--skip-run`/`--dry-run`/`--stop-after`), pid 파일·타임스탬프 로그·heartbeat·완료 모니터·`status`/`kill` |
| 기본 스크립트 | `DEFAULT_BACKTEST_PIPELINE_SCRIPT = scripts/open_trading_api_pipeline.sh` (`:41`; env `SLACK_BACKTEST_PIPELINE_SCRIPT`로 교체 가능) |
| 파이프라인 | start(:8002 기동+90s 헬스대기) → master collect → symbol validation → baselines(`update_baselines.sh`) → research loop |
| research loop | `scripts/run_research_loop.sh` — snapshot → proposal → YAML 후보 → **REST :8002 백테스트** → registry → shadow watch |

## 3. 목표 아키텍처

- **데이터**: parquet 스토어(`_workspace/gate2/paths.json` → `data/toss_minute_parquet_backfill`,
  2017-01-02부터 2,072 거래일 파티션 실재; 라이브 수집분은 설계안 3 수집기가 채움).
- **엔진**: `app/research/replay/backtest_api.run_backtest` (M1 완료) — provider/artifact/기간/
  초기자본 주입, `BacktestResult`(return·mdd·trade_count·win_rate·equity_curve·full report) 반환.
- **서버 없음**: `.venv/bin/python` 절대경로 직접 호출 — launchd 최소 PATH에서도 동작
  (이번 uv/npm 사고의 교훈; `eod_wrapper.sh`의 PYTHON 해석 선례 재사용).

## 4. 설계

### 4.1 신규 CLI — `app/tools/run_native_backtest.py`

```
.venv/bin/python -m app.tools.run_native_backtest \
  --manifest _workspace/gate2/paths.json \
  [--artifact <score_v2 json>]      # 기본: 매니페스트 artifact_out (현행 후보 가중치)
  [--symbols-file data/minute_universe/current_plus_etf200_20260624.txt]
  [--start YYYYMMDD --end YYYYMMDD] # 기본: 가용 최신 60거래일
  [--initial-capital 100000000]
  [--out reports/native_backtest/<ts>.json]
```

- 조립: 매니페스트 → `ParquetMinuteProvider`(M2-S2와 **공용 구성기**) → `run_backtest` →
  요약 JSON 저장 + stdout 마지막에 한 줄 `RESULT: return=…% mdd=…% trades=… win=…%` 출력.
- 데이터 신선도 게이트: 요청 윈도우가 parquet 커버리지를 벗어나면 **명시 경고 + 가용 윈도우로
  축소**해 계속(암묵 fail-open 금지 — 축소 사실을 RESULT 라인과 JSON에 기록).

### 4.2 신규 파이프라인 스크립트 — `scripts/native_backtest_pipeline.sh`

기존 argv 계약을 그대로 수용(= `backtest_control.py` 무변경으로 스왑 가능):

| 기존 옵션 | 네이티브 의미 |
|------|------|
| `--account` | 리포트 네이밍/알림 문맥에만 사용 (계좌 API 호출 없음) |
| `--date` | 백테스트 윈도우 끝일 (기본: 가용 최신일) |
| `--skip-run` | freshness 프로브+계획 출력만, 리플레이 미실행 |
| `--dry-run` | 실행 계획 에코 후 종료 (기존과 동일) |
| `--stop-after` | no-op 수용 (서버가 없음 — 경고 없이 무시) |

스테이지: (0) parquet 커버리지/신선도 프로브 → (1) 네이티브 baseline 백테스트(§4.1 CLI) →
(2) *(M2-S3 완료 후)* research loop `--backend native` → (3) 요약 로그. 단계 (2)는 M2 완료
전까지 기본 skip — **2단계 롤아웃**: 5a(baseline-only, M1+S2만 필요) 먼저, 5b(research loop
native) 나중.

### 4.3 Slack 레이어 — 변경 최소화

- `backtest_control.py`는 로직 무변경. `DEFAULT_BACKTEST_PIPELINE_SCRIPT`만 native 스크립트로
  전환(1줄). 롤백 스위치는 이미 존재: `SLACK_BACKTEST_PIPELINE_SCRIPT` env로 구 스크립트 지정.
- 완료 모니터는 로그 tail을 인용하므로 §4.1의 `RESULT:` 한 줄이 그대로 Slack 완료 메시지에
  실린다 — 추가 통합 코드 불필요.
- env 화이트리스트의 `OPEN_TRADING_API_ROOT`/`BT_URL` 제거는 M4(은퇴 정리)로 이연 — 무해.

### 4.4 재설계 계획과의 편입

`docs/backtester_redesign_20260706.md` §4 M2에 **S5a/S5b(Slack 파이프라인 교체)** 슬라이스로
편입. S2(매니페스트→provider 구성기)는 M2와 이 설계의 공용 선행 슬라이스. 대량 weight search는
계속 게이트④ operator-run — Slack 백테스트는 **단일 리플레이 1회**로 한정(경쟁 실행 금지,
`RUNNING_OWNER.md` 규약과 충돌 없음).

## 5. 슬라이스 (구현 시 /delegation-plan 대상)

| # | 내용 | 규모 |
|---|------|------|
| S2 | 매니페스트→`ParquetMinuteProvider` 구성기 (M2와 공용, 순수 함수) | 소형 TDD |
| S5a-1 | `run_native_backtest.py` CLI (조립·신선도 게이트·RESULT 라인·요약 JSON) | 중형 TDD |
| S5a-2 | `native_backtest_pipeline.sh` (argv 계약 5종 + dry-run 계약 테스트) | 소형 |
| S5a-3 | `backtest_control` 기본 스크립트 전환 + 기존 테스트 그린 확인 | 소형 |
| S5b | research loop `--backend native` 연결 (M2-S3 완료 후) | 중형 |

## 6. 테스트 전략

- CLI 단위: 합성 parquet 픽스처(2~3일×2심볼)로 조립→실행→요약 스키마·RESULT 라인 검증;
  윈도우 축소 경고 케이스; artifact 로드 실패 fail-closed.
- 스크립트 계약: `--dry-run`이 argv 5종을 전부 에코하는지; `--skip-run` 시 리플레이 미호출.
- 회귀: `tests/`의 backtest_control 기존 테스트 전부 그린(레이어 무변경 증명).

## 7. 리스크와 완화

| # | 리스크 | 완화 |
|---|------|------|
| R-A | 장중 실행 시 CPU/IO 경합 (분봉 리플레이) | 기본 윈도우 상한 60거래일 + `nice` 실행 + 로그에 장중 실행 경고 |
| R-B | parquet 스테일 (수집 공백 시 최신일 뒤짐) | §4.1 신선도 게이트가 축소·보고; 설계안 3 수집기가 근본 해소 |
| R-C | 게이트④ 탐색과 경쟁 실행 | Slack 경로는 단일 리플레이 한정; 대량 탐색은 operator-run 유지 |
| R-D | 사이드카 시절 결과와의 연속성 단절 | 은퇴 전 1회 교차 대조(M2-S4)로 승계 근거 확보 후 전환 |

## 8. 운영자 절차 (구현 완료 후)

1. Slack 봇 재시작(상수 전환 반영) — launchd 조작은 운영자.
2. `backtest` 1회 실행 → 완료 메시지의 `RESULT:` 라인 확인.
3. 이상 시 롤백: 봇 환경에 `SLACK_BACKTEST_PIPELINE_SCRIPT=scripts/open_trading_api_pipeline.sh`.

## 9. 구현 완료 (2026-07-07, TDD)

| 슬라이스 | 산출물 | 테스트 | 상태 |
|------|------|------|------|
| S2 | `app/research/replay/provider_assembly.py` (manifest 로드·root 해석·`available_window`/`available_dates`·`clamp_window`·`resolve_artifact` 폴백·`build_parquet_provider`) | `tests/test_provider_assembly.py` (10) | ✅ |
| S5a-1 | `app/tools/run_native_backtest.py` (`run_native_backtest`·freshness 게이트·`plan_only`·`format_result_line`·`main`) | `tests/test_run_native_backtest.py` (7) | ✅ |
| S5a-2 | `scripts/native_backtest_pipeline.sh` (argv 계약 `--account/--date/--skip-run/--dry-run/--stop-after`) | `tests/test_native_backtest_pipeline_script.py` (4) | ✅ |
| S5a-3 | `backtest_control.DEFAULT_BACKTEST_PIPELINE_SCRIPT` → native (env 롤백 보존) | `tests/test_backtest_control.py::DefaultPipelineScriptTests` (2) | ✅ |
| S5b | research loop `--backend native` | — | ⏳ M2-S3(autotuner yaml↔artifact 브리지) 선행 필요 — 별도 위임 |

**end-to-end 실검증**: `scripts/native_backtest_pipeline.sh --account … --skip-run` → native CLI →
**실 parquet 스토어(2072 파티션)** → `RESULT: plan_only days=60 window=2025-03-18..2025-06-16
adjusted=False truncated=True` + 요약 JSON. :8002 curl 거부·uv/npm 부재 실패 경로 완전 소거.
전체 스위트 3301 pass/1 skip(회귀 0).

**발견(설계안 3과 연동)**: parquet 최신 파티션이 **2025-06-16**(약 13개월 전) — Toss 백필이
거기서 멈춤. §7 R-B의 신선도 갭이 실재하며, 설계안 3(라이브 매분 수집기)이 근본 해소 경로.
현재 백테스트는 2025-06까지 데이터로 정상 동작.
