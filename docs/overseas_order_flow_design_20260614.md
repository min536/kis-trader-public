# Overseas (US Stock) Order Flow — Design

> Date: 2026-06-14
> Status: **IMPLEMENTED (Phases 0–6 complete)** — see Implementation Status below.
> Scope: build a full overseas order-flow mirroring the domestic pipeline, with the **gate2 v2 weighted scorer** as the selection model. Mock-only orders (live stays operator-gated, R7). US markets (NAS/NYS/AMS) only.
> Decisions locked by the user: **(1) adopt gate2 v2 weighted-sum scorer**, **(2) build a new US scanner**, **(3) neutral-fill the two volume-rank rules (keep the 15-condition structure)**.

## Implementation Status (2026-06-14)

All 7 phases shipped via TDD (full suite green: 2558 passed). Live order path remains operator-gated (R7). End-to-end verified: SELL-then-BUY cycle scans a US universe, selects the top gate2 v2 candidate, sizes (USD/whole-share), passes ET-clock order-guard + USD risk caps, and submits via the mock-gated `place_overseas_limit_order`.

| Phase | What shipped | Key modules |
|-------|--------------|-------------|
| 0 | gate2 leaf promoted research→runtime-eligible (shim left behind) | `app/gate2/{schema,score_v2,adapter}.py`, `app/gate2/artifacts/score_v2_us_w0.json` |
| 1 | USD snapshot + v1-style score_components (volume neutral=50) | `app/overseas_stock/{market_snapshot,scoring}.py` |
| 2 | gate2 v2 selection + US scanner + price-detail fetch | `app/overseas_stock/selection.py`, `market_data.fetch_overseas_price_detail`, `tr_ids.resolve_price_detail_tr_id` |
| 3 | USD sizing + sell sizing + marketable-limit price | `app/overseas_execution/{position_sizing,sell_sizing,limit_price}.py` |
| 4 | order_guard ET-clock injection (additive, risk-gated) + USD risk caps + US cost (no tax) + ET session | `app/execution/order_guard.py` (refactor), `app/overseas_execution/order_guard.py`, `app/overseas_risk/guards.py`, `app/overseas_runtime/cost.py`, `app/overseas_stock/session.py` |
| 5 | buy/sell flow orchestration + policy | `app/overseas_execution/{buy_flow,sell_flow,policy}.py` |
| 6 | SELL-then-BUY run_cycle + ET state writer + operator CLI | `app/overseas_runtime/{run_cycle,state}.py`, `app/tools/overseas_cycle.py` |

**Still deferred (per §7, not phases):** US weight-search calibration (the volume neutral-fill + default threshold is provisional), USD equity source for pnl-brake/regime, FX-native exposure model, sell-trigger strategy port (cycle takes an explicit `sell_plan`), mock holdings reliability.

---

## 1. Goal

Today the overseas side has only the **order-POST primitive** (`place_overseas_limit_order`, mock-gated, limit-only) plus quote/orderable/balance fetchers. It has **none** of the domestic selection→sizing→guard→risk→state pipeline, and is not wired into any `run_cycle`.

This design builds the missing six layers on top of the existing overseas I/O primitives, using **gate2 v2** (`final_score = Σ score_i·weight_i`) as the selection scorer — a model the domestic side has prototyped (research) but not yet adopted. So overseas deliberately **leads** domestic on the scorer; the design keeps the gate2 core as shared leaf code so domestic can adopt the same later.

## 2. Canonical domestic pipeline (the thing we mirror)

| # | Layer | Domestic module | Key fn |
|---|-------|-----------------|--------|
| 1 | Scan (quote + features) | `app/scanner/` | `scan_target_symbols`, `app/scanner/scoring.py` |
| 2 | Score / Select | `app/scanner/scoring.py` `service.py` | `calculate_selection_score` → `select_top_candidate` (v1 mixed scorer) |
| 3 | Size | `app/execution/position_sizing.py` | `calculate_position_sizing` (4-cap min) |
| 4 | Order guard | `app/execution/order_guard.py` | `evaluate_buy_order_guard` / `evaluate_sell_order_guard` |
| 5 | Risk guard | `app/risk/guards.py` | `evaluate_buy_risk_guards` / `evaluate_sell_risk_guards` |
| 6 | Order POST | `app/domestic_stock/order.py` | `buy_market` / `sell_market` (market, KRW) |
| 7 | State / Log / Slack | `app/core/order_log.py` + state | `log_order_event` + `record_cycle_action` + `mark_*` + slack |

