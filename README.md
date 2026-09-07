# kis-trader

한국투자증권 Open API를 사용하는 Python 모의투자·백테스트 연구 프로젝트입니다. 매매 조건, 위험 관리, 실행 파이프라인, 데이터 분석, Slack 운영 도구와 읽기 전용 대시보드 소스를 제공합니다.

이 배포본은 개인 운영 저장소에서 별도로 준비한 소스 스냅샷입니다. 실제 계좌 정보, 인증 자료, 거래·잔고 기록, 수집한 시세 데이터, 과거 Git 이력은 포함하지 않습니다. 대시보드 예제의 계좌·종목·금액은 합성 데이터입니다.

## 살펴보기

- [아키텍처](docs/ARCHITECTURE.md)와 [시스템 구성](docs/system_architecture_schema.md)
- [실행 파이프라인](docs/current_cycle_pipeline.md)
- [백테스트](docs/BACKTESTING.md)
- [위험 관리 목록](docs/risk_guard_catalog_20260705.md)
- [공개 범위와 제한](PUBLIC_RELEASE.md)
- [외부 자료와 라이선스](THIRD_PARTY_NOTICES.md)

Python 3.13 환경에서 개발 의존성을 설치합니다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints.txt
.venv/bin/python -m pytest tests
```

테스트는 개인 인증 정보가 없는 별도 환경에서 실행하세요. 소스 스냅샷의 무결성은 다음 명령으로 확인할 수 있습니다.

```sh
.venv/bin/python scripts/verify_release_manifest.py
```

## 합성 데이터 화면 미리보기

다음 명령은 정적 파일만 제공합니다. 계좌 연결이나 주문 처리가 없는 데모입니다.

```sh
.venv/bin/python -m http.server 4187 --bind 127.0.0.1 --directory workspace/claude-design/kis-trader-v2/project
```

브라우저에서 `http://127.0.0.1:4187/kis-trader%20Ops%20Console.html`을 엽니다. React와 Babel은 고정 버전 CDN에서 불러옵니다. 정적 미리보기에는 데이터 API가 없으므로 화면은 데모 상태를 유지합니다. 실제 운영 대시보드는 인증 기능이 있는 공개 웹 서비스가 아니며 인터넷에 노출하지 마세요.

## 사용 범위

학습과 개인 모의투자 연구를 위한 코드입니다. 수익을 보장하거나 개인에게 투자 판단을 권유하는 서비스가 아닙니다. API 사용 자격과 약관은 사용자가 각 제공자에게 확인해야 하며, 이 저장소의 공개는 시세 데이터 재배포나 타인 계좌 운용을 허용한다는 뜻이 아닙니다. 한국투자증권·토스증권 등과의 공식 제휴나 보증을 표시하지 않습니다.

프로젝트 자체 코드에 대한 범용 오픈소스 라이선스는 아직 지정하지 않았습니다. 소스 공개와 별개로, 별도 허락 없는 재사용·재배포 권한을 부여하지 않습니다. 명시적으로 포함한 외부 구성요소에는 해당 구성요소의 라이선스가 적용됩니다.
