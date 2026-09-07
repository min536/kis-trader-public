# 설계안 3 — Toss 분봉 장중 매분 수집기 (국장 200종목 + 장후 미장 옵션) (2026-07-07)

관련: `docs/backtester_redesign_20260706.md` §3(수집→parquet→리플레이 체인),
`docs/slack_backtest_native_pipeline_design_20260707.md` §7 R-B(신선도 게이트 — 이 수집기가
근본 해소), 메모리 `gate2-replay-research-track`.

## 0. 결론 요약

현행 Toss 수집은 **백필 전용**이다: `toss_minute_fetcher`는 과거 구간을 커서로 내려받는
operator-run 도구이고, EOD 파이프라인(`check_eod.sh`)에는 분봉 수집 단계가 아예 없다(실측:
toss/minute/fetch/parquet 무매칭). 즉 장중에 Toss API는 놀고, 당일 분봉은 나중에 백필로만
생긴다. **장중 매분 직전-분봉을 확정 수집하는 상주 수집기**를 신설한다 — KR 세션 200종목
1차, KR 마감 후 미장(US) 수집은 심볼체계 프로브를 통과하면 2단계로. 별도 프로세스·시세 전용
API로 트레이딩 크리티컬 표면과 완전 분리.

## 1. 현행 자산 (실측 앵커)

| 자산 | 실체 |
|------|------|
| 백필 fetcher | `app/research/ingest/toss_minute_fetcher.py` — `openapi.tossinvest.com /api/v1/candles`, 토큰 20h 갱신, rate 4.5/s 기본, 재시도/백오프, 심볼별 커서 상태(`raw_dir/_state`) |
| CLI | `app/tools/fetch_toss_minute_bars.py` — `--symbols-file/--raw-dir/--before/--rate-limit-per-second/--mode` |
| raw 계약 | `raw_dir/date=YYYY-MM-DD/symbol=XXXXXX_YYYYMMDD.csv` (KIS 수집기와 동일 레이아웃, `write_minute_csv` 공유) |
| parquet | `app/tools/build_backfill_parquet.py` → `data/toss_minute_parquet_backfill` (2017-01-02~, 2,072 파티션) + `_workspace/gate2/paths.json` 매니페스트 |
| 유니버스 | `data/minute_universe/current_plus_etf200_20260624.txt` — 200종목(6자리 KRX 코드) |
| 스케줄 | launchd `com.kis-trader.eod-pipeline`(월~금 16:00) — 분봉 수집 단계 **없음** |

KIS 분봉 수집기(`fetch_kis_minute_bars.py`)도 있으나 장중 상주용으로는 부적합: KIS API는
실행 계좌 스로틀과 예산을 공유한다(레인 파이프라인이 BUY 시세용으로 별도 스코프 자격증명을
쓰는 것과 같은 이유). **장중 1차 소스는 Toss, KIS는 백필/검증 보조** — 재설계 문서 R-4의
이중 소스 원칙 유지.

## 2. 요구사항

1. KR 정규장(09:00~15:30 KST) 동안 **각 분 시작마다** 직전 분의 확정 분봉을 200종목에 대해 append.
2. KR 마감 후 **미장(US) 분봉 수집 옵션** — 나중에 필요할 수 있으니 쌓아두는 성격(2단계).
3. 산출물은 기존 raw CSV/parquet 계약과 동일 스키마 → 게이트① QA·네이티브 백테스터(설계안 1)에
   즉시 접속.

## 3. 설계

### 3.1 신규 모듈 — `app/research/ingest/toss_minute_live.py` (순수 로직)

- `plan_sweep(now, universe, watermarks) -> SweepPlan`: 이번 분에 조회할 (symbol, 기대 분) 목록.
  심볼별 **워터마크**(마지막 저장 분) 기반 — 정상 시 직전 1분, 갭 발생 시 캐치업 페이지 수 산출.
- `merge_bars(existing_rows, fetched_rows) -> rows`: 당일 CSV upsert — (symbol, minute) 키 dedup,
  나중 값 우선(미확정 bar 재조회 덮어쓰기).
- 세션 캘린더 게이트: KR 09:00~15:31 / US 22:30~05:00(서머타임)·23:30~06:00(표준시) —
  DST 판정은 `app/research/us_regime/calendar_map.py` 자산 재사용. 휴장일 스킵.

### 3.2 신규 CLI 루프 — `app/tools/run_toss_minute_live.py` (상주 프로세스)

- 매분 **:05초** 정렬 기동(직전 분 bar 확정 지연 흡수), `count=2`로 조회해 직전 분+그 앞 분을
  upsert(늦은 확정치 자가 보정).
- 율량: 200콜 @ 4.5rps ≈ **44.4s/스윕** — 분 내 완료, 여유 ~10s. 스윕 소요 55s 초과 시 다음
  스윕을 `count=3`으로 확장(자가 치유) + 경고 로그.
- 재시작/슬립 복구: 기동 시 워터마크 대비 당일 갭을 기존 커서-백필 경로로 캐치업 후 매분 모드 진입.
- 저장: 기존 raw 계약 그대로(`date=…/symbol=….csv` upsert). **당일 장 마감 후** 기존
  `build_backfill_parquet` 경로로 parquet 병합(EOD 파이프라인에 단계 추가 — plist 변경은 운영자).
- 관측성: heartbeat 파일(`data/toss_minute_live.heartbeat.json` — 마지막 스윕 시각/성공률) +
  수집 정지 5분↑/스윕 실패율 임계 초과 시 SlackNotifier 1회성 알림(기존 게이트 재사용).
