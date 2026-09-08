# API를 어떻게 사용하나요?

이 문서는 공개본의 **실제 구현 경로**를 설명합니다. 개인 계정의 연결 상태나 과거 호출 이력을 공개하는 문서가 아닙니다. 공개 준비 과정에서는 아래 증권사·주문 API를 호출하지 않았습니다.

## 역할별 연결

| 연결 | 프로젝트에서의 역할 | 인증·설정의 종류 | 외부로 전달되는 정보 |
|---|---|---|---|
| KIS Open API | 시세·계좌 조회, 모의 주문, 분봉 수집 | 본인 앱 키·시크릿, 접근 토큰, 계좌 설정 | 인증 정보, 조회 종목·기간, 계좌 조회 조건, 모의 주문 내용 |
| 토스증권 Open API | 연구용 분봉 수집·보완 | 본인 클라이언트 자격 증명과 접근 토큰 | 인증 정보, 조회 종목·기간·캔들 조건 |
| OpenDART | 공시 목록 조회와 감시 | `DART_API_KEY` | 인증키, 공시 검색 조건 |
| Slack | 알림 전송, 운영 명령 수신·응답 | 봇 토큰, Socket Mode 앱 토큰, 채널·사용자 설정 | 알림에 담긴 운영 상태·주문 정보와 명령 응답 |
| Anthropic | 선택형 LLM 응답 캐시 생성 | `ANTHROPIC_API_KEY`, 별도 SDK | 사용자가 준비한 연구 프롬프트와 입력 특징·요약 |
| 별도 로컬 연구 서비스 | 후보 전략의 외부 백테스트 요청 | 로컬 서비스 주소·별도 저장소 위치 | 전략 YAML 등 연구 요청; HTTP 대상은 loopback으로 제한 |

인증값과 수집 결과는 배포본에 포함하지 않습니다. 코드를 직접 운영하면 설정한 제공자에게 위 정보가 전달되므로, 공개 소스와 개인 운영 데이터의 범위를 구분해 사용해야 합니다.

## 1. KIS: 인증에서 모의 주문까지

### 인증과 요청 공통 처리

[인증 모듈](../app/auth/token.py)은 `POST /oauth2/tokenP`에 `client_credentials` 요청을 보내 접근 토큰을 발급받습니다. [설정 모듈](../app/auth/settings.py)이 선택한 환경·호스트와 자격 증명을 사용하고, 요청 헤더에 Bearer 토큰, 앱 키·시크릿과 거래 식별자 `tr_id`를 넣습니다.

토큰 캐시는 환경별 파일과 앱 키 지문을 확인해 재사용합니다. 코드의 캐시 재사용 기준은 6시간이며, 이는 이 프로젝트의 갱신 정책이지 제공자의 토큰 유효기간을 설명하는 값이 아닙니다. 캐시 디렉터리·파일에는 제한된 파일 권한을 적용합니다.

주요 설정 이름은 `KIS_ENV`, `KIS_APP_MOCK_KEY`, `KIS_APP_MOCK_SECRET`, `KIS_BASE_MOCK_URL` 등이며, 계좌 설정과 호환 별칭의 정확한 해석은 [settings.py](../app/auth/settings.py)를 따릅니다. 이 문서는 실전 주문 설정 절차를 제공하지 않습니다.

### 구현된 호출 경로

아래는 코드에서 확인한 대표 경로입니다. API의 전체 목록이나 현재 계정별 사용 가능 기능 목록은 아닙니다.

