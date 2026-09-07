# High-Throughput API Budget Profile 설계 (500종목/분 관찰 목표)

> 근거: `docs/0611plan.md` §6. 2026-06-27 현재 기본 운영 프로파일은
> live read-only BUY quote lane을 사용해 200종목/분 관찰로 상향되었다.
> 적용은 환경변수 오버라이드로만 하며, live 적용은 사람 승인 + `/risk-assessment` +
> `/deploy-checklist`를 별도로 거친다. 이 문서 자체는 어떤 런타임 변화도 일으키지 않는다.

## 1. 현재 기본값 (app/auth/settings_fields.py 실측)

| 노브 (env) | 기본값 | 의미 |
|------------|-------|------|
| `API_SOFT_MAX_REQUESTS_PER_SECOND` | 4 | 초당 소프트 요청 상한 |
| `API_SOFT_MAX_QUOTES_PER_TICK` | 20 | tick당 시세 조회 상한 |
| `SCAN_SYMBOLS_MAX_PER_CYCLE` | 200 | 사이클당 관찰 심볼 수 |
| `BUY_SCAN_INTERVAL_SECONDS` | 60 | BUY 스캔 주기 |
| `SELL_CHECK_INTERVAL_SECONDS` | 30 | SELL 감시 주기 |
| `BUY_SCAN_SHALLOW_TOP_K` | 200 | shallow 통과 상위 K |
| `BUY_SCAN_DEEP_EVAL_LIMIT` | 200 | deep eval 상한 |
| `BUY_SCAN_QUOTE_KIS_ENV` | `live` | BUY scan 현재가 조회 전용 read-only lane |
| `BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND` | 20 | live BUY quote lane 로컬 초당 상한 |
| `BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS` | 0.05 | live BUY quote lane 최소 호출 간격 |
| `LIVE_SNAPSHOT_TTL_SECONDS` / `..._REFRESH_INTERVAL_SECONDS` | 420 / 180 | 스냅샷 신선도 |

관찰 처리량(현재) ≈ `200심볼 × (60/60)사이클/분` = **200심볼/분** — 목표 500/분 대비 40%.

## 2. 예산 산식

분당 총 요청 = `API_SOFT_MAX_REQUESTS_PER_SECOND × 60`.
BUY 관찰 가용분 = 총 요청 − (SELL 감시 + 잔고/주문가능 + 주문 실행 reserve + 안전 여유).

목표 상태(스테이지 HT-3) 예시, 15 r/s 기준:

```text
총:                15 × 60 = 900 req/min
SELL 감시:        보유 N×(60/30) = N×2.0  (N=10 가정 ≈ 20)
잔고/주문가능:     ~20
실행 reserve:      api_buy_scan_min_request_reserve / quote_reserve 유지분 ~60
안전 여유(15%):    ~135
BUY 관찰 가용:     ≈ 660 → 목표 500/min 충족(여유 ~25%)
```

관찰 심볼/분 = `SCAN_SYMBOLS_MAX_PER_CYCLE × (60 / BUY_SCAN_INTERVAL_SECONDS)`.
profile rotation(`BUY_SCAN_PROFILE_ROTATION_ENABLED=true`)이 사이클마다 다른 층을 돌므로
**유니버스 커버리지**는 (심볼/분 × TTL/60) 까지 확장된다.

## 3. 스테이지 사다리 (관찰 기반 단계 상승)

| 스테이지 | r/s | SCAN/CYCLE | INTERVAL | DEEP_EVAL | QUOTES/TICK | 관찰 심볼/분 |
|---------|-----|-----------|----------|-----------|-------------|--------------|
| 현행 (live quote lane) | 20 | 200 | 60 | 200 | 20 | 200 |
| HT-1 | 20 | 300 | 60 | 300 | 20 | 300 |
| HT-2 | 20 | 400 | 60 | 400 | 20 | 400 |
| HT-3 (목표) | 20 | 500 | 60 | 500 | 20 | 500 |

- execution lane 하드 실링: mock portfolio/SELL/order 쪽은 기존 4 r/s budget을 유지한다.
- live BUY quote lane은 read-only 전용으로 20 r/s를 사용하되, EGW00201 발생 시 즉시 backoff한다.
- `SELL_CHECK_INTERVAL_SECONDS`는 모든 스테이지에서 불변(보유 보호가 BUY 관찰보다 우선).
- 모의(mock) 환경의 KIS 한도는 실전보다 낮을 수 있음 — 사다리는 환경별로 **별도 실측** 후 적용.

## 4. 스테이지 상승 게이트 (각 스테이지 최소 2 세션 관찰)

다음 전부 충족 시에만 다음 스테이지로:

```text
rate-limit 응답(backoff 발동) 0건
MAIN_LOOP_EXCEPTION / API timeout 증가 없음 (memory: timeout_error_handling 참조)
throttle metrics summary 의 대기시간 p95가 직전 스테이지 대비 2배 미만
SELL 감시 지연 없음 (sell check 주기 이탈 0)
주문 실행 reserve 고갈 0회 (api_buy_scan_min_*_reserve 바닥 안 침)
```

하나라도 위반 → 직전 스테이지로 즉시 복귀(env 원복), 원인 분석 후 재시도.

## 5. 적용 방법 (env 오버라이드 — 기본값 무변경)

스테이지는 셸 프로파일/세션 환경으로만 정의한다. 예: HT-1 =
`SCAN_SYMBOLS_MAX_PER_CYCLE=300 BUY_SCAN_SHALLOW_TOP_K=300
BUY_SCAN_DEEP_EVAL_LIMIT=300 BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND=20`.

- `.env` 파일은 수정하지 않는다(에이전트 보호 파일). 운영자가 launch 환경에서 주입.
- adaptive midday / degraded mode 의 스케일 변수(`ADAPTIVE_MIDDAY_*`, `DEGRADED_MODE_*`)는
  비율 의미가 유지되는지 스테이지마다 확인(미세조정 필요 시 같은 비율로 동반 상향).
- 적용 순서: mock soak(HT-1부터) → 스테이지별 게이트 통과 → live는 read-only/shadow 관찰
  후 사람 승인.

## 6. 리스크

| 리스크 | 완화 |
|--------|------|
| KIS burst 한도(초당 순간) 초과 | r/s 소프트 상한 + 균등 분산(기존 throttle), 15 상한 |
| 토큰 만료/재발급 폭주 | 기존 token cache 경로 유지, 스테이지 상승과 무관 |
| SELL 감시 굶주림 | SELL 주기 불변 + reserve 우선권(기존 구조 유지) |
| rate-limit 연쇄 백오프 | 게이트 §4의 0건 기준 — 1건이라도 발생 시 즉시 강등 |
| 모의/실전 한도 차이 | 환경별 사다리 분리 실측 |

## 7. 상태

- [x] 설계 (이 문서)
- [ ] mock 환경 HT-1 실측 (분봉 데이터 수집과 함께 진행 예정 — 보류 중)
- [ ] 스테이지 게이트 자동 리포트(기존 throttle metrics 활용) — 필요 시 후속
