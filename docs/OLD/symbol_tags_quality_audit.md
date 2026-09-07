# symbol_tags quality audit

> **Status: HISTORICAL / superseded audit snapshot.** This audit was written
> before the symbol-tags v1 migration. The current canonical reference is
> `docs/SYMBOL_TAGS.md` (not included in this source snapshot) plus `config/symbol_tags.yaml`.
> Current state: `asset:stock` / `asset:etf` are implemented, ETF rows use
> listing-market tags plus `asset:etf` instead of `market:etf`, `122630` has
> `risk:leveraged` and `risk:derivative`, and the previous
> `307950`/`057050` duplicate-name issue is resolved
> (`307950=현대오토에버`, `057050=현대홈쇼핑`). Treat the detailed tables below as
> dated audit evidence, not as the current defect list.

## 목적

작성 당시 `config/symbol_tags.yaml`의 태깅 품질을 read-only로 점검했다. 목표는 태그가 형식적으로만 존재하는지, Slack 표시/검증/reporting/postrun analysis 용도로 어느 정도 신뢰할 수 있는지 확인하는 것이었다.

이 감사는 config 수정 없이 수행됐다. 현재 상태 판단은 위 status block과 `docs/SYMBOL_TAGS.md` (not included in this source snapshot)를 우선한다.

## 기준과 범위

작성 당시 참조한 규칙:

- `docs/SYMBOL_TAGS.md`: `symbol_tags.yaml`은 종목명/태그 메타데이터 저장소이며 live 매매 판단의 필수 입력값이 아니다.
- `app/core/symbol_tags.py`: known namespace와 recommended value를 기준으로 invalid/unknown namespace/unknown value를 검증한다.
- `app/tools/validate_symbol_tags.py`: namespace별 태그 수, unknown namespace/value, invalid tag, scan universe와의 차이를 출력한다.
- `config/symbol_tags.yaml`: 157개 symbol의 name/tags를 보유한다.

이 감사는 태그 의미의 타당성 후보를 찾는 작업이다. 회사 업종, ETF 성격, cap 등은 자동 확정하지 않고 사람이 검토할 후보로만 기록한다.

## 작성 당시 태그 커버리지 요약

| 항목 | 값 |
|------|----|
| 전체 symbol 수 | 157 |
| market 태그 보유 | 157 / 157 |
| sector 태그 보유 | 157 / 157 |
| universe 태그 보유 | 157 / 157 |
| cap 태그 보유 | 152 / 157 |
| theme 태그 보유 | 73 tags |
| risk 태그 보유 | 40 tags |
| fit 태그 보유 | 27 tags |
| liq/vol/price 태그 | 작성 당시 YAML 사용 없음 |

작성 당시 형식 커버리지는 높았다. 모든 symbol에 `market`, `sector`, `universe`가 있고, validator 기준 invalid/unknown value도 없었다.

다만 품질 커버리지는 균일하지 않았다. 특히 당시 ETF rows의 `market:etf` 사용, `asset` namespace 부재, `057050`/`307950` duplicate-name 의심은 이후 v1 migration과 name correction으로 해결됐다.

## 작성 당시 namespace/value 분포

### namespace별 태그 수

| namespace | count |
|-----------|------:|
| market | 157 |
| sector | 157 |
| universe | 157 |
| cap | 152 |
| theme | 73 |
| risk | 40 |
| fit | 27 |
| liq | 0 |
| vol | 0 |
| price | 0 |

### market 분포 (historical)

| value | count |
|-------|------:|
| kospi | 132 |
| kosdaq | 19 |
| etf | 6 |

### sector 분포 (historical)

| value | count |
|-------|------:|
| etc | 26 |
| bio | 13 |
| semiconductor | 11 |
| chemical | 9 |
| food | 9 |
| finance | 8 |
| holding | 8 |
| shipbuilding | 7 |
| entertainment | 7 |
| auto | 6 |
| energy | 6 |
| battery | 6 |
| internet | 5 |
| defense | 5 |
| retail | 5 |
| securities | 5 |
| insurance | 4 |
| construction | 4 |
| game | 4 |
| telecom | 3 |
| cosmetic | 3 |
| steel | 3 |

