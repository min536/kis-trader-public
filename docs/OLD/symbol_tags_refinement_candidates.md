# symbol_tags refinement candidates

> **Status: DATED AUDIT SNAPSHOT.** This file preserves the human-review backlog
> from the pre-`asset` migration audit. The current canonical schema is
> `docs/SYMBOL_TAGS.md` (not included in this source snapshot). Current config already has
> `asset:stock` / `asset:etf`, accepts `risk:leveraged` and `risk:derivative`,
> and has `057050 = 현대홈쇼핑` while `307950 = 현대오토에버`; therefore rows below
> that mention `market:etf`, missing `asset`, or duplicated `057050/307950`
> names are historical evidence, not direct current TODOs.

## 목적

`docs/symbol_tags_quality_audit.md` 결과를 바탕으로, 실제 `config/symbol_tags.yaml` 수정 전에 사람이 검토해야 할 refinement 후보를 정리한다.

이번 문서는 후보 목록과 판단 기준을 남기는 작업이다. `config/symbol_tags.yaml`, validator, Slack, tests, scanner, strategy, runtime 코드는 수정하지 않는다.

## audit 기반 요약

작성 당시 상태와 현재 disposition:

- validator는 정상 통과한다.
- 전체 symbol 수는 157개다.
- `market`, `sector`, `universe`는 157/157 커버된다.
- cap 누락 5개는 모두 ETF다.
- ETF 6개가 `market:etf` + `sector:etc` 중심이던 문제는 `asset:etf` 도입으로 schema 차원에서는 해소됐다. ETF cap/theme/exposure 정책은 여전히 사람이 검토할 후보이다.
- `122630 KODEX 레버리지`의 `risk:leveraged`, `risk:derivative` 후보는 schema 값으로 수용된 상태다.
- `sector:etc` 26개 중 ETF 6개 외 개별주 20개는 사람이 검토할 후보다.
- core 27개 중 14개는 `theme`, `risk`, `fit` 중 하나 이상이 누락되어 있다.
- `307950`/`057050` 중복명 의심은 최신 config에서 해소됐다(`307950=현대오토에버`, `057050=현대홈쇼핑`). 외부 마스터 교차검증은 별도 품질 작업으로 남는다.
- `fit`, `risk`, `theme`는 전체 universe에 균일하게 부여된 태그로 보기 어렵다.

## 우선순위

| Priority | Scope | Why |
|----------|-------|-----|
| P0 | 현재 code/name 교차검증 | 중복명 이슈는 해소됐지만, repo 내부 name source가 36개라 vendor master 기반 검증은 계속 필요하다. |
| P1 | ETF 6개 exposure/cap/theme 정책 | `asset:etf`는 도입됐지만 ETF 표시/분석 품질은 아직 얕다. |
| P1 | leveraged/derivative risk 표시 검증 | 값은 수용됐으므로 Slack/reporting 표시가 의도대로 보이는지 확인한다. |
| P2 | sector:etc 개별주 20개 | 표시/분석 품질을 올릴 수 있으나 자동 확정은 위험하다. |
| P2 | core 14개 theme/risk/fit 누락 | core metadata 균일성 개선 후보지만 정책 결정이 먼저 필요하다. |
| P3 | ETF cap/theme 정책 | ETF에 일반 주식식 cap/theme를 붙일지 schema 정책이 필요하다. |

## 후보 분류 기준

| Group | Meaning | Default action |
|-------|---------|----------------|
| A. 오류 의심 | code/name 불일치, 중복 name, 태그가 지나치게 부족한 항목 | 실제 config 수정 전 원천 데이터 확인 |
| B. 비교적 확실한 수정 후보 | 현재 namespace 안에서 개선 방향이 비교적 선명한 항목 | 작은 단위로 수정 가능하나 검증 후 적용 |
| C. 사람이 판단해야 하는 후보 | 업종/테마/리스크 해석 또는 schema 정책 결정이 필요한 항목 | 자동 수정 금지 |