Orchestrated SELL-then-BUY inside `run_cycle` (`app/main.py:732`).

## 3. Architecture Decision Records

### ADR-1 — Promote the gate2 v2 leaf out of `app/research/` → `app/gate2/`
**Why:** runtime must not import `app.research` (leaf-invariant in `quality_sentinel.py:25`, policy in `0611plan.md` §9). To use gate2 v2 in the overseas **runtime**, the pure scoring code must live in a runtime-eligible location.
**What:** move the stdlib-only trio `app/research/gate2/{schema,score_v2,adapter}.py` → `app/gate2/{schema,score_v2,adapter}.py`. They have **zero** non-sibling, non-test importers, so this is mechanical. Re-point the two research consumers (`weight_search.py`, `shadow_report.py`) and the 5 gate2 test files to `app.gate2`. Optionally leave `app/research/gate2/__init__.py` re-exporting from `app.gate2` so research entry points keep working.
**Result:** `app.gate2` is shared leaf code; both overseas runtime and (future) domestic runtime can import it; research keeps using it. No behavior change → existing gate2 tests stay green.

### ADR-2 — New packages mirroring `app/execution/` + `app/risk/`
```
app/overseas_execution/   # buy_flow, sell_flow, position_sizing, sell_sizing
app/overseas_risk/        # guards (USD daily caps); pnl_brake/regime DEFERRED
app/overseas_runtime/     # run_cycle orchestrator, runtime-state writer  (report.py already here)
```
New selection/scanner/snapshot/session/cost logic lands under `app/overseas_stock/` (the existing overseas I/O package). `app/main.py` only gains a thin import + call site if overseas is wired into the main loop — no net-new top-level defs (boundary hook).

### ADR-3 — USD representation: **float USD**, lean on broker-computed orderable
Domestic snapshots use `int` KRW prices (`MarketSnapshot.current_price: int`, `ExecutionSnapshot` all-int). US prices have cents → we use **`float` USD** in overseas snapshots/sizing (a new `OverseasMarketSnapshot`/`OverseasExecutionSnapshot`, not the domestic int dataclasses).
**FX:** rather than thread a USD↔KRW basis through all sizing math, the first cut leans on `fetch_overseas_orderable` which returns broker-computed `max_order_qty` + `orderable_cash` (already in account currency) + `fx_rate`. Sizing caps (per-trade budget, exposure) are expressed in **USD**. FX is surfaced/recorded but the broker's orderable is the binding cash constraint. (A full FX-native equity model is deferred — see §6.)

### ADR-4 — ET clock injection for the order guard
`order_guard.py` keys "today"/cooldowns on `get_korean_now()` (lines 60,104,115,151). A US session straddles two KST dates, so the guard must run on an **exchange-local (ET) clock**.
**Chosen:** refactor `evaluate_buy/sell_order_guard` (+ helpers) to accept an optional injected `now`/`clock` (default `get_korean_now` → domestic behavior unchanged, additive). Overseas passes an ET `now`.
**Gate:** `app/execution/order_guard.py` is the **trading-critical surface** → this refactor requires the `/risk-assessment` gate and all existing order_guard tests must stay green. (Alternative considered: duplicate the guard in `app/overseas_execution/` — rejected, ~400 lines of drift-prone duplication.)

### ADR-5 — Neutral-fill the two volume-rank conditions (= 50), with a US calibration step
`live_volume_rank_strength_score` / `live_volume_power_rank_strength_score` have **no US data source** (they come from a KIS *domestic* volume-rank TR). Per the user's choice we keep the 15-condition structure and feed a **neutral strength score = 50.0** for both, so the adapter yields `volume_rank_score = volume_power_score = 50`.
**Calibration caveat (must address before any real use):** in `default_artifact()` both carry weight **1.0**, so a constant 50 each contributes a flat **100** to every symbol — and `buy_threshold` is **60**. A constant ≥ threshold means the volume conditions (a) don't affect ranking but (b) by themselves nearly clear the gate. → The **US artifact** (`app/gate2/artifacts/score_v2_us_w0.json`) must either set the two volume weights to a deliberate value (e.g. 0 to neutralize, or a researched value) and/or re-derive `buy_threshold` via a US `weight_search`. Wiring works with the default; **correct thresholds require a US weight-search pass** (deferred, tracked).