### universe 분포 (historical)

| value | count |
|-------|------:|
| extended | 130 |
| core | 27 |

### cap 분포 (historical)

| value | count |
|-------|------:|
| mid | 74 |
| large | 45 |
| small | 22 |
| mega | 11 |

작성 당시 cap 누락은 5개이며 모두 ETF였다.

### theme 분포 (historical)

| value | count |
|-------|------:|
| dividend | 14 |
| secondary_battery | 8 |
| low_pbr | 7 |
| ai | 6 |
| ev | 6 |
| defense_export | 6 |
| shipbuilding_cycle | 5 |
| cosmetic_export | 5 |
| entertainment_ip | 4 |
| hbm | 3 |
| bio_cdmo | 3 |
| nuclear | 3 |
| china_consumption | 1 |
| hydrogen | 1 |
| obesity | 1 |

복수 theme 종목은 작성 당시 11개였다. 대부분 의도된 복합 테마처럼 보였으나, theme가 표시/분석에 사용될 경우 과도한 중복 의미가 없는지 사람이 한 번 확인하는 편이 좋다는 판단이었다.

### risk 분포 (historical)

| value | count |
|-------|------:|
| cycle_sensitive | 19 |
| theme_spike | 8 |
| commodity_sensitive | 5 |
| fx_sensitive | 3 |
| news_sensitive | 3 |
| earnings_sensitive | 2 |

복수 risk 종목은 작성 당시 3개였다.

### fit 분포 (historical)

| value | count |
|-------|------:|
| momentum | 11 |
| defensive | 11 |
| trend_following | 4 |
| mean_reversion | 1 |

fit 태그는 작성 당시 157개 symbol 중 27개 tag만 존재했다. 이 감사에서는 fit을 전체 universe의 일관된 전략 분류라기보다 core/관심 종목 일부의 보조 설명으로 보는 것이 안전하다고 판단했다.

## 작성 당시 validator 결과

실행 명령:

```bash
PYTHONPATH=. python3 -m app.tools.validate_symbol_tags
```

결과 요약:

| 항목 | 결과 |
|------|------|
| 총 태깅 종목 수 | 157 |
| unknown namespace 태그 | 0 |
| unknown value 태그 | 0 |
| invalid 태그 | 0 |
| 중복 제거 | 0 |
| 조회 universe 종목 수 | 36 |
| universe에 있으나 태그 없음 | 0 |
| 태그에 있으나 universe에 없음 | 121 |
| 최종 결과 | 정상 |

해석:

- 형식 validation은 통과했다.
- `태그에 있으나 universe에 없음: 121`은 작성 당시 `symbol_tags.yaml`이 157개 태그 메타데이터 파일이고, scanner 쪽 조회 universe가 36개로 별도 관리되는 구조라 발생했다.
- 따라서 이 결과만으로는 태그 품질이 충분하다고 판단할 수 없었다. validator는 schema 형식 검증에 가깝고, 업종/테마/리스크 의미 정확도까지 검증하지 않는다.

## 작성 당시 sector:etc 종목 목록

`sector:etc`는 작성 당시 26개였다. ETF 6개는 `asset` namespace가 아직 없어 `sector:etc`에 묶인 상태였고, 개별주 20개도 사람이 업종 재검토할 후보였다.

> Current disposition: ETF rows now use `asset:etf`, and the duplicated
> `057050`/`307950` name issue is resolved. The table below is retained as dated
> audit evidence; only individual-stock `sector:etc` refinement remains a live
> review candidate.

