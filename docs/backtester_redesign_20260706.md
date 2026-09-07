# Backtester 재설계안 — open-trading-api 사이드카 은퇴, kis-trader 네이티브 일원화 (2026-07-06)

## 0. 결론 요약

현행 백테스트는 사이드카 리포(`~/open-trading-api/backtester`, QuantConnect **Lean** 기반,
REST `localhost:8002`)의 5단계 흐름(start → master collect → symbol validation → baselines →
research loop)에 결합돼 있다. 그러나 kis-trader에는 이미 **분봉 수집(KIS+Toss) → parquet →
실전-코드 리플레이 → EvaluationRecord → weight search** 전 체인이 구축·검증돼 있다
(gate2 트랙, D1~V1 완료). 사이드카는 **일봉/마스터 데이터와 전략 DSL 재구현**이라는 이중
자산을 유지하는 비용 대비, 실전 코드와의 구현 괴리라는 구조적 약점이 크다.

**제안: 사이드카 5단계 흐름을 은퇴시키고, 백테스터를 kis-trader 네이티브 리플레이로 일원화한다.**
단, 교차검증(독립 구현 대조) 기능만은 "기록 vs 리플레이 parity gate"로 대체 근거를 명시한다.

## 1. 현행 지도 (실측)

### 사이드카 흐름과 실체

| 단계 | 실체 (open-trading-api/backtester) |
|------|------|
| start | `start.sh` — backend(:8002)+frontend 기동 |
| master collect | `scripts/download_master.py` + `setup_lean_data.sh` — KRX 마스터·Lean 데이터 준비 |
| symbol validation | 수집 심볼 정합 검증 (backend/kis_backtest) |
| baselines | 기준 전략 백테스트 → kis-trader `_workspace/autotuner/baselines/` 소비 |
| research loop | proposal 백테스트 반복 — kis-trader가 REST로 위임 |

엔진: `kis_backtest/{lean,dsl,codegen,providers,strategies}` — **Lean DSL로 전략을
재구현**해서 돌리는 구조. REST 계약: `POST /api/backtest/run-custom`
(yaml_content·symbols·dates·initial_capital·비용 파라미터).

### kis-trader 쪽 결합점 (8개 도구 + autotuner)

| kis-trader 모듈 | 결합 형태 |
|------|------|
| `app/tools/run_proposal_backtest.py` | :8002 run-custom 호출 (autotuner proposal 검증) |
| `app/autotuner/candidate_suggest.py` + `app/tools/autotuner_suggest.py` | proposal→백테스트 루프 |
| `app/tools/shadow_watch.py` | 실계좌 vs Lean 백테스트 그림자 대조 (`_DEFAULT_BT_URL=:8002`, `initial_capital` 1억 하드코딩) |
| `app/tools/export_backtest_result_summary.py` | 사이드카 결과 아티팩트 요약 |
| `app/tools/backtester_portability_report.py` | kis-trader 로직→Lean 이식 갭 측정 (이식이 전제였던 시절의 도구) |
| `app/tools/open_trading_api_status.py` + `app/integrations/open_trading_api/adapter.py` | 사이드카 상태·감사 (허용 명령은 read-only 2종) |
| `app/tools/kis_api_sanity_check.py` | 사이드카 연동 sanity |

### kis-trader 네이티브 자산 (대체 재료 — 전부 실재 확인)

- **수집**: `app/research/ingest/kis_minute_fetcher.py`(KRX 분봉),
  `toss_minute_fetcher.py`(Toss Securities `openapi.tossinvest.com`, 동일 raw CSV 레이아웃
  `raw_dir/date=…/symbol=…csv`, 커서 재개), `minute_csv_to_parquet.py` → parquet 스토어
  (`_workspace/gate2/paths.json` 매니페스트).
