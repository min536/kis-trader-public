# Multi-Account Parallelization Plan

## 1. 목적
현재 `kis-trader`의 단일 계좌/프로세스 구조에서 발생하는 사이클 지연 문제를 해결하기 위해, 여러 계좌와 AppKey를 사용하여 유니버스를 분할 처리하는 병렬 운영 설계안을 정의한다.

## 2. 현재 가능한 기반
`app/auth/account_scope.py`에 구현된 `account_signature` 체계가 이미 존재한다.
- 예: `mock_12345678_01` (환경_계좌번호_상품코드)
- 이 signature는 `runtime_state`, `order_log`, `cycle_snapshots` 등의 파일명 suffix로 활용되고 있어, 계좌번호가 다르면 핵심 상태 데이터는 이미 물리적으로 분리될 준비가 되어 있다.

## 3. 이미 계좌별로 분리되는 항목
다음 항목들은 `account_signature`를 통해 계좌별로 독립적인 경로를 가진다:
- **Runtime State**: `data/runtime_state_{sig}.json`
- **Order Log**: `logs/orders_{sig}.jsonl`
- **Cycle Snapshots**: `data/cycle_snapshots_{sig}.jsonl`
- **Performance Report**: `logs/performance_summary_{sig}.jsonl`

## 4. 아직 분리되지 않은 위험 요소
병렬 운영을 위해 반드시 해결해야 할 공유 자원 충돌 항목이다:

### A. Session Lock (`logs/kis_trader.session.lock`)
- 현재 전역 단일 파일로 존재하여 두 번째 프로세스 실행을 차단한다.
- 해결: ~~`LOCK_FILE_OVERRIDE` 환경변수~~ → ✅ **DONE (검증 2026-06-25).** `app/main.py`가 `logs/app_main_{get_account_signature(settings)}.lock`로 **계좌 signature 기반 자동 분리** (env+cano+acnt_prdt_cd+app_key+app_secret 해시). `acquire_app_main_lock`는 파라미터화됨. (LOCK_FILE_OVERRIDE env보다 자동·항상안전; 기능 동등.)

### B. Live Snapshot (`data/live_snapshot.json`)
- 여러 프로세스가 동일한 파일에 동시 쓰기를 수행할 경우 데이터 오염이 발생한다.
- 해결: ✅ **DONE (2026-06-25, `5677f1e`).** `live_snapshot.py`가 `KIS_LIVE_SNAPSHOT_DIR`/`KIS_LIVE_SNAPSHOT_LOG_DIR` env로 snapshot/PID/heartbeat 디렉토리를 오버라이드(`resolve_snapshot_*_dir`). 기본값 = 기존 repo `data/`+`logs/`(무migration). 계좌별로 distinct dir 설정 시 충돌 없음.
- **Ergonomics (2026-06-25, `2082635`/`9dfd2c1`):** 운영자 수동 env 선정을 줄이기 위해 `app.tools.multi_account_env`가 label별로 충돌 없는 3개 dir env를 생성(shell 출력 `shlex` escape, slug 충돌 거부)하고, `app.tools.multi_account_preflight`가 병렬 실행 전 경로 격리를 `.resolve()` 정규화 기준으로 검증한다.
- **런타임 zero-config 자동분리는 보류(결정 2026-06-25):** `SNAPSHOT_PATH`가 import-time 모듈 상수이고 실제 *writer*가 `scripts/live_snapshot.py`(operator script)에 있어, read 측만 `data/{signature}/`로 자동 스코프하면 write는 bare `data/`로 남는 silent split이 된다. 명시적 env 메커니즘이 이미 정확하므로 그것을 유지한다.