Confidence 의미:

- `high`: 현재 파일과 상품명만 봐도 후보 방향이 강하다. 그래도 config 수정 전 확인은 필요하다.
- `medium`: 후보 가능성은 높지만 업종/상품 성격 확인이 필요하다.
- `low`: 현 상태에서 문제 후보로만 기록하고, 외부/원천 확인 없이는 수정하지 않는다.

## A. 오류 의심 후보

> Historical note: the `057050` / `307950` duplicate-name rows below were resolved
> after this audit snapshot. Do not use this table to re-open that exact issue
> unless current `config/symbol_tags.yaml` regresses.

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| 307950 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended | code/name 매핑 불일치 의심 | code 또는 name 확인 후 정정 여부 결정 | high | 작성 당시 `057050`도 같은 name을 사용하던 duplicate-name 후보였다. 이후 해소됨. | no | KRX/KIS 종목명, scanner symbol map, 실제 거래 가능 symbol 확인 |
| 057050 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended | code/name 매핑 불일치 의심 | code 또는 name 확인 후 정정 여부 결정 | high | 작성 당시 `307950`과 같은 name이던 duplicate-name 후보였다. 이후 해소됨. | no | KRX/KIS 종목명, scanner symbol map, 실제 거래 가능 symbol 확인 |
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended | 태그가 너무 적음 / ETF schema 부족 | `asset:etf` 도입 후보, ETF cap/theme 정책 결정 | high | 작성 당시 pre-asset migration 상태였고 3개 태그뿐이었다. | no | ETF 상장시장, cap optional 여부, benchmark/theme 정책 |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended | 태그가 너무 적음 / ETF risk 부족 | `asset:etf`, `risk:leveraged`, `risk:derivative` 후보 | high | 상품명상 레버리지 ETF다. | no | risk recommended values 추가 여부, ETF risk 표시 정책 |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended | 태그가 너무 적음 / ETF theme 또는 sector 노출 부족 | `asset:etf` 후보, 은행 exposure 표현 방식 결정 | medium | 은행 ETF로 보이나 v1에서는 etf_kind/theme 정책이 아직 미정이다. | no | ETF theme를 `theme`로 둘지, v2 ETF namespace로 미룰지 |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended | 태그가 너무 적음 / ETF theme 노출 부족 | `asset:etf` 후보, `theme:secondary_battery` 검토 | medium | 상품명상 2차전지 테마 ETF다. 다만 ETF theme 정책 필요 | no | ETF에 theme를 붙일지, v2 ETF 분류로 둘지 |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended | 태그가 너무 적음 / ETF theme 노출 부족 | `asset:etf` 후보, `theme:secondary_battery` 검토 | medium | 상품명상 2차전지 산업 ETF다. 다만 ETF theme 정책 필요 | no | ETF에 theme를 붙일지, v2 ETF 분류로 둘지 |
| 069500 | KODEX 200 | market:etf, sector:etc, cap:mega, universe:extended, fit:defensive | ETF인데 일반 주식 schema처럼 보임 | `asset:etf` 후보, cap/fit 유지 여부 결정 | medium | ETF 중 유일하게 cap/fit이 있어 ETF 태그 정책이 불균일하다. | no | ETF에 cap/fit을 허용할지, Slack 표시 영향 |

