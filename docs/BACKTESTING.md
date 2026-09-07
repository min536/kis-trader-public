---
Purpose: 백테스팅 환경 및 실행 방법 요약
Read when: 백테스트 스크립트를 실행하거나 유니버스 데이터 수집 관련 작업 전
Do not use for: 실제 주문 실행 확인
---

# Backtesting (TL;DR)

## Universe 및 Data Fetch
- `run_full154_fetch.sh`: full154 유니버스 스캔 및 가격 데이터 백테스트용 페치 스크립트.
- `backtester/engine_backtest/cli.py`를 통해 데이터를 수집하여 `data/universe_batches/`에 저장하고 병합함.
- 주의: KIS API Rate Limit(초당 제한)이 존재하므로 백테스트 데이터 수집 시에도 `retry-backoff-seconds`를 충분히 주어야 함.

## Backtesting 실행
- `app/tools/` 및 `backtester/`의 스크립트들을 통해 수행.
- Compact Payload: 백테스트 시 KIS API 응답 속도 및 제한을 고려하여 필요한 정보만 추출하는 방식을 사용.
- Results: 결과 파일들은 주로 `data/` 혹은 `results/` 내에 JSON/CSV로 저장됨.