### ADR-6 — Limit-price selection (a genuinely new concern)
Domestic is a **market** order (no price). Overseas is **limit-only** (`ORD_DVSN='00'`). The flow must *choose* `unit_price`. First cut: **marketable limit** = latest quote `last_price` (optionally + a small configurable offset bps, e.g. cross the spread by `OVERSEAS_LIMIT_OFFSET_BPS`) so a mock buy fills near the touch. This is config-driven and isolated in `app/overseas_execution/limit_price.py`.

### ADR-7 — Preserve the mock-only safety invariants verbatim
`require_mock_env` (base_url + `KIS_ENV` cross-check, runs first, outside try) and `_assert_limit_only` egress tripwire and live `NotImplementedError` TR-ids are **non-negotiable** (a prior re-gate caught an inf-price leak). The new layers feed `qty`+`unit_price` into the existing `place_overseas_limit_order`; they do **not** add a second order path. Live enablement remains an operator gate, not a code change.

## 4. The gate2 v2 selection flow (overseas)

```
for each universe symbol:
  quote+OHLC  ──▶ OverseasMarketSnapshot (float USD)            [app/overseas_stock/market_snapshot.py]
              ──▶ v1-style score_components (strength scores;    [app/overseas_stock/scoring.py]
                  volume_rank/power = 50 neutral)
              ──▶ condition_scores_from_v1(components, caps)     [REUSE app/gate2/adapter.py]
              ──▶ weighted_gate2_score(conditions, weights)      [REUSE app/gate2/score_v2.py]
              ──▶ Gate2ScoreResult{final_score, contributions, missing}
select: candidates with final_score ≥ buy_threshold, ranked by final_score (then symbol)   [app/overseas_stock/selection.py]
        artifact = load_artifact(score_v2_us_w0.json) or default_artifact()
```

`score_components` keys the adapter consumes (must match exactly — from `app/scanner/scoring.py:299`):
`intraday_pullback_strength_score`, `rebound_from_low_strength_score`, `controlled_down_strength_score`, `gap_down_open_strength_score`, `range_recovery_strength_score`, `live_volume_rank_strength_score`(=50), `live_volume_power_rank_strength_score`(=50), plus the cap-normalized terms (`trend_alignment_score`, `macd_momentum_score`, `cost_quality_score`, `mean_reversion_bonus`, `velocity_bonus/penalty`, `diversification_bonus`, `portfolio_correlation_penalty`, `variance_increase_penalty`). Terms with no US source map to `None` → contribute 0 + recorded in `missing` (observability), which is the adapter's designed behavior.

The strength scores are **ratio-based** (pullback %, rebound-from-low %, gap %, range recovery %) → **currency-agnostic**, so they port to float-USD snapshots without KRW assumptions.

## 5. New module inventory

```
# Foundation (ADR-1)
app/gate2/schema.py            # MOVED from research: ScoreV2Artifact, CONDITION_SCORE_NAMES, load/default_artifact
app/gate2/score_v2.py          # MOVED: weighted_gate2_score, passes_threshold
app/gate2/adapter.py           # MOVED: condition_scores_from_v1
app/gate2/artifacts/score_v2_us_w0.json   # US weights artifact (default until US weight-search)

# Selection (Phase 1-2)
app/overseas_stock/market_snapshot.py   # OverseasMarketSnapshot(float USD) + builder; needs OHLC from quote endpoint
app/overseas_stock/scoring.py           # v1-style score_components from US snapshot; volume = 50 neutral
app/overseas_stock/scanner.py           # universe scan/funnel over fetch_overseas_quote
app/overseas_stock/selection.py         # snapshot→components→adapter→gate2 v2→threshold→select top
app/overseas_stock/session.py           # US/Eastern RTH + holiday calendar (replaces get_korean_market_session)

# Sizing (Phase 3)
app/overseas_execution/position_sizing.py  # USD/FX-aware, whole-share; input = fetch_overseas_orderable
app/overseas_execution/sell_sizing.py      # trigger→fraction table (no KR tax)
app/overseas_execution/limit_price.py      # ADR-6 marketable-limit selection

# Guards + Risk (Phase 4)
app/execution/order_guard.py            # REFACTOR: inject clock (ADR-4, risk gate)
app/overseas_risk/guards.py             # USD daily order/notional caps, reads overseas_orders_*.jsonl
app/overseas_runtime/cost.py            # US fee model (SEC/TAF/commission), NO sell-tax

# Orchestration (Phase 5-6)
app/overseas_execution/buy_flow.py      # mirror run_buy_order_flow (limit-only, USD)
app/overseas_execution/sell_flow.py     # mirror run_sell_order_flow (trigger-driven, limit-only)
app/overseas_runtime/state.py           # runtime-state writer matching guard read schema (ET-dated)
app/overseas_runtime/run_cycle.py       # SELL-then-BUY orchestrator + operator entry

# REUSED AS-IS
app/overseas_stock/{order,order_record,tr_ids,exchanges,config,runtime_env,market_data,quote,models}.py
app/risk/schema.py   (currency-agnostic dataclasses)
```

