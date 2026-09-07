# Post-main.py Split Condition Plan

## 1. 목적
`main.py`의 비대한 구조를 분리한 이후, 종목 태그 및 런타임 상태에 기반한 세밀한 필터링과 유니버스 라우팅(Universe Routing)을 구현하기 위한 계획을 정의한다.

## 2. 현재 상태 및 문제점
- **조건 부재**: 현재 `main.py`는 `SCAN_SYMBOLS` 리스트를 단순히 순회하며, 특정 종목(ETF, 레버리지 등)을 태그 기반으로 자동 제외하는 로직이 부족하다.
- **오케스트레이션 집중**: `main.py`에 직접 필터 조건을 넣으면 코드가 다시 복잡해지며 유지보수가 어려워진다.
- **런타임 미인식**: API 부하(Pressure)나 지연 시간(Latency)에 따라 스캔 범위를 동적으로 조절하는 정책이 없다.

## 3. 추천 모듈 구조
조건 판단 로직을 `main.py`에서 분리하여 다음 모듈로 구성한다.

- **`app/strategy/symbol_filters.py`**: 
    - 개별 종목이 현재 전략상 매수/스캔 대상인지 판단하는 순수 함수(Pure Function).
    - 예: `is_etf_excluded(symbol)`, `is_risk_tag_allowed(symbol)`.
- **`app/strategy/universe_routing.py`**: 
    - 계좌 프로필(Core/Extended)에 따라 어떤 종목군을 처리할지 결정.
    - 예: `get_target_universe(profile_name)`.
- **`app/strategy/runtime_policy.py`**: 
    - API Budget이나 지연 상태에 따라 스캔 강도를 조절.
    - 예: `should_reduce_scan_depth(api_pressure)`.

## 4. 추가 예정 조건 후보 (v1/v2)

### v1: 정적 태그 기반 필터 (Safety First)
- **Asset Filter**: `asset:etf` 종목 자동 매수 제외.
- **Risk Filter**: `risk:leveraged`, `risk:derivative` 종목 자동 매수 제외.
- **Universe Filter**: `universe:excluded` 태그 종목 스캔에서 완전히 배제.

### v2: 동적/프로필 기반 필터 (Optimization)
- **Priority Routing**: `universe:core` 종목은 매 사이클 스캔, `universe:extended`는 3사이클에 한 번 스캔.
- **API Pressure Control**: 
    - `EGW00201` 발생 시 또는 API 잔량이 20% 미만일 때 `extended` 스캔 일시 중단.
    - Cycle Latency가 5초 이상일 때 심층 분석(`deep_eval`) 대상 축소.
- **Multi-account Split**: 
    - 실전 계좌 프로필은 `universe:core`만 전담.
    - 모의 계좌 프로필은 `universe:extended`를 포함하여 광범위 스캔.

## 5. 구현 및 도입 순서 (Phase)

- **Phase 1: Read-only Preview**
    - 태그 필터를 적용했을 때 현재 `SCAN_SYMBOLS` 중 몇 개가 제외되는지 로그로만 출력.
    - 실제 매매 동작에는 영향을 주지 않음.
- **Phase 2: Scan-only Validation**
    - `scan_only` 모드 실행 시에만 태그 필터를 적용하여 탐색 결과 확인.
- **Phase 3: Buy-path Integration**
    - 실제 매수 후보 선정 단계에서 ETF 및 고위험군 종목을 필터링하도록 연결.
- **Phase 4: Runtime Pressure Policy**
    - API Budget 상태를 인자로 받아 스캔 범위를 조절하는 정책 함수 적용.
- **Phase 5: Multi-account Routing**
    - `account_signature`별로 서로 다른 유니버스 필터를 적용하는 설정(Profile) 도입.

## 6. 핵심 원칙
- **Separation of Concerns**: `main.py`는 오케스트레이션(흐름 제어)만 담당하며, "어떤 종목을 뺄 것인가?"는 `symbol_filters.py`가 담당한다.
- **Tag-driven**: 모든 필터 기준은 하드코딩하지 않고 `symbol_tags.yaml`의 태그를 기반으로 수행한다.
- **Fail-safe**: 필터 로직에서 에러 발생 시 보수적으로 "매수 금지" 처리하거나 전체 유니버스를 유지하는 안전 장치를 둔다.

## 7. 권고 사항
**월요일 정규 세션(Regular Session) 전에는 필터 로직을 실제 매수 경로에 반영하지 않는다.** Phase 1의 로그 출력(Preview)까지만 구현하여 정합성을 먼저 검증할 것을 권고한다.
