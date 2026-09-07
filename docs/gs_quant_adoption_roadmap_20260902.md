# GS Quant 선별 도입 로드맵

> 상태: ACTIVE — 2026-09-02 감사 기준. `지금 당장 가져올 것`의 1차 연결·검증 완료.
>
> 원본: [goldmansachs/gs-quant](https://github.com/goldmansachs/gs-quant)  
> 우리 포크: [example-user/gs-quant](https://github.com/example-user/gs-quant)  
> 감사한 upstream `master`: `ccbd4ae780f51be4e01ecbf834c7b93583fec57f`  
> 확인한 PyPI 릴리스: `gs-quant 2.1.7`

## 1. 결론

GS Quant 전체를 `kis-trader`에 설치하거나 임베드하지 않는다. GS Quant의 순수 계산식,
테스트 사례, 모듈 경계 중 우리 프로젝트에 맞는 부분만 하나씩 독립 구현한다. 모든 신규
기능은 백테스트·리서치에서 먼저 관찰하고, 별도 승격 결정 전에는 실시간 매수·매도 판단에
사용하지 않는다.

현재 `constraints.txt`는 NumPy `2.4.4`를 고정하지만 GS Quant 2.1.7은 NumPy `<2.4`를
요구한다. 프로젝트 제약을 적용한 설치는 실패했고, 제약을 빼면 NumPy를 `2.3.5`로 내리며
신규 패키지 29개를 설치한다. 따라서 직접 의존성 추가는 현재 기술적으로도 부적합하다.

## 2. 하나씩 가져오는 표준 절차

각 후보는 아래 절차를 순서대로 통과해야 한다. 뒤 단계를 앞당기지 않는다.

1. **포크 동기화**: 작업 시작 전에 upstream과 우리 포크의 기준 커밋을 맞춘다.
2. **출처 고정**: 참고한 upstream 커밋, 파일, 함수, 라이선스를 이 문서에 기록한다.
3. **분류**: 즉시·나중·고민·미도입 중 하나에만 배치한다.
4. **계약 추출**: 코드 복사 전에 입력, 출력, 결측치, 짧은 구간, 부호, 연율화 규칙을 명세한다.
5. **Red 테스트**: 우리 데이터 형태로 실패 테스트를 먼저 작성한다.
6. **독립 구현**: 기존 NumPy·Pandas 또는 표준 라이브러리만 사용해 최소 구현한다.
7. **기준 비교**: 고정 입력 벡터로 GS Quant 결과 및 수학적 기대값과 비교한다.
8. **리포트 전용 연결**: 백테스트 JSON/요약에만 노출한다. 주문·전략 입력에는 연결하지 않는다.
9. **승격 판단**: 데이터 누수, 비용, 결측, 성능, 재현성 검증 후 별도 문서와 리스크 검토를 거친다.

포크 동기화 명령은 다음 하나로 고정한다.

```bash
gh repo sync example-user/gs-quant --source goldmansachs/gs-quant
```

`kis-trader` 저장소에는 GS Quant remote, submodule 또는 vendored source tree를 추가하지 않는다.

## 3. 지금 당장 가져올 것

### 3.1 Drawdown 경로

**연결 상태: DONE (2026-09-02)**

| 항목 | 내용 |
|---|---|
| 참고 | `gs_quant/timeseries/econometrics.py::max_drawdown` |
| 우리 목적 | 최종 MDD 숫자뿐 아니라 각 거래일의 고점 대비 낙폭 경로를 보존 |
| 착지점 | `backtester/analytics/robust_risk.py` |
| 연결 | 백테스트 JSON `risk_diagnostics.drawdown_path` 및 요약 scalar |
| 게이트 | 빈 곡선, 상승 곡선, 복수 고점, 0 이하 값 처리 테스트 |
| 사용 제한 | 백테스트 보고 전용. 손절·포지션 사이징 입력 금지 |

GS Quant는 drawdown을 음수 비율로 표현한다. 우리 신규 경로도 `0`에서 시작해 손실 구간을
음수 퍼센트로 표현한다. 기존 `max_drawdown_pct`는 양수 손실 크기이므로 호환성을 유지한다.

### 3.2 Rolling annualized volatility

**연결 상태: DONE (2026-09-02)**

| 항목 | 내용 |
|---|---|
| 참고 | `gs_quant/timeseries/econometrics.py::volatility` |
| 우리 목적 | 백테스트 후반 한 점이 아니라 기간별 위험 변화 확인 |
| 착지점 | `backtester/analytics/robust_risk.py` |
| 기본 계약 | 단순 일간 수익률, 표본 표준편차(`ddof=1`), 252거래일 연율화, 퍼센트 출력 |
| 기본 창 | 수익률 20개 |
| 게이트 | 표본 부족은 `None`, 상수 수익률은 `0`, 잘못된 창은 명시적 오류 |
| 사용 제한 | 백테스트 보고 전용. 변동성 타기팅·주문 수량 입력 금지 |

### 3.3 Winsorized return 진단

**연결 상태: DONE (2026-09-02)**

| 항목 | 내용 |
|---|---|
| 참고 | `gs_quant/timeseries/statistics.py::winsorize` |
| 우리 목적 | 단일 비정상 수익률이 위험 통계를 얼마나 왜곡하는지 표시 |
| 착지점 | `backtester/analytics/robust_risk.py` |
| 기본 계약 | 전체 표본 평균 ± `2.5 ×` 표본 표준편차로 진단용 수익률 제한 |
| 출력 | 원본·제한값·클립 여부, 상하한, 클립 개수 |
| 게이트 | 양·음 극단값, 빈/단일 표본, 원본 불변성 |
| 사용 제한 | 원본 equity curve를 수정하지 않음. 거래 신호·성과 원장 대체 금지 |

### 3.4 고정 비교 벡터와 출처 기록

**연결 상태: DONE (2026-09-02)**

GS Quant를 테스트 의존성으로 설치하지 않는다. 대신 공개 수식과 독립적으로 계산 가능한
고정 입력을 회귀 테스트에 둔다. 각 구현 docstring에는 참고 프로젝트, 감사 커밋, 의미가
같지 않은 부분을 기록한다. 이 문서가 출처와 승격 상태의 정본이다.

## 4. 나중에 가져올 것

| 우선순위 | 후보 | 선행 조건 | 예상 착지점 | 가져오는 이유 |
|---:|---|---|---|---|
| 1 | Rolling OLS / rolling beta | KOSPI·KOSDAQ 등 정합한 벤치마크 시계열과 결측 정책 | `backtester/analytics/` | 시장 노출과 전략 고유 성과 분리 |
| 2 | 명시적 MissingDataStrategy | 데이터 공급자별 결측 유형 인벤토리 | `backtester/engine_backtest/data_provider.py` 인접 모듈 | fill/skip/fail 정책을 암묵 분기에서 계약으로 승격 |
| 3 | TransactionModel 확장 | 현행 수수료·세금·슬리피지 모델 감사 | `backtester/engine_backtest/portfolio.py` 인접 모듈 | 고정·비례·합성 비용 모델 비교 |
| 4 | CashAccrualModel | 장기 백테스트와 현금 이자 정책 확정 | 백테스트 전용 모듈 | 현금 비중이 큰 전략의 성과 왜곡 축소 |
| 5 | RSI·Bollinger·지표 parity test | 현행 EMA/MACD의 warm-up 계약 고정 | `app/math_models`가 아닌 우선 `backtester/analytics/` | 기존 지표 중복을 피하면서 엣지케이스 강화 |
| 6 | Event study / seasonality | 이벤트 라벨과 누수 없는 관측창 | research 전용 | 실적·정책 이벤트 전후 분석 |

이 분류의 항목은 아직 구현하지 않는다. 각 선행 조건이 충족될 때 한 항목씩 새 TDD 슬라이스로
승격한다.

## 5. 가져오는 것을 고민해볼 것

| 후보 | 기대 이익 | 핵심 우려 | 재검토 조건 |
|---|---|---|---|
| GS Quant 별도 분석 서비스 | 원본 함수와 즉시 비교 | 별도 NumPy 환경과 운영 복잡도 | 반복적인 대규모 parity 비교가 필요할 때 |
| Trigger/Action DSL | 전략 표현과 실행 분리 | 현행 실제 전략 함수 재사용 원칙과 이중 체계 위험 | 전략 수가 늘어 현재 설정 방식이 병목일 때 |
| RelativeDate 규칙 | 영업일 상대 날짜 표현 | GS holiday dataset은 인증 의존, 한국장 정합 불명 | 공식 휴장일 소스를 별도로 확보했을 때 |
| Factor risk attribution | 종목·섹터 위험 분해 | GS 모델 데이터와 권한 필요 | 독립 팩터 데이터셋 또는 정식 Marquee 권한 확보 시 |
| GS Quant MCP | 데이터 질의 자동화 | OAuth·쿠키·비밀 관리와 KIS 도구 체계 중복 | 기관용 Marquee 자격과 명확한 운영 목적 확보 시 |
| 파생상품 pricing/risk 구조 | 고급 위험 분석 | 현물 중심 KIS 모의투자 범위와 불일치 | 프로젝트 범위가 파생상품 연구로 공식 확장될 때 |

이 분류는 승인도 거절도 아니다. 조건이 생기기 전까지 코드·설정·비밀 경로에 연결하지 않는다.

## 6. 가져오지 않는 것

| 제외 대상 | 제외 이유 |
|---|---|
| `gs-quant` 전체 런타임 의존성 | NumPy 고정 충돌, 신규 의존성 29개, 불필요한 결합 |
| Git submodule·vendored 전체 소스 | 업데이트·보안·라이선스 감사 범위가 과도함 |
| Marquee `GsSession` 인증 경로 | 기관용 client ID/secret이 필요하며 KIS 인증과 무관 |
| GS Dataset·SecurityMaster를 KIS 시세 소스로 사용 | 국내 종목 식별자·원장·정합성 책임이 달라짐 |
| GS pricing/risk API를 주문 전 필수 경로로 사용 | 외부 장애가 국내 주식 주문 가능성을 좌우하게 됨 |
| GS backtest 엔진으로 현행 엔진 교체 | 실제 전략 코드 재사용·parity 목표를 잃고 엔진 괴리가 생김 |
| GS holiday dataset을 장 운영 게이트로 사용 | 인증 의존이며 KRX 공식 세션 근거가 아님 |
| MCP server를 봇 런타임에 내장 | 비밀·네트워크·권한 표면이 불필요하게 확대됨 |
| 자동 생성 `target/` 모델과 API 스키마 일괄 복사 | 사용하지 않는 대규모 타입 표면과 유지비 발생 |
| GS 결과를 검증 없이 live feature로 승격 | 백테스트 개선이 실제 주문 안전성을 증명하지 않음 |

## 7. 라이선스와 출처 규칙

GS Quant는 Apache License 2.0이며 `NOTICE`에 Goldman Sachs 저작권 고지가 있다.

- 원본 코드 또는 실질적 파생 코드를 복사하면 해당 파일에 원저작권·라이선스·변경 고지를 유지한다.
- 배포 대상에 복사 코드가 포함되면 Apache 2.0과 필요한 NOTICE 고지를 함께 제공한다.
- 일반적인 수학식을 독립 구현하더라도 참고한 upstream 파일과 커밋을 이 문서에 기록한다.
- Goldman Sachs 명칭이나 상표를 우리 기능의 보증·제휴 표현으로 사용하지 않는다.

## 8. 즉시 연결 범위와 완료 기준

이번 1차 연결은 다음 파일 범위만 허용한다.

```text
backtester/analytics/__init__.py
backtester/analytics/robust_risk.py
backtester/engine_backtest/metrics.py
backtester/engine_backtest/report.py
tests/test_robust_risk.py
tests/test_backtest_metrics.py
docs/gs_quant_adoption_roadmap_20260902.md
docs/README.md
```

완료 기준:

1. drawdown, rolling volatility, winsorized return 단위 테스트가 Red → Green을 거친다.
2. 기존 `max_drawdown_pct` 의미와 값이 변하지 않는다.
3. JSON report에 전체 `risk_diagnostics` 경로가 추가된다.
4. summary metrics에는 JSON/CSV에 안전한 scalar만 추가된다.
5. `.env`, 인증, 네트워크, KIS API, 실시간 전략·주문 모듈을 전혀 사용하지 않는다.
6. 집중 테스트와 관련 백테스트 회귀 테스트가 통과한다.

## 9. 1차 연결 검증 기록

### 9.1 Red → Green

- Red: `backtester.analytics`가 없는 상태에서 신규 테스트 collection이
  `ModuleNotFoundError`로 실패함을 확인했다.
- Green: 신규 위험진단·기존 metrics 집중 테스트 `49 passed`.
- 관련 회귀: backtest grid·runner·records 포함 `77 passed`.
- 전체 회귀: `3549 passed, 1 skipped, 723 subtests passed`.

### 9.2 GS Quant 격리 환경 비교

GS Quant 2.1.7을 프로젝트와 분리된 임시 환경에 설치해 동일 입력을 비교했다.

| 항목 | GS Quant | 우리 구현 | 판정 |
|---|---|---|---|
| 2일 rolling volatility | `224.4994432064` | `224.4994432064` | 수치 일치. 우리는 날짜 정렬을 위해 초기 부족 구간 `None`을 보존 |
| Winsorize (`limit=1`) | `[0.1, -0.0821367205, 0.1]` | 동일 | 수치 일치 |
| Drawdown | `[0, 0, -0.1, -0.1]` | `[0, 0, -0.1, -0.01]` | 의도적 차이. GS는 누적 최악 낙폭, 우리는 현재 underwater 경로 |

기존 `max_drawdown_pct`가 누적 최악 낙폭을 계속 담당한다. 신규 drawdown path는 회복 정도를
보여주는 보조 경로이며 기존 지표를 대체하지 않는다.

### 9.3 실제 연결 결과

- `compute_metrics()`에 `current_drawdown_pct`, `rolling_volatility_20d_pct`,
  `winsorized_return_clipped_count` scalar를 추가했다.
- JSON report 최상위에 전체 `risk_diagnostics` 경로를 추가했다.
- text summary에 현재 drawdown, 20일 rolling volatility, winsor clip 개수를 추가했다.
- 전체 연결은 백테스트 결과를 읽기만 하며 실시간 전략·주문·KIS API·인증 경로에는 import되지 않는다.