| code | name | tags |
|------|------|------|
| 307950 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended |
| 069500 | KODEX 200 | market:etf, sector:etc, cap:mega, universe:extended, fit:defensive |
| 000120 | CJ대한통운 | market:kospi, sector:etc, cap:mid, universe:extended |
| 003490 | 대한항공 | market:kospi, sector:etc, cap:large, universe:extended |
| 006260 | LS | market:kospi, sector:etc, cap:mid, universe:extended |
| 021240 | 코웨이 | market:kospi, sector:etc, cap:mid, universe:extended |
| 036830 | 솔브레인홀딩스 | market:kospi, sector:etc, cap:small, universe:extended |
| 047050 | 포스코인터내셔널 | market:kospi, sector:etc, cap:mid, universe:extended |
| 057050 | 현대오토에버 | market:kospi, sector:etc, cap:mid, universe:extended |
| 064400 | 투루윈 | market:kosdaq, sector:etc, cap:small, universe:extended |
| 086280 | 현대글로비스 | market:kospi, sector:etc, cap:large, universe:extended |
| 091160 | CJ씨푸드 | market:kospi, sector:etc, cap:small, universe:extended |
| 102110 | DB금융투자 | market:kospi, sector:etc, cap:small, universe:extended |
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended |
| 139260 | 미디어젠 | market:kosdaq, sector:etc, cap:small, universe:extended |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended |
| 192820 | COSMO신소재 | market:kosdaq, sector:etc, cap:small, universe:extended |
| 229200 | 아이센스 | market:kosdaq, sector:etc, cap:small, universe:extended |
| 233740 | 녹십자홀딩스 | market:kospi, sector:etc, cap:small, universe:extended |
| 240810 | 원익IPS소재 | market:kosdaq, sector:etc, cap:small, universe:extended |
| 241560 | 두산밥캣 | market:kospi, sector:etc, cap:mid, universe:extended |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended |
| 310970 | 고려B&H | market:kospi, sector:etc, cap:small, universe:extended |
| 357780 | 솔루스첨단소재 | market:kospi, sector:etc, cap:small, universe:extended |

작성 당시 주의 후보:

- `307950`과 `057050`이 모두 `현대오토에버`로 표시됐다. 이 이슈는 이후 `057050=현대홈쇼핑`, `307950=현대오토에버`로 해소됐다.
- `102110 DB금융투자`는 `sector:securities` 후보처럼 보였다.
- ETF 6개는 `asset:etf` 도입 후 `sector:etc` 유지 여부를 다시 판단해야 한다는 후보였다. 현재 schema migration 자체는 완료됐고, ETF exposure/theme/cap 정책은 별도 후보로 남는다.

## 작성 당시 cap 누락 종목 목록

cap 누락은 작성 당시 5개였으며 모두 ETF였다.

| code | name | tags |
|------|------|------|
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended |

해석:

- ETF에 cap을 넣을지 말지는 schema 정책 문제다.
- 단순히 cap을 채워 넣기보다 ETF에는 cap이 필수인지, optional인지 먼저 결정해야 한다. 이 정책 판단은 `asset:etf` 도입 이후에도 별도 follow-up으로 남는다.

## ETF 6개 작성 당시 태그 상태

| code | name | historical tags | audit note |
|------|------|--------------|------------|
| 069500 | KODEX 200 | market:etf, sector:etc, cap:mega, universe:extended, fit:defensive | 작성 당시 ETF 중 유일하게 cap/fit이 있었다. 현재는 ETF 표시 정책 후보 |
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended | 태그가 3개뿐이고 cap/fit/risk/theme 없음 |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended | 작성 당시 leveraged/derivative risk 후보가 아직 반영되지 않았던 row |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended | 은행 sector/theme ETF인데 ETF 분류 namespace 없음 |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended | 2차전지 theme ETF인데 theme 없음 |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended | 2차전지 industry ETF인데 theme 없음 |

작성 당시 품질 판단과 현재 disposition:

- 작성 당시 ETF 태그는 표시/분석용으로 충분히 신뢰하기 어렵다고 판단했다.
- 작성 당시 `market:etf`가 상품 유형 역할을 하고 있었고, ETF 세부 성격은 거의 표현되지 않았다.
- 현재는 `asset:etf`와 최소 risk schema가 도입됐다. 남은 이슈는 ETF cap/theme/exposure 표시 정책이다.

## core 종목 중 theme/risk/fit 누락

작성 당시 core 27개 중 14개는 `theme`, `risk`, `fit` 중 하나 이상이 비어 있었다.

| code | name | missing | historical tags |
|------|------|---------|--------------|
| 035420 | NAVER | risk | market:kospi, sector:internet, theme:ai, cap:mega, universe:core, fit:momentum |
| 035720 | 카카오 | theme, risk | market:kospi, sector:internet, cap:large, universe:core, fit:mean_reversion |
| 086790 | 하나금융지주 | risk | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:large, universe:core, fit:defensive |
| 028260 | 삼성물산 | risk, fit | market:kospi, sector:holding, theme:low_pbr, cap:large, universe:core |
| 066570 | LG전자 | theme, fit | market:kospi, sector:semiconductor, cap:large, universe:core, risk:fx_sensitive |
| 138040 | 메리츠금융지주 | risk | market:kospi, sector:finance, theme:dividend, cap:large, universe:core, fit:momentum |
| 000810 | 삼성화재 | risk | market:kospi, sector:insurance, theme:dividend, cap:large, universe:core, fit:defensive |
| 316140 | 우리금융지주 | risk | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:large, universe:core, fit:defensive |
| 024110 | 기업은행 | risk | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:mid, universe:core, fit:defensive |
| 402340 | SK스퀘어 | risk, fit | market:kospi, sector:holding, theme:low_pbr, cap:large, universe:core |
| 051910 | LG화학 | fit | market:kospi, sector:chemical, theme:secondary_battery, cap:large, universe:core, risk:cycle_sensitive |
| 032830 | 삼성생명 | risk | market:kospi, sector:insurance, theme:dividend, cap:large, universe:core, fit:defensive |
| 017670 | SK텔레콤 | risk | market:kospi, sector:telecom, theme:ai, theme:dividend, cap:large, universe:core, fit:defensive |
| 030200 | KT | risk | market:kospi, sector:telecom, theme:dividend, cap:large, universe:core, fit:defensive |

해석:

- core 종목의 기본 태그는 충분하지만, theme/risk/fit은 일부만 정성적으로 달려 있다는 작성 당시 판단이었다.
- 특히 `fit`은 core 전체에 균일하게 부여된 값이 아니므로 전략 적합성 분석에 직접 쓰기에는 이르다.
- `risk`가 없는 종목이 반드시 안전하다는 뜻은 아니다. 이 감사에서는 risk를 "알려진 위험이 태깅된 종목" 정도로 해석해야 한다고 판단했다.

## 복수 theme 종목

| code | name | themes |
|------|------|--------|
| 005930 | 삼성전자 | ai, hbm |
| 000660 | SK하이닉스 | ai, hbm |
| 105560 | KB금융 | low_pbr, dividend |
| 055550 | 신한지주 | low_pbr, dividend |
| 086790 | 하나금융지주 | low_pbr, dividend |
| 316140 | 우리금융지주 | low_pbr, dividend |
| 024110 | 기업은행 | low_pbr, dividend |
| 017670 | SK텔레콤 | ai, dividend |
| 373220 | LG에너지솔루션 | secondary_battery, ev |
| 006400 | 삼성SDI | secondary_battery, ev |
| 042700 | 한미반도체 | ai, hbm |

복수 theme 자체는 문제가 아니다. 다만 Slack/reporting에서 상위 2개 theme만 표시되므로 theme 순서가 의미를 가지는지 정책화할 필요가 있다.

## 복수 risk 종목

| code | name | risks |
|------|------|-------|
| 196170 | 알테오젠 | news_sensitive, theme_spike |
| 247540 | 에코프로비엠 | cycle_sensitive, theme_spike |
| 028300 | HLB | news_sensitive, theme_spike |

