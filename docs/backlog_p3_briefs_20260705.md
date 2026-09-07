# 백로그 P3 브리프 묶음 (E14, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E14** 산출물.
> 블루프린트 §16.1의 P3 백로그 중 **문서/브리프로 종결되는 항목**(F-13/F-14/F-15/F-17/F-18/F-22)을
> 한 곳에 모은다. **비가역 조치(파일 이동·삭제·untrack)는 전부 운영자 게이트** — 본 문서는 증거·
> 권고까지. 코드가 필요한 F-21(time_utils 수렴)·F-23(1천줄 모듈 상환)은 §7에 코드-트랙으로 이관.

---

## §1. F-13 — 데이터 보존·백업 정책 (운영 P2급이나 문서로 종결)

- **현황**(블루프린트 실측): data 14G / logs 1.7G / results 1.0G / archive 1.3G ≈ **18GB**.
  `retention_gzip` 도구는 있으나 **정책 문서·오프사이트 백업 없음**. data/ 분봉 원본은 유실 시
  재수집 고비용(US는 경로 B 수집 필요 — 더 큼).
- **권고 정책(초안 — 운영자 확정)**:
  1. **재수집 불가/고비용 계층**(data/ 분봉 원본, results/ 게이트 산출물) → 주 1회 오프사이트
     백업 대상. **재생성 가능 계층**(logs/, __pycache__, _workspace/ 중간산출) → 백업 제외.
  2. logs/ 는 `retention_gzip`으로 N일 후 압축, M일 후 삭제(N/M 운영자 확정).
  3. 백업 매체·주기·검증(복원 테스트)은 **운영자 절차**(에이전트 밖).
- **종결**: 정책 1장 = 본 절. 백업 실행/스케줄은 운영자. **F-14와 짝**(백업 없으면 DR 불가).

## §2. F-14 — DR(재해복구) 리허설 절차 (BP-15 S1 후속)

- **전제 충족**: BP-15 S1(`dee2dfa`)로 requirements-dev.txt/constraints.txt 신설 → **재클론 후
  테스트 기동 가능**해짐(F-02 해소). DR의 최상위 차단기가 제거됐으니 이제 절차화 가능.
- **DR 경로(초안 — 운영자 확정)**:
  1. 리포 재클론 → `.venv` 생성 → `pip install -r requirements-dev.txt -c constraints.txt` →
     `.venv/bin/python -m pytest -q`로 **기준선(3,147) 재현** = 코드 복구 확인.
  2. `.env`/토큰 캐시 **재발급**(비밀은 백업 대상 아님 — 재발급 경로 문서화가 핵심). KIS
     자격증명 재발급 절차는 운영자 보안 소관.
  3. data/ 복원은 F-13 백업에서. launchd 잡은 현재 정본 label인
     `com.kis-trader.session`으로 재등록한다. `com.kistrades.regularsession`는 legacy label이며
     현재 정규 세션 복구 대상으로 쓰지 않는다.
  4. 세션 재시작은 운영자 게이트:
     `launchctl kickstart -k gui/$(id -u)/com.kis-trader.session`.
- **종결**: 절차 1장 = 본 절 + runbook 편입(운영자). 실제 리허설 실행은 운영자.

## §3. F-15 — ML 트랙 소유권/상태 브리프

- **실측**: `scripts/run_ml_retraining.sh`(6.5KB, 최종 2026-06-10) + `scripts/run_research_loop.sh`
  (12KB) 존재. docs 등재처: `system_architecture_schema.md`·`tools_inventory.md`·
  `runbook_open_trading_api.md`에 참조 있음 — **관리 밖 자산 아님**(로드맵엔 미등재였을 뿐).
- **상태 판정**: 오프라인 ML baseline 재실험 파이프라인. tools_inventory에서 연결 도구
  (`run_ml_baseline_experiment`/`compare_ml_label_viability`/`ml_research_common` 등)가 **active/ops**
  분류 — 살아있는 리서치 자산. 런타임 전략과는 분리(오프라인).
- **종결**: 본 브리프가 소유권을 "오프라인 리서치 트랙(휴면 아님, 운영자 수동 실행)"으로 명문화.
  활성 여부는 운영자 확인 — 미사용이면 tools archive 브리프(BP-8) 대상에 편입 검토.

## §4. F-17 — 루트 산재 owner-gate 브리프