## B. 비교적 확실한 수정 후보

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended | 레버리지 ETF risk 누락 | `risk:leveraged`, `risk:derivative` 추가 후보 | high | migration plan의 v1 risk 후보와 직접 맞는다. | no | `risk` recommended values에 leveraged/derivative 추가 후 적용 |
| ETF 6개 | market:etf 종목 전체 | market:etf, sector:etc 중심 | asset namespace 부재 | `asset:etf` 추가 후보 | high | v1 schema 핵심이 asset namespace 도입이다. | no | Option A/B, validator, Slack 표시 정책 결정 |
| 일반 주식 151개 | market:kospi/kosdaq 개별주 | asset namespace 없음 | asset namespace 부재 | `asset:stock` 추가 후보 | high | v1 schema에서 일반 주식은 `asset:stock`으로 표현한다. | no | validator/test 업데이트 후 일괄 추가 |
| 102110 | DB금융투자 | market:kospi, sector:etc, cap:small, universe:extended | sector:etc 재검토 | `sector:securities` 후보 | high | 현재 recommended sector에 `securities`가 있고 회사명상 증권업 후보가 강하다. | no | 종목명/업종 확인 |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended | ETF theme 표현 후보 | `theme:secondary_battery` 후보 또는 v2 ETF namespace 대기 | medium | 이름상 2차전지 노출이 명확하나 ETF theme 정책이 필요하다. | no | ETF에 theme 사용 여부 결정 |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended | ETF theme 표현 후보 | `theme:secondary_battery` 후보 또는 v2 ETF namespace 대기 | medium | 이름상 2차전지 노출이 명확하나 ETF theme 정책이 필요하다. | no | ETF에 theme 사용 여부 결정 |

주의: 이 그룹도 실제 config에 바로 자동 반영하지 않는다. validator/test/Slack 정책 변경과 함께 적용해야 한다.

## C. 사람이 판단해야 하는 후보

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| sector:etc 개별주 20개 | 여러 종목 | 아래 별도 표 참조 | 업종 분류 품질 | 확신 가능한 종목만 기존 sector로 이동 | medium | `sector:etc`가 많아 분석 품질이 낮아질 수 있다. | no | 회사 사업, KRX 업종, 기존 sector vocabulary 적합성 |
| core 14개 | 여러 종목 | 아래 별도 표 참조 | theme/risk/fit 균일성 | 비워둘지 보강할지 정책 결정 | medium | core인데 metadata 깊이가 불균일하다. | no | 태그 의미, Slack 표시, postrun analysis 사용 방식 |
| ETF 5개 cap 누락 | 117700, 122630, 157490, 305540, 305720 | cap 없음 | ETF cap 정책 | cap optional 또는 ETF 전용 기준 결정 | low | ETF에는 일반 주식식 시총 분류가 부적절할 수 있다. | no | ETF cap 정의 여부 |
| ETF theme 후보 | 157490, 305540, 305720 등 | theme 없음 | ETF exposure 표현 방식 | `theme` 사용 또는 v2 ETF namespace 대기 | medium | 상품명상 exposure는 보이나 schema 정책이 먼저다. | no | `theme`와 `etf_kind`/`benchmark` 경계 |
| fit 전체 | 27 tags only | fit이 일부 종목에만 있음 | 전략 적합성 태그 균일성 | fit을 selective metadata로 유지 또는 전체 core 보강 | low | fit은 live decision과 연결하면 위험하다. | no | fit의 의미, 사용처, Slack/reporting 요구 |
| risk 전체 | 40 tags only | risk가 없는 종목 의미 불명확 | risk 태그 의미 정책 | "known risk only"인지 "risk 없음"인지 문서화 | medium | risk 없음이 안전함을 뜻하지 않는다. | no | risk taxonomy와 표시 정책 |

## sector:etc 개별주 후보 표

