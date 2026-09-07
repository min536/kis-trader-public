---
Purpose: W4 implementation/contract notes — slippage measurement infra + cost-policy single source.
Read when: running measure_slippage, reviewing W4, or executing the operator-gated cost realignment.
Status: commit ② (slippage infra) + ③ (cost single source) DONE + adversarial-verified. Numbers operator-gated.
Date: 2026-06-13
---

# W4 — Slippage measurement + cost-policy unification (PR after PR-2/PR-3)

> Grounding: [five_day_plan_replan_20260611.md](five_day_plan_replan_20260611.md) §1 (Day2),
> §4 "W2+W4", §5. Unblocked by W2 price capture. **Invariant (§4/§5): this round measures
> only — it does NOT change effective cost numbers; applying numbers is an operator gate.**

## Commit ② — slippage measurement infra (code)

| file | role | mine? |
|------|------|-------|
| slippage_report.py (not included in this source snapshot) | dataclasses (`SlippageReportSummary`/`SlippageSideSummary`) + `format_slippage_report` text formatter | aux-authored (uncommitted) |
| slippage_measure.py (not included in this source snapshot) | `build_slippage_report_summary(records, …)` — records → summary | yes |
| measure_slippage.py (not included in this source snapshot) | CLI: bounded order-log read → summary → text. No broker. | yes |

As-built note: the plan named one analysis module; it shipped as three (formatter / aggregator /
CLI) — cleaner separation. The policy-unification regression lives in
test_cost_policy.py (not included in this source snapshot) (plan said `test_backtest_portfolio.py`).

### ⚠️ Slippage scope is SUBMIT-SIDE only — reads ~0 today (not a real fill measurement)

Slippage bps = `(execution − reference) / reference × 10000`, sign-adjusted so **adverse is
positive** (buy filled above reference, sell filled below). `execution = quote_at_submit`,
`reference = reference_price_krw` (both W2 fields).

**Today `quote_at_submit == reference_price_krw`** (no re-quote between decision and submit, and
the order-cash ack carries no fill price — see [w2_price_capture_notes.md](w2_price_capture_notes.md)),
so every production sample is exactly `0.00bps`. The infra is correct and ready; **real fill
slippage needs a live-shadow / fill source — an operator gate** (mock fills are immediate market
fills). `missing_reference_count` measures coverage (filled orders lacking a usable reference).

The CLI prints an explicit scope note so `0.00bps … within policy` is never misread as a clean
real-execution result (the W2 overclaim trap). `measure_slippage --date YYYY-MM-DD` filters to a
KST day (validated); default = today, all records.

## Commit ③ — cost-policy single source (code, value-preserving)

`shadow_watch.py` and `run_proposal_backtest.py` each hardcoded the backtester REST cost params.
costs.py (not included in this source snapshot) now centralizes them in **one immutable source**
`LEGACY_BACKTEST_COST_PARAMS` (current values), and both tools reference it — **byte-identical
numbers, zero behavior change** (adversarially confirmed `json.dumps(old)==json.dumps(new)`; the
autotuner approval line receives the same payloads). `canonical_backtest_cost_params(settings)` is
the settings-aligned **migration target**, deliberately **unused** (wiring it changes numbers →
operator gate).

### Cost reconciliation — tool legacy vs live-canonical (the operator's decision)

| param | legacy (tools today) | canonical (settings) | delta |
|-------|---------------------|----------------------|-------|
| commission_rate | 0.00015 (1.5 bps) | `sell_fee_bps`/1e4 = 0.00015 (1.5 bps) | **none** |
| tax_rate | 0.0023 (23 bps) | `sell_tax_bps`/1e4 = 0.0015 (15 bps) | **−8 bps** |
| slippage | 0.001 (10 bps) | `sell_slippage_bps`/1e4 = 0.0005 (5 bps) | **−5 bps** |

### Operator-gated migration (do NOT apply autonomously)

Canonical is **cheaper** → backtested proposal PnL **rises** → **the autotuner B-1 approval line
must be re-run; prior passes are stale** (§3 operator gate, §5 "운영자 결정 → 별도 risk-gate PR").
To adopt, swap `LEGACY_BACKTEST_COST_PARAMS` → `canonical_backtest_cost_params(get_settings())` at
**all three** sites:

1. shadow_watch.py (not included in this source snapshot) — `**LEGACY_BACKTEST_COST_PARAMS` in the payload.
2. run_proposal_backtest.py (not included in this source snapshot) `_call_bt_api` — the
   `commission_rate`/`tax_rate` `params.get(..., LEGACY[...])` defaults + the `slippage` value.
3. run_proposal_backtest.py (not included in this source snapshot) `_load_baseline_config` —
   the `commission_rate`/`tax_rate` fallback strings.

Note the canonical mapping collapses the two-sided settings (buy/sell fee & slippage) onto the
**sell** leg, because the backtester takes single-value params; buy==sell under the defaults.
Confirming that collapse is part of the operator's gated review.

## Adversarial verification (2026-06-13)

3 parallel refute-mandate skeptics (slippage math / cost-rewire safety / completeness):

- **Slippage math: UPHELD** — sign convention, `_percentile` (== numpy linear), counting,
  `sample_count`, and formatter composition all correct. Found the **honesty gap** (the CLI output
  lacked the submit-side caveat) → fixed (scope note). Plus `--date` validation + a `-0.00bps`
  display normalization.
- **Cost rewire: VALUE-PRESERVING confirmed** — byte-identical payloads, config-override preserved,
  `canonical_*` genuinely unused, no import cycle. Hardened the shared source to immutable
  (`MappingProxyType`) to foreclose a future cross-tool mutation leak.
- **Completeness: UPHELD** — ②/③ complete; the value-preserving de-dup is the correct reading of
  "단일화 + 비용 비율 변경 안 함". Closed its two gaps: the last duplicate literal
  (`_load_baseline_config`) folded into the constant, and this doc (the operator-gate deliverable).

## Verification

- Full suite green: `.venv/bin/python -m pytest -q` → **2291 passed, 588 subtests** (exit 0).
- New tests: `test_slippage_measure.py` (5), `test_measure_slippage_cli.py` (6),
  `test_cost_policy.py` (3) — 14 tests / ~46 subtests.
- `app/main.py` untouched; no broker/order API touched. No cost **number** changed.
- The CLI test depends on the aux-authored `app/reporting/slippage_report.py` +
  `tests/test_slippage_report.py` (uncommitted) — they must land in the same PR.