- **리플레이 엔진**: `app/research/replay/` — `parquet_provider`/`minute_provider`(데이터),
  `payload_synth`(KIS 응답 페이로드 합성), `scan_driver`(**실전 스캔 로직 그대로 구동**),
  `broker_sim`(체결·비용), `sizing`, `sim_runner`/`record_runner`(EvaluationRecord 생산).
- **스코어러/탐색**: `app/gate2/score_v2.py`(runtime-eligible), `app/tools/build_gate2_records.py`,
  `app/tools/run_gate2_weight_search.py`(Stage A/B, `--max-stage-b-finalists` 최신 반영),
  `build_regime_library.py`.
- **운영 체계**: gate2 4게이트(변환→패리티→레코드→탐색) operator-run 절차 +
  `gate2-research-orchestrator` 하네스, replay parity 테스트(`test_replay_parity`).

## 2. 왜 은퇴가 옳은가 (판정 근거)

1. **데이터 중복**: master collect가 준비하는 일봉/마스터는 분봉 parquet의 하위 해상도 —
   분봉에서 유도 가능. 수집 파이프는 KIS+Toss 이중 소스로 이미 사이드카보다 강하다.
2. **구현 괴리 (결정적)**: Lean DSL 재구현 전략 ≠ 실전 `app/strategy`/스캔 로직.
   `backtester_portability_report.py`가 존재한다는 것 자체가 이 갭 관리 비용의 증거.
   네이티브 리플레이는 `scan_driver`가 **실전 코드 자체**를 분봉 위에서 구동하므로 괴리 0이 설계값.
3. **운영 부담**: 사이드카 서버 기동/버전 동기/:8002 가용성/이중 자격증명 설정(`kis_devlp.yaml`).
4. **이미 증명됨**: gate2 트랙이 변환·패리티·레코드 생산을 operator-run으로 완주해 왔다
   (메모리 `gate2-replay-research-track`). 재설계는 신규 구축이 아니라 **일원화 선언 + 어댑터 교체**다.

**남길 가치 1개 — 교차검증**: shadow_watch의 "독립 구현과 대조" 가치는 동일-코드 리플레이가
줄 수 없다(버그 동반 재생 한계). 대체 논거: (a) **parity gate** — 실기록(cycle_snapshots) vs
리플레이 재생의 일치 검증이 이미 게이트 ②로 존재, (b) 비용·체결 모델은 `broker_sim`에
집중시키고 실체결 로그와 정기 대사(§5 R-2). 이 두 축이 교차검증을 승계한다.

## 3. 목표 아키텍처 (전부 in-repo)

```
[수집]   kis_minute_fetcher ─┐
         toss_minute_fetcher ─┴→ raw CSV → minute_csv_to_parquet → parquet store
                                                    │  (_workspace/gate2/paths.json)
[검증]   게이트① 변환 검증 + coverage/quality QA (gate2-artifact-qa)
                                                    │
[리플레이] payload_synth → scan_driver(실전 스캔) → broker_sim/sizing → sim_runner
                                                    │
[레코드]  record_runner → EvaluationRecord (게이트③)│
                                                    │
[루프]    run_gate2_weight_search (게이트④) · autotuner proposal 평가(신규 어댑터)
          · quant-analyst 분석/시각화
```

5단계 대응: start→(없음, 서버리스) / master collect→분봉 수집기 / symbol validation→게이트①
QA / baselines→네이티브 baseline 리플레이 / research loop→게이트④+autotuner 네이티브 어댑터.

## 4. 마이그레이션 계획 (M0~M4) — 진행 상태 (2026-07-06)

