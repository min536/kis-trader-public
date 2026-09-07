---
Purpose: symbol tagging system의 역할과 검증 방법 요약
Read when: 종목 태그, Slack 태그 표시, tag validation 관련 작업 전
Do not use for: live 매매 판단 로직 변경
---

# Symbol Tags (TL;DR)

- `config/symbol_tags.yaml`은 종목명/태그 메타데이터 저장소입니다.
- 현재 live 매매 판단 로직의 필수 의존성이 아닙니다.
- Slack 알림, 표시, 검증, 분석 보조에 사용됩니다.

## 개요

`config/symbol_tags.yaml`은 조회 대상 종목에 대한 메타데이터 태그를 관리합니다.
태그는 현재 주로 표시, 검증, 로그/성과 분석 보조 메타데이터로 사용됩니다.
향후 전략 필터링 등에 활용될 여지는 있으나, 현재 **live 매매 판단의 필수 입력값이 아니며 의사결정에 직접 개입하지 않습니다**.

## 파일 구조

```yaml
symbols:
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
```

## 태그 네임스페이스

| Namespace | 설명 | 예시 값 |
|-----------|------|---------|
| `market`  | 상장시장 구분 | kospi, kosdaq, konex |
| `asset`   | 자산/상품 유형 | stock, etf |
| `sector`  | 업종 | semiconductor, bio, finance |
| `theme`   | 테마 | ai, hbm, dividend, low_pbr |
| `cap`     | 시가총액 규모 | mega, large, mid, small |
| `liq`     | 유동성 수준 | very_high, high, mid, low |
| `vol`     | 변동성 수준 | very_high, high, mid, low |
| `price`   | 주가 수준 | penny, low, mid, high |
| `universe`| 유니버스 분류 | core, extended, experimental |
| `fit`     | 전략 적합성 | momentum, mean_reversion, defensive |
| `risk`    | 리스크 요인 | cycle_sensitive, fx_sensitive, theme_spike, leveraged, derivative |

## 새 종목 추가

```yaml
  "123456":
    name: "새종목"
    tags:
      - market:kospi
      - asset:stock
      - sector:etc
      - universe:extended
```

확신이 없는 태그는 넣지 않아도 됩니다. 최소한 `sector:etc`, `universe:extended` 정도만 넣으면 충분합니다.

`market`은 상장시장 의미로만 사용합니다. ETF는 `market:etf`를 사용하지 않고, 상장시장 태그와 `asset:etf`를 함께 부여합니다.

## 검증 명령

```bash
python -m app.tools.validate_symbol_tags
python -m app.tools.validate_symbol_tags --path config/symbol_tags.yaml
```

출력 내용:
- 총 태깅 종목 수, namespace별 태그 수
- unknown namespace / invalid tag 목록
- 조회 universe와의 불일치 (있으면 경고)

## 프로그래밍 API

```python
from app.core.symbol_tags import load_symbol_tags

reg = load_symbol_tags()
reg.get_name("005930")                        # "삼성전자"
reg.get_tags("005930")                        # ("market:kospi", ...)
reg.has_tag("005930", "theme:ai")             # True
reg.get_tags_by_namespace("005930", "theme")  # ("theme:ai", "theme:hbm")
reg.symbols_with_tag("sector:semiconductor")  # ("005930", "000660")
```

## 주의사항

- 태그 파일이 없거나 파싱에 실패해도 프로그램은 정상 동작합니다 (빈 registry fallback).
- live 매매 판단 로직에 태그 기반 필터를 직접 적용하지 마세요.
- unknown namespace 태그는 경고만 출력하고 로드됩니다. namespace 없는 태그는 invalid 처리됩니다.
