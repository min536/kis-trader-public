# symbol master validation plan

> **Status: PARTIALLY IMPLEMENTED.** The offline validator exists:
> `app/tools/validate_symbol_master.py` with `tests/test_validate_symbol_master.py`.
> The networked vendor snapshot fetch script described below
> (`scripts/fetch_kis_stocks_info_snapshot.py`) is still a future/manual step.
> Use this document as the design record plus fetch-runbook seed, not as proof
> that a fresh vendor snapshot has already been collected.

## 목적

KIS open-trading-api의 stocks_info 스크립트를 참조해 공식 종목 마스터 데이터를 수동 fetch하고, `config/symbol_tags.yaml`의 code/name/market/asset 정합성을 오프라인으로 검증하는 절차를 정리한다.

현재 `symbol_tags.yaml`은 157개 종목(asset:stock 151, asset:etf 6)을 관리하며, 최근 `057050` 종목명 오류 수정 사례에서 보듯 code/name 불일치를 사전에 자동 감지하는 체계가 필요하다. repo 내부 정적 cross-check source(`symbol_names.py`)가 36개에 한정되어 121개 종목의 name이 교차 검증되지 않는 상태다.

---

## KIS stocks_info에서 활용할 파일

### 데이터 소스 구조

stocks_info의 Python 파일은 정적 데이터 파일이 아니다. 각 스크립트가 KIS DWS 서버(`new.real.download.dws.co.kr`)에서 `.mst.zip` 파일을 다운로드하고, 압축 해제 후 cp949 인코딩 고정폭 바이너리 파일을 pandas DataFrame으로 파싱하는 구조다.

### 활용 대상 파일

| 파일 | 다운로드 URL | 용도 | 우선도 |
|:-----|:-------------|:-----|:------:|
| `kis_kospi_code_mst.py` | `https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip` | KOSPI 종목 마스터 | 필수 |
| `kis_kosdaq_code_mst.py` | `https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip` | KOSDAQ 종목 마스터 | 필수 |
| `kis_konex_code_mst.py` | `https://new.real.download.dws.co.kr/common/master/konex_code.mst.zip` | KONEX 종목 마스터 | 참고 |
| `sector_code.py` | `https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip` | 업종코드 → 업종명 | 선택 |
| `theme_code.py` | `https://new.real.download.dws.co.kr/common/master/theme_code.mst.zip` | 테마코드 + 종목 매핑 | 선택 |

---

## 각 파일이 제공하는 데이터

### kospi_code.mst / kosdaq_code.mst

파일 구조: Part1 (가변폭: 단축코드 + 표준코드 + 한글명) + Part2 (고정폭: 228바이트/222바이트 메타데이터).

| 필드 | 위치 | 검증 활용 |
|:-----|:-----|:----------|
| 단축코드 | Part1[0:9] | code 매칭 |
| 표준코드 | Part1[9:21] | ISIN, 참고용 |
| 한글명 | Part1[21:] | name 검증의 핵심 |
| 시가총액규모 | Part2 | cap 태그 교차 검증 |
| 지수업종대분류 | Part2 | sector 매핑 후보 |
| 지수업종중분류 | Part2 | sector 상세 매핑 |
| ETP | Part2 | asset:etf 교차 검증 |
| SPAC | Part2 | 제외 대상 필터 |
| 거래정지 | Part2 | 매매 불가 경고 |
| 관리종목 | Part2 | 위험 종목 경고 |
| KRX반도체/바이오 등 | Part2 | sector 교차 검증 |
| 상장일자 | Part2 | 참고용 |
| 시가총액 | Part2 | cap 규모 교차 검증 |

market 구분: kospi_code.mst에 존재하면 `market:kospi`, kosdaq_code.mst에 존재하면 `market:kosdaq`.

### konex_code.mst

KONEX 종목 마스터. 우리 symbol_tags에 KONEX 종목이 없으므로 "tags에는 있으나 KOSPI/KOSDAQ master에 없는 code"가 KONEX에 속하는지 확인하는 fallback 용도.