- 안전 경계: 시세 API만 호출(주문 API 없음), `TOSS_API_KEY`/`TOSS_SECRET_KEY`는 env로만 접근
  (값 출력·로그 금지), app.main·레인 파이프라인과 프로세스/락 완전 분리.

### 3.3 스케줄 — launchd 잡 신설 (운영자 설치)

`com.kis-trader.toss-minute-live`: 평일 08:55 기동 → 프로세스 내부 캘린더 게이트가 세션 윈도우
밖에서는 idle-sleep(US 옵션 켜면 야간 윈도우도 커버). 중복 기동 방지 fcntl 락(세션 락 선례,
단 **별도 락 파일** — 트레이딩 세션 락과 무관). **설치/기동/중지는 운영자 게이트.**

### 3.4 미장(US) — Phase 2 (프로브 선행)

- **P0 프로브**: Toss `/api/v1/candles`의 US 심볼 코드 체계는 미확인(사내 문서 부재). 운영자
  승인 하에 2~3개 심볼 소규모 실조회 프로브로 (a) 코드 포맷 (b) 분봉 제공 여부 (c) 세션 경계
  타임존을 확정. **프로브 실패 시 US는 별도 소스 재검토로 범위 분리** — 이 설계는 KR을 막지 않음.
- 통과 시: US 유니버스 파일(`data/minute_universe/us_*.txt`, 소규모 시작) + 야간 윈도우 스윕
  활성화. raw 계약 동일, parquet은 별도 디렉터리(`data/toss_minute_parquet_us`) + 매니페스트 키 추가.

## 4. 용량·율량 산정

| 항목 | KR (200종목) | US 옵션 (예: 100종목) |
|------|------|------|
| API 콜 | 200/분 × 390분 = 78,000/일 (4.5rps 상한 준수) | +100/분 × 390분(정규장) |
| raw CSV | ≈ 200×390×~60B ≈ 4.7MB/일 | 비례 |
| parquet | < 1MB/일 (압축) | 비례 |
| 파일 수 | 일 1파티션 + 심볼당 1파일 = 201/일 | 비례 — 월 병합 정책은 후속 |

## 5. 슬라이스 (구현 시 /delegation-plan 대상)

| # | 내용 | 규모 |
|---|------|------|
| T1 | `plan_sweep`/`merge_bars`/캘린더 게이트 순수 함수 | 소형 TDD |
| T2 | 워터마크·재시작 캐치업 (커서 백필 재사용 연결) | 중형 TDD |
| T3 | CLI 상주 루프 + heartbeat + 락 | 중형 |
| T4 | Slack 정지 알림 + 런북(launchd plist 초안 포함) | 소형 |
| T5 | (P2) US 프로브 → 야간 윈도우 + US 유니버스 | 프로브 후 산정 |

## 6. 리스크와 완화

| # | 리스크 | 완화 |
|---|------|------|
| R-a | 비공식 API 변경/차단 | 4.5rps 준수·지수 백오프(기존 재사용)·User-Agent 안정화; 차단 시 백필 모드로 후퇴, KIS로 EOD 보완 |
| R-b | 백필 작업과 동시 실행 시 rate 공유 초과 | 단일 소비자 원칙 — 라이브 수집 중 백필 금지(락 공유) 또는 야간만 백필 |
| R-c | 미확정 bar / 늦은 체결 반영 | `count=2` 재조회 upsert가 직전 분을 1회 자동 보정 |
| R-d | 머신 슬립/재부팅 갭 | 기동 캐치업(§3.2) + heartbeat 알림; wake-guard(caffeinate)는 세션 wrapper 선례 따름 — 운영자 선택 |
| R-e | 시세 데이터 이용 범위 | 사적 연구/백테스트 용도 한정, 재배포 없음 |

## 7. 착수 제안

1. T1~T4 구현(KR만) → 운영자: launchd 설치 → 1거래일 병행 관찰(heartbeat·스윕 성공률).
2. 관찰 통과 시 EOD parquet 병합 단계 연결 → 설계안 1의 신선도 게이트가 "당일 데이터 있음"으로
   전환되는지 확인.
3. US는 P0 프로브 결과 보고 후 별도 승인.

## 8. 구현 완료 (2026-07-07, TDD)

| 슬라이스 | 산출물 | 테스트 | 상태 |
|------|------|------|------|
| T1 | `app/research/ingest/toss_minute_live.py` — `is_kr_session_open`(주중 09:00~15:31)·`plan_sweep`(워터마크 갭 캐치업+캡)·`merge_bars`(분키 idempotent upsert, later-wins) | `tests/test_toss_minute_live.py` (8) | ✅ |
| T2 | 워터마크·재시작 캐치업(커서 백필 연결) | — | ⏳ T1 위에 조립 |
| T3 | CLI 상주 루프 + heartbeat + 락 | — | ⏳ 상주 프로세스(운영자 launchd) |
| T4 | Slack 정지 알림 + 런북(plist 초안) | — | ⏳ T3 후 |
| T5 | (P2) US 프로브 → 야간 윈도우 | — | ⏳ 운영자 승인 프로브 선행 |

**설계 정합 근거(설계안 1과 연동)**: 실 parquet 스토어 최신 파티션이 **2025-06-16**로 확인됨
(약 13개월 신선도 갭). T1은 그 갭을 메우는 매분 수집기의 순수 로직 코어이며, T3 상주 루프가
`plan_sweep`/`merge_bars`/세션 게이트를 주입 소비하는 구조. T3~T5는 상주 프로세스·launchd 설치·
US 프로브 승인이 필요해 operator 게이트로 남김(에이전트가 실행하지 않음).
