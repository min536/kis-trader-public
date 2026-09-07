---
Purpose: Codex/Claude Code/Antigravity 등 AI agent가 작업 시작 전에 반드시 읽어야 하는 최소 필수 컨텍스트
Read when: kis-trader 리포지토리에서 작업을 시작하기 전
Do not use for: 상세 아키텍처나 개별 모듈의 세부 로직 확인 (다른 문서 참조)
---

# AI Agent Brief (TL;DR)

## 0. Context Priority (읽기 우선순위)

**먼저 읽을 파일**: `AGENTS.md`, `docs/AI_AGENT_BRIEF.md`, `README.md`, `docs/agent_workflows/README.md`, `app/main.py` (symbol 탐색만)

**필요할 때만 읽을 경로**:
- `app/tools/` — 운영 도구 7개(`compare_runtime_quality`, `recommend_score_guards`, `validate_symbol_tags`, `postrun_diagnostics`, `eod_health_check`, `live_health_check`, `preopen_operator_brief`)만 우선 참조. 나머지 40+개는 ML/research 전용.
- `backtester/ai_integration/`, `backtester/reports/`
- `config/symbol_tags.yaml`
- `tests/test_ai_*`, `tests/test_llm_cache_builder.py`, `tests/test_model_routing.py`, `tests/test_replay.py`

**기본 context 제외** (`.claudeignore`/`.antigravityignore` 적용):
`data/`, `results/`, `logs/`, `workspace/data.js`, `workspace/shot*.jpg`, `workspace/claude-design/`, `archive/`, `research/`

`workspace/`는 read-only Operator Console shell이다. Console 작업 시에는 `workspace/README.md`, `workspace/index.html`, `workspace/Operator Workspace.html`, `workspace/app.js`, `workspace/styles.css`만 선별적으로 읽고, generated snapshot인 `workspace/data.js`는 전체 열람하지 않는다.

사용자가 이 repo에서 별도 수식어 없이 "사이트"라고 말하면 `workspace/claude-design/kis-trader-v2/`의 v2 ops console preview를 뜻한다. 정적 `workspace/` Operator Console은 legacy fallback이고, Streamlit 대시보드는 v2 site로 이전되어 제거됐다.

## 1~3. 프로젝트 목적, 진입점, 핵심 규칙

→ `AGENTS.md` 참조 (중복 방지). 추가 사항만 기재:

- **Config**: `.env`는 공용 기본값, 정규 세션은 `config/regular_session.env` 우선. production `.env`(`SCAN_SYMBOLS`)는 gitignored.
- `app/main.py`는 약 1,447줄(Stage A+B 슬리밍 완료, 2026-07-04 실측)이지만 조밀 — 전체 읽지 말고 symbol 중심 탐색
- `python -c 'import app.main'` 수준의 import check만 허용

## 4. 주요 경로 및 매핑
- **주문**: `app/domestic_stock/order.py`
- **Slack 알림**: `app/notifications/slack.py`
  - *Slack alert 변경 시 정책*: 기존에 정의된 실패 사유와 API 코드는 반드시 유지해야 함. (Boilerplate는 숨기되 에러 코드는 보존)
- **태그 (Symbol Tags)**: `config/symbol_tags.yaml`
  - 이 태그들은 display/filter 메타데이터 용도일 뿐, **live 매매 판단 로직의 필수 의존성이 아님.**
- **스캐너**: `app/scanner/`
- **백테스트**: `app/tools/` 및 `backtester/`
- **유니버스 (Universe)**:
  - `full154`: 백테스트/대규모 데이터 페치용 (ex. `run_full154_fetch.sh`)
  - `scan36`: 실제 라이브 `SCAN_SYMBOLS` 제약
  - *Target Symbols 결정 경로*: `app.auth.settings` 및 런타임 환경 변수. production의 `SCAN_SYMBOLS`는 gitignored된 설정에 의존할 수 있으므로 코드만으로 완전 단정하지 말 것.

## 5. 작업 전/후 테스트 명령 (Local Only)
작업 후에는 외부 API가 호출되지 않는 아래 명령들만 사용하여 검증할 것:
```bash
python -m app.tools.validate_symbol_tags
python -c 'import app.main; print("app.main import OK")'
PYTHONPATH=. pytest tests/test_symbol_tags.py tests/test_slack_notifier.py
```

## 6. 대용량 파일 접근 등급

repo 전체 data/logs/results/archive 합산 약 995만 줄, 최대 단일 파일 342MB.
AI agent가 실수로 전체 열람하면 context 폭발 또는 심각한 지연이 발생한다.

### 등급 정의