### idxcode.mst (sector_code.py)

업종코드(4자리) → 업종명 매핑만 제공. 종목별 업종은 kospi/kosdaq .mst의 `지수업종대분류`, `지수업종중분류`, `지수업종소분류`와 JOIN해야 도출된다.

### theme_code.mst (theme_code.py)

테마코드(3자리) + 테마명 + 종목코드 3-tuple. 종목 → 테마 역매핑이 가능하므로 sector:etc 개선 후보 생성과 theme 태그 교차 검증에 활용 가능.

---

## 직접 다운로드를 validation에 붙이면 안 되는 이유

1. **네트워크 의존성**: DWS 서버가 장 외 시간에 접속 불가하거나 일시적으로 다운될 수 있다. validation이 외부 네트워크에 의존하면 CI/로컬 테스트가 불안정해진다.
2. **재현성 부재**: 같은 날 두 번 실행해도 장중/장후 데이터가 달라질 수 있다. 검증 결과가 시점에 따라 달라지면 diff 추적이 어렵다.
3. **SSL 우회 필요**: 원본 스크립트가 `ssl._create_unverified_context`를 사용한다. 보안 정책상 검증 도구에서 SSL 검증을 비활성화하는 것은 바람직하지 않다.
4. **부작용 격리**: fetch 실패가 validation 실패로 이어지면 안 된다. 데이터 수급과 검증은 분리해야 한다.
5. **KIS DWS ≠ KIS Open API**: DWS 서버는 API rate limit과 무관하지만, 그래도 validation마다 수백KB zip을 다운로드하는 것은 불필요하다.

---

## 추천 구조

```
fetch (수동, 네트워크)          validate (자동, 오프라인)
┌─────────────────────┐      ┌───────────────────────────────┐
│ scripts/             │      │ app/tools/                    │
│   fetch_kis_stocks_  │      │   validate_symbol_master.py   │
│   info_snapshot.py   │ ──→  │                               │
│                      │      │ reads: data/vendor/kis_...    │
│ downloads .mst.zip   │      │ reads: config/symbol_tags.yaml│
│ parses → CSV         │      │ output: console report        │
│ saves to data/vendor │      │ exit code: 0=pass, 1=issues   │
└─────────────────────┘      └───────────────────────────────┘
```

### scripts/fetch_kis_stocks_info_snapshot.py

```
역할: KIS DWS 서버에서 종목 마스터 다운로드 → CSV 변환 → vendor snapshot 저장
실행: python scripts/fetch_kis_stocks_info_snapshot.py
빈도: 월 1회 또는 종목명 변경 / 상장폐지 의심 시
네트워크: 필요 (수동 실행 시만)
```

주요 동작:
1. kospi_code.mst.zip, kosdaq_code.mst.zip 다운로드
2. cp949 고정폭 바이너리 파싱 (kis_kospi_code_mst.py, kis_kosdaq_code_mst.py 로직 참조)
3. 필요 필드만 추출: 단축코드, 한글명, ETP여부, 시가총액규모, 업종대/중/소분류, 거래정지, 관리종목
4. `data/vendor/kis_stocks_info/YYYYMMDD/kospi.csv`, `kosdaq.csv` 로 저장
5. 선택적으로 idxcode.mst, theme_code.mst도 fetch → `sector.csv`, `theme.csv`
6. macOS 호환 (원본은 Windows 경로 `\\` 사용 — `os.path.join`으로 변환)
7. 임시 파일(.zip, .mst, .tmp)은 완료 후 삭제

### app/tools/validate_symbol_master.py

```
역할: vendor snapshot CSV + config/symbol_tags.yaml 교차 검증
실행: python -m app.tools.validate_symbol_master
      python -m app.tools.validate_symbol_master --snapshot-dir data/vendor/kis_stocks_info/20260512
네트워크: 불필요
```

주요 동작:
1. vendor snapshot 디렉토리에서 최신(또는 지정) kospi.csv, kosdaq.csv 로드
2. config/symbol_tags.yaml 로드
3. 검증 항목별 결과 출력
4. severity에 따라 exit code 결정

