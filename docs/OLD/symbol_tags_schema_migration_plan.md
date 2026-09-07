# symbol_tags schema migration plan

> **Status: HISTORICAL / mostly implemented.** This plan was written before the
> `asset` namespace migration. The current canonical reference is
> `docs/SYMBOL_TAGS.md` (not included in this source snapshot): `asset:stock` / `asset:etf` are already
> supported, ETF rows no longer need `market:etf`, and `risk:leveraged` /
> `risk:derivative` are accepted values. Keep this file as the design record and
> use it only for remaining follow-up policy questions such as ETF cap/theme
> semantics, `sector:etc` refinement, and future ETF-specific namespaces.

## 목적

`config/symbol_tags.yaml`의 태그 체계를 ETF 예외 처리 중심이 아니라 전체 종목/상품 메타데이터 schema 관점에서 재검토한다.

이번 계획의 v1 목표는 `market`과 `asset`의 의미를 분리하는 것이다. `market`은 장기적으로 상장시장, `asset`은 상품/자산 유형을 표현한다. 실제 config, validator, Slack, scanner, runtime 코드는 월요일 regular session 전에는 수정하지 않는다.

이 문서는 구현 전 의사결정과 migration 순서를 고정하기 위한 계획서이며, `symbol_tags.yaml`을 live trading decision의 source of truth로 전환하지 않는다.

## 작성 당시 태그 체계 요약

작성 당시 `config/symbol_tags.yaml`은 하나의 파일로 157개 종목/상품의 이름과 태그를 관리했다.

작성 당시 namespace는 `market`, `sector`, `theme`, `cap`, `liq`, `vol`, `price`, `universe`, `fit`, `risk`가 코드상 known namespace로 정의되어 있었다. 현재 schema는 `asset`까지 포함한다.

작성 당시 ETF 6개는 `market:etf`로 구분되어 있었다. 이 문제는 `asset:etf` 도입과 `market`의 상장시장 의미 정리로 해소됐다.

## 작성 당시 namespace/value 분포

2026-05-10 작성 당시 `config/symbol_tags.yaml` 기준 집계:

| 항목 | 분포 |
|------|------|
| 총 symbol 수 | 157 |
| market | kospi 132, kosdaq 19, etf 6 |
| universe | core 27, extended 130 |
| sector | etc 26, bio 13, semiconductor 11, chemical 9, food 9, finance 8, holding 8, shipbuilding 7, entertainment 7, auto 6, energy 6, battery 6, internet 5, defense 5, retail 5, securities 5, insurance 4, construction 4, game 4, telecom 3, cosmetic 3, steel 3 |
| theme | dividend 14, secondary_battery 8, low_pbr 7, ai 6, ev 6, defense_export 6, shipbuilding_cycle 5, cosmetic_export 5, entertainment_ip 4, hbm 3, bio_cdmo 3, nuclear 3, china_consumption 1, hydrogen 1, obesity 1 |
| cap | mid 74, large 45, small 22, mega 11 |
| fit | momentum 11, defensive 11, trend_following 4, mean_reversion 1 |
| risk | cycle_sensitive 19, theme_spike 8, commodity_sensitive 5, fx_sensitive 3, news_sensitive 3, earnings_sensitive 2 |
| liq/vol/price | 작성 당시 YAML 사용 없음 |

작성 당시 `market:etf` 종목:

| code | name | 작성 당시 주요 태그 |
|------|------|---------------|
| 069500 | KODEX 200 | market:etf, sector:etc, cap:mega, universe:extended, fit:defensive |
| 117700 | ARIRANG 200 | market:etf, sector:etc, universe:extended |
| 122630 | KODEX 레버리지 | market:etf, sector:etc, universe:extended |
| 157490 | TIGER 은행 | market:etf, sector:etc, universe:extended |
| 305540 | TIGER 2차전지테마 | market:etf, sector:etc, universe:extended |
| 305720 | KODEX 2차전지산업 | market:etf, sector:etc, universe:extended |

## 작성 당시 태그 사용처와 boundary

작성 당시 `symbol_tags`는 live trading decision의 직접 입력이라기보다 표시/검증/메타데이터 용도에 가까웠다. 이 boundary는 현재도 유지한다.

작성 당시 확인된 사용처:

