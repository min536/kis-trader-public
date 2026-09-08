# kis-trader

**한국투자증권 Open API로 만드는 Python 모의투자·퀀트 리서치 시스템.**

Paper trading, backtesting, and observable trading operations with the KIS Open API.

시장 데이터를 수집하고, 매매 후보를 평가하고, 위험 조건을 확인한 뒤 모의 주문을 실행하는 과정을 연구합니다. 같은 프로젝트 안에서 백테스트·리플레이, 운영 상태 점검, Slack 알림과 읽기 전용 대시보드까지 연결합니다.

이 저장소에는 전략·실행·위험 관리 구현과 이를 검증하는 테스트를 함께 공개합니다. 개인 운영 저장소에서 분리한 소스 스냅샷이며, 계좌 연결 없이 살펴볼 수 있는 합성 데이터 화면을 포함합니다.

[API 사용 방식](docs/API_USAGE.md) · [아키텍처](docs/ARCHITECTURE.md) · [설계 결정](docs/DESIGN_DECISIONS.md) · [공개 범위 검토](docs/PUBLIC_SCOPE_AUDIT.md) · [MIT 라이선스](LICENSE)

## 무엇을 담고 있나요?

| 영역 | 제공하는 구현 | 코드 |
|---|---|---|
| 모의투자 실행 | 국내주식 시장가 주문, 잔고·주문 가능 금액 조회, 해외주식 모의 지정가 주문 어댑터 | [국내주식](app/domestic_stock/), [해외주식](app/overseas_stock/) |
| 매매 판단과 위험 관리 | 후보 스캔·점수화, 매수·매도 판단, 재진입 제한, 손익·시장 상태 기반 가드 | [스캐너](app/scanner/), [전략](app/strategy/), [위험 관리](app/risk/) |
| 실행 파이프라인 | 매수·매도 작업 분리, 요청 예산·마감 시간 관리, 주문 처리 상태 추적과 잔고 대조 | [파이프라인](app/pipeline/), [런타임](app/runtime/) |
| 데이터와 연구 | KIS·토스 분봉 수집, CSV→Parquet 변환, 누락 구간 검사, 과거 데이터 리플레이 | [리서치](app/research/) |
| 백테스트와 후보 검증 | 엔진 백테스트, 패리티 검사, 전략 후보 평가, 사람의 승인 정보를 확인하는 설정 적용 절차 | [백테스터](backtester/), [후보 검증](app/autotuner/) |
| 운영 도구 | Slack 알림·명령, DART 공시 감시, 상태·데이터 품질 점검, 읽기 전용 운영 화면 | [알림](app/notifications/), [운영 도구](app/tools/), [대시보드](workspace/claude-design/kis-trader-v2/) |
| 선택형 AI 실험 | LLM 응답 캐시 생성, 저장된 응답 재생과 평가 | [AI 연구 모듈](backtester/ai_integration/) |

## 어떻게 연결되나요?

기본 흐름은 **시세·계좌 조회 → 후보 평가 → 위험 조건 확인 → 모의 주문 → 상태 대조·기록·알림**입니다. 연구 모듈은 수집 데이터를 별도로 가공해 백테스트와 리플레이에 사용합니다.

- **KIS Open API**: 인증, 시세·계좌 조회와 모의 주문. 별도 시세 조회 경로와 주문 경로의 책임을 구분합니다.
- **토스증권 Open API**: 분봉 수집과 연구 데이터 보완에 사용합니다.
- **OpenDART**: 공시 목록을 조회하고 운영 알림으로 연결합니다.
- **Slack API**: 운영 알림 전송과 허용된 사용자의 명령 처리에 사용합니다.
- **Anthropic API**: 선택형 오프라인 LLM 실험의 응답 캐시를 만드는 데 사용합니다.

인증 흐름, 실제 호출 경로, 오류·호출량 처리, 각 모듈의 경계는 [API 사용 문서](docs/API_USAGE.md)에 정리했습니다. 문서는 배포된 구현을 설명하며, 특정 계정의 현재 연결 상태를 뜻하지 않습니다.

## 먼저 살펴보기