### C. Token Cache (`.token_cache.json`)
- 현재 `mock`/`live` 환경별로만 분리되어 있어, 동일 환경 내 다른 AppKey 사용 시 덮어쓰기 경쟁이 발생한다.
- 해결: ✅ **계좌별 분리 가능 (검증 2026-06-25, 코드변경 불요).** `get_token_cache_path`(`app/auth/settings.py`)가 이미 `KIS_CREDENTIAL_CACHE_DIR` env로 캐시 디렉토리 오버라이드를 지원 → 계좌별 distinct dir로 분리. **코드 자동분리는 보류**: 호출자 `app/auth/token.py`가 file-guard 보호로 편집 불가 + `issue_access_token_for`의 의도적 env-only(account 비의존) 캐싱 계약(`test_auth_token`)이 있어, settings 내부 자동 account 스코프는 그 계약을 깨고 trading-critical 리스크. 운영자 env 분리가 안전.

## 5. KIS Rate-Limit Bucket 미확정 문제
가장 큰 기술적 불확실성 요소이다.
- KIS 모의투자 API의 Rate Limit Bucket이 **AppKey 기준**인지, **계좌 기준**인지, 아니면 **IP 기준**인지 공식 문서로 확정할 수 없다.
- 만약 IP 기준이라면 여러 프로세스를 띄워도 전체 초당 요청 합산 제한에 걸리게 되며, 이 경우 공유 Throttle 설계가 추가로 필요하다.

## 6. 권장 Architecture

### 안 A: 여러 OS 프로세스 + 계좌별 Env/Profile (추천)
- 각 계좌를 별도의 `run_session.sh` 프로세스로 실행.
- 장점: 구현이 매우 단순하며(`*_OVERRIDE` 변수 활용), 특정 계좌/프로세스만 종료/롤백하기 쉽다. `Settings` 싱글톤이나 전역 변수 리팩토링 부담이 없다.

### 안 B: 단일 app.main Multi-Account Scheduler
- 하나의 프로세스 내에서 여러 계좌 컨텍스트를 비동기로 관리.
- 장점: API Budget을 프로세스 내에서 통합 관리하기 쉽다.
- 단점: 현재 코드베이스의 `Settings` 및 `Throttle` 전역 상태 구조를 대폭 리팩토링해야 하므로 리스크가 크다.

**결론: 안정성과 구현 효율을 위해 안 A를 우선 추진한다.**

### 안 A-1: 단일 세션 내 BUY Quote Lane 분리 (2026-06-27 적용)
- 목적: 현재 운영 세션은 `mock` 계좌를 포트폴리오/SELL/주문 실행 lane으로 유지하면서, BUY scan의 현재가 조회만 더 빠른 `live` AppKey/토큰을 사용한다.
- 설정: `BUY_SCAN_QUOTE_KIS_ENV=live`이면 BUY scan quote 호출이 live-scoped credential(`KIS_APP_LIVE_*`, `KIS_BASE_LIVE_URL`)만 사용한다. generic `KIS_APP_KEY` fallback은 허용하지 않아 mock 실행 credential이 live 조회 lane에 섞이지 않게 한다.
- 제한: 이것은 완전한 다중 프로세스 병렬 운영이 아니라 read-only quote lane 분리이다. KIS rate-limit bucket이 IP 기준이면 live/mock AppKey 분리만으로도 전체 제한을 공유할 수 있으므로 운영 관찰이 필요하다.
- 관찰: runtime state와 cycle snapshot의 `last_timing_summary.buy_scan_quote_account_mode` / `buy_scan_quote_account_env`로 실제 BUY 조회 lane을 확인한다.

## 7. 계좌별 필요한 분리 항목 Checklist
병렬 운영 시 다음 항목들이 독립적으로 관리되는지 확인해야 한다:
- [ ] Env 설정 파일 (`config/*.env`)
- [ ] AppKey / AppSecret
- [ ] 계좌번호 / 상품코드
- [ ] Token Cache 경로
- [ ] Runtime State 파일
- [ ] Order Log / Cycle Snapshots
- [ ] Candidate Outcome / Performance Report
- [ ] Slack Runtime Snapshot (계좌 식별자 포함 필요)
- [ ] Session Lock 파일
- [ ] Live Snapshot 파일