| 위치 | 역할 |
|------|----------|
| `app/core/symbol_tags.py` | `config/symbol_tags.yaml` 로드, namespace/value 검증, 중복 제거, query API 제공 |
| `app/tools/validate_symbol_tags.py` | 총 태깅 종목 수, namespace별 태그 수, unknown namespace/value, invalid tag, scan universe와의 차이 출력 |
| `app/notifications/slack.py` | 주문 알림에서 종목명 fallback 및 `태그`/`주의` 표시용 요약 생성 |
| `tests/test_symbol_tags.py` | loader, validation, query API 동작 검증 |
| `tests/test_slack_notifier.py` | Slack 주문 알림에 태그와 risk가 표시되는지 검증 |
| `docs/SYMBOL_TAGS.md` | 태그 체계와 사용법 설명 |

확인된 비사용/주의 사항:

- `market:etf`로 실제 trading decision을 분기하는 코드는 작성 당시 확인되지 않았다.
- scanner/strategy live decision은 작성 당시 `symbol_tags.yaml`에 직접 의존하지 않는 것으로 확인됐다.
- 실제 운영 scan universe는 runtime configuration의 `SCAN_SYMBOLS`, `BUY_TARGET_SYMBOLS`, `KIS_TARGET_SYMBOL`에 의존할 수 있다.
- 따라서 `symbol_tags.yaml`은 아직 scan universe의 source of truth가 아니다.

## 문제점

### market:etf의 의미 혼재

`market:kospi`, `market:kosdaq`은 상장시장 의미다. 반면 `market:etf`는 상품 유형 의미다. 같은 namespace 안에 상장시장과 상품 유형이 섞이면 향후 validator, Slack 표시, reporting, postrun analysis에서 의미가 흐려진다.

v1에서는 이 혼재를 해결하기 위해 `asset` namespace를 추가한다.

### universe와 usage/role 혼동 가능성

이 문서의 v1 설계에서 `universe:core`, `universe:extended`는 프로젝트의 종목 관리 범위를 표현한다. 여기에 `reference`, `watch`, `tradable` 같은 값을 바로 추가하면 universe가 "분석 후보군", "표시 목적", "실거래 가능 여부"를 동시에 의미하게 된다.

v1에서는 기존 `core/extended` 중심 체계를 유지한다. `reference/watch/tradable`은 지금 `universe`에 넣지 않고, 필요하면 나중에 `usage` 또는 `role` namespace로 별도 검토한다.

### liq/vol/price 같은 동적 태그 후보

`liq`, `vol`, `price`는 작성 당시 recommended value가 있지만 YAML에서는 사용되지 않았다. 이 값들은 정적인 metadata라기보다 시장 상황과 기간에 따라 바뀌기 쉬운 동적 특성이므로, 수동 YAML 태그로 관리하면 오래된 정보가 남을 수 있다.

v1에서는 `liq/vol/price` 정리를 하지 않는다. 필요하면 runtime metric 또는 report 산출물로 분리하는 방향을 별도 검토한다.

### symbol_tags.yaml이 아직 scan source of truth가 아님

운영 scan universe는 runtime configuration의 `SCAN_SYMBOLS`, `BUY_TARGET_SYMBOLS`, `KIS_TARGET_SYMBOL` 또는 scanner 쪽 symbol map에 의해 결정될 수 있다. `symbol_tags.yaml`의 `universe` 값만 바꿔도 운영 scan 대상이 바뀌는 구조가 아니다.

따라서 v1 migration은 live behavior 변경이 아니라 metadata schema 정리로 다룬다.

## 최종 v1 schema 제안

v1의 핵심 변경은 `asset` namespace 추가다.

권장 namespace:

| Namespace | v1 정책 |
|-----------|---------|
| `asset` | 새로 추가. `asset:stock`, `asset:etf`를 우선 사용 |
| `market` | 장기적으로 상장시장 의미로 정리. `market:kospi`, `market:kosdaq`, `market:konex` |
| `sector` | 기존 유지 |
| `theme` | 기존 유지 |
| `cap` | 기존 유지 |
| `universe` | 기존 `core`, `extended`, `experimental`, `excluded` 유지 |
| `fit` | 기존 유지 |
| `risk` | 기존 유지. ETF 관련 최소 risk 값 추가 검토 |
| `liq`, `vol`, `price` | v1에서는 정리하지 않음 |

v1 recommended values 초안:

```text
asset:
  - stock
  - etf
  - etn    # 필요 시 추가
  - reit   # 필요 시 추가

market:
  - kospi
  - kosdaq
  - konex
  - etf    # deprecated compatibility 후보

risk:
  - existing values...
  - leveraged
  - inverse
  - derivative
```

`asset:etn`, `asset:reit`는 당장 추가하지 않아도 된다. 실제 종목이 들어오는 시점에 추가한다.

## v2로 미룰 항목