## 6. Phasing (each phase independently TDD-able)

- **Phase 0 — gate2 promotion (ADR-1).** Move trio → `app/gate2/`, re-point importers, keep all gate2 tests green. No new behavior. *Foundation; low risk.*
- **Phase 1 — overseas snapshot + scoring.** `OverseasMarketSnapshot` (float USD) + `app/overseas_stock/scoring.py` producing `score_components` with neutral volume. Requires extending the quote fetch to return OHLC (open/high/low) — verify the KIS overseas price endpoint yields them; if not, this is a data gap to resolve first.
- **Phase 2 — gate2 v2 selection.** `selection.py` wiring snapshot→adapter→`weighted_gate2_score`→threshold→rank. Plus the US artifact JSON (default weights for now).
- **Phase 3 — USD sizing.** `position_sizing.py` over `fetch_overseas_orderable`, whole-share, USD caps; `sell_sizing.py` trigger table; `limit_price.py`.
- **Phase 4 — guards + risk (RISK GATE).** order_guard clock-injection refactor (`/risk-assessment`); `overseas_risk/guards.py` USD caps reading the overseas order log; `cost.py` US fees.
- **Phase 5 — buy/sell flow.** `overseas_execution/buy_flow.py` + `sell_flow.py` orchestrating selection→sizing→guard→risk→`place_overseas_limit_order`→state/log/slack.
- **Phase 6 — runtime wiring.** `run_cycle.py` SELL-then-BUY + operator CLI; `state.py` ET-dated writer.

## 7. Blockers / deferrals

| # | Item | Disposition |
|---|------|-------------|
| 1 | **US OHLC for scoring** — `fetch_overseas_quote` currently extracts only last/change/pct | **Resolved:** `price-detail` (HHDFS76200200, already referenced in `quote.py:67`) returns open/high/low/base(prev close). Phase 1 adds an OHLC fetch via this endpoint. |
| 2 | **US weight-search** — default artifact threshold (60) + volume weights mis-calibrated under neutral-fill (ADR-5) | Wire with defaults; schedule a US `weight_search` pass before any real reliance |
| 3 | **USD equity / operating-equity source** for pnl-brake + regime | **Deferred** — ship Phase 4 risk with daily order/notional caps only; pnl-brake/regime later |
| 4 | **FX-native exposure model** | Deferred — lean on broker orderable (ADR-3); record fx_rate |
| 5 | **Mock balance/holdings reliability** — `probe.py` exists because mock account mapping was uncertain; `build_overseas_report.holdings` hardcoded empty | Verify holdings via probe before sell-watch/sizing depends on `fetch_overseas_holdings` |
| 6 | **Live order path** | Stays operator-gated (R7); out of scope |

## 8. Test strategy

TDD per CLAUDE.md (failing test first, one at a time — `tdd_guard` ratchet). Strict ratchet execution delegated to sub-agents with exact specs (per memory `tdd-guard-delegation`). Each phase ships with its own `tests/test_overseas_*.py`. Reused gate2 leaf keeps its existing tests (re-pointed imports). The mock-only invariants get adversarial re-gate before the flow phases land (per the prior inf-price-leak lesson).