## 8. 태그 기반 Universe Routing 예시
`SymbolTagRegistry` API를 활용하여 유니버스를 분할한다.
- **Account A (Core)**: `universe:core` 태그 종목 담당
- **Account B (Extended)**: `universe:extended` 태그 종목 담당
- **라우팅 활용**: `asset:etf`, `risk:leveraged`, `risk:derivative` 태그를 특정 계좌에만 할당하거나 제외하는 방식으로 부하 분산.
- **필요 도구**: ✅ **DONE (2026-06-25, `2082635`).** `app/strategy/universe_routing.py` — 태그 쿼리(AND/OR + exclude)를 `SCAN_SYMBOLS` CSV로 변환. 순수 read-only(`SymbolTagRegistry`), 런타임 미배선, CLI: `python -m app.strategy.universe_routing --tags universe:core [--mode OR] [--exclude risk:leveraged] [--json]`.

## 9. Read-Only 실험 계획
구현 전 안전하게 검증하기 위한 실험 단계이다:
1. **AppKey A/B Token 발급**: 서로 다른 AppKey로 독립적인 토큰 발급이 가능한지 확인.
2. **단일 Quote 조회**: 각 계좌/AppKey로 현재가 조회를 수행하여 API 응답 확인.
3. **Token Cache 분리**: 각 AppKey의 토큰이 서로의 캐시를 침범하지 않는지 확인.
4. **Balance 조회**: 각 계좌의 잔고 데이터가 정확히 분리되어 조회되는지 확인.
5. **Rate-Limit 관찰**: EGW00201(Rate Limit) 발생 없이 최소 호출만 수행하며 상태 관찰.

## 10. 절대 하지 말아야 할 것
- ❌ 주문 API를 사용한 Bucket/제한 테스트 (안전 최우선)
- ❌ 대량 Quote 조회를 통한 부하 테스트 (EGW00201 유도 금지)
- ❌ State/경로 분리 완료 전 병렬 `app.main` 실행 (데이터 오염 위험)
- ❌ Live Snapshot 공유 상태에서 동시 쓰기 수행

## 11. 단계별 로드맵
- **Phase 0: 문서화 (현재)** - 설계 및 리스크 정리
- **Phase 1: 계좌별 경로 분리** - ✅ **메커니즘 완료 (2026-06-25).** Lock=signature 자동분리, Snapshot=`KIS_LIVE_SNAPSHOT_DIR`/`KIS_LIVE_SNAPSHOT_LOG_DIR` env, Token Cache=`KIS_CREDENTIAL_CACHE_DIR` env. 운영자가 계좌별 env(cache/snapshot dir)를 distinct로 설정하면 병렬 mock 안전. Ergonomics: `multi_account_env`(env 생성) + `multi_account_preflight`(격리 검증). 런타임 zero-config 자동분리는 §4.B 사유로 보류.
- **Phase 2: Read-Only Multi-Account Sanity** - 🔶 **툴링 완료 / 실행은 운영자 게이트 (2026-06-25, `2082635`/`9dfd2c1`).** offline 도구(`multi_account_env` env 생성 + `multi_account_preflight` 경로 격리 검증) + 운영자 runbook([multi_account_phase2_runbook.md](multi_account_phase2_runbook.md)). §9 실제 API sanity(token/quote/cache/balance/rate-limit)는 **운영자가 real KIS로 수행** — 에이전트 미실행.
- **Phase 3: Tag-to-SCAN_SYMBOLS Helper** - ✅ **DONE (2026-06-25, `2082635`).** `app/strategy/universe_routing.py`(§8 참조).
- **Phase 4: Paper/Mock 병렬 관찰** - 모의투자 환경에서 병렬 운영 안정성 테스트 (운영자 트랙; 계좌별 집계는 `app.dashboard.multi_account_overview` read-only CLI로 관찰 — discover→per-signature load→C-2 콘솔 요약)
- **Phase 5: 제한적 Live 운영** - 검증 완료 후 실전 계좌 도입 검토 (별도 게이트)

## 12. 권고 사항
**월요일 정규 세션(Regular Session) 전에는 어떠한 병렬 운영 관련 코드 구현이나 실행도 하지 않는다.** 현재의 단일 계좌 운영 안정성을 먼저 확인한 후, 장 종료 이후 또는 주중에 실험을 진행한다.