Python 3.13 환경에서 개발 의존성을 설치합니다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints.txt
.venv/bin/python -m pytest tests
```

테스트는 개인 인증 정보가 없는 별도 환경에서 실행하세요. 외부 데이터가 필요한 일부 검증은 해당 자료를 별도로 준비해야 합니다. 소스 스냅샷의 무결성은 다음 명령으로 확인할 수 있습니다.

```sh
.venv/bin/python scripts/verify_release_manifest.py
```

실제 계좌를 연결하는 실행에는 본인의 API 이용 자격·인증 정보와 운영 설정이 필요합니다. 공개본의 정규 세션 설정은 비어 있는 예제이며, 개인 운영 설정은 제공하지 않습니다. [운영 문서](docs/OPERATIONS.md)는 운영 절차를 설명하는 참고 자료입니다.

## 합성 데이터 화면 미리보기

기본 운영 화면은 **v2 Operator Site(기본 read-only UI)**입니다. 운영 서버 소스는 `workspace/claude-design/kis-trader-v2/server.py`에 있습니다. 기존 **Streamlit 대시보드**는 **폐기됨** 상태입니다.

다음 명령은 계좌 연결이나 주문 처리가 없는 정적 데모를 제공합니다.

```sh
.venv/bin/python -m http.server 4187 --bind 127.0.0.1 --directory workspace/claude-design/kis-trader-v2/project
```

브라우저에서 `http://127.0.0.1:4187/kis-trader%20Ops%20Console.html`을 엽니다. 화면의 계좌·종목·금액은 합성 데이터입니다. React와 Babel은 고정 버전 CDN에서 불러오며, 정적 미리보기에는 데이터 API가 없어 데모 상태를 유지합니다. 실제 운영 대시보드는 인증 기능이 있는 공개 웹 서비스가 아니므로 인터넷에 노출하지 마세요.

## 공개 범위와 재현성

2026-09-08 원본 대조에서 `app/`, `tests/`, `backtester/`, `scripts/`의 관리 대상 파일이 공개본에 모두 포함된 것을 확인했습니다. 전략·위험 관리·연구 코드를 보존하고, 실제 인증 정보, 계좌·거래 기록, 수집 데이터, 개인 작업 기록과 재배포 근거가 확인되지 않은 외부 자료를 제외했습니다.

- 공개 준비 당시 분리된 환경에서 **3,565개 테스트 통과, 1개 건너뜀**을 확인했습니다. 건너뛴 검증은 실제 운영 스냅샷이 필요한 리플레이 패리티 검사입니다. 이는 수익성이나 실전 운용 적합성을 입증하는 결과가 아닙니다.
- 외부 데이터, 개인 설정, 별도 로컬 연구 서비스는 배포하지 않습니다. 따라서 이 소스만으로 개인 운영 결과 전체를 재현할 수는 없습니다.
- 제외한 운영 기록의 일반적인 기술 원칙은 [설계 결정](docs/DESIGN_DECISIONS.md)에 별도로 정리했습니다.

자세한 포함량과 비교 기준은 [공개 범위 검토](docs/PUBLIC_SCOPE_AUDIT.md), 제외 정책은 [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md), 외부 자료 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요.

## 더 읽기

- [시스템 구성과 외부 연구 서비스 경계](docs/system_architecture_schema.md)
- [실행 파이프라인](docs/current_cycle_pipeline.md)
- [백테스트](docs/BACKTESTING.md)
- [위험 관리 목록](docs/risk_guard_catalog_20260705.md)
- [AI 운영 보조 워크플로우](docs/agent_workflows/README.md)

## 라이선스와 사용 범위

프로젝트 자체 코드와 문서는 [MIT 라이선스](LICENSE)로 제공합니다. 저작권·라이선스 고지를 유지하면 사용, 수정, 재배포와 상업적 이용이 가능합니다. 외부 구성요소에는 [각 구성요소의 기존 라이선스](THIRD_PARTY_NOTICES.md)가 적용됩니다.

이 프로젝트는 학습과 모의투자 연구를 위한 코드입니다. 수익을 보장하거나 개인에게 투자 판단을 권유하는 서비스가 아닙니다. API 사용 자격과 약관은 사용자가 각 제공자에게 확인해야 하며, 코드의 MIT 라이선스가 시세 데이터 재배포나 타인 계좌 운용 권한을 부여하지는 않습니다. 한국투자증권·토스증권 등과 공식 제휴하거나 보증을 받는 프로젝트가 아닙니다.