| 단계 | 내용 | 규모 | 상태 |
|------|------|------|------|
| M0 | **동결 선언**: 사이드카 신규 기능 금지, 은퇴 예정 명시 | 문서 | ✅ 이 문서로 발효 |
| M1 | **네이티브 백테스트 진입점**: `app/research/replay/backtest_api.py` | 소형 TDD | ✅ 구현·검증 |
| M2 | **autotuner 어댑터 교체**: yaml/proposal → `ScoreV2Artifact` 브리지 + :8002 치환 | 중형 | ⏳ 위임계획(§4.2) — crux는 yaml↔artifact 매핑 |
| M3 | **shadow_watch `--initial-capital`** (1억 하드코딩 제거) | 소형 TDD | ✅ 구현·검증 |
| M4 | **정리·은퇴**: portability_report·open_trading_api_status·adapter 아카이브, 사이드카 참조 제거 | 소형 | ⏳ 스테이징(§4.3) — M2 완료 후 파괴적 |

### 4.1 M1 — 구현 완료 (`app/research/replay/backtest_api.py`)

- `run_backtest(*, artifact, provider, dates, settings, initial_capital, costs=None, intrabar_mode)`
  → `BacktestResult`(initial/final equity·return%·mdd·trade_count·win_rate·day_count·equity_curve·
  full report dict). 검증된 `run_portfolio_replay`(실전 scan+sell 로직)에 위임 — REST/Lean 불필요.
- `sim_cost_params_from_settings(settings)` — 라이브 비용정책(settings bps 5종) → `SimCostParams`.
  **정본 비용 브리지**(§5 R-3 해소 경로): 백테스트가 라이브와 동일 bps로 비용 계산.
- 테스트 `tests/test_backtest_api.py` 4개 PASS: 비용 매핑(전체/결측), 빈 윈도우 정규화 충실성
  (독립 `run_portfolio_replay`와 report 동등), costs 생략 시 settings 유도.

### 4.2 M2 — 위임계획 (yaml/proposal → ScoreV2Artifact 브리지가 crux)

M2는 단순 클라이언트 치환이 아니다: `run_proposal_backtest`는 **`kis_trader_{family}.kis.yaml`**
(Lean DSL 표현)에 파라미터를 패치해 사이드카에 POST한다. 네이티브 M1은 **`ScoreV2Artifact`
(weights/threshold/normalization_caps)** 를 입력받는다 — **두 전략 표현 사이 변환기가 없다**
(이것이 §2에서 지적한 "구현 괴리"의 실체). 안전한 인라인 치환 불가 → 슬라이스로 위임:

- **S1 (crux)**: `proposal changes` / `kis.yaml` 파라미터 → `ScoreV2Artifact` 매핑기
  (`app/research/replay/proposal_artifact.py` 신규, 순수 함수). 수용 테스트: 알려진 proposal
  → 기대 artifact weights/threshold. 매핑 불가 파라미터는 명시적 `Unsupported`로 실패-닫힘.
- **S2**: parquet provider 구성기(`paths.json` 매니페스트 → `ParquetMinuteProvider`, 심볼·기간 주입).
- **S3**: `run_proposal_backtest`에 **native 백엔드 옵션 추가**(`--backend native|rest`, 기본 `rest`
  유지 — 하위호환·무회귀). native면 S1→S2→`run_backtest`→기존 metrics 스키마로 매핑.
- **S4**: autotuner 루프 1회 네이티브 완주 → 사이드카 결과와 **1회 교차 대조**(은퇴 전 마지막 활용,
  §7). 통과 시 기본 백엔드를 native로 전환.

`/delegation-plan` 규율 대상(2슬라이스+). 게이트 실행(대량 변환·탐색)은 operator-run 유지.

### 4.3 M4 — 스테이징 (M2·교차대조 통과 후에만 파괴적 실행)

`portability_report`·`open_trading_api_status`·`adapter` 은퇴는 사이드카 의존이 M2로 완전 대체된
뒤에만 아카이브(어댑터 감사로그 보존). 이 세션에서는 **아무것도 제거하지 않음** — 순서 역전 시
autotuner 백테스트 경로가 끊긴다.

## 4.4 설계안-구현 정합 검토 (2회, 2026-07-06)