v1에서 하지 않을 것:

- `etf_kind` namespace 추가
- `etf_scope` namespace 추가
- `benchmark` namespace 추가
- `universe:reference`, `universe:watch`, `universe:tradable` 추가
- sector 대량 rename
- `liq`, `vol`, `price` 정리
- `symbol_tags.yaml`을 scan universe source of truth로 전환
- live trading decision에 태그 연결

v2 후보:

| 후보 | 검토 조건 |
|------|----------|
| `etf_kind:index/sector/theme/bond/commodity` | ETF 수가 늘고 reporting에서 분류가 필요해질 때 |
| `etf_scope:domestic/global` | 해외/국내 exposure 분석이 필요해질 때 |
| `benchmark:kospi200/...` | benchmark별 성과 분석이 필요해질 때 |
| `usage` 또는 `role` namespace | reference/watch/tradable 의미가 실제 workflow에서 분리되어 필요해질 때 |
| tag 기반 scan source of truth | config와 운영 scan universe를 통합할 준비가 되었을 때 |

## market:etf migration 선택지

### Option A: 단기 호환 유지

ETF 6개에 `asset:etf`만 추가하고 `market:etf`는 유지한다.

예시:

```yaml
"069500":
  name: "KODEX 200"
  tags:
    - market:etf
    - asset:etf
    - sector:etc
    - cap:mega
    - universe:extended
    - fit:defensive
```

장점:

- Slack 표시가 당장 `ETF`로 유지된다.
- validator recommended values와 기존 테스트 영향이 작다.
- config 변경의 blast radius가 작다.

단점:

- `market:etf`의 의미 혼재가 남는다.
- deprecated 값을 일정 기간 관리해야 한다.
- 추후 제거 migration이 한 번 더 필요하다.

### Option B: 즉시 market:kospi + asset:etf 전환

ETF 6개를 `market:kospi`와 `asset:etf` 형태로 바로 전환한다.

예시:

```yaml
"069500":
  name: "KODEX 200"
  tags:
    - market:kospi
    - asset:etf
    - sector:etc
    - cap:mega
    - universe:extended
    - fit:defensive
```

장점:

- `market` namespace가 즉시 상장시장 의미로 정리된다.
- schema 의미가 가장 깔끔하다.
- deprecated 값을 오래 유지하지 않아도 된다.

단점:

- Slack 표시가 `ETF`에서 `KOSPI` 중심으로 바뀔 수 있다.
- ETF임을 Slack/reporting에서 보여주려면 `asset` 표시 정책도 함께 확인해야 한다.
- validator/test 업데이트 범위가 Option A보다 넓을 수 있다.

### 현재 disposition

2026-05-11 월요일 regular session 전에는 실제 config 수정이 없었고, 이후 v1 migration은 구현됐다.

현재는 `docs/SYMBOL_TAGS.md` (not included in this source snapshot)를 기준으로 한다. 이 파일의 Option A/B 비교는 역사적 의사결정 기록이며, 새 작업에서 `market:etf`를 되살리지 않는다.

## universe 정책

v1에서는 기존 universe 체계를 유지한다.

허용 값:

- `universe:core`
- `universe:extended`
- `universe:experimental`
- `universe:excluded`

정책:

- `core`와 `extended`는 현재 프로젝트의 종목 관리 범위를 표현한다.
- `reference`, `watch`, `tradable`은 v1에서 추가하지 않는다.
- 실제 거래 가능 여부는 universe가 아니라 주문/scan config, 장 시간 체크, 계좌/예산/리스크 로직에서 다룬다.
- `symbol_tags.yaml`의 universe를 바꾸는 것만으로 운영 scan 대상이 바뀐다고 가정하지 않는다.

## ETF 정책

v1 ETF 정책:

- ETF는 `asset:etf`로 표현한다.
- ETF 세부 분류는 최소화한다.
- 레버리지/인버스/파생 성격처럼 리스크 커뮤니케이션에 필요한 최소 태그만 `risk`로 검토한다.
- `risk:leveraged`, `risk:inverse`, `risk:derivative` 추가를 검토한다.
- `etf_kind`, `etf_scope`, `benchmark`는 v2로 미룬다.

현재 disposition: `asset:etf`, `risk:leveraged`, `risk:derivative`는 도입됐다. ETF cap/theme/exposure와 추가 ETF-specific namespace는 여전히 정책 후보로 남는다.

작성 당시 레버리지 ETF 예시:

```yaml
"122630":
  name: "KODEX 레버리지"
  tags:
    - market:etf
    - asset:etf
    - sector:etc
    - universe:extended
    - risk:leveraged
    - risk:derivative
```

