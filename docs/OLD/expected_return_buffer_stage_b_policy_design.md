# Expected Return Buffer — Stage B Experimental Policy Design

> **Status**: Design document only. No production code has been changed. No implementation is authorized without explicit approval after offline replay evidence is reviewed.
>
> **Created**: 2026-05-17
>
> **Related**: expected_return_buffer_target_review.md (not included in this source snapshot) (Stage A evidence), todo.md (not included in this source snapshot)

---

## 1. Purpose

Stage B is not an implementation phase. It defines the experimental variants that should be tested next — in an isolated, offline-only environment — to evaluate whether relaxing the expected return buffer gate for specific candidate profiles is justified.

Stage A confirmed that:

- The buffer gate is a material hard gate (20% rejection rate in 11 clean sessions).
- All 130 Set B buffer-rejected candidates are high-score (≥3.0), average score 4.42.
- Buffer-rejected EOD outcomes are profile-specific — not uniformly favorable.
- No production code change is justified on candidate-level evidence alone.

Stage B defines exactly what should be tested next, what safety caps apply, and what constitutes sufficient evidence to proceed toward implementation.

---

## 2. Evidence Summary from Stage A

This section summarizes only the decision-relevant findings. Full evidence is in expected_return_buffer_target_review.md (not included in this source snapshot) §8.

### 2.1 Buffer Materiality

- **Set A** (12 sessions, incl. 20260508): 316/854 deep_eval candidates blocked by buffer gate (37.0%).
- **Set B** (11 sessions, excl. 20260508): 130/649 blocked (20.0%).
- Median `net_profit_buffer_bps` ≈ −28 bps in both sets. This closely tracks round-trip cost, confirming the structural cause: when `current_price >= open_price`, gross expected profit is zero.
- Buffer rejection is the #1 rejection reason in Set A; #3 in Set B (still material at 23.5% of rejections).

### 2.2 20260508 Sensitivity

- 20260508 was an extreme outlier (186/205 deep_eval candidates buffer-rejected, 90.7% rate).
- Excluding it reduces the headline rate from 37% to 20%, but the structural cause persists unchanged.
- In Set B, 100% of buffer-rejected candidates are high-score (≥3.0), average score 4.42 — higher than the passed cohort's average. The score inversion is *stronger* without 20260508.

### 2.3 Post-Rejection Outcome — Profile Split

EOD horizon, Set B (11 sessions):

| Profile | N | Mean EOD (bps) | Pos% | Neg% |
| :--- | :--- | :--- | :--- | :--- |
| momentum | 47 | **−21.4** | 38.2% | 52.9% |
| pullback | 41 | **+18.1** | 48.3% | 31.0% |
| recovery | 42 | **+9.5** | 63.6% | 27.3% |

Short-horizon (5m/30m): all profiles show adverse initial movement before EOD recovery. Per-date variance is high (20260421 alone drives much of the positive aggregate). The profile split is the key finding: momentum outcome is adverse; pullback/recovery is favorable.

### 2.4 BUY Slot Pressure

| Variant | New unique symbols (Set B, 11 sessions) | Mean/day |
| :--- | :--- | :--- |
| V1 no_buffer (all hi-score buf-rej) | +39 | +3.5 |
| V2 soft_penalty (score ≥ 3.41) | +37 | +3.4 |
| V3 profile-filtered (pullback/recovery only) | +28 | +2.5 |

Budget and exposure constraints are the binding constraint on RISK_OFF days (8 of 11 sessions), not the daily slot limit. V3 adds ~+2.5 symbols/day under NORMAL-regime and does not systematically breach the 30-slot limit.

### 2.5 Limitations of no_buffer and Weak Soft_penalty

- **V1 no_buffer** is operationally feasible as a comparator but not a production candidate. It disables a cost-awareness gate with no selectivity — it passes all buffer-rejected candidates indiscriminately, including adverse momentum ones.
- **V2 soft_penalty** with −0.25 fixed penalty is nearly identical to V1 in Set B (100% of buffer-rejected are already above 3.16; a −0.25 penalty leaves 95% above 3.41). The fixed small penalty provides negligible filtering.
- Both V1 and V2 are useful as upper-bound comparators in offline evaluation. Neither is a production recommendation.

---

## 3. Candidate Experimental Policies

### Variant B0 — Baseline (Current Production)

**Rule**: No change to current production behavior.

```
target_exit_price_krw = max(open_price, current_price)
net_profit_buffer_bps >= MIN_NET_PROFIT_BUFFER_BPS (20.0)
```

- Hard rejection if buffer < 20 bps.
- Used as the baseline against which all experimental variants are measured.
- This is the only variant authorized for live production at this time.

### Variant B1 — No_buffer Comparator

**Rule**: Disable only the expected return buffer gate. All other gates (score, pass_count, budget, exposure, cooldown) remain unchanged.

- **Purpose**: upper-bound comparator only. Measures the maximum candidate expansion possible by removing the gate entirely, inclusive of adverse momentum candidates.
- **Not a production candidate.** Use only in offline evaluation to establish the upper-bound impact.
- Profile-split outcome analysis must accompany any B1 offline run to prevent the adverse momentum subset from distorting aggregate metrics.