복수 risk는 적절해 보이나, risk 값이 없는 종목과 비교 가능한 방식으로 전체 universe에 균일하게 적용된 것은 아니다.

## 태그가 너무 적거나 많은 종목

### 태그 3개 이하

| count | code | name | tags |
|------:|------|------|------|
| 3 | 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended |
| 3 | 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended |
| 3 | 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended |
| 3 | 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended |
| 3 | 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended |

모두 ETF였다. 작성 당시 ETF tagging은 최소 식별 수준에 가까웠다.

### 태그 8개 이상

| count | code | name | tags |
|------:|------|------|------|
| 8 | 196170 | 알테오젠 | market:kosdaq, sector:bio, theme:bio_cdmo, cap:large, universe:extended, fit:momentum, risk:news_sensitive, risk:theme_spike |
| 8 | 105560 | KB금융 | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:mega, universe:core, fit:defensive, risk:earnings_sensitive |
| 8 | 055550 | 신한지주 | market:kospi, sector:finance, theme:low_pbr, theme:dividend, cap:large, universe:core, fit:defensive, risk:earnings_sensitive |
| 8 | 005930 | 삼성전자 | market:kospi, sector:semiconductor, theme:ai, theme:hbm, cap:mega, universe:core, fit:momentum, risk:cycle_sensitive |
| 8 | 000660 | SK하이닉스 | market:kospi, sector:semiconductor, theme:ai, theme:hbm, cap:mega, universe:core, fit:momentum, risk:cycle_sensitive |

태그가 많은 종목들은 대체로 core 또는 관심도가 높은 종목이었다. 문제라기보다 전체 universe와 비교해 태깅 깊이가 불균일하다는 작성 당시 신호였다.

## 작성 당시 사람이 검토해야 할 후보 목록

작성 당시 우선 검토 후보:

> Current disposition: ETF schema, `122630` leveraged/derivative risk values,
> and `057050`/`307950` duplicate-name concerns are resolved. Keep this list as
> historical context; current follow-ups are ETF cap/theme/exposure policy,
> individual-stock `sector:etc` refinement, and `theme`/`risk`/`fit` meaning
> policy.

| 후보 | 이유 |
|------|------|
| ETF 6개 전체 | 작성 당시 `market:etf` 의미 혼재, `asset:etf` 부재, ETF별 성격/위험 태그 부족 |
| 122630 KODEX 레버리지 | 작성 당시 leveraged/derivative risk 후보가 아직 반영되지 않았음 |
| 305540 TIGER 2차전지테마, 305720 KODEX 2차전지산업 | ETF명상 2차전지 theme 노출이 있으나 theme 태그 없음 |
| 157490 TIGER 은행 | 은행 ETF지만 sector/theme 정책 미정 |
| sector:etc 개별주 20개 | 업종 분류가 분석/표시용으로 충분하지 않을 수 있음 |
| 307950/057050 현대오토에버 | 작성 당시 duplicate-name 후보. 이후 name correction으로 해소 |
| 102110 DB금융투자 | `sector:etc`가 적절한지 검토 필요 |
| 192820 COSMO신소재, 357780 솔루스첨단소재 | 소재/배터리/화학 쪽 분류 검토 후보 |
| 036830 솔브레인홀딩스, 233740 녹십자홀딩스 | holding/company-specific 분류 검토 후보 |
| core 14개 theme/risk/fit 누락 종목 | core 태그 품질을 균일하게 만들지 여부 결정 필요 |

주의:

- 위 목록은 자동 수정 목록이 아니다.
- 회사명만 보고 업종/테마를 확정하면 오류가 날 수 있다.
- 특히 ETF, 지주사, 복합기업, 금융사는 단일 sector로 압축하기 어렵다.

## 자동 수정하면 안 되는 항목

자동 수정 금지 후보:

- `sector:etc`를 회사명 키워드만 보고 일괄 치환
- ETF에 일반 주식과 동일한 `sector`, `cap`, `fit` 규칙을 강제 적용
- `risk`가 없는 종목에 임의로 risk를 대량 추가
- `theme`가 없는 종목에 유행 테마를 대량 추가
- `fit`을 live strategy decision과 연결
- `universe:core/extended`를 실제 scan universe source of truth로 간주
- cap 누락 ETF에 임의 cap을 채우는 작업
- 검증 없이 code/name 중복처럼 보이는 항목을 삭제하거나 변경

## 작성 당시 v1에서 우선 수정할 후보

작성 당시 v1 후보는 "의미 혼재를 줄이고, 표시/검증 품질을 올리되 live behavior를 바꾸지 않는 작업"으로 제한했다.

1. `asset` namespace 도입 후 일반 주식에 `asset:stock`, ETF에 `asset:etf` 추가
2. ETF 6개에 대한 `market:etf` 유지/전환 옵션 결정
3. `122630 KODEX 레버리지`에 `risk:leveraged`, `risk:derivative` 추가 여부 검토
4. ETF 5개의 cap 누락을 채울지, ETF에서는 cap optional로 둘지 정책 결정
5. `sector:etc` 개별주 20개를 사람이 검토해 확신 가능한 항목만 좁게 수정
6. core 14개 종목의 theme/risk/fit 누락을 일괄 보정할지, 비어 있음 자체를 허용할지 정책 결정
7. `307950/057050 현대오토에버` code/name 상태 확인

2026-06-07 disposition:

- 1, 2, 3, 7은 완료됐다. 현재 canonical schema는 `docs/SYMBOL_TAGS.md`와 `config/symbol_tags.yaml`이다.
- 4, 5, 6은 정책/품질 후보로 남는다. 자동 수정 목록이 아니다.

v1에서 하지 않을 것:

- 태그를 live trading decision에 연결
- scanner/strategy/runtime 동작 변경
- `symbol_tags.yaml`을 scan universe source of truth로 전환
- ETF 세부 namespace를 과도하게 추가
- session 직전 config 변경

## 작성 당시 최종 품질 판단

작성 당시 태그 파일은 형식적으로는 정상이었다. 모든 symbol에 핵심 최소 태그가 있고, validator도 통과했다.

하지만 분석/표시용 신뢰도는 "기본 메타데이터로는 사용 가능하나, 세밀한 업종/테마/리스크 분석의 source of truth로 쓰기에는 아직 부족"으로 판단했다.

작성 당시 특히 다음 세 가지가 품질 리스크였다.

- ETF가 `market:etf`와 `sector:etc`에 묶여 있어 상품 유형/상장시장/테마/위험이 분리되지 않는다.
- `sector:etc`가 26개로 많고, 일부 개별주는 더 구체적인 분류가 가능해 보인다.
- `theme`, `risk`, `fit`은 일부 관심 종목 중심으로만 달려 있어 전체 universe에 균일한 의미로 사용하기 어렵다.

2026-06-07 disposition:

- ETF의 `market`/`asset` 의미 분리, `122630` risk value, `057050`/`307950` name drift는 완료된 이슈다.
- 남은 품질 후보는 ETF cap/theme/exposure 정책, `sector:etc` 개별주 refinement, `theme`/`risk`/`fit` 의미 정책이다.
- 현재 상태 판단은 `docs/SYMBOL_TAGS.md` (not included in this source snapshot)와 `config/symbol_tags.yaml`을 우선한다.

## 실행 방법 요약

이번 감사에서 실행한 read-only/local 명령:

```bash
git status --short
git branch --show-current
PYTHONPATH=. python3 -m app.tools.validate_symbol_tags
git diff --check
```

이번 작업에서 하지 않은 것:

- config 수정 없음
- code/tests/runtime 수정 없음
- API 호출 없음
- `app.main` 실행 없음
- commit/push 없음