---

## vendor snapshot 저장 위치

```
data/
└── vendor/
    └── kis_stocks_info/
        └── 20260512/           ← fetch 날짜 기준
            ├── kospi.csv       ← KOSPI 종목 마스터
            ├── kosdaq.csv      ← KOSDAQ 종목 마스터
            ├── sector.csv      ← 업종코드 (선택)
            ├── theme.csv       ← 테마코드+종목 (선택)
            └── metadata.json   ← fetch 시각, 소스 URL, row count
```

`.gitignore` 정책:
- `data/vendor/` 디렉토리는 `.gitignore`에 추가한다.
- vendor snapshot은 repo에 commit하지 않는다.
- 검증 결과 리포트만 docs에 필요시 commit한다.

---

## 검증 항목

### 1. code/name mismatch (severity: **error**)

symbol_tags.yaml의 `name`과 KIS 마스터의 `한글명`이 다른 경우.

```
예: symbol_tags에서 057050=현대오토에버, master에서 057050=현대홈쇼핑
→ ERROR: code 057050 name mismatch | tags=현대오토에버 | master=현대홈쇼핑
```

정규화 규칙:
- 양쪽 모두 strip() 적용
- 공백 정규화 (연속 공백 → 단일 공백)
- 후행 숫자 접미사 무시하지 않음 (우선주 구분 등)

### 2. market mismatch (severity: **error**)

symbol_tags.yaml의 `market:kospi`인 종목이 kosdaq.csv에만 존재하거나, `market:kosdaq`인 종목이 kospi.csv에만 존재하는 경우.

```
예: symbol_tags에서 196170=market:kosdaq, 그런데 kospi.csv에 있음
→ ERROR: code 196170 market mismatch | tags=kosdaq | master=kospi
```

### 3. asset:etf mismatch (severity: **warning**)

symbol_tags.yaml에서 `asset:etf`인 종목이 마스터의 ETP 플래그가 아니거나, `asset:stock`인 종목이 ETP=Y인 경우.

```
예: symbol_tags에서 069500=asset:stock, master ETP=Y
→ WARNING: code 069500 asset mismatch | tags=stock | master=ETP
```

### 4. tags에는 있으나 official master에 없는 code (severity: **error**)

symbol_tags.yaml에 등록되어 있으나 kospi.csv, kosdaq.csv 어디에도 없는 코드.

```
→ ERROR: code XXXXXX in symbol_tags but not in any KIS master | suspected delisted or code error
```

KONEX master에 있는 경우:
```
→ WARNING: code XXXXXX found in KONEX master only | symbol_tags market tag may need review
```

### 5. official master에는 있으나 tags에 없는 code (severity: **info**)

KIS 마스터에 있으나 symbol_tags.yaml에 등록되지 않은 코드. 전체 출력은 불필요하고 시가총액 상위 종목만 info로 표시.

```
→ INFO: 시가총액 상위 50 중 symbol_tags에 없는 code: XXXXXX (한글명)
```

### 6. suspected delisted/renamed (severity: **error**)

검증 4번의 확장. master에 없으면서 code 형식이 정상(6자리 숫자)인 경우 상장폐지 또는 종목 코드 변경 의심.

### 7. 거래정지/관리종목 경고 (severity: **warning**)

symbol_tags.yaml에 등록된 종목이 마스터에서 거래정지=Y 또는 관리종목=Y인 경우.

```
→ WARNING: code XXXXXX 거래정지 | name=한글명
→ WARNING: code XXXXXX 관리종목 | name=한글명
```

### 8. sector:etc 개선 후보 (severity: **info**)

symbol_tags.yaml에서 `sector:etc`인 종목에 대해 마스터의 업종 분류 + 테마 코드 기반으로 sector 후보를 제안.

```
→ INFO: code 057050 sector:etc → 업종대분류=유통, 테마=홈쇼핑 | sector 후보: retail
```

현재 sector:etc 27개가 대상.

