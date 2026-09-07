# symbol name consistency audit

> **Status: CODE/NAME AUDIT WITH HISTORICAL TAG SNAPSHOT.** The duplicate-name
> conclusion remains current: `307950=현대오토에버` and `057050=현대홈쇼핑`.
> Tag examples that mention `market:etf` came from the pre-`asset` migration
> snapshot; the current canonical tag schema is `SYMBOL_TAGS.md` (not included in this source snapshot).

## 목적

`config/symbol_tags.yaml`의 전체 157개 symbol에 대해 code/name 매핑이 repo 내부의 다른 symbol map 및 data 등장 정보와 일관되는지 read-only로 점검한다.

이번 감사는 문서화만 수행한다. `config/symbol_tags.yaml`, `app/scanner/symbol_names.py`, tests, runtime, scanner, strategy 코드는 수정하지 않는다.

주의: code/name 결론은 현재 기준으로 유지된다. 직전 작업에서 `057050`의 name이 `현대홈쇼핑`으로 정정된 상태라, 현재 `config/symbol_tags.yaml`에는 `307950`/`057050` duplicate name이 남아 있지 않다. 다만 `market:etf`가 등장하는 tag 예시는 작성 당시 snapshot이며 현재 schema를 뜻하지 않는다.

## 조사 범위

code/name source:

- `config/symbol_tags.yaml`
- `app/scanner/symbol_names.py`

code presence source:

- `data/prices_scan_symbols_*.csv`
- `data/universe_batches/**/*.csv`
- `data/live_snapshot.json`
- `data/runtime_state.json`
- `docs/**/*.md`
- `tests/**/*.py`
- `scripts/**/*.sh`
- `scripts/**/*.py`
- `app/**/*.py`

가격 CSV와 universe CSV는 name을 포함하지 않으므로 code presence 근거로만 사용한다. 외부 웹/API 호출은 하지 않았다.

## code/name source 목록

| Source | Type | Count | Note |
|--------|------|------:|------|
| `config/symbol_tags.yaml` | code -> name | 157 | 전체 symbol metadata source |
| `app/scanner/symbol_names.py` | code -> name | 36 | scanner/live display용 core/rotating/exploration map |

기타 repo 내부에서 독립적인 정적 `code -> Korean name` map은 확인되지 않았다. runtime/dashboard/reporting 파일은 대부분 `app.scanner.symbol_names.get_symbol_name()`을 fallback으로 사용하거나 runtime snapshot의 `symbol_name` 필드를 전달한다.

## 전체 요약

| Check | Result |
|-------|--------|
| `symbol_tags.yaml` symbol 수 | 157 |
| `symbol_names.py` symbol 수 | 36 |
| 양쪽 공통 code 수 | 36 |
| 공통 code의 name 불일치 | 0 |
| duplicate name in `symbol_tags.yaml` | 0 |
| duplicate name in `symbol_names.py` | 0 |
| `symbol_tags.yaml`에는 있으나 `symbol_names.py`에는 없는 code | 121 |
| `symbol_names.py`에는 있으나 `symbol_tags.yaml`에는 없는 code | 0 |
| 가격/universe CSV에는 있으나 `symbol_tags.yaml`에는 없는 code | 0 |
| `symbol_tags.yaml`에는 있으나 가격/universe CSV에는 없는 code | 4 |
| name empty/placeholder 후보 | 0 |

## duplicate name 후보

현재 working tree 기준 duplicate name 후보는 없다.

| Source | Duplicate candidates |
|--------|----------------------|
| `config/symbol_tags.yaml` | 없음 |
| `app/scanner/symbol_names.py` | 없음 |

이전 감사에서 문제였던 `307950`/`057050` duplicate는 현재 working tree 기준으로는 해소되어 있다.

## source 간 name 불일치 후보

`config/symbol_tags.yaml`과 `app/scanner/symbol_names.py`에 모두 존재하는 36개 code를 비교한 결과, name 불일치 후보는 없다.

| code | config name | symbol_names.py name | status |
|------|-------------|----------------------|--------|
| 36 common codes | - | - | all matched |

## symbol_tags에는 있으나 symbol_names에는 없는 code

121개 code가 `config/symbol_tags.yaml`에는 있으나 `app/scanner/symbol_names.py`에는 없다.

이 자체가 오류는 아니다. `symbol_names.py`는 36개 scan universe 중심의 display map이고, `symbol_tags.yaml`은 full154에 가까운 metadata 파일이다. 다만 이 121개는 `symbol_tags.yaml`의 name이 repo 내부 다른 정적 name map으로 교차검증되지 않는다는 뜻이다.