- **실측 루트 산재**: `main.py`(app.main shim), `run_full154_fetch.sh`, `research/`(런타임 산출물),
  `reports/`, `workspace/` vs `_workspace/`(이중성), `CODEX.md`.
- **처분 권고(운영자 결정)**:
  | 대상 | 성격 | 권고 |
  |------|------|------|
  | `main.py` | app.main shim(4줄) | **유지** — 훅이 실행 차단(BP-14 S1). 이동 시 훅 정규식 갱신 필요 |
  | `run_full154_fetch.sh` | full154 fetch ops | 유지(ops 참조 있음) — scripts/로 이동은 참조 갱신 동반 |
  | `research/` | 런타임 산출물 | 산출물 디렉토리 — `.gitignore` 확인 후 유지/정리 |
  | `reports/` | 산출물 | 동상 |
  | `workspace/` vs `_workspace/` | 이중성 | **혼선 위험** — `_workspace/`(하네스 중간산출)와 `workspace/`(F-18 대상) 역할 명문화 |
- **불변**: 이동/삭제는 전부 운영자. `-m app.tools.x` 경로·shim 실행 경계를 깨지 않도록 확인 선행.

## §5. F-18 — workspace/claude-design tar.gz untrack 브리프

- **실측**: `git ls-files workspace/` = **47파일 추적**. 그 중 바이너리·비소스:
  `kis-trader-design-bundle.tar.gz`, `kis-trader-v2/Dockerfile`, `docker-compose.yml`,
  `chats/chat*.md`(대화 로그), `_fig/**` figma 토큰(tokens.css/typography.css/inventory.json).
- **문제**: 바이너리 tar.gz + 설계 대화 로그가 git 이력에 박혀 **이력 오염**(현 pack 20.78MiB —
  급하지 않으나 커짐). 이건 트레이딩 봇 소스와 무관한 별도 설계 실험 산출물.
- **권고(운영자 결정)**: `workspace/claude-design/`를 `git rm --cached`로 **untrack + .gitignore
  추가**(파일은 디스크에 보존). tar.gz는 이력에서 완전 제거하려면 filter-repo가 필요하나 —
  **이력 재작성은 비가역·협업 파괴적**이라 별도 신중 결정(현재는 untrack만 권고).
- **종결**: 본 브리프 = 증거+권고. 실제 `git rm --cached`/이력 재작성은 운영자.

## §6. F-22 — 톱레벨 backtester/ 경계 문서화

- **실측**: `backtester/`(app/ 밖 레거시 패키지)를 **app+tests 30파일**이 import.
- **판정**: app/ 밖에 살아있는 코드 — 리팩토링 시 경계 누락 위험(R6/BP-4 결의 부채와 동류).
  **당장 이동 비권고** — 30 importer가 걸려 있어 이동은 대규모 diff(trading-critical 인접 이력
  오염). 
- **권고**: 경계를 문서로 고정 — "backtester/는 오프라인 backtest 엔진 레거시 패키지,
  런타임(app/main·pipeline) 경로 아님, app/ 리팩토링 시 이 30 import를 함께 스캔". `app/backtest/`
  수렴은 R6 이후 별건 판단(F-23과 같은 상환 큐).
- **종결**: 본 절이 경계 문서. 수렴 결정은 운영자/후속 리팩토링.

## §7. 코드-트랙 이관 (문서로 종결 안 됨 — 별건)

| 항목 | 왜 여기서 종결 못 하나 | 이관 |
|------|----------------------|------|
| F-21 naive-now `time_utils` 수렴 | tools/research 27곳 코드 수정 필요(실행 경로는 0건 ✓) | 소형 코드 슬라이스 — TDD로 별건(US 자산 착수 시 우선) |
| F-23 1천줄 모듈 5개 상환 | buy_flow 1,794 등 대규모 relocation | **R6(BP-4/E1) 이후** 순차. settings_fields는 분할보다 "설정 카탈로그 자동생성" 선행 |

## §8. 검증 (Green 재현)

- F-13~F-22 6항목 전부 실측 근거(du·git ls-files·grep importer 수) + 처분 권고 명기.
- 비가역 조치는 전부 운영자 게이트로 표기(honesty).
- F-21/F-23은 코드-트랙으로 정직하게 이관(문서 subset의 경계 명시) → E14 문서 subset Green 충족.