ETF를 제외한 `sector:etc` 20개다. 아래 후보 수정은 "가능성"이며 자동 변경 목록이 아니다.

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| 307950 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended | code/name 확인 우선 | 매핑 확인 전 sector 수정 보류 | high | 작성 당시 duplicate-name 후보. 이후 해소됨 | no | code/name 정합성 |
| 000120 | CJ대한통운 | market:kospi, sector:etc, cap:mid, universe:extended | sector:etc | sector 후보 검토 | medium | 물류/운송은 현재 sector vocabulary에 직접 값이 없다. | no | `sector:etc` 유지 또는 sector vocabulary 확장 여부 |
| 003490 | 대한항공 | market:kospi, sector:etc, cap:large, universe:extended | sector:etc | sector 후보 검토 | medium | 항공/운송 sector가 현재 없다. | no | vocabulary 확장 여부 |
| 006260 | LS | market:kospi, sector:etc, cap:mid, universe:extended | 복합기업 | sector 후보 검토 | low | 지주/전선/전력 인프라 등 복합 성격 가능 | no | 주사업과 분류 기준 |
| 021240 | 코웨이 | market:kospi, sector:etc, cap:mid, universe:extended | sector:etc | sector 후보 검토 | medium | 렌탈/소비재 성격이나 현재 vocabulary가 애매하다. | no | consumer/retail 등 vocabulary 여부 |
| 036830 | 솔브레인홀딩스 | market:kospi, sector:etc, cap:small, universe:extended | holding/company-specific | `sector:holding` 후보 검토 | medium | 이름상 holding 성격 가능 | no | 실제 상장명/사업 확인 |
| 047050 | 포스코인터내셔널 | market:kospi, sector:etc, cap:mid, universe:extended | 복합/무역/에너지 | sector 후보 검토 | medium | trading/energy/holding 성격이 섞일 수 있다. | no | 주사업과 기존 taxonomy |
| 057050 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended | code/name 확인 우선 | 매핑 확인 전 sector 수정 보류 | high | 작성 당시 duplicate-name 후보. 이후 해소됨 | no | code/name 정합성 |
| 064400 | 투루윈 | market:kosdaq, sector:etc, cap:small, universe:extended | sector:etc | sector 후보 검토 | low | 이름만으로 확정 위험 | no | 업종/사업 확인 |
| 086280 | 현대글로비스 | market:kospi, sector:etc, cap:large, universe:extended | sector:etc | sector 후보 검토 | medium | 물류/운송 성격이나 현재 vocabulary가 없다. | no | vocabulary 확장 여부 |
| 091160 | CJ씨푸드 | market:kospi, sector:etc, cap:small, universe:extended | sector:etc | `sector:food` 후보 | medium | 이름상 식품 성격 후보 | no | 업종 확인 |
| 102110 | DB금융투자 | market:kospi, sector:etc, cap:small, universe:extended | sector:etc | `sector:securities` 후보 | high | 기존 recommended sector에 securities가 있다. | no | 종목명/업종 확인 |
| 139260 | 미디어젠 | market:kosdaq, sector:etc, cap:small, universe:extended | sector:etc | sector/theme 후보 검토 | low | 이름만으로 확정 위험 | no | 사업 확인 |
| 192820 | COSMO신소재 | market:kosdaq, sector:etc, cap:small, universe:extended | sector:etc | chemical/battery 후보 검토 | medium | 소재/2차전지 관련 후보지만 확인 필요 | no | 업종과 theme 확인 |
| 229200 | 아이센스 | market:kosdaq, sector:etc, cap:small, universe:extended | sector:etc | bio 후보 검토 | medium | 의료기기/헬스케어 성격 후보 | no | existing `bio` 범위에 포함할지 |
| 233740 | 녹십자홀딩스 | market:kospi, sector:etc, cap:small, universe:extended | holding/company-specific | holding 또는 bio 후보 검토 | medium | holding과 bio exposure 모두 가능 | no | 분류 기준 |
| 240810 | 원익IPS소재 | market:kosdaq, sector:etc, cap:small, universe:extended | sector:etc | semiconductor/chemical 후보 검토 | medium | 이름상 소재/반도체 후보지만 확인 필요 | no | 종목명/업종 확인 |
| 241560 | 두산밥캣 | market:kospi, sector:etc, cap:mid, universe:extended | sector:etc | construction/industrial 후보 검토 | medium | 건설장비 성격 후보, 현재 industrial sector 없음 | no | vocabulary 확장 여부 |
| 310970 | 고려B&H | market:kospi, sector:etc, cap:small, universe:extended | sector:etc | sector 후보 검토 | low | 이름만으로 확정 위험 | no | 종목 존재/업종 확인 |
| 357780 | 솔루스첨단소재 | market:kospi, sector:etc, cap:small, universe:extended | sector:etc | chemical/battery 후보 검토 | medium | 소재/전지 관련 후보지만 확인 필요 | no | 업종과 theme 확인 |