```text
000080, 000100, 000120, 000150, 000720, 000880, 001040, 001450, 001680, 002380,
002790, 003230, 003490, 003550, 004000, 004170, 004370, 005180, 005490, 005940,
006260, 006360, 006400, 006800, 007310, 008560, 008770, 009150, 009830, 010060,
010130, 010950, 011170, 011210, 011780, 011790, 012330, 014680, 015760, 016360,
018260, 018880, 021240, 023530, 028050, 028300, 032640, 033780, 034220, 034230,
034730, 035250, 035900, 036460, 036570, 036830, 039030, 039490, 041510, 042660,
042700, 047040, 047050, 051600, 051900, 053800, 057050, 058470, 064350, 064400,
066970, 069620, 069960, 071050, 078930, 079550, 082740, 086280, 086520, 086900,
088350, 090430, 091160, 091180, 096770, 097950, 102110, 103140, 117700, 122630,
128940, 131970, 139260, 139480, 145020, 157490, 185750, 192820, 207940, 214150,
227540, 229200, 233740, 240810, 241560, 251270, 259960, 261220, 271560, 272210,
278540, 293490, 302440, 305540, 305720, 310970, 323410, 326030, 352820, 357780,
377300
```

## symbol_names에는 있으나 symbol_tags에는 없는 code

없다.

`app/scanner/symbol_names.py`의 36개 code는 모두 `config/symbol_tags.yaml`에도 존재한다.

## data에는 있으나 tags에는 없는 code

가격/universe CSV 기준으로는 없다.

`data/prices_scan_symbols_*.csv`와 `data/universe_batches/**/*.csv`에 등장하는 code는 모두 `config/symbol_tags.yaml`에 존재한다.

별도 참고로, `data/live_snapshot.json`에는 live top/ranking source 성격의 59개 code가 있으며 이 중 52개는 `symbol_tags.yaml`에 없다. 이 파일은 현재 시장 snapshot 후보를 담는 runtime data라 full154 metadata coverage와 직접 일치하지 않는다.

`data/live_snapshot.json`에 있으나 `symbol_tags.yaml`에는 없는 code:

```text
000500, 001440, 002020, 003470, 004710, 006340, 006345, 010170, 011000, 012200,
012205, 012860, 013000, 019010, 036710, 041830, 044820, 048770, 052330, 061090,
067170, 076610, 078150, 079190, 084650, 086960, 090710, 092190, 114800, 166480,
192410, 222040, 223250, 229000, 251340, 252670, 252710, 264850, 265520, 291680,
298340, 304670, 319400, 320000, 323350, 336570, 396500, 429270, 452200, 456200,
462330, 900300
```

## tags에는 있으나 data에는 없는 code

가격/universe CSV 기준으로 4개 code가 `config/symbol_tags.yaml`에는 있으나 `data/prices_scan_symbols_*.csv`와 `data/universe_batches/**/*.csv`에는 확인되지 않았다.

| code | name | config tags summary | audit note |
|------|------|---------------------|------------|
| 008560 | 메리츠증권 | market:kospi, sector:securities, cap:mid, universe:extended | 가격/universe CSV 미등장. external verification required |
| 055550 | 신한지주 | market:kospi, sector:finance, universe:core | core인데 가격/universe CSV 미등장. source coverage 확인 필요 |
| 086790 | 하나금융지주 | market:kospi, sector:finance, universe:core | core인데 가격/universe CSV 미등장. source coverage 확인 필요 |
| 105560 | KB금융 | market:kospi, sector:finance, universe:core | core인데 가격/universe CSV 미등장. source coverage 확인 필요 |

해석:

- 이 4개가 곧 code/name 오류라는 뜻은 아니다.
- 다만 `universe:core`인 금융지주 3개가 가격/universe CSV에 없다는 점은 data snapshot 범위와 config universe 의미가 어긋나는 신호일 수 있다.
- `008560 메리츠증권`은 code/name 자체도 외부 확인이 필요하다. repo 내부에는 `symbol_tags.yaml` 외 정적 name 근거가 없다.

## live_snapshot/runtime_state 검토

구조화된 JSON 파싱 기준:

| Source | symbol-like code count | tags에 없는 code | 307950 | 057050 |
|--------|-----------------------:|-----------------:|--------|--------|
| `data/live_snapshot.json` | 59 | 52 | 등장 | 미등장 |
| `data/runtime_state.json` | 30 | 0 | 등장 | 미등장 |
| `data/runtime_state_mock_12345678_01.json` | 0 | 0 | 미등장 | 미등장 |

`live_snapshot.json`은 시장 ranking/top symbol snapshot 성격이라 `symbol_tags.yaml` coverage와 직접 비교해 오류로 보기는 어렵다.

`runtime_state.json`에 등장하는 symbol-like code 30개는 모두 `symbol_tags.yaml`에 존재한다.

## ETF 이름과 stock 이름 혼재 여부

작성 당시 `market:etf` 6개는 모두 ETF 브랜드/상품명처럼 보였다. 현재 config에서는 ETF를 상장시장 `market`과 `asset:etf`로 표현한다.

| code | name | note |
|------|------|------|
| 069500 | KODEX 200 | ETF name |
| 117700 | ARIRANG 200 | ETF name |
| 122630 | KODEX 레버리지 | ETF name |
| 157490 | TIGER 은행 | ETF name |
| 305540 | TIGER 2차전지테마 | ETF name |
| 305720 | KODEX 2차전지산업 | ETF name |

ETF name과 stock name이 명백히 섞인 후보는 발견되지 않았다. 작성 당시 `market:etf` 자체는 schema migration plan에서 다룬 대로 상장시장과 상품 유형의 의미가 혼재된 상태였고, 이 schema drift는 이후 해소됐다.