| 기능 | 메서드·경로 | 호출하는 코드 |
|---|---|---|
| 접근 토큰 발급 | `POST /oauth2/tokenP` | [token.py](../app/auth/token.py) |
| 주문 본문 Hashkey 발급 | `POST /uapi/hashkey` | [token.py](../app/auth/token.py) |
| 국내 현재가 | `GET /uapi/domestic-stock/v1/quotations/inquire-price` | [quote.py](../app/domestic_stock/quote.py) |
| 국내 잔고 | `GET /uapi/domestic-stock/v1/trading/inquire-balance` | [balance.py](../app/domestic_stock/balance.py) |
| 국내 매수 가능 금액·수량 | `GET /uapi/domestic-stock/v1/trading/inquire-psbl-order` | [orderable.py](../app/domestic_stock/orderable.py) |
| 국내 모의 시장가 주문 | `POST /uapi/domestic-stock/v1/trading/order-cash` | [order.py](../app/domestic_stock/order.py) |
| 국내 일별 분봉 | `GET /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` | [kis_minute_fetcher.py](../app/research/ingest/kis_minute_fetcher.py) |
| 해외 현재가·상세 시세 | `GET /uapi/overseas-price/v1/quotations/price`, `price-detail` | [market_data.py](../app/overseas_stock/market_data.py) |
| 해외 주문 가능 금액·잔고 | `GET /uapi/overseas-stock/v1/trading/inquire-psamount`, `inquire-balance` | [market_data.py](../app/overseas_stock/market_data.py), [잔고 조회](../app/overseas_stock/balance.py) |
| 해외 모의 지정가 주문 | `POST /uapi/overseas-stock/v1/trading/order` | [order.py](../app/overseas_stock/order.py) |

국내 주문 어댑터는 모의 매수·매도 거래 식별자 `VTTC0012U`·`VTTC0011U`를 사용합니다. 주문 본문으로 Hashkey를 만든 뒤 주문 요청을 보냅니다. 해외 주문 어댑터는 요청 전 모의 환경을 검사하고 지정가 주문만 허용합니다. 장 시간·위험 조건 확인은 상위 [런타임 단계](../app/runtime/cycle_phases/)와 [실행 모듈](../app/execution/)까지 함께 보아야 하며, 저수준 주문 함수를 바로 호출하는 것을 안전한 진입점으로 간주하면 안 됩니다.

### 조회 경로와 주문 경로

[BUY 시세 프리페치](../app/scanner/quote_account.py)는 주문 계정과 별도인 시세 조회 경로를 지원합니다. 별도 경로의 국내 시세 요청은 현재가 조회 URL과 `FHKST01010100` 거래 식별자를 검사합니다. `live` 시세 환경을 사용하는 코드가 존재하지만, 이 경로의 역할은 **읽기 전용 시세 조회**이며 주문·체결 요청을 보내는 통로가 아닙니다. 공개 데모에서는 이 연결을 켜지 않습니다.

### 호출량·오류·중복 주문 대응

- [throttle.py](../app/core/throttle.py)와 [kis_rate_limits.py](../app/core/kis_rate_limits.py)가 요청 간격과 호출량을 제어합니다. 설정된 클라이언트 제한은 구현 정책이며 제공자의 최신 허용량을 대신하지 않습니다.
- [runtime_budget.py](../app/core/runtime_budget.py)와 시세 프리페치가 한 주기의 요청 예산·남은 시간을 관리합니다. 토큰·Hashkey 요청도 호출 예산에 영향을 줍니다.
- 공통 요청 함수의 기본 재시도는 일부 일시적 연결 오류에 적용합니다. 조회 및 토큰·Hashkey 요청과 달리, 주문 POST는 기본 자동 재시도 대상에서 제외합니다. 응답이 불확실한 주문을 그대로 다시 보내지 않기 위한 구분입니다.
- 요청 횟수·소요 시간을 분류별로 집계하고, `EGW00201` 등 호출 제한 응답을 기록합니다. [잔고 대조](../app/core/reconciliation.py)는 기대 보유량과 브로커 조회 상태의 차이를 확인합니다.

