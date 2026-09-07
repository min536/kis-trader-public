"""Phase 5 — MCP-assisted candidate suggestion (proposals only).

The external open-trading-api backtester/MCP *screens* parameter moves and emits
proxy-backtest results. ``suggest_candidates`` converts those screening results
into whitelist-validated entries of the curated *plan* that the human-gated
propose-cycle (Step 4) consumes. It is the safe core of "MCP-assisted candidate
generation": it SUGGESTS only — it never writes live, never auto-applies, and
drops any screened candidate that is not a whitelisted, in-bounds Tier A/B move.
The trust boundary (§3) and non-goals (§8) of docs/live_autotuner_plan.md hold:
every suggestion still flows through propose -> human approve -> gated runtime.
"""

from __future__ import annotations

from app.autotuner.validator import load_whitelist


def _is_valid_move(spec, to_value) -> bool:
    """A screened move is suggestible only if it is a whitelisted, in-bounds,
    non-blocked Tier A/B numeric move (Tier C/D never suggested)."""
    if not spec or spec.get("blocked"):
        return False
    if spec.get("tier") not in {"A", "B"}:
        return False
    if not isinstance(to_value, (int, float)) or isinstance(to_value, bool):
        return False
    bound_min, bound_max = spec.get("bound_min"), spec.get("bound_max")
    if bound_min is not None and to_value < bound_min:
        return False
    if bound_max is not None and to_value > bound_max:
        return False
    return True


def suggest_candidates(screening, *, baseline_values, date, mode="shadow"):
    known = load_whitelist().get("parameters", {})
    plan = []
    seq = 0
    for item in screening:
        # only suggest candidates the screener did not fail (verdict optional).
        if "verdict" in item and item.get("verdict") != "pass":
            continue
        param = item.get("parameter")
        if not _is_valid_move(known.get(param), item.get("to_value")):
            continue
        seq += 1
        plan.append(
            {
                "proposal_id": f"atp_{date}_{mode}_{seq:04d}",
                "parameter": param,
                "from_value": baseline_values.get(param),
                "to_value": item["to_value"],
                "reason": "MCP-screened candidate (proxy backtest)",
                "backtest_eval": item.get("backtest_eval"),
            }
        )
    return plan