## 057050/307950 특별 검토

### 307950 repo 내부 근거

| Source | Evidence |
|--------|----------|
| `config/symbol_tags.yaml` | `307950` name = `현대오토에버` |
| `app/scanner/symbol_names.py` | `307950` name = `현대오토에버` |
| 가격/universe CSV | `307950` 별도 가격 시계열 존재 |
| `data/live_snapshot.json` | `307950` 등장 |
| `data/runtime_state.json` | `307950` 등장 |

repo 내부에서는 `307950 = 현대오토에버`가 가장 강하게 지지된다.

### 057050 repo 내부 근거

| Source | Evidence |
|--------|----------|
| `config/symbol_tags.yaml` | 현재 working tree 기준 `057050` name = `현대홈쇼핑` |
| `app/scanner/symbol_names.py` | `057050` 없음 |
| 가격/universe CSV | `057050` 별도 가격 시계열 존재 |
| `data/live_snapshot.json` | 미등장 |
| `data/runtime_state.json` | 미등장 |

repo 내부에는 `057050`의 name을 교차검증할 정적 symbol map이 없다. 현재 name인 `현대홈쇼핑`은 사용자가 제공한 외부 확인 근거에 의해 정정된 값이다.

### 결론

- 현재 working tree 기준으로 `307950`/`057050` duplicate name 문제는 해소되어 있다.
- repo 내부만 보면 `307950 = 현대오토에버`는 확정에 가깝다.
- repo 내부만으로 `057050 = 현대홈쇼핑`을 독립 확정할 수는 없다.
- 다만 이전 상태의 `057050 = 현대오토에버`는 repo 내부에서도 근거가 약했고, 현재 정정 방향은 타당한 high-confidence correction으로 볼 수 있다.

## high-confidence 수정 후보

현재 working tree 기준 high-confidence code/name 수정 후보:

| code | current name | candidate action | confidence | reason |
|------|--------------|------------------|------------|--------|
| 057050 | 현대홈쇼핑 | 이미 정정됨. commit 전 검증 대상 | high | 이전 duplicate name 문제의 원인. 사용자가 외부 확인 제공 |

추가로 즉시 수정할 high-confidence 후보는 발견되지 않았다.

## external verification required 후보

repo 내부만으로 name을 독립 검증하기 어려운 후보:

| Category | Codes | Reason |
|----------|-------|--------|
| `symbol_tags.yaml`에는 있으나 `symbol_names.py`에는 없는 121개 | 위 목록 참조 | 정적 name cross-check source가 없음 |
| 가격/universe CSV에 없는 tags code | 008560, 055550, 086790, 105560 | data coverage 또는 code/name 정합성 확인 필요 |
| live snapshot에 있으나 tags에 없는 code | 52개 | live ranking source coverage 확장 여부 판단 필요 |

특히 우선 확인 후보:

| code | name | reason |
|------|------|--------|
| 008560 | 메리츠증권 | 가격/universe CSV 미등장, `symbol_names.py` 미등장 |
| 055550 | 신한지주 | core인데 가격/universe CSV 미등장 |
| 086790 | 하나금융지주 | core인데 가격/universe CSV 미등장 |
| 105560 | KB금융 | core인데 가격/universe CSV 미등장 |

## 자동 수정하면 안 되는 항목

- `symbol_names.py`에 없는 121개 code를 자동으로 삭제하지 않는다.
- 가격/universe CSV에 없는 4개 code를 자동으로 삭제하지 않는다.
- live snapshot에만 등장하는 52개 code를 자동으로 `symbol_tags.yaml`에 추가하지 않는다.
- runtime JSON이나 docs의 과거 audit 문구를 name source로 간주하지 않는다.
- 외부 확인 없이 code/name을 회사명 유추로 바꾸지 않는다.
- symbol name audit과 sector/theme/asset schema migration을 같은 변경으로 섞지 않는다.

## 권장 다음 단계

1. 현재 `057050` name 정정 diff를 별도 커밋으로 보존한다.
2. `PYTHONPATH=. python3 -m app.tools.validate_symbol_tags`와 관련 tests를 통과시킨 뒤 커밋한다.
3. 이후 별도 작업에서 `008560`, `055550`, `086790`, `105560`의 data coverage/종목명 정합성을 확인한다.
4. 필요하면 `symbol_names.py`의 역할을 36개 scan display map으로 유지할지, `symbol_tags.yaml` name을 fallback source로 더 적극적으로 사용할지 문서화한다.
5. live snapshot에 자주 등장하지만 tags에 없는 code를 metadata coverage 확장 후보로 분리해 검토한다.

## 실행 방법 요약

이번 감사에서 사용한 로컬/read-only 명령:

```bash
git status --short
git branch --show-current
git status -sb
rg ...
python3 ...
git diff --check
```

이번 작업에서 하지 않은 것:

- API 호출 없음
- 외부 웹 호출 없음
- `app.main` 실행 없음
- config/code/tests 수정 없음
- commit/push 없음
