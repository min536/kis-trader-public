# Postrun Audit Commands

월요일 regular session 종료 후 실행할 audit 명령 정리.

> **주의**: 아래 명령은 로컬 로그/데이터를 읽는 오프라인 분석 도구입니다.
> 한국투자증권 API를 호출하지 않으며, trading 행위를 수행하지 않습니다.

---

## 목적

- 당일 regular session의 정상 운영 여부 확인
- 매수 선정 파이프라인 병목(bottleneck) 분석
- rate-limit / budget 압박 수준 확인
- core rescue / defensive mode 동작 확인
- 다음 세션을 위한 guard 수치 재검토

---

## 실행 전 확인사항

1. session이 정상 종료되었는지 확인 (`scripts/stop_session.sh` 또는 자연 종료)
2. `.venv` 활성화 확인: `source .venv/bin/activate`
3. 프로젝트 루트(`kis-trader/`)에서 실행
4. `ACCOUNT` 값 확인 (현재: `mock_12345678_01`)
5. `DATE` 값 확인 (YYYYMMDD 형식, 예: `20260512`)

---

## 권장 실행 순서

### 1. 통합 EOD 체크 (scripts/check_eod.sh)

가장 권장되는 방법. 1~11단계를 자동으로 순차 실행합니다.

```bash
./scripts/check_eod.sh mock_12345678_01
# 또는 날짜 지정:
./scripts/check_eod.sh mock_12345678_01 20260512
```

**포함 단계:**
1. `eod_health_check` — 핵심 카운트/압력/rescue 상태
2. `export_signal_dataset` — signal 데이터 CSV export
3. `analyze_signal_dataset` — signal 분포 분석
4. `export_signal_outcome_dataset` — outcome export
5. `analyze_signal_outcome_dataset` — outcome 분석
6. `postrun_diagnostics` — 종합 진단 (funnel/bad-pattern/rejection/budget)
7. `analyze_core_bucket` — core 버킷 심층 분석
8. `export_ml_candidate_dataset` — ML 데이터 export
9. `build_ml_labels` — ML 라벨 생성
10. 누적 ML 데이터셋 빌드
11. ML retraining (walk-forward)

**확인할 출력 항목:**
- `eod_health_check` 섹션의 executed/final_candidate 비율
- budget rescue / core rescue 적용 횟수
- rate_limit_triggered / sell_watch_partial 횟수
- postrun conclusion의 bottleneck 유형

**주의사항:**
- ML 단계(8-11)는 실패해도 non-fatal (경고만 출력)
- DATE를 생략하면 자동으로 최신 market day를 감지

---

### 2. 개별 명령 실행 (check_eod.sh 대신 수동으로 실행할 경우)

#### 2-1. eod_health_check

```bash
python3 -m app.tools.eod_health_check --account mock_12345678_01
# 날짜 지정:
python3 -m app.tools.eod_health_check --date 20260512 --account mock_12345678_01
# 세션 지정:
python3 -m app.tools.eod_health_check --date 20260512 --account mock_12345678_01 --session REGULAR
```

**인자:**
- `--account` (필수): 계좌 식별자
- `--date` (선택): YYYYMMDD. 생략 시 최신 market day 자동 감지
- `--session` (선택, 기본값: REGULAR)
- `--last-n-cycles` (선택): postrun에 전달할 최근 N 사이클 제한

---

#### 2-2. postrun_diagnostics

```bash
python3 -m app.tools.postrun_diagnostics --date 20260512 --account mock_12345678_01
# 세션 + 최근 사이클 제한:
python3 -m app.tools.postrun_diagnostics --date 20260512 --account mock_12345678_01 --session REGULAR --last-n-cycles 5
# JSON 출력:
python3 -m app.tools.postrun_diagnostics --date 20260512 --account mock_12345678_01 --json
```

**인자:**
- `--date` (필수): YYYYMMDD
- `--account` (필수): 계좌 식별자
- `--session` (선택)
- `--last-n-cycles` (선택): 최근 N 사이클만 분석
- `--json` (선택): JSON 형태로 출력

**확인할 출력 항목:**
- Funnel Summary (total → final_candidate → executed)
- Bad Pattern Counts (legacy 패턴은 구 로그 잔재)
- Recent Rejection Reasons 분포
- Bucket → Deep-Eval / Final-Candidate / Executed 테이블
- Core Bucket Deep-Eval Analysis
- Budget / Rate-Limit Pressure
- Diagnostic Conclusion (bottleneck 유형 + hint + next_steps)

---

#### 2-3. compare_runtime_quality

```bash
python3 -m app.tools.compare_runtime_quality \
    --account mock_12345678_01 \
    --baseline-date 20260509 \
    --target-date 20260512
```

**인자:**
- `--account` (필수)
- `--baseline-date` (필수): 비교 기준일 YYYYMMDD
- `--target-date` (필수): 비교 대상일 YYYYMMDD
- `--stdout-a` (선택): baseline stdout 로그 파일 경로
- `--stdout-b` (선택): target stdout 로그 파일 경로

**주의사항:**
- stdout 로그가 없으면 live_snapshot / stale fallback 지표가 `n/a`로 표시
- `--baseline-date`는 이전에 정상 운영된 날짜를 사용

---

#### 2-4. recommend_score_guards

```bash
python3 -m app.tools.recommend_score_guards \
    --account mock_12345678_01 \
    --dates 20260509,20260512
```

**인자:**
- `--account` (필수)
- `--dates` (필수): 쉼표 구분 날짜 목록 (YYYYMMDD 또는 YYYY-MM-DD)
- `--session` (선택, 기본값: REGULAR)
- `--json` (선택): JSON 형태로 출력

**주의사항:**
- 여러 날짜를 포함할수록 추천값의 신뢰도가 높아짐
- 확인 필요: 추천된 `buy_min_score` / `buy_rule_required_pass_count`를 즉시 적용하지 말고, score_gap과 함께 검토

---

#### 2-5. retention_check (read-only 보존정책 위반 스캔)

```bash
python3 -m app.tools.retention_check   # §7 정책 위반(14일 초과 plain 파일 · 100MB 초과 active 파일) 보고, 읽기 전용 (쓰기는 --json <path> 뿐)
```

---

## 불명확하거나 추가 확인 필요한 부분

| 항목 | 상태 |
|------|------|
| `compare_runtime_quality`의 `--stdout-a/b` 인자에 사용할 정확한 로그 파일 명명 규칙 | stdout 로그가 `logs/app_stdout_YYYYMMDD.log` 형식인 경우 자동 감지됨. 다른 형식이면 수동 지정 필요 |
| `recommend_score_guards` 결과의 settings 반영 방법 | 확인 필요 — YAML config에 반영하는 것으로 추정되나 정확한 절차는 별도 확인 |
| `postrun_diagnostics --json` 출력의 후속 파이프라인 존재 여부 | 확인 필요 |

---

## 이 문서에서 다루지 않는 것

- API 호출이 필요한 실시간 명령
- `app.main` 실행
- `run_full154_fetch.sh` (별도 조사 문서 참조: `docs/run_full154_fetch_inventory.md`)