**1차 — 결정적**: `completion_audit.py`(backtester 플랜) FAIL이나 지적 전량 오탐 —
#3 "미구현 test_backtest_api·test_replay_parity"는 백틱 파일명을 함수핀으로 오파싱(두 파일 실재),
#5 스테일 import는 `__future__ annotations` 오탐(pyflakes 독립검사 미사용 import 0).
`tests/test_backtest_api.py` 5 PASS(비용매핑 전체/결측, 빈윈도우 정규화 충실성=독립
`run_portfolio_replay`와 report 동등, **trades+equity_curve 있는 report 정규화**, costs 생략 시
settings 유도), 전체 스위트 3235 PASS.

**2차 — 적대적(주장 대조)**: §4.1~4.3 주장을 코드와 1:1 대조 —
- M1 `run_backtest`는 `run_portfolio_replay`에 kw 그대로 위임(재구현 없음) → 괴리 0 설계 성립.
  위임 충실성은 빈 경로에서 report 동등, 정규화는 합성 trades-report로 필드별 검증(경계 폐쇄).
- §4.2 M2 "위임계획·crux=yaml↔artifact": `run_proposal_backtest`가 `kis_trader_{family}.kis.yaml`
  패치→:8002 POST인 반면 M1은 `ScoreV2Artifact` 입력 — 변환기 부재 실측 확인. **M2 완료라 거짓주장
  하지 않음**(⏳ 표기 정확).
- §4.3 M4 "이 세션 제거 없음": `git status`상 삭제 파일 0 — 파괴적 정리 미실행 정확.
- 잔여 경계: 실데이터(parquet 11GB) 관통 백테스트는 operator-run이라 이 세션 미실행 — M1 위임
  충실성으로 대체 근거. 판정: **설계 정합 PASS**(M1·M3 구현, M2·M4 스테이징 정확 반영).

## 5. 리스크와 완화

| # | 리스크 | 완화 |
|---|------|------|
| R-1 | 동일-코드 리플레이의 자기검증 순환(전략 버그를 백테스트도 재생) | 게이트② parity(실기록 대조) 상시화 + 실계좌-리플레이 일일 대사(M3). "코드 검증"과 "전략 성과 평가"를 분리 인식 |
| R-2 | 체결·슬리피지 현실성 (Lean 체결 모델 상실) | `broker_sim`을 단일 비용·체결 모델 소유자로 승격, 실체결 로그(orders_*.jsonl)와 월 1회 캘리브레이션 리포트 |
| R-3 | 비용 파라미터 이원화(`LEGACY_BACKTEST_COST_PARAMS` vs broker_sim) | M2에서 canonical costs로 통합(shadow_watch 주석의 "operator-gated migration" 완수) |
| R-4 | Toss 소스 의존(비공식 API 변동) | KIS 수집기가 1차, Toss는 백필/보조 — 동일 CSV 계약이라 소스 탈락 시에도 파이프 불변 |
| R-5 | US/해외 확장 시 캘린더·통화 | `us_regime/calendar_map.py` 기존 자산 활용, M1 계약에 market 파라미터 예약 |
| R-6 | 사이드카에만 있는 과거 결과 아티팩트 | M4에서 결과 아카이브 후 read-only 보존 (연구 이력) |

## 6. 하지 않는 것

- Lean 엔진 포크/임베드 — 유지비만 남는다.
- 사이드카 리포 삭제 — KIS 공식 샘플/문서 가치는 별개로 유지(참조용).
- 실시간 페이퍼-트레이딩 대체 — 그것은 mock 세션의 역할, 백테스터 범위 아님.

## 7. 착수 제안

1. 이 문서 승인 → M0 효력.
2. M1을 `/delegation-plan` 계획서로 구획화(리플레이 조립 계약 + 수용 테스트 핀 명시).
3. M2 완료 시점에 autotuner 루프를 네이티브로 1회 완주 → 사이드카 결과와 1회 교차 대조
   (은퇴 전 마지막 활용) → 이후 :8002 의존 제거.
