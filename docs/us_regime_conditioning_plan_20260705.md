# BP-1 경로 A — US 레짐 조건화 weight-search 계획서 (E3, 2026-07-05)

> **문서 지위**: master_completion_plan_20260705.md (not included in this source snapshot) **E3** /
> 블루프린트 §4. **경로 A 재설계** — [us_weight_search_plan_20260704.md](us_weight_search_plan_20260704.md)
> S0가 "네이티브 US 레코드 불가"로 RED였고, 운영자가 2026-07-04에 **경로 A(regime-conditioning)**
> 채택. 본 문서는 그 축으로 슬라이스를 재작성한다. 트랙: **research-only**(`/risk-assessment`
> 불요). 런타임 무변경(현행 US artifact 유지 — 교체는 운영자 게이트). 프로파일: 신규 모듈 TDD.

---

## §0. 축 (네이티브 대신 조건화)

네이티브 US 레코드는 분봉 미시구조가 없어 불가(S0 RED). 대신 **`app/research/us_regime/`
자산**(US 일봉→k-means 레짐 + `calendar_map`으로 US일↔KR일 매핑) 위에서 **기존 KR gate2
레코드를 US 레짐으로 조건화**해 탐색한다:

```
US 일봉 → 레짐 라벨(us_day→regime)   [기존: regimes.kmeans/choose_k]
US일 ↔ KR일 매핑                     [기존: calendar_map.to_frame]
target 레짐의 US일 → 조건부 KR일 집합  [기존: conditional_kr_days / 본 계획 S1이 guard 추가]
조건부 KR일의 레코드 로드 → weight search [기존: load_records_for_days + weight_search.walk_forward_search]
```

**핵심 재사용 앵커**(검증됨): `conditional_kr_days`(regimes.py:109), `load_records_for_days`(:162),
`records_from_frame`(:122), `weight_search.walk_forward_search`. S1은 이 위에 **홀드아웃 leak
방지 + 커버리지 guard**를 얹는 순수 선택기다(현행 `conditional_kr_days`엔 둘 다 없음).

## §1. 슬라이스 순서와 Red→Green

| 슬라이스 | 내용 | Red 지점 | Green 지점 |
|----------|------|----------|-----------|
| **S1**(본 항목) | 조건부 KR일 **선택기** `us_regime_conditioning.py` — 홀드아웃 제외 + 레짐 커버리지 + 최소일수 guard | 합성 fixture(us_day→regime, kr↔us 쌍, target, holdout) 테스트가 모듈 부재로 실패 | 손계산 선택 결과 일치 + **홀드아웃 US일 제외 단언** green + 빈 레짐/최소일수 degrade-safe green |
| S2 | pandas frame 브리지 (calendar_map frame → S1 입력) + `load_records_for_days` 연결 | frame→pairs 변환 테스트 실패 | 실 frame에서 S1 선택기 동일 결과 green |
| S3 | CLI + bounded I/O + artifact JSON (`ScoreV2Artifact` 왕복, `allow_nan=False`) | CLI 부재 import 실패 | artifact 왕복 green + inf/NaN 거부 green |
| S4 | 홀드아웃 e2e + OOS 리포트 | leak 테스트(홀드아웃 KR일이 train에 진입)가 배선 전 실패 | 합성 e2e green + leak 0 green |

## §2. S1 설계 (순수 선택기)

- **모듈**: `app/research/gate2/us_regime_conditioning.py`.
  `select_conditioned_kr_days(regime_by_us_day, kr_us_pairs, *, target_regime, holdout_us_days=(),
  min_kr_days=0) -> ConditionedSelection`.
- **입력**: `regime_by_us_day`(Mapping us_day→regime), `kr_us_pairs`(Iterable (kr_day, us_day)),
  `target_regime`(str), `holdout_us_days`(제외할 US일 — **leak 방지**), `min_kr_days`(커버리지 하한).
- **출력** `ConditionedSelection`: `selected_kr_days`(정렬 tuple), `regime_coverage`(regime→us_day
  수), `excluded_holdout_count`, `meets_minimum`(bool).
- **leak 방지 핵심**: `holdout_us_days`에 속한 US일에 매핑된 KR일은 **선택에서 제외**(train
  오염 차단). 이게 블루프린트 §4 RED③/S4 leak 게이트의 조기 구현.
- **degrade-safe**: 빈 레짐(target에 US일 0) → 빈 선택 + meets_minimum=False. 불량 쌍(non-tuple,
  결측)은 스킵(raise 금지). 미지 us_day(regime 맵에 없음)는 무레짐으로 스킵.

## §3. 게이트 판정

- **GREEN(S1~S4)**: 전부 Green + 위임 검증 ④⑤ PASS + 위생 grep(런타임 모듈이 이 research 코드
  import 0건).
- **GREEN(실행→artifact 후보)**: S4 OOS에서 조건화가 rank-neutral 아님 + 홀드아웃 성과 비붕괴.
  **실데이터 실행은 O7-2(US CSV 적재) 의존** — S1~S4는 합성으로 무의존 완주.
- **RED**: ① 홀드아웃 US↔KR 대응 불가 → 보고(임의 진행 금지). ② artifact inf/NaN →
  `allow_nan=False` 위반. ③ 런타임이 research 코드 import(경계 위반).

## §4. 위임·검증 (§3 인스턴스)

①불요 → ②본 계획서 → ③S1 순수함수 test-first(상위/opus), S2~S4 opus →
④`completion_audit.py --plan docs/us_regime_conditioning_plan_20260705.md --base <SHA>
--hygiene app/research/gate2/us_regime_conditioning.py --run-suite` → ⑤3-스켑틱
math(선택 산술)/robustness(불량 쌍·빈 레짐)/completeness(홀드아웃 제외가 실제로 leak을 막나).
