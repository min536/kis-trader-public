# workspace/ inventory

작성일: 2026-05-08
갱신: 2026-06-07 — workspace를 read-only Operator Console로 승격

## 목적 추정

`workspace/`는 read-only Operator Console이다. `index.html`은 `Operator Workspace.html`로 진입시키고, 해당 HTML은 `styles.css`, `data.js`, `app.js`를 로드한다. `data.js`는 `scripts/refresh_workspace_data.py`가 생성하는 console data artifact다.

현재 README Quick Reference는 Operator Console을 `python3 scripts/refresh_workspace_data.py` 후 `workspace/index.html`을 여는 방식으로 안내한다. Streamlit dashboard는 같은 data loader를 사용하는 보조/개발용 read-only dashboard다.

Console은 read-only surface다. 주문/설정/엔진 제어/런타임 override activation surface가 아니며, `workspace/data.js` 갱신도 기본적으로 local dashboard artifacts만 읽는다.

## 현재 파일 구성

| 파일 | 크기 | 구성/역할 | tracked |
| --- | ---: | --- | --- |
| `workspace/README.md` | — | read-only Operator Console 안내 및 refresh 명령 | yes |
| `workspace/index.html` | — | Operator Console entrypoint. `Operator Workspace.html`로 진입 | yes |
| `workspace/Operator Workspace.html` | — | canonical console shell. read-only banner + `styles.css?v=7`, `data.js?v=4`, `app.js?v=4` 로드 | yes |
| `workspace/operator_workspace_standalone.html` | — | CSS/JS가 포함된 standalone console HTML. read-only banner 포함 | yes |
| `workspace/styles.css` | 30,658 bytes | 정적 workspace 스타일 | yes |
| `workspace/app.js` | 35,227 bytes | `window.MOCK_DATA` 기반 Desk/Book/Trace UI 렌더링 | yes |
| `workspace/data.js` | 14,645 bytes | `scripts/refresh_workspace_data.py`가 생성한 `window.MOCK_DATA` 스냅샷 | yes |
| `workspace/shot-book.jpg` | 62,743 bytes | 1465x865 JPEG asset. `shot2`~`shot4`와 동일 hash | yes |
| `workspace/shot2.jpg` | 62,743 bytes | 1465x865 JPEG asset. `shot-book`/`shot3`/`shot4`와 동일 hash | yes |
| `workspace/shot3.jpg` | 62,743 bytes | 1465x865 JPEG asset. `shot-book`/`shot2`/`shot4`와 동일 hash | yes |
| `workspace/shot4.jpg` | 62,743 bytes | 1465x865 JPEG asset. `shot-book`/`shot2`/`shot3`와 동일 hash | yes |
| `workspace/shot5.jpg` | 64,388 bytes | 1465x865 JPEG asset | yes |
| `workspace/shot_book.jpg` | 63,636 bytes | 1480x865 JPEG asset | yes |

`git ls-files -s workspace` 기준으로 위 파일은 모두 tracked 상태다. `git ls-files workspace --others --exclude-standard` 결과 untracked 파일은 없었다.

## 참조 여부 조사

검색 범위:

- `app/`
- `scripts/`
- `tests/`
- `docs/`
- `README.md`
- `AGENTS.md`
- `OPERATIONS.md`
- `run_full154_fetch.sh`
- 존재 여부 확인 대상: `pyproject.toml`, `Makefile`

주요 키워드:

- `workspace`
- `refresh_workspace_data`
- `data.js`
- `workspace/data.js`
- `workspace/index.html`
- `operator_workspace`
- `Operator Workspace`
- `shot*.jpg`

결과:

- `scripts/refresh_workspace_data.py`만 `workspace/data.js`를 직접 쓴다. 기본 실행은 local dashboard artifacts만 읽고 broker/API를 호출하지 않는다.
- `workspace/Operator Workspace.html` 내부에서만 `styles.css`, `data.js`, `app.js`를 로드한다.
- `workspace/data.js`에는 read-only Operator Console data이며 local dashboard artifacts에서 생성되었다는 주석이 있다.
- `docs/AI_AGENT_BRIEF.md`는 generated data/assets만 기본 context 제외로 두고, console 작업 시 source shell 파일을 선별적으로 읽도록 안내한다.
- `app/dashboard/streamlit_app.py`에는 화면 제목/상태 키로 `Operator Workspace` 또는 `workspace_page` 문자열이 있으나, 이는 Streamlit 보조 dashboard 내부 UI 용어다.
- `app/tools/run_proposal_backtest.py`, `app/tools/export_backtest_result_summary.py`의 `.lean-workspace` 언급은 외부 Lean backtester workspace 경로로, 이 리포지토리의 `workspace/`와 무관하다.
- `README.md`, `docs/ARCHITECTURE.md`는 `workspace/`를 read-only Operator Console로 안내한다.
- `pyproject.toml`, `Makefile`은 현재 루트에서 발견되지 않았다.