공식 API 분류와 서비스 안내는 [KIS Developers](https://apiportal.koreainvestment.com/apiservice)를 참고하세요. 위 경로·동작 설명의 직접 근거는 링크한 공개 소스입니다.

## 2. 토스증권: 분봉 수집

[toss_minute_fetcher.py](../app/research/ingest/toss_minute_fetcher.py)는 `TOSS_API_KEY`·`TOSS_SECRET_KEY`를 OAuth 클라이언트 자격 증명으로 사용해 `/oauth2/token`에서 토큰을 받고, `GET /api/v1/candles`를 호출합니다. 이 모듈에는 토스 주문 처리가 없습니다.

종목·봉 간격·조회 개수·수정주가 여부와 `before` 커서를 전달합니다. 종목별 진행 상태를 저장해 중단된 수집을 이어가며, KIS 수집기와 같은 날짜·종목별 CSV 구조로 정리합니다. [변환기](../app/research/ingest/minute_csv_to_parquet.py)가 이를 연구용 Parquet 데이터로 변환하고, [커버리지 검사](../app/research/coverage/)가 누락 구간을 점검합니다. 다운로드된 시세나 토큰은 공개하지 않습니다.

공식 설명: [토스증권 시세 API](https://developers.tossinvest.com/docs/market-data).

## 3. OpenDART: 공시 감시

[disclosure_sentinel.py](../app/notifications/disclosure_sentinel.py)는 `GET https://opendart.fss.or.kr/api/list.json`에 검색 조건과 `crtfc_key`를 전달합니다. 새 공시를 식별해 운영 알림으로 연결하고, 중복 처리를 위한 상태를 로컬에 저장합니다. `DART_API_KEY`가 없으면 감시 기능은 비활성 상태로 동작합니다.

공식 설명: [OpenDART 공시정보 개발가이드](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001).

## 4. Slack: 운영 상태와 명령

[slack.py](../app/notifications/slack.py)는 Slack Web API의 `chat.postMessage`로 알림을 보냅니다. [slack_bot.py](../app/notifications/slack_bot.py)는 `slack_sdk`의 Socket Mode로 `app_mention` 이벤트를 받고, 허용 채널·사용자·팀 설정을 확인한 뒤 상태 조회나 백테스트·대시보드 운영 명령을 처리합니다.

명령은 단순 조회 외에 로컬 작업 시작·중지 기능도 포함합니다. [backtest_control.py](../app/notifications/backtest_control.py)는 실행 시간, 프로세스 상태와 완료 감시를 관리합니다. 개인 Slack 식별자·토큰·실제 알림 내역은 배포본에 없습니다.

공식 설명: [Slack Web API](https://docs.slack.dev/apis/web-api/).

## 5. Anthropic: 선택형 오프라인 연구

[AnthropicClient](../backtester/ai_integration/llm_cache_builder/client.py)는 별도 `anthropic` SDK의 `messages.create`로 연구 프롬프트를 전송합니다. [캐시 빌더](../backtester/ai_integration/llm_cache_builder/)와 [평가 모듈](../backtester/ai_integration/evaluator.py)은 응답을 저장·재생해 실험에 사용합니다. 이 기능은 기본 의존성에 포함되지 않는 선택형 연구 도구이며, LLM 응답을 곧바로 증권사 주문에 보내는 API 통로가 아닙니다.

외부 호출 없이 사용할 수 있는 모의 클라이언트도 있습니다. 실제 클라이언트를 사용하면 준비한 입력이 외부 서비스로 전송되므로 연구 입력에 개인 계좌·인증 정보를 넣지 마세요. 코드에 있는 모델 기본값이나 비용 추정표는 현재 서비스 조건을 보장하지 않습니다.

공식 설명: [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create).

## 6. 별도 로컬 백테스트 서비스

[run_proposal_backtest.py](../app/tools/run_proposal_backtest.py)는 전략 YAML을 별도 서비스의 `/api/backtest/run-custom`으로 전달할 수 있습니다. [open-trading-api 어댑터](../app/integrations/open_trading_api/adapter.py)가 HTTP 대상의 loopback 여부와 로컬 자료 접근 범위를 검사합니다.

이 연결에 필요한 별도 저장소·서버·데이터는 공개본에 포함하지 않았습니다. 여기서 말하는 로컬 `open-trading-api` 연구 서비스와 증권사의 공식 KIS REST 서버는 서로 다른 연결입니다. 저장소 자체의 [엔진 백테스터](../backtester/engine_backtest/)와도 구분해야 합니다.
