# Website Dashboard Improvement Plan

## 1. 목적
`workspace/` read-only Operator Console과 `app/dashboard/` 공통 데이터 layer를 실제 운영에서 신뢰할 수 있는 **Read-only 운영 관제 센터**로 발전시키기 위한 로드맵을 정의한다.

## 2. 현재 Dashboard Inventory
- **Operator Console (`workspace/`)**:
    - canonical read-only 운영 console.
    - `workspace/index.html` → `Operator Workspace.html` 진입.
    - `workspace/data.js`는 `python3 scripts/refresh_workspace_data.py`가 local dashboard artifacts에서 생성.
    - broker/API read는 기본값이 아니며 `--with-broker-balance`를 명시해야 함.
- **공통 데이터/보조 대시보드 (`app/dashboard/`)**:
    - `data_loader.py`를 통해 로컬 JSON/JSONL 파일을 파싱.
    - Streamlit 앱은 같은 data loader를 직접 호출하는 보조/개발용 read-only dashboard.
    - `Desk`, `Book`, `Trace` view model과 metric builder를 workspace console refresh가 재사용.

## 3. 현재 데이터 Source
- `data/runtime_state_{sig}.json`: 실시간 엔진 상태 (brake, regime 등)
- `logs/orders_{sig}.jsonl`: 오늘 발생한 주문/체결 스트림
- `data/cycle_snapshots_{sig}.jsonl`: 매 사이클의 판단 근거 (funnel 데이터)
- `logs/performance_summary_{sig}.jsonl`: 계좌 잔고 및 수익률 요약
- `data/live_snapshot.json`: KIS DWS 기반 종목 마스터 및 실시간 가격 fallback

## 4. Console v1 계획 (Read-only 관제 강화)
v1은 **가시성(Visibility)** 개선에 집중한다.

- **Session Status**: 현재 엔진이 작동 중인지, 마지막 사이클이 언제였는지(`heartbeat freshness`) 명시.
- **API Health**: 남은 Request/Quote Budget과 현재 Backoff 발생 여부를 실시간 게이지로 표시.
- **Cycle Latency**: 최근 사이클 소요 시간(`elapsed_ms`)을 그래프로 표시하여 병목 현상 감지.
- **Orders Today**: 오늘 발생한 주문 내역을 사이드바 또는 하단 탭에 요약 노출.
- **Rich Symbol Tags**: 종목 옆에 `[KOSPI]`, `[ETF]`, `[Risk:High]` 등 태그 칩을 표시하여 시각적 인지 속도 향상.
- **Account Signature**: 화면 상단에 현재 보고 있는 계좌 식별자를 명시.

## 5. Dashboard v2 계획 (Multi-Account 지원)
v2는 여러 계좌/프로세스를 동시에 관리하는 기능을 추가한다.

- **Multi-Account Discovery**: `data/` 디렉터리의 파일 패턴을 분석하여 실행 중인 모든 계좌 정보를 자동으로 리스트업.
- **Global Overview**: 모든 계좌의 총 자산, 오늘 수익률, API 부하 상태를 한눈에 볼 수 있는 요약 대시보드.
- **Profile Switching**: 사이드바에서 계좌/프로필을 선택하면 해당 계좌의 상세 `Trace` 및 `Book`으로 즉시 전환.
- **Resource Pressure Map**: 어떤 계좌가 API 할당량을 많이 쓰고 있는지 비교 분석.

## 6. 금지 기능 (Read-only 원칙 준수)
대시보드에서 다음 기능은 보안 및 운영 안전을 위해 **절대 금지**한다.
- ❌ **주문 실행/취소 버튼**: UI를 통한 직접 주문 금지.
- ❌ **설정 수정 UI**: `.env`나 `yaml` 파일을 웹에서 수정하는 기능 금지.
- ❌ **엔진 제어**: `app.main` 프로세스를 시작하거나 중지하는 버튼 금지 (SSH/스크립트 전용).
- ❌ **Live Decision 변경**: 런타임에 전략 파라미터를 강제로 주입하는 UI 금지.

## 7. 구현 옵션 비교

| 옵션 | 장점 | 단점 | 추천 여부 |
|:---|:---|:---|:---:|
| **workspace Console 확장** | canonical UI 유지, 정적 shell + local generated data로 단순하고 빠름 | 자동 refresh/serve는 별도 설계 필요 | ✅ **v1 추천** |
| **Streamlit 확장** | 현재 구조 유지, Python만으로 빠른 구현 | 레이아웃 커스텀 한계, 부하 시 속도 저하 | 🟡 보조/개발용 |
| **FastAPI + React** | 최고의 UI/UX, 멀티 계좌 확장성 우수 | 별도 서버 관리 필요, 구현 공수 높음 | 🟡 **v2 장기 검토** |
| **Slackbot 확장** | 별도 웹 불필요, 즉시성 높음 | 정보 밀도 낮음, 그래프 표현 한계 | ✅ **보완재** |

## 8. 단계별 로드맵
1. **Phase 0 (현재)**: 현황 조사 및 설계 문서화.
2. **Phase 1**: Operator Console에 API Budget, Latency, Rich Tag 칩 추가.
3. **Phase 2**: `account_signature` 기반 다중 계좌 선택 기능 구현.
4. **Phase 3**: 데이터 로딩 최적화를 위해 파일을 직접 읽는 대신 가벼운 API 서버 도입 검토.

## 9. 권고 사항
운영 중에는 console을 read-only로 유지한다. 주문/설정/엔진 제어/런타임 override activation surface를 추가하지 않는다.