## scripts/refresh_workspace_data.py 사용 여부

`scripts/refresh_workspace_data.py`는 존재한다. 이 스크립트는 `app.dashboard.data_loader.load_dashboard_data`와 `app.dashboard.metrics`의 여러 view-model builder를 import해 `workspace/data.js`를 갱신한다.

기본 실행은 broker/API 호출 없이 local dashboard artifacts만 읽는다.

```bash
python3 scripts/refresh_workspace_data.py
```

mock KIS 잔고 snapshot을 추가로 fetch해 performance report를 먼저 저장하려면 명시적 broker-read flag가 필요하다.

```bash
python3 scripts/refresh_workspace_data.py --with-broker-balance
```

현재 조사 범위에서 이 스크립트를 자동 호출하는 코드는 발견되지 않았다. Operator가 console data refresh를 원할 때 수동으로 실행한다.

## app/dashboard/와의 관계

`app/dashboard/`는 console과 Streamlit 보조 dashboard가 공유하는 read-only dashboard data/metric layer다.

근거:

- `README.md` Quick Reference: Operator Console은 `workspace/index.html`, refresh는 `scripts/refresh_workspace_data.py`
- `docs/ARCHITECTURE.md`: `workspace/`: read-only Operator Console, `app/dashboard/`: console/Streamlit 공통 data layer
- `scripts/refresh_workspace_data.py`: `load_dashboard_data()`와 metric builders를 호출해 `workspace/data.js` 생성
- `app/dashboard/streamlit_app.py`: 같은 `load_dashboard_data()`를 직접 호출하는 보조 Streamlit dashboard

Stage 3 dashboard loader 개선은 `app/dashboard/data_loader.py`에 적용되었다. Operator Console refresh script가 이 loader를 사용하므로 `workspace/data.js` 생성도 tail/max-lines 개선의 직접 효과를 받는다. Streamlit dashboard도 같은 loader를 직접 사용한다.

## 위험도 평가

- 기능 영향 위험: 낮음. Console은 read-only generated data를 표시하며 자동매매 실행 경로에 연결되지 않는다.
- 보존 가치: 높음. `workspace/`는 operator-facing console shell이다.
- 혼동 위험: 낮음. `README.md`, HTML banner, `data.js` header가 read-only Operator Console과 refresh source를 명시한다.
- 민감정보 위험: 낮음~중간. API key/token은 보이지 않지만, 계좌 상태/보유 종목/평가금액 형태의 mock account snapshot이 tracked 파일에 포함되어 있다.
- 삭제 위험: 중간. 직접 운영 참조는 없지만, 아직 사용자가 수동으로 정적 HTML을 열어 보는 비공식 workflow가 있을 수 있다.

## 권장 판단

- 보존: 유지. `workspace/`는 read-only Operator Console이다.
- archive 후보: 낮음. console shell은 운영 UI 자산으로 유지한다.
- untrack 후보: 중간. `workspace/data.js`는 생성 파일 성격이 강하므로, 향후 정책 결정 후 `.gitignore` 전환을 검토할 수 있다.
- 삭제 후보: 낮음. 삭제보다 console/data refresh contract 유지가 우선이다.

## 다음 액션 후보

1. `workspace/data.js`를 계속 생성 파일로 tracked 유지할지, `.gitignore` 대상으로 전환할지 별도 판단한다.
2. Console data 자동 refresh가 필요해지면 별도 supervisor/cron 설계를 추가하되, broker/API 호출 없는 local-only refresh를 기본값으로 유지한다.
3. `--with-broker-balance`는 operator가 mock broker read snapshot을 명시적으로 원할 때만 사용한다.

## 검증

2026-05-08 최초 inventory는 문서 추가만 대상이었다. 2026-06-07에는 `tests/test_workspace_static_ui_contract.py`로 Operator Console contract를 검증한다.