### 9. theme 후보 (severity: **info**)

theme_code.mst에서 종목이 속한 테마 목록을 출력하고, symbol_tags.yaml의 theme 태그와 비교.

```
→ INFO: code 005930 KIS themes=[반도체, AI반도체, HBM] | tags themes=[ai, hbm] | 추가 후보: 반도체
```

---

## severity 정책

| Severity | 의미 | exit code | 조치 |
|:---------|:-----|:---------:|:-----|
| **error** | code/name/market 불일치 또는 마스터 미등록 | 1 | 즉시 확인 및 수정 필요 |
| **warning** | asset 불일치, 거래정지, 관리종목 등 | 0 | 확인 후 판단 |
| **info** | sector/theme 개선 후보, coverage gap | 0 | 참고 사항 |

exit code 규칙:
- error가 1건 이상이면 exit 1
- warning/info만이면 exit 0

---

## 자동 수정 금지 항목

validator는 리포트만 출력하며 어떤 파일도 수정하지 않는다.

1. ❌ symbol_tags.yaml의 name을 마스터 기준으로 자동 변경하지 않는다.
2. ❌ symbol_tags.yaml에서 마스터 미등록 code를 자동 삭제하지 않는다.
3. ❌ 마스터에 있으나 tags에 없는 code를 자동 추가하지 않는다.
4. ❌ market/asset 태그를 자동으로 변경하지 않는다.
5. ❌ sector:etc를 자동으로 다른 sector로 변경하지 않는다.
6. ❌ theme 태그를 자동으로 추가/삭제하지 않는다.
7. ❌ vendor snapshot CSV를 자동으로 다운로드하지 않는다.

이유: 종목명 변경, 합병, 코드 이관 등의 맥락을 사람이 판단해야 안전하다.

---

## 기존 도구와의 관계

| 도구 | 역할 | 데이터 소스 |
|:-----|:-----|:------------|
| `app/tools/validate_symbol_tags.py` | YAML 구조/문법 검증 (namespace, value, 중복) | config/symbol_tags.yaml만 |
| `app/tools/validate_symbol_master.py` | 외부 마스터 기준 내용 검증 (code/name/market/asset) | config/symbol_tags.yaml + vendor snapshot CSV |

두 도구는 독립적으로 실행하며, 순차 실행 시 validate_symbol_tags → validate_symbol_master 순서를 권장한다.

---

## 구현 일정 / 현재 disposition

초기 계획은 아래와 같았다. 현재는 validator와 테스트가 구현되어 있고, 남은 작업은 fetch script와 실제 vendor snapshot 수집이다.

| 단계 | 시점 | 내용 |
|:-----|:-----|:-----|
| 1 | 완료 | 설계 문서 작성 (이 문서) |
| 2 | 남음 | `scripts/fetch_kis_stocks_info_snapshot.py` 구현 |
| 3 | 남음 | 최초 vendor snapshot fetch 실행 |
| 4 | 완료 | `app/tools/validate_symbol_master.py` 구현 |
| 5 | snapshot 준비 후 | 첫 교차 검증 실행 및 결과 확인 |
| 6 | 결과 확인 후 | error 항목 수정, sector:etc 개선 검토 |

이유:
- 이 도구는 오프라인 검증 전용이며 운영 코드(`app/main.py`)에 영향 없다.
- 월요일 세션 전에 새 파일을 추가/커밋하는 것보다 세션 결과 확인 후가 안전하다.
- 현재 symbol_tags.yaml에 긴급한 오류가 있다는 증거는 없다.

---

## 구현 시 테스트 계획

### fetch 스크립트 테스트

fetch 스크립트는 네트워크 의존이므로 자동화 테스트 대상이 아니다. 수동 실행 후 결과를 확인한다.

확인 사항:
- kospi.csv, kosdaq.csv가 정상 생성되는지
- CSV에 단축코드, 한글명 컬럼이 있는지
- row count가 합리적인지 (KOSPI ~1000, KOSDAQ ~1700 정도)
- cp949 인코딩이 정상 변환되는지 (한글 깨짐 없음)
- 임시 파일(.zip, .mst, .tmp)이 삭제되는지