### Variant B2 — Profile-Filtered Relaxation (Leading Experiment Candidate)

**Rule**: Relax the buffer gate for pullback- and recovery-profile candidates only. Apply hard gate only to momentum candidates (current behavior).

Detailed policy:

| Condition | Action |
| :--- | :--- |
| `selection_profile in ("pullback", "recovery")` AND `score_deep >= BUY_MIN_SCORE` AND daily remaining cap not exhausted | Pass candidate (buffer gate skipped) |
| `selection_profile == "momentum"` | Apply buffer gate as-is (no relaxation) |
| Any other profile | Apply buffer gate as-is |
| `selection_profile in ("pullback", "recovery")` BUT `score_deep < BUY_MIN_SCORE` | Reject (score gate still applies) |

**Safety caps** (see §4 for full cap set):

- Daily additional pullback/recovery buffer-relaxed candidates capped at +5 unique symbols per day, above baseline.
- Score floor: `score_deep >= BUY_MIN_SCORE` (currently 3.16).
- RISK_OFF: apply tighter per-day additional cap (+3) or suspend relaxation entirely.
- Never override emergency/risk guards.

**Rationale**: Pullback and recovery profiles showed favorable EOD outcomes (+18.1 and +9.5 bps mean). They constitute 63 of 130 Set B buffer rejects (48%). Excluding momentum (adverse at −21.4 bps) substantially improves expected outcome quality.

**Status**: Leading experimental candidate for offline evaluation.

### Variant B3 — Dynamic Buffer Penalty

**Rule**: Do not hard-reject on buffer failure. Convert buffer deficit into a score penalty proportional to (or scaled by) the deficit magnitude. Candidate competes in the scored pool at its adjusted score rather than being hard-rejected.

Candidate penalty formulas (to be evaluated offline):

| Formula | Effect |
| :--- | :--- |
| Fixed: `adjusted_score = score_deep − 0.25` | Confirmed insufficient in Stage A (V2 data) |
| Fixed larger: `adjusted_score = score_deep − 0.75` | Moderate filter; untested |
| Deficit-scaled: `adjusted_score = score_deep − k × abs(deficit_bps) / cost_bps` | Proportional; requires calibration of k |

**Constraints**:

- Do not lower the effective score floor below `BUY_MIN_SCORE` for any penalty formula.
- Preserve order pressure caps: score adjustment does not bypass daily limit, budget, or exposure guards.
- A fixed penalty of −0.25 is insufficient (Stage A V2 evidence). Any design must use a larger or scaled penalty.
- This variant requires more parameter tuning than B2 and is therefore the **secondary experimental candidate**.

**Status**: Secondary candidate. Evaluate after B2 results are available.

### Variant B4 — Family-Specific Target Formula

**Rule**: Use different `target_exit_price_krw` formulas for different strategy families.

| Strategy family | Proposed target | Rationale |
| :--- | :--- | :--- |
| Pullback / open_recovery | `max(open_price, current_price)` (current) | Consistent with pullback-to-open logic |
| Recovery / range_recovery | `current_price × (1 + intraday_range_pct)` or similar | Intraday range recovery target |
| Momentum continuation | Not recommended yet | Momentum EOD outcome was adverse (−21.4 bps); formula design requires additional evidence |

**Why deferred**:

- Requires per-family target calibration before any offline test.
- Changes the cost calculation path (`calc_expected_net_profit_buffer_bps`), not just the gate.
- Momentum continuation target design is not recommended at this stage given adverse outcome evidence.
- B2 and B3 are gate-level changes that are easier to reason about. B4 would change the cost structure itself.

**Status**: Deferred. Do not implement B4 unless B2/B3 evidence is insufficient to make a recommendation.

---

## 4. Safety Caps

All experimental variants (B1–B4) must operate under the following caps during offline evaluation and any future experimental branch testing:

| Cap | Rule |
| :--- | :--- |
| **No live deployment** | All Stage B variants are offline-only until explicit approval after replay evidence is reviewed |
| **RISK_OFF exclusion or tighter cap** | In RISK_OFF regime, either suspend relaxation entirely or use stricter per-day cap (+3 additional candidates max) |
| **Daily additional candidate cap** | B2: +5 unique symbols/day above baseline (NORMAL); B3: TBD after calibration |
| **Profile-level cap** | B2 applies only to pullback and recovery profiles; momentum is not relaxed |
| **Score floor** | `score_deep >= BUY_MIN_SCORE` (3.16) is required for any buffer-relaxed candidate |
| **Emergency / risk guard bypass** | Never. All existing emergency and risk guards remain active regardless of experimental variant |
| **Policy B daily BUY limits** | Maintained unchanged: NORMAL 30 / CAUTION 15 / RISK_OFF 10 |
| **20260511 exclusion** | Exclude 2026-05-11 from all offline evaluation date sets (contaminated window) |
| **Budget / exposure guards** | Maintained unchanged. The expected return buffer gate is not the exposure gate |
| **B1 as comparator only** | B1 (no_buffer) is used only as an upper-bound comparator, never as a standalone production candidate |

