# Runbook — 모의투자 계좌/키 회전 (새 대회 개시)

**목적**: 모의투자 대회가 갱신되어 `.env`의 계좌번호·mock app key·mock app secret이 바뀔 때,
구 키에 결박된 캐시/상태로 세션이 조용히 불능이 되는 것을 방지한다.

**적용 대상**: `.env`의 다음 3값 중 하나라도 교체될 때.
`KIS_CANO*`, `KIS_APP_MOCK_KEY`(또는 `KIS_APP_KEY`), `KIS_APP_MOCK_SECRET`(또는 `KIS_APP_SECRET`).

**소요**: ~2분. **실행 주체**: 운영자(사람). 에이전트는 준비·검증만, 세션 조작은 하지 않는다.

**배경**: 상세 리스크·설계는 `docs/mock_account_rotation_review_20260706.md` (not included in this source snapshot).

---

## 사전 조건

- 새 세션(`app.main` / launchd 잡)이 **아직 안 떠 있어야** 한다. 떠 있으면 먼저 정상 종료.
- `.env`는 에이전트가 수정하지 않는다 — 운영자가 편집기로 3값을 교체한 상태에서 시작.

## 절차

### 1. 자격증명이 실제로 새 값으로 해석되는지 확인 (마스킹 출력만)

```bash
cd ~/kis-trader
.venv/bin/python -c "
from app.auth.settings import get_settings
s = get_settings()
print('app_key =', s.app_key[:4] + '***')
print('cano    =', s.cano[:4] + '***', '(len=%d, digits=%s)' % (len(s.cano), s.cano.isdigit()))
print('prdt_cd =', s.acnt_prdt_cd)
print('base    =', s.base_url)
"
```

체크:
- `app_key` 앞 4자가 **새 키**인가? 구 키라면 → `.env`에 scoped(`KIS_APP_MOCK_KEY`)와
  generic(`KIS_APP_KEY`) 변형이 공존하고 한쪽만 갱신됨. mock에서는 scoped가 우선
  (`app/auth/settings.py::_resolve_scoped_kis_value`). 두 변형 모두 갱신하거나 generic을 제거.
- `cano`의 `len=8`, `digits=True` 인가? 아니면 하이픈·공백·상품코드 혼입 → `.env` 재확인.
  (설계안 B 반영 후에는 세션 startup sanity가 이 형식오류를 error로 차단한다.)
- `base`가 mock 호스트(`openapivts...`)인가?

### 2. 구 키로 발급된 토큰 캐시 제거

> **설계안 A(token.py 지문 결박)가 반영되면 이 단계는 불필요** — 캐시가 app_key 지문
> 불일치를 감지해 자동 재발급한다. 반영 전까지는 수동 삭제가 유일한 차단책이다.

```bash
# 기본 캐시 경로 (KIS_CREDENTIAL_CACHE_DIR 미설정 시)
rm -f ~/.cache/kis-trader/kis_auth_mock.json
```

- `KIS_CREDENTIAL_CACHE_DIR`를 별도 설정했다면 그 디렉터리의 `kis_auth_mock.json`.
- **live 캐시(`kis_auth_live.json`)는 건드리지 않는다** — read-only quote lane 전용이며
  mock 키 교체와 무관하고 유효하다.

### 3. 구 세션 고아 프로세스 부재 확인

```bash
pgrep -fl "python -m app.main"   # 아무 것도 안 나와야 정상
```

- 락은 계좌 시그니처별(`logs/app_main_{signature}.lock`)이라 구 세션 고아가 새 세션 기동을
  막지는 않지만, 구 키 고아는 인증 오류 알림을 스팸한다. 있으면 정리.
- launchd 재기동은 운영자 단발 kickstart만 사용한다 (`pkill`+이중 kickstart 금지):
  `launchctl kickstart -k gui/$(id -u)/com.kis-trader.session`.
  `com.kistrades.regularsession`는 legacy label이므로 현재 정규 세션 복구 대상으로 쓰지 않는다.

### 3b. 절대 KRW 캡을 새 시드 자본에 맞게 재산출

상대(%) 파라미터는 자동 스케일되지만 아래 3종은 **절대액**이라 시드 자본이 바뀌면
상대적 의미가 조용히 왜곡된다 (상세: [`competition_reset_design_20260706.md`](../competition_reset_design_20260706.md) §RR-1):