### validator 테스트

`tests/test_validate_symbol_master.py`:

| 테스트 | 검증 내용 |
|:-------|:----------|
| `test_name_mismatch_detected` | tags name ≠ master name → error 출력 |
| `test_market_mismatch_detected` | tags market:kospi인데 kosdaq에만 있음 → error |
| `test_missing_from_master_detected` | tags에 있으나 master에 없음 → error |
| `test_etf_mismatch_detected` | tags asset:stock인데 ETP=Y → warning |
| `test_clean_pass` | 모든 항목 일치 → exit 0 |
| `test_sector_etc_suggestion` | sector:etc인 종목에 업종 기반 후보 제안 → info |
| `test_name_normalization` | 공백 차이가 mismatch로 잡히지 않음 |
| `test_snapshot_not_found` | vendor snapshot 없으면 안내 메시지 + exit 0 |

테스트는 mock CSV 데이터를 사용하며 네트워크 호출이 없다.

---

## 실행 예시 명령

### fetch (수동, 네트워크 필요)

```bash
# 최초 snapshot 생성
python scripts/fetch_kis_stocks_info_snapshot.py

# 특정 날짜 디렉토리 지정
python scripts/fetch_kis_stocks_info_snapshot.py --output-dir data/vendor/kis_stocks_info/20260512

# sector/theme도 함께 fetch
python scripts/fetch_kis_stocks_info_snapshot.py --include-sector --include-theme
```

### validate (오프라인, 네트워크 불필요)

```bash
# 최신 snapshot 자동 탐색
PYTHONPATH=. python -m app.tools.validate_symbol_master

# 특정 snapshot 지정
PYTHONPATH=. python -m app.tools.validate_symbol_master \
  --snapshot-dir data/vendor/kis_stocks_info/20260512

# symbol_tags 경로 지정
PYTHONPATH=. python -m app.tools.validate_symbol_master \
  --tags-path config/symbol_tags.yaml \
  --snapshot-dir data/vendor/kis_stocks_info/20260512

# severity 필터
PYTHONPATH=. python -m app.tools.validate_symbol_master --min-severity warning
```

### 기존 validator와 순차 실행

```bash
# 구조 검증 → 마스터 교차 검증
PYTHONPATH=. python -m app.tools.validate_symbol_tags && \
PYTHONPATH=. python -m app.tools.validate_symbol_master
```

### 테스트 실행

```bash
PYTHONPATH=. pytest tests/test_validate_symbol_master.py -v
```

---

## 예상 출력 형식

```
============================================================
  Symbol Master Validation Report
  snapshot: data/vendor/kis_stocks_info/20260512
  tags: config/symbol_tags.yaml (157 symbols)
  master: kospi=987 kosdaq=1723
============================================================

[ERROR] code/name mismatch:
  057050 | tags=현대오토에버 | master=현대홈쇼핑

[ERROR] not in any KIS master:
  (none)

[ERROR] market mismatch:
  (none)

[WARNING] asset:etf mismatch:
  (none)

[WARNING] 거래정지:
  (none)

[WARNING] 관리종목:
  (none)

[INFO] sector:etc 개선 후보 (27개 중):
  000120 CJ대한통운 | 업종=운수창고 | sector 후보: logistics
  003490 대한항공   | 업종=운수창고 | sector 후보: logistics
  021240 코웨이     | 업종=서비스  | sector 후보: service
  ...

[INFO] theme 교차 검증 (참고):
  005930 삼성전자 | KIS themes=[반도체, AI반도체, HBM] | tags=[ai, hbm]
  ...

============================================================
  결과: 1 error, 0 warnings, 12 info
  exit code: 1
============================================================
```

---

## 이 문서에서 하지 않은 것

- code 구현 없음
- config/symbol_tags.yaml 수정 없음
- tests 수정 없음
- API 호출 없음
- 네트워크 다운로드 없음
- app.main 실행 없음
- commit/push 없음