## ETF 6개 후보 표

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| 069500 | KODEX 200 | market:etf, sector:etc, cap:mega, universe:extended, fit:defensive | ETF schema 불균일 | `asset:etf` 추가, market 처리 Option A/B 결정 | high | ETF 6개 중 하나이며 v1 asset 도입 대상 | no | market:etf 유지 여부, cap/fit 유지 여부 |
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended | ETF schema 부족 | `asset:etf` 추가, cap optional 여부 결정 | high | ETF인데 3개 태그뿐 | no | 상장시장, benchmark/cap 정책 |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended | ETF risk 부족 | `asset:etf`, `risk:leveraged`, `risk:derivative` 후보 | high | 레버리지 상품 | no | risk values 추가, Slack 표시 |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended | ETF exposure 부족 | `asset:etf` 추가, 은행 exposure 표현 방식 결정 | medium | 이름상 은행 ETF | no | theme/sector/ETF namespace 정책 |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended | ETF theme 부족 | `asset:etf`, `theme:secondary_battery` 후보 또는 v2 대기 | medium | 이름상 2차전지 테마 | no | ETF theme 정책 |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended | ETF theme 부족 | `asset:etf`, `theme:secondary_battery` 후보 또는 v2 대기 | medium | 이름상 2차전지 산업 | no | ETF theme 정책 |

## core 누락 태그 후보 표

core 27개 중 `theme`, `risk`, `fit` 중 하나 이상 누락된 14개다. 이 표는 보강 후보를 찾기 위한 목록이지, 누락을 반드시 채우라는 의미가 아니다.

| code | name | 작성 당시 tags | 문제 유형 | 후보 수정 | confidence | 이유 | 자동 수정 가능 여부 | 필요한 추가 확인 |
|------|------|-----------|-----------|-----------|------------|------|----------------------|------------------|
| 035420 | NAVER | market:kospi, sector:internet, theme:ai, cap:mega, universe:core, fit:momentum | risk 없음 | risk 비워둘지 정책 결정 | low | risk 없음이 안전함을 의미하지 않도록 문서화 필요 | no | risk taxonomy |
| 035720 | 카카오 | market:kospi, sector:internet, cap:large, universe:core, fit:mean_reversion | theme/risk 없음 | theme/risk 보강 여부 결정 | low | 인터넷 대형주지만 theme 부여 기준 필요 | no | theme policy |
| 086790 | 하나금융지주 | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | 금융주 risk 일괄 정책 필요 | no | risk policy |
| 028260 | 삼성물산 | market:kospi, sector:holding, theme:low_pbr, cap:large, universe:core | risk/fit 없음 | fit/risk 보강 여부 결정 | low | holding/복합기업 성격 | no | fit 기준 |
| 066570 | LG전자 | market:kospi, sector:semiconductor, cap:large, universe:core, risk:fx_sensitive | theme/fit 없음 | sector/theme/fit 동시 재검토 | medium | `sector:semiconductor` 적절성도 사람이 확인할 만하다. | no | 업종 분류와 fit 기준 |
| 138040 | 메리츠금융지주 | market:kospi, sector:finance, theme:dividend, cap:large, universe:core, fit:momentum | risk 없음 | risk 비워둘지 정책 결정 | low | 금융주 risk 일괄 정책 필요 | no | risk policy |
| 000810 | 삼성화재 | market:kospi, sector:insurance, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | 보험 sector risk 정책 필요 | no | risk policy |
| 316140 | 우리금융지주 | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | 금융주 risk 일괄 정책 필요 | no | risk policy |
| 024110 | 기업은행 | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:mid, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | 금융주 risk 일괄 정책 필요 | no | risk policy |
| 402340 | SK스퀘어 | market:kospi, sector:holding, theme:low_pbr, cap:large, universe:core | risk/fit 없음 | fit/risk 보강 여부 결정 | low | holding/투자회사 성격 | no | fit 기준 |
| 051910 | LG화학 | market:kospi, sector:chemical, theme:secondary_battery, cap:large, universe:core, risk:cycle_sensitive | fit 없음 | fit 보강 여부 결정 | low | fit은 전체 core에 균일하지 않다. | no | fit policy |
| 032830 | 삼성생명 | market:kospi, sector:insurance, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | 보험 sector risk 정책 필요 | no | risk policy |
| 017670 | SK텔레콤 | market:kospi, sector:telecom, theme:ai, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | risk 없음 의미를 명확히 해야 한다. | no | risk policy |
| 030200 | KT | market:kospi, sector:telecom, theme:dividend, cap:large, universe:core, fit:defensive | risk 없음 | risk 비워둘지 정책 결정 | low | risk 없음 의미를 명확히 해야 한다. | no | risk policy |