---

## 5. Required Offline Evaluation Before Implementation

Before any experimental variant is implemented in production code, the following offline evaluation must be completed and reviewed:

### 5.1 Evaluation Date Set

- Primary: **Set B dates** — 11 sessions (20260416–20260507, excl. 20260508 and 20260511).
- Optional extension: additional clean dates (no stale-process contamination, full session).
- Exclude 20260508 (outlier) and 20260511 (contaminated window) from all evaluation.

### 5.2 Required Metrics Per Variant

For each experimental variant (at minimum B0 baseline, B1 comparator, B2 leading candidate):

| Metric | How to compute |
| :--- | :--- |
| Candidate-level change | Additional unique symbols added per session vs baseline |
| Final candidate count | After all gates except the relaxed one |
| Estimated BUY slot pressure | Additional unique symbols that would reach the BUY submission step |
| Post-rejection outcome by profile | EOD return distribution by selection_profile for newly admitted candidates |
| Intraday timing risk | 5m and 30m movement for newly admitted candidates |
| Per-date variance | Mean, positive rate, negative rate by date — not just aggregate |
| Budget/exposure binding analysis | How many additional candidates would have been limited by budget/exposure rather than the buffer gate |

### 5.3 Optional — Ranking / Priority Impact

If feasible: replay candidate scoring and ranking under each variant to assess whether newly admitted candidates would have displaced existing candidates in priority ordering.

### 5.4 Offline Execution Constraints

- Use `signal_outcomes` and `candidate_outcomes` data from `logs/` (local cycle snapshots only).
- No broker API calls.
- No live session execution.
- No modification to production code in `app/`.
- Analysis scripts go in `/tmp/kis-buffer-review/` or a separate offline analysis directory.

### 5.5 Approval Gate

No production code change is authorized until:
1. Offline evaluation for the chosen variant is complete.
2. Results are documented (either in this file or a linked evaluation report).
3. Explicit user approval is given for the specific code change scope.

---

## 6. Recommended Stage B Path

Based on Stage A evidence:

1. **Start with B2 (profile-filtered pullback/recovery relaxation)** as the leading experiment.
   - Directly addresses the confirmed profile split finding.
   - Most conservative variant — does not admit adverse momentum candidates.
   - Simple gate condition change — easier to reason about and reverse than B3 or B4.
   - Adds mean +2.5 unique symbols/day (V3 estimate), well within NORMAL-regime limits.

2. **Run B1 (no_buffer) alongside B2 as an upper-bound comparator.**
   - Do not use B1 results in isolation. Always compare against B2 and B0 baseline.
   - B1 confirms how much of the favorable pullback/recovery signal is diluted by including adverse momentum.

3. **Evaluate B3 (dynamic buffer penalty) as secondary.**
   - Only if B2 evidence is insufficient or if the profile classification is found to be unreliable as a gate condition.
   - Requires penalty scale calibration — more parameter surface than B2.

4. **Do not implement B4 (family-specific target formula) yet.**
   - B4 changes the cost calculation structure, not just the gate.
   - Requires per-family target calibration that is not yet evidenced.
   - Revisit after B2/B3 results are available and only if the gate-level changes are insufficient.

---

## 7. Implementation Boundary

**This document does not authorize any production code change.**

- Any code change for testing must be in a **separate experimental branch** (`exp/buffer-b2` or similar) that does not touch the `main` branch production code.
- The experimental branch must not be run against live broker sessions until explicitly approved.
- Offline analysis scripts (e.g., `/tmp/kis-buffer-review/`) may be created or extended without production code risk.
- Production merge requires:
  1. Completed offline replay/evaluation evidence as defined in §5.
  2. Documented results showing favorable or neutral outcome vs baseline.
  3. Explicit user approval specifying the exact scope of the production change.
- The `target_exit_price_krw` formula and `passes_profit_buffer` gate in `app/scanner/service.py` must not be modified until the above conditions are met.

---

## 8. Status

| Item | Status |
| :--- | :--- |
| Stage A analysis complete | ✅ |
| Stage B policy design document | ✅ Created 2026-05-17 |
| B2 offline evaluation plan | ✅ Created 2026-05-17 — see B2 offline evaluation plan (not included in this source snapshot) |
| B2 offline evaluation (run + results) | ✅ Run 2026-05-17 — **REJECT**: dominated by 20260421; RISK_OFF adverse |
| B1 comparator offline evaluation | ✅ Run as part of B2 evaluation (V1 no_buffer comparator) |
| V4 (RISK_OFF-disabled) evaluation | ⏳ Pending — requires ≥ 5 more NORMAL-regime sessions |
| B3 evaluation | ⏳ Pending (secondary, after V4) |
| B4 design | 🔒 Deferred |
| Any production code change | 🔒 Deferred — B2 rejected; V4 pending more data |
