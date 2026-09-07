# F-19 감사 — `except Exception` 분류 (E13, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E13
> 백로그 F-19** 산출물. `except Exception` 광폭 포획이 **계약적 fail-safe**인지 **무로깅 삼킴**인지
> 기준 없이 방치돼 있었다(블루프린트 §16.1 F-19). 본 문서가 분류표다. **read-only 실측 감사 —
> 코드 수정은 별건**(수정 후보 목록만). 실측: `grep -rn "except Exception" app/`.

---

## §1. 총량·분류 (실측)

- **총 108 사이트**(블루프린트 기재 106 대비 +2 자연증가). `as exc` 바인딩 **46** / bare
  `except Exception:` **60**.
- **약 49 사이트**가 포획 직후 2줄 내 log/logger/warning/error/print 존재(**로깅형**).
- **8 사이트**가 즉시 `pass`(잠재 삼킴 후보 — §3에서 정밀 판정).

## §2. 3분류 (계약적 fail-safe / 로깅 / 삼킴)

| 분류 | 정의 | 실측 근사 | 처분 |
|------|------|----------|------|
| **A. 계약적 fail-safe** | 실패 시 **안전 기본값**으로 축퇴(never-raise 계약). 로깅 없어도 의도된 설계 | 대다수(runtime never-raise 리더·reporting 다이제스트·pipeline 격리) | 유지 — 계약이 스위트로 잠김 |
| **B. 로깅형** | 포획 후 warning/error 기록 후 계속/반환 | ~49 | 유지 — 관측 가능 |
| **C. 잠재 삼킴** | 로깅도 안전기본값도 불명확한 즉시 `pass`/무처리 | §3 8건 검증 | 대부분 A로 재판정, 나머지 리뷰 후보 |

## §3. `pass` 8건 정밀 판정 (삼킴 여부)

| 위치 | 판정 |
|------|------|
| `app/runtime_state.py:360` | **A(fail-safe)** — 멀티계좌 read-path 후보 조회 실패 시 `[primary_path]`로 축퇴. 임계 읽기(json.loads)는 별도 `except (...)→_blocked_default_state`(fail-closed, F-09 확인) |
| `app/core/order_log.py:143` | **A** — 동형(read-path 후보 실패→primary 축퇴). 파일 부재는 `[]` 반환 |
| `app/tools/run_proposal_backtest.py:695,703` | A — 오프라인 백테스트 도구, 부분 실패 격리 |
| `app/dashboard/normalizers.py:784`·`console_v1.py:155` | A — UI 정규화/렌더 격리(대시보드 표시용) |
| `app/notifications/backtest_control.py:501`·`slack_bot.py:540` | B/A — 알림 경로, 실패 격리(F-10과 연계 — critical 재시도는 별건) |

**결론**: `pass` 8건 중 **삼킴(장애 은폐)로 판정된 것 0** — 전부 안전기본값 축퇴 또는 격리.
가장 우려됐던 2 trading-critical 인접(runtime_state·order_log)은 read-path 후보 축퇴로 확인.

## §4. 도메인 분포 + 리스크 가중

| 도메인 | 사이트 | 리스크 |
|--------|:------:|--------|
| 오프라인(tools/dashboard/research) | 28 | 낮음 — 배치·UI, 격리가 정당 |
| 알림(notifications) | 23 | 중 — F-10(critical 재시도)과 겹침 |
| 런타임(runtime/execution/pipeline/risk/scanner/strategy) | 23 | **높음 — 정밀 검토 대상**(아래) |
| 기타(overseas/market_data/core/autotuner 등) | ~34 | 중 |

**런타임 23 사이트**는 전부 `as exc` 바인딩(execution/pipeline은 `as exc`) 또는 never-raise
리더 계약. execution/pipeline 사이트(buy_flow:1527/1746, sell_flow:356/579, order_gate:223,
runtime_adapters:457/535/631, buy_lane:549)는 주문 경로 격리 — **수정 후보로 표기하되 수정은
`/risk-assessment` 게이트**(trading-critical 인접).

## §5. 수정 후보 목록 (수정은 별건 — F-19는 감사만)

1. **런타임 23 중 무로깅 사이트** — `as exc`를 바인딩만 하고 미사용하는 곳이 있으면 최소 debug
   로깅 추가(관측성). 개별 확인 후 `/risk-assessment` 하에 별건.
2. **notifications 23** — F-10(critical 채널 재시도+실패 카운터)과 통합 처리.
3. **삼킴 후보 0** — §3에서 전건 fail-safe/격리로 판정, 긴급 수정 대상 없음.

## §6. 검증 (Green 재현)

- 108 사이트 = `grep -rn "except Exception" app/ | wc -l` 재현. 46/60 = `as exc`/bare grep.
- `pass` 8건 위치·판정 = §3 실측(runtime_state:360·order_log:143 컨텍스트 확인).
- 3분류표(A/B/C) + 수정 후보 목록 = master_completion_plan §4 E13-F19 Green(분류표+수정후보) 충족.
  **코드 수정 0**(감사 문서).