## 자동 수정하면 안 되는 항목

자동 수정 금지:

- `sector:etc`를 회사명 키워드만 보고 일괄 치환
- ETF에 일반 주식과 동일한 `sector`, `cap`, `fit` 규칙 강제 적용
- `risk`가 없는 종목에 임의 risk 대량 추가
- `theme`가 없는 종목에 유행 테마 대량 추가
- `fit`을 live trading decision 또는 strategy filter에 연결
- `universe:core/extended`를 scan universe source of truth로 간주
- cap 누락 ETF에 임의 cap 추가
- `307950`/`057050` 같은 code/name 의심 항목을 확인 없이 삭제 또는 변경
- 월요일 regular session 전 config 수정

## 실제 config 수정 전 필요한 결정

1. ETF에는 `cap`을 optional로 둘지.
2. ETF의 exposure를 기존 `theme`에 넣을지, v2의 `etf_kind`/`benchmark`까지 기다릴지.
3. `fit`을 선택적 설명 태그로 유지할지, core 전체에 균일하게 보강할지.
4. `risk`가 없는 상태를 "위험 없음"이 아니라 "태깅된 known risk 없음"으로 문서화할지.
5. `sector:etc` 개별주를 기존 vocabulary로만 줄일지, 새 sector vocabulary가 필요한지.
6. vendor master snapshot으로 현재 code/name/market/asset 정합성을 주기 검증할지.

## 권장 다음 단계

1. `python -m app.tools.validate_symbol_tags`로 현재 schema 상태를 먼저 확인한다.
2. vendor snapshot이 준비되면 `python -m app.tools.validate_symbol_master`로 현재 code/name/market/asset 정합성을 확인한다.
3. ETF 6개는 schema보다 exposure/cap/theme 표시 정책을 먼저 정한다.
4. `sector:etc` 개별주는 high-confidence 항목부터 작은 배치로 검토한다.
5. core 누락 태그는 자동 보강하지 않고, `theme`/`risk`/`fit`의 의미 정책을 먼저 정한다.
6. 각 config 변경 후 `python -m app.tools.validate_symbol_tags`와 관련 tests를 실행한다.

## 실행 방법 요약

이번 작업은 문서 작성만 수행한다. 실제 config 수정 전 후보 검토용 명령:

```bash
git status --short
git branch --show-current
python -m app.tools.validate_symbol_tags
PYTHONPATH=. pytest tests/test_symbol_tags.py tests/test_slack_notifier.py
```

이번 작업에서 하지 않는 것:

- `config/symbol_tags.yaml` 수정
- `docs/SYMBOL_TAGS.md` 수정
- validator/Slack/tests/runtime/scanner/strategy 수정
- API 호출
- `app.main` 실행
- commit/push