## 일반 주식 정책

v1 일반 주식 정책:

- 일반 주식에는 `asset:stock`을 추가한다.
- 기존 `market:kospi` 또는 `market:kosdaq`은 유지한다.
- 기존 `sector`, `theme`, `cap`, `universe`, `fit`, `risk`는 유지한다.
- sector/theme/cap 대량 rename은 v1에서 하지 않는다.
- 새 태그는 Slack/reporting/postrun analysis/validation부터 활용하고 live trading decision에는 직접 연결하지 않는다.

## 실제 구현 순서 (historical)

아래 순서는 작성 당시 implementation plan이다. 현재는 1, 2, 3, 4, 6, 7의 핵심 schema 변경이 완료된 상태로 취급한다.

1. `docs/SYMBOL_TAGS.md`를 새 schema 설명으로 업데이트한다.
2. `app/core/symbol_tags.py`에 `asset` namespace를 추가한다.
3. `risk` recommended values에 `leveraged`, `inverse`, `derivative` 추가를 검토한다.
4. `tests/test_symbol_tags.py`를 업데이트해 `asset` namespace와 unknown value 정책을 검증한다.
5. Slack 표시 정책을 확인한다. `asset`을 표시할지, ETF 표시를 어떻게 유지할지 결정한다.
6. `config/symbol_tags.yaml`에 `asset:stock` / `asset:etf` 태그를 추가한다.
7. ETF 6개에 대해 `market:etf` 유지 또는 `market:kospi` 전환 여부를 결정한다.
8. `python -m app.tools.validate_symbol_tags`를 실행한다.
9. 필요한 경우 `PYTHONPATH=. pytest tests/test_symbol_tags.py tests/test_slack_notifier.py`를 실행한다.

이번 historical plan에서는 위 순서를 실행하지 않았다. 현재는 이미 구현된 항목과 남은 정책 후보를 분리해서 다룬다.

## 위험한 설계와 피해야 할 것

피해야 할 설계:

- `market`에 상장시장과 상품 유형을 계속 섞는 것
- `universe`에 `reference/watch/tradable`을 섞어 운영 의미를 흐리는 것
- ETF 세부 분류를 v1에서 과하게 늘리는 것
- `liq`, `vol`, `price` 같은 동적 값을 수동 YAML의 장기 truth처럼 다루는 것
- `symbol_tags.yaml` 변경만으로 scan universe가 바뀐다고 가정하는 것
- 태그를 곧바로 live trading decision에 연결하는 것
- regular session 직전 config/validator/Slack/runtime을 함께 바꾸는 것
- ETF만 예외 처리하고 전체 schema 의미를 정리하지 않는 것

## 예시 YAML

### KOSPI stock

```yaml
"005930":
  name: "삼성전자"
  tags:
    - market:kospi
    - asset:stock
    - sector:semiconductor
    - theme:ai
    - theme:hbm
    - cap:mega
    - universe:core
    - fit:momentum
    - risk:cycle_sensitive
```

### KOSDAQ stock

```yaml
"196170":
  name: "알테오젠"
  tags:
    - market:kosdaq
    - asset:stock
    - sector:bio
    - theme:bio_cdmo
    - cap:large
    - universe:extended
    - fit:momentum
    - risk:news_sensitive
    - risk:theme_spike
```

### deprecated historical ETF compatibility example

```yaml
"069500":
  name: "KODEX 200"
  tags:
    - market:etf
    - asset:etf
    - sector:etc
    - cap:mega
    - universe:extended
    - fit:defensive
```

### ETF with market:kospi + asset:etf canonical form

```yaml
"069500":
  name: "KODEX 200"
  tags:
    - market:kospi
    - asset:etf
    - sector:etc
    - cap:mega
    - universe:extended
    - fit:defensive
```

### deprecated historical leveraged ETF compatibility example

```yaml
"122630":
  name: "KODEX 레버리지"
  tags:
    - market:etf
    - asset:etf
    - sector:etc
    - universe:extended
    - risk:leveraged
    - risk:derivative
```

## 실행 방법 요약

이 historical plan의 문서 작업은 문서 추가만 했다. 실행 또는 검증이 필요한 명령은 다음 read-only/문서 검증 범위에 한정했다.

```bash
git status --short
git diff --check
git diff --name-only
```

구현 이후 재검증에 사용할 후보 명령:

```bash
python -m app.tools.validate_symbol_tags
PYTHONPATH=. pytest tests/test_symbol_tags.py tests/test_slack_notifier.py
```
