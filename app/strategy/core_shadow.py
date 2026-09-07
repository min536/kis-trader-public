"""core_shadow — Shadow evaluation of core-bucket entry rules.

DIAGNOSTICS ONLY.  These rules are evaluated in parallel with the live buy
decision but do NOT affect any order, candidate selection, or final_candidate
outcome.  The results are logged as flat fields on the candidate outcome record
so they can be inspected with the existing analysis CLIs after 3–5 days of
live session data.

Shadow rule family (core bucket only, deep_eval rows only):

  Pre-gate : trend_alignment_score >= 0.25
             (all three EMA conditions aligned — "추세정배열")

  Rule 1   : non_overextension
             prev_day_change_pct < 3.0%  AND  gap_up_open_pct < 1.0%

  Rule 2   : macd_momentum_confirmed
             macd_momentum_score >= 0.20  ("MACD강세")

  Rule 3   : intraday_stability
             current >= open (pullback_pct <= 0),
             OR pullback from open < 2.0% with range_recovery_ratio >= 0.40

  Shadow pass: trend pre-gate passed  AND  rule passes >= 2 of 3

All thresholds reference values already established in technical.py,
calculate_selection_score, and the diagnostic tooling.  No new indicators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.scanner.service import SymbolAnalysisResult

# ---------------------------------------------------------------------------
# Thresholds (diagnostics — not used in live trading)
# ---------------------------------------------------------------------------
_TREND_ALIGNMENT_GATE: float = 0.25     # technical.py "추세정배열" boundary
_MACD_MOMENTUM_GATE: float = 0.20      # technical.py "MACD강세" boundary
_NON_OVERHEAT_PREV_DAY_MAX: float = 3.0
_NON_OVERHEAT_GAP_UP_MAX: float = 1.0
_INTRADAY_PULLBACK_MAX_PCT: float = 2.0
_INTRADAY_RECOVERY_MIN_RATIO: float = 0.40
_SHADOW_REQUIRED_PASS_COUNT: int = 2   # of 3 rules

# Fixed rule order for pattern encoding ("P F P" → non_overextension passes,
# macd_momentum_confirmed fails, intraday_stability passes).
SHADOW_RULE_ORDER: tuple[str, ...] = (
    "non_overextension",
    "macd_momentum_confirmed",
    "intraday_stability",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _null_shadow_fields() -> dict[str, Any]:
    """Return all-null shadow fields for rows that are not evaluated."""
    return {
        "core_shadow_evaluated": False,
        "core_shadow_trend_gate_passed": None,
        "core_shadow_passed_count": None,
        "core_shadow_pattern": None,
        "core_shadow_passed": None,
    }


def _evaluate(raw_result: "SymbolAnalysisResult") -> dict[str, Any]:
    """Run the shadow rule family against a deep-eval SymbolAnalysisResult.

    Uses score_components (already computed, no extra API calls) and
    market_snapshot (already fetched during deep_eval).
    """
    sc: dict[str, float] = raw_result.score_components
    snap = raw_result.market_snapshot

    trend_alignment_score = float(sc.get("trend_alignment_score", 0.0) or 0.0)
    trend_gate_passed = trend_alignment_score >= _TREND_ALIGNMENT_GATE

    if not trend_gate_passed:
        return {
            "core_shadow_evaluated": True,
            "core_shadow_trend_gate_passed": False,
            "core_shadow_passed_count": 0,
            "core_shadow_pattern": "BLOCKED",
            "core_shadow_passed": False,
        }

    # Values derived from score_components (already computed in deep_eval)
    macd_momentum_score = float(sc.get("macd_momentum_score", 0.0) or 0.0)
    gap_up_open_pct = float(sc.get("gap_up_open_pct", 0.0) or 0.0)
    pullback_pct = float(sc.get("pullback_pct", 0.0) or 0.0)
    range_recovery_ratio = float(sc.get("range_recovery_ratio", 0.0) or 0.0)

    # prev_day_change_pct lives on the snapshot, not in score_components
    prev_day_change_pct = float(getattr(snap, "prev_day_change_pct", 0.0) or 0.0)

    # Rule 1: non_overextension
    # Neither yesterday nor this morning shows overheated price action.
    non_overextension_passed: bool = (
        prev_day_change_pct < _NON_OVERHEAT_PREV_DAY_MAX
        and gap_up_open_pct < _NON_OVERHEAT_GAP_UP_MAX
    )

    # Rule 2: macd_momentum_confirmed
    # MACD histogram is positive and expanding ("MACD강세").
    macd_confirmed_passed: bool = macd_momentum_score >= _MACD_MOMENTUM_GATE

    # Rule 3: intraday_stability
    # Price holds near or above the open — not in a sharp intraday decline.
    # (a) at or above open:  pullback_pct == 0  (pullback_pct = max(open-current, 0)/open)
    # (b) mild pullback:     < 2% below open AND range already 40%+ recovered
    if pullback_pct <= 0.0:
        intraday_stability_passed: bool = True
    else:
        intraday_stability_passed = (
            pullback_pct < _INTRADAY_PULLBACK_MAX_PCT
            and range_recovery_ratio >= _INTRADAY_RECOVERY_MIN_RATIO
        )

    rule_passes = [
        non_overextension_passed,
        macd_confirmed_passed,
        intraday_stability_passed,
    ]
    passed_count = sum(rule_passes)
    pattern = " ".join("P" if p else "F" for p in rule_passes)
    shadow_passed = passed_count >= _SHADOW_REQUIRED_PASS_COUNT

    return {
        "core_shadow_evaluated": True,
        "core_shadow_trend_gate_passed": True,
        "core_shadow_passed_count": passed_count,
        "core_shadow_pattern": pattern,
        "core_shadow_passed": shadow_passed,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_core_shadow_fields(
    raw_result: "SymbolAnalysisResult | None",
    selection_bucket: str,
) -> dict[str, Any]:
    """Return shadow evaluation fields for inclusion in the candidate outcome record.

    Called from _build_buy_candidate_outcome_records in main.py.
    Returns all-null fields for non-core or non-deep-eval rows so that
    every outcome record has a consistent schema.

    IMPORTANT: this function has NO side effects and does NOT influence
    any live buy decision.  It reads from already-computed data only.
    """
    if raw_result is None or selection_bucket != "core":
        return _null_shadow_fields()
    return _evaluate(raw_result)
