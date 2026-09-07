# end-of-day review workflow

When asked to review a finished market day:

1. Inspect the latest relevant logs for the date:
   - candidate outcomes
   - cycle stats
   - orders
   - cycle snapshots
2. Run existing health/diagnostic tools when useful.
3. Summarize:
   - final_candidate count
   - executed count
   - trade_budget_limited count
   - budget_rescue_applied count
   - core_rescue_applied count
   - core deep_evaluated count
   - rate-limit / sell partial pressure
4. Explain the biggest remaining bottleneck.
5. Recommend the next smallest safe fix or next diagnostic step.