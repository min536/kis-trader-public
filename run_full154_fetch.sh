#!/bin/bash
set -e
source .venv/bin/activate
mkdir -p data/universe_batches

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 012450,047810,272210,082740,009540,329180,010140,042660,034020,267260,241560,051910,006400,003670,247540,373220,086520,066970,005490,196170,068270,207940,028300,128940,000100 --output data/universe_batches/batch_01.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 326030,302440,069620,185750,000270,005380,012330,064350,011210,161390,005930,000660,009150,058470,036830,014680,017670,030200,032640,035420,035720,323410,377300,028260,402340 --output data/universe_batches/batch_02.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 034730,003550,000880,096770,010950,078930,011170,009830,004000,002380,103140,066570,018260,034220,032830,000810,138040,316140,024110,006800,039490,071050,088350,005940,016360 --output data/universe_batches/batch_03.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 001450,008560,000720,047040,006360,028050,079550,259960,293490,036570,251270,352820,041510,035900,015760,036460,051600,003490,086280,000120,011200,023530,004170,069960,139480 --output data/universe_batches/batch_04.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 057050,271560,004370,033780,097950,003230,007310,000080,005180,090430,051900,192820,002790,307950,010130,047050,064400,018880,006260,001040,000150,034230,008770,069500,122630 --output data/universe_batches/batch_05.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 102110,091160,305720,305540,310970,091180,139260,227540,229200,042700,357780,240810,131970,011780,011790,010060,145020,214150,086900,021240,039030,053800,035250,001680,233740 --output data/universe_batches/batch_06.csv

python -m backtester.engine_backtest.cli fetch --start 2024-01-01 --end 2026-04-14 --mode best-effort --max-retries 3 --retry-backoff-seconds 2.0 --symbols 261220,278540,157490,117700 --output data/universe_batches/batch_07.csv

python -m app.tools.merge_price_csvs \
  data/universe_batches/batch_01.csv \
  data/universe_batches/batch_02.csv \
  data/universe_batches/batch_03.csv \
  data/universe_batches/batch_04.csv \
  data/universe_batches/batch_05.csv \
  data/universe_batches/batch_06.csv \
  data/universe_batches/batch_07.csv \
  --output data/prices_scan_symbols_full154.csv