| 등급 | 의미 | 규칙 |
|------|------|------|
| **Green** | 자유롭게 읽어도 됨 | 제한 없음 |
| **Yellow** | 부분만 읽어야 함 | head/tail/wc/du만 사용, 전체 cat/grep/read_text 금지 |
| **Red** | 기본적으로 열지 않음 | 사용자 명시 요청 시에만 제한적 접근, 먼저 크기 확인 |
| **Black** | 절대 전체 열람 금지 | tail -n, wc -l, du -h, 전용 loader만 허용 |

### Green — 자유롭게 읽어도 되는 파일

- `app/**/*.py`
- `tests/**/*.py`
- `docs/**/*.md`
- `README.md`
- `scripts/*.py`, `scripts/*.sh`
- `config/symbol_tags.yaml`
- 단, `.env`는 민감 정보 포함이므로 내용 출력 금지

### Yellow — 제한적으로만 읽는 파일

- `data/runtime_state*.json`
- `data/live_snapshot.json`
- `data/account_scope_meta.json`
- `logs/orders_*.jsonl`
- `logs/performance_summary_*.jsonl`
- `data/cycle_snapshots_*.jsonl`
- `logs/app_stdout_*.log`
- `logs/candidate_outcomes_*.jsonl`
- `logs/cycle_stats_*.jsonl`

**Yellow 규칙:**
- 전체 `cat` 금지
- 전체 `grep` 금지
- 전체 `read_text()` / `open().read()` 금지
- `head -n 20`, `tail -n 20`, `wc -l`, `ls -lh`, `du -h` 같은 제한 명령만 사용
- JSONL은 `tail -n 20` 또는 필요한 N줄만 확인
- 큰 JSONL은 Python으로 필요한 key만 요약

### Red — 기본적으로 읽지 않는 파일

- `archive/**`
- `results/**`
- `logs/ml/**`
- `research/**`
- `workspace/data.js`
- `workspace/shot*.jpg`
- `workspace/claude-design/**`
- `*.parquet`, `*.zip`, `*.pkl`, `*.pyc`
- `__pycache__/**`, `.pytest_cache/**`, `.DS_Store`

**Red 규칙:**
- 일반 코드 작업에서는 검색 대상에서 제외
- 필요한 경우 먼저 `ls -lh`, `wc -l`, `du` 로 크기 확인 후 접근
- 사용자가 명시적으로 요청하거나 관련 작업일 때만 제한적으로 접근

### Black — 절대 전체 열람 금지 파일

| 파일 | 크기 | 사유 |
|------|------|------|
| `data/cycle_snapshots_mock_12345678_01.jsonl` | 342MB | active snapshot, 줄당 payload 대형 |
| `archive/data/cycle_snapshots_mock_12345678_01.jsonl` | 210MB | historical snapshot |
| `archive/logs/app_stdout_20260415.log` | 74MB / 220만 줄 | historical stdout |
| `logs/performance_summary_mock_12345678_01.jsonl` | 44MB | active perf summary |
| `logs/orders_mock_12345678_01.jsonl` | 35MB | active orders |
| `logs/app_stdout_20260508.log` | 10MB / 33만 줄 | recent stdout |
| `results/**/*.json` | 각 1~5MB / 각 ~9만 줄 | backtest results |

**Black 규칙:**
- `cat` 금지
- 전체 `grep` 금지
- 전체 `open()` / `read_text()` 금지
- 필요한 경우 `tail -n`, `wc -l`, `du -h`, 전용 loader(`_safe_read_jsonl` with `max_lines`)만 사용

### Safe Commands (허용)

```bash
git status --short
ls -lh data/*.jsonl logs/*.jsonl
du -ah data logs results archive | sort -h | tail -30
wc -l <file>
tail -n 20 <file>
head -n 10 <file>
# ripgrep with exclusions
rg "pattern" app tests docs scripts config \
  --glob '!data/**' --glob '!logs/**' \
  --glob '!results/**' --glob '!archive/**'
```

### Unsafe Commands (금지)

```bash
# 전체 cat — context 폭발
cat data/cycle_snapshots_*.jsonl

# 무제한 recursive grep — 대용량 파일 포함
rg "pattern" .
grep -r "pattern" .

# Python 전체 read — 메모리 폭발
python -c 'Path("data/cycle_snapshots...").read_text()'

# results 전체 열기
cat results/**/*.json
```

### .rgignore 권장 사항

아래 `.rgignore` 파일을 repo root에 추가하면 `rg` 명령이 자동으로 대용량 경로를 제외한다.
현재는 제안만 기록하며, 실제 파일 생성은 별도 작업에서 수행한다.

```
# .rgignore (proposed)
data/
logs/
results/
archive/
research/
workspace/
*.parquet
*.zip
*.pkl
```

## 7. 커밋 전 체크리스트
- [ ] 코드 동작이 변경되지 않았는가?
- [ ] 매매 판단 로직을 건드리지 않았는가?
- [ ] 주문/실시간 API 호출을 테스트에 포함하지 않았는가?
- [ ] 문서 변경 후 실행 방법을 요약하여 리포트했는가?
