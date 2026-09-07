# kis-trader project rules

- Do not change live trading logic broadly unless explicitly requested.
- Prefer the smallest safe fix over broad refactors.
- Keep buy/sell strategy changes narrowly scoped and explain risk clearly.
- Reuse existing logs, diagnostics tools, and dataset exporters whenever possible.
- Preserve existing analysis pipeline:
  - signal dataset
  - signal outcome dataset
  - EOD health check
  - postrun diagnostics
- Do not increase API-heavy load unless explicitly justified.
- Keep fixes auditable with explicit logging fields.
- Prefer offline diagnostics and replay tooling before changing live rules.
- Avoid changing multiple bottlenecks at once.
- For generated data, prefer:
  - raw logs in JSONL
  - analysis datasets in Parquet
  - CSV only as optional export