```
BUY_MAX_BUDGET_PER_TRADE_KRW   (기본 1,000,000)  ← 새 시드의 0.5~3% 권장
BUY_DAILY_MAX_NOTIONAL_KRW     (기본 500,000)    ← 새 시드의 0.3~5% 권장
SELL_DAILY_MAX_NOTIONAL_KRW    (기본 7,000,000)  ← 새 시드의 ≤30% 권장
```

- 새 대회 시드 자본을 확인하고, 구 대회와 다르면 위 3종을 목표 비율로 재계산해 `.env`
  (운영자 편집) 또는 세션 env에 반영한다. 시드가 같으면 조치 불필요.
- capital-scale report(첫 스냅샷 후 비율 warning)가 반영되면 이 단계는 자동 검출로 보조된다.

### 3c. 구 대회 로그 아카이브 + 대회 라벨 (선택, 세션 기동 전후 무관)

- `KIS_ACCOUNT_LABEL`(예: `mock-contest-2026H2`)을 세션 env에 지정하면
  `data/account_scope_history.jsonl` 스코프 원장에 대회명이 함께 기록된다.
- 구 시그니처 8패밀리 파일(`runtime_state_/cycle_snapshots_/performance_snapshots_/orders_/
  performance_summary_/cycle_stats_/candidate_outcomes_/backtest_signals_`)은
  `archive/accounts/<날짜>_<라벨>_<구sig>/`로 이동해 보존한다. 도구는 기본 dry-run:

```bash
# dry-run: 이동 대상 확인만
.venv/bin/python -m app.tools.archive_account_scope --retired

# 운영자 게이트: dry-run 결과 확인 후 실제 이동 + MANIFEST.json 작성
.venv/bin/python -m app.tools.archive_account_scope --retired --apply
```

  **현재 활성 시그니처 파일은 도구가 거부하며 절대 이동 금지.**

> **원장 공백 주의(2026-07-06 감사 발견)**: `--retired`는 `data/account_scope_history.jsonl`
> 원장 기반인데, 원장은 D1 배포 **이후의** 회전만 기록한다. D1 이전에 일어난 회전(이번
> 2026-07-06 회전 포함)의 구 시그니처는 원장에 없으므로 **명시 지정**으로 아카이브한다:
>
> ```bash
> # 은퇴 시그니처 확인 (활성 시그니처는 meta의 last_account_signature)
> ls data/runtime_state_*.json
> head -c 300 data/account_scope_meta.json
> # 명시 아카이브 (예: 2026-07-06 회전의 구 대회 + 6월 legacy)
> .venv/bin/python -m app.tools.archive_account_scope \
>   --signature mock_acct_0123456789abcdef --signature mock_12345678_01
> # 확인 후 --apply
> ```

### 4. 세션 기동 (운영자 게이트)

- 정상 기동 후 첫 사이클 로그에서 `account scope changed`(previous→current 시그니처)가
  찍히는지 확인 — 새 시그니처로 상태 파일이 새로 시작됨을 의미(정상).
- 첫 주문/잔고 호출이 인증오류(EGW00123류) 없이 통과하는지 확인.

## 검증 / 성공 기준

- [ ] 1단계 마스킹 출력의 app_key·cano·base가 모두 새 대회 값.
- [ ] mock 토큰 캐시 삭제됨(또는 설계안 A 반영으로 자동 무효화).
- [ ] 고아 프로세스 없음.
- [ ] 첫 사이클에서 인증오류 없이 잔고/현재가 조회 성공.

## 롤백 / 문제 시

- 인증오류가 계속되면: 2단계 캐시가 실제로 지워졌는지, 1단계 app_key가 새 키인지 재확인.
- 계좌 자체 거부(KIS 40910000 "모의투자 주문 불가한 계좌") 알림이 뜨면 → 대회 계좌 활성화
  상태를 브로커 측에서 확인. 코드 문제가 아니라 계좌 상태 문제.
- 광범위 장애로 번지면 `/incident-response`.

## 변경 이력

| 날짜 | 변경 |
|------|------|
| 2026-07-06 | 최초 작성 (새 모의대회 계좌·키 회전 대응). 설계안 A 반영 시 2단계 조건부 강등 예정. |
| 2026-07-06 | capital-scale report와 `archive_account_scope` 도구 반영. |
