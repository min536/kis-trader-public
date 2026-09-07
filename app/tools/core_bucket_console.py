"""Console rendering helpers for core-bucket diagnostics."""
from __future__ import annotations

from typing import Any

from app.tools.core_bucket_analysis import _pct


_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"


_CYAN = "\033[96m"
_BOLD = "\033[1m"


def _header(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


_STRATEGY_ORDER = [
    "intraday_pullback",
    "rebound_from_low",
    "controlled_down_day",
    "gap_down_open",
    "range_recovery",
]


def _format_metric_row(label: str, left: dict[str, Any], right: dict[str, Any]) -> str:
    return (
        f"  {label:<24} "
        f"{left['avg']:>8.3f} / {left['median']:>8.3f}    "
        f"{right['avg']:>8.3f} / {right['median']:>8.3f}"
    )


def print_terminal(result: dict[str, Any]) -> None:
    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(_header("═" * 76))
    print(_header("  Analyze Core Bucket Diagnostics"))
    print(_header("═" * 76))
    print(f"Candidate log : {result['candidate_file']}")
    print(f"Cycle snapshots: {result['cycle_snapshots_file']}")
    print(f"Session filter : {result['session']} | Total rows loaded: {result['total_rows']}")
    if result["errors"]:
        print(_warn(f"Load warnings  : {len(result['errors'])}"))
    print()

    print(_header("1. Overall Bucket Comparison"))
    for bucket_name, stats in sorted(result["bucket_stats"].items()):
        print(f"  [{bucket_name}]")
        print(f"    Total Rows   : {stats['total_rows']}")
        print(f"    Pre-Gate Rej : {stats['pre_gate_rejected']}")
        print(f"    Deep Eval    : {stats['deep_eval']}")
        print(f"    Final Cand   : {stats['final_candidate']}")
        print(f"    Executed     : {stats['executed']}")
        score_stats = stats["score_deep"]
        if score_stats["count"] > 0:
            print(f"    Deep Scores  : {score_stats['count']} valid")
            print(f"      min/max    : {score_stats['min']:.4f} / {score_stats['max']:.4f}")
            print(f"      avg/median : {score_stats['avg']:.4f} / {score_stats['median']:.4f}")
        else:
            print("    Deep Scores  : NONE valid")
        print()

    core_stats = result["core_losers_stats"]
    non_core_stats = result["non_core_final_stats"]

    print(_header("2. Core Deep-Eval Losers vs Non-Core Final Candidates"))
    print(f"  Core deep-eval losers      : {core_stats['count']}")
    print(f"  Non-core final candidates  : {non_core_stats['count']}")
    print()
    print("  Top Core Loser Symbols:")
    for item in core_stats["top_symbols"]:
        print(f"    {item['symbol']} ({item.get('symbol_name') or '-'}) x {item['count']}")
    print("  Top Non-Core Final Symbols:")
    for item in non_core_stats["top_symbols"]:
        print(f"    {item['symbol']} ({item.get('symbol_name') or '-'}) x {item['count']}")
    print()
    print("  Core Rejection Reasons:")
    for reason, count in sorted(core_stats["reasons"].items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")
    print("  Core Selection Outcomes:")
    for outcome, count in sorted(core_stats["outcomes"].items(), key=lambda x: -x[1]):
        print(f"    {outcome}: {count}")
    print("  Non-Core Final Buckets:")
    for bucket_name, count in sorted(non_core_stats["buckets"].items(), key=lambda x: -x[1]):
        print(f"    {bucket_name}: {count}")
    print()

    comparison = result["composition_comparison"]
    core_profile = comparison["core_deep_eval_losers"]
    non_core_profile = comparison["non_core_final_candidates"]

    print(_header("3. Score Composition Comparison"))
    print(
        f"  Detail coverage            "
        f"{core_profile['detail_coverage_count']}/{core_profile['row_count']} "
        f"({core_profile['detail_coverage_pct']})    "
        f"{non_core_profile['detail_coverage_count']}/{non_core_profile['row_count']} "
        f"({non_core_profile['detail_coverage_pct']})"
    )
    print("  Metric                    CORE losers avg/med      NON-CORE finals avg/med")
    print(_dim("  " + "─" * 72))
    for field, label in (
        ("score_deep", "score_deep"),
        ("passed_count", "passed_count"),
        ("technical_total_score", "technical_total"),
        ("trend_alignment_score", "trend_alignment"),
        ("macd_momentum_score", "macd_momentum"),
        ("mean_reversion_bonus", "mean_reversion_bonus"),
        ("overextension_penalty", "overextension_penalty"),
        ("net_profit_buffer_bps", "net_profit_buffer_bps"),
        ("expected_cost_bps", "expected_cost_bps"),
        ("expected_cost_penalty", "expected_cost_penalty"),
        ("cost_quality_score", "cost_quality_score"),
    ):
        print(_format_metric_row(label, core_profile[field], non_core_profile[field]))
    print()
    print(
        "  Technical nonzero rows     "
        f"{core_profile['technical_nonzero_count']} / {core_profile['technical_available_count']}    "
        f"{non_core_profile['technical_nonzero_count']} / {non_core_profile['technical_available_count']}"
    )
    print(
        "  Mean-reversion nonzero     "
        f"{core_profile['mean_reversion_nonzero_count']} / {core_profile['detail_coverage_count']}    "
        f"{non_core_profile['mean_reversion_nonzero_count']} / {non_core_profile['detail_coverage_count']}"
    )
    print(f"  Core cost block reasons    : {core_profile['cost_block_reasons'] or {}}")
    print(f"  Non-core cost block reasons: {non_core_profile['cost_block_reasons'] or {}}")
    print(f"  Core top highlights        : {core_profile['top_highlights'] or {}}")
    print(f"  Non-core top highlights    : {non_core_profile['top_highlights'] or {}}")
    print(f"  Core top penalties         : {core_profile['top_penalties'] or {}}")
    print(f"  Non-core top penalties     : {non_core_profile['top_penalties'] or {}}")
    print()

    print(_header("4. Sample Rows"))
    print("  Core loser samples:")
    for row in core_stats["samples"]:
        print(
            "    "
            f"{row['symbol']}({row.get('symbol_name') or ''}) | "
            f"cycle={row['cycle_id']} | score={row['score_deep']} | "
            f"passed={row.get('passed_count')} | reason={row.get('rejection_reason')} | "
            f"tech={row.get('technical_total_score')} | "
            f"mean_rev={row.get('mean_reversion_bonus')} | "
            f"cost_penalty={row.get('expected_cost_penalty')}"
        )
    print("  Non-core final samples:")
    for row in non_core_stats["samples"]:
        print(
            "    "
            f"{row['symbol']}({row.get('symbol_name') or ''}) | "
            f"cycle={row['cycle_id']} | score={row['score_deep']} | "
            f"passed={row.get('passed_count')} | "
            f"tech={row.get('technical_total_score')} | "
            f"mean_rev={row.get('mean_reversion_bonus')} | "
            f"cost_penalty={row.get('expected_cost_penalty')}"
        )
    print()

    print(_header("5. Human Summary"))
    print(f"  {result['human_summary']}")
    print()

    shr = result.get("strategy_hit_rate", {})
    core_shr = shr.get("core_deep_eval_losers", {})
    non_core_shr = shr.get("non_core_final_candidates", {})
    print(_header("6. Strategy Hit Rate (strategy_pass_pattern)"))
    print(
        f"  Parsed rows: core={core_shr.get('parsed_rows', 0)} | "
        f"non-core={non_core_shr.get('parsed_rows', 0)}"
    )
    if core_shr.get("parsed_rows", 0) == 0 and non_core_shr.get("parsed_rows", 0) == 0:
        print(_dim("  strategy_pass_pattern not present in data (field added recently)"))
    else:
        print(f"  {'Strategy':<26} {'CORE pass%':>10}  {'NON-CORE pass%':>14}")
        print(_dim("  " + "─" * 54))
        core_per = core_shr.get("per_strategy", {})
        non_core_per = non_core_shr.get("per_strategy", {})
        for strategy in _STRATEGY_ORDER:
            c = core_per.get(strategy, {})
            n = non_core_per.get(strategy, {})
            c_pct = c.get("pass_pct", "0.0%")
            n_pct = n.get("pass_pct", "0.0%")
            c_detail = f"{c.get('pass_count', 0)}/{c.get('total', 0)}"
            n_detail = f"{n.get('pass_count', 0)}/{n.get('total', 0)}"
            print(f"  {strategy:<26} {c_pct:>6} ({c_detail:>7})  {n_pct:>6} ({n_detail:>7})")
    print()

    pcd = result.get("passed_count_distribution", {})
    print(_header("7. passed_count Distribution (core deep-eval losers)"))
    rpc = result.get("rule_contrast_interpretation", {}).get("inferred_required_pass_count")
    rpc_str = f"  inferred required_pass_count = {rpc}" if rpc is not None else "  required_pass_count could not be inferred"
    print(rpc_str)
    total_dist = sum(pcd.values())
    for k in sorted(
        pcd.keys(),
        key=lambda x: (x == "null", int(x) if isinstance(x, int) else (int(x) if str(x).lstrip("-").isdigit() else 99)),
    ):
        count = pcd[k]
        bar = "█" * min(count, 40)
        pct_str = _pct(count, total_dist)
        threshold_marker = ""
        if rpc is not None and isinstance(k, int) and k >= rpc:
            threshold_marker = _ok(" ✓ passes threshold")
        elif rpc is not None and k != "null":
            threshold_marker = _warn(" ✗ below threshold")
        print(f"  passed_count={k:>2}  {count:>4}  {pct_str:>6}  {bar}{threshold_marker}")
    print()

    tfp = result.get("top_fail_patterns", [])
    print(_header("8. Top Fail-Pattern Combinations (core deep-eval losers)"))
    print(f"  {'pattern':<12} {'count':>6}  passing rules")
    print(_dim("  " + "─" * 62))
    for entry in tfp:
        pat = entry.get("pattern", "")
        cnt = entry.get("count", 0)
        rules = ", ".join(entry.get("passing_rules") or ["(none)"])
        pc = entry.get("pass_count", 0)
        print(f"  {pat:<12} {cnt:>6}  [{pc} pass]  {rules}")
    print()

    rc = result.get("rule_contrast_interpretation", {})
    print(_header("9. Rule-Contrast Interpretation"))
    verdict = rc.get("verdict", "—")
    verdict_color = _warn if "mismatch" in verdict else (_ok if verdict == "threshold_only" else _dim)
    print(f"  Verdict       : {verdict_color(verdict)}")
    print(f"  Explanation   : {rc.get('explanation', '—')}")
    sg = rc.get("structural_gap_rules") or []
    tc = rc.get("threshold_candidate_rules") or []
    sh = rc.get("shared_rules") or []
    if sg:
        print(f"  Structural gaps (never fire for core): {sg}")
    if tc:
        print(f"  Threshold-sensitive rules             : {tc}")
    if sh:
        print(f"  Shared rules (fire for both, no discriminating power): {sh}")
    dp = rc.get("dominant_fail_pattern")
    dpc = rc.get("dominant_fail_pattern_count", 0)
    dr = rc.get("dominant_passing_rules") or []
    gap = rc.get("gap_to_threshold")
    if dp:
        print(f"  Dominant pattern: \"{dp}\"  x{dpc}  → passing: {dr or ['(none)']}")
        if gap is not None and gap > 0:
            print(f"  Gap to threshold: {gap} more rule(s) needed beyond dominant pattern")
        elif gap is not None and gap <= 0:
            print(_ok("  Dominant pattern already meets threshold — failure must be from another cause"))
    print()

    _print_threshold_simulation(result.get("threshold_simulation") or {})
    _print_rule_lift_opportunities(result.get("rule_lift_opportunities") or {})
    _print_symbol_failure_diagnosis(result.get("symbol_failure_diagnosis") or {})
    _print_shadow_section(result.get("shadow_analysis") or {})


def _print_threshold_simulation(payload: dict[str, Any]) -> None:
    print(_header("10. Threshold Simulation"))
    if not payload.get("available"):
        print(_dim("  required_pass_count could not be inferred from current rows."))
        print()
        return

    print(f"  Current threshold : {payload.get('current_required_pass_count')}")
    print(f"  Deep-eval rows    : {payload.get('deep_eval_row_count', 0)}")
    print(f"  Current hits      : {payload.get('current_hits', 0)}")
    print()
    for scenario in payload.get("scenarios") or []:
        threshold = scenario.get("threshold")
        meets = scenario.get("would_meet_threshold_count", 0)
        delta = scenario.get("delta_vs_current_threshold", 0)
        flipped = scenario.get("flipped_row_count", 0)
        delta_text = f"+{delta}" if delta > 0 else str(delta)
        print(
            f"  threshold={threshold} -> meets={meets} | "
            f"delta={delta_text} | flipped={flipped}"
        )
        if flipped > 0:
            print(f"    flipped symbols : {scenario.get('flipped_symbols') or {}}")
            print(f"    flipped reasons : {scenario.get('flipped_reasons') or {}}")
    print()


def _print_rule_lift_opportunities(payload: dict[str, Any]) -> None:
    print(_header("11. One-More-Rule Opportunities"))
    if not payload.get("available"):
        print(_dim("  rule-lift opportunities unavailable without inferred threshold."))
        print()
        return

    print(
        f"  Near-threshold rows : {payload.get('near_threshold_row_count', 0)} | "
        f"multi-rule short rows : {payload.get('multi_rule_short_row_count', 0)}"
    )
    ranked_rules = payload.get("ranked_rules") or []
    if not ranked_rules:
        print(_dim("  No rows are exactly one rule short today."))
        print()
        return
    for item in ranked_rules[:5]:
        pct = float(item.get("non_core_final_pass_rate", 0.0)) * 100.0
        print(
            f"  {item['rule']:<24} near-miss rows={item['near_threshold_row_count']:<3} "
            f"symbols={item['symbols']} | non-core final pass={pct:.0f}%"
        )
    print()


def _print_symbol_failure_diagnosis(payload: dict[str, Any]) -> None:
    print(_header("12. Per-Symbol Failure Diagnosis"))
    if not payload.get("available"):
        print(_dim("  symbol diagnosis unavailable without inferred threshold."))
        print()
        return

    for item in payload.get("symbols") or []:
        top_rules = ", ".join(rule["rule"] for rule in item.get("top_missing_rules") or []) or "-"
        print(
            f"  {item['symbol']}({item.get('symbol_name') or '-'}) | "
            f"{item['verdict']} | rows={item['rows']} | "
            f"avg passed={item['avg_passed_count_deep']:.2f} | "
            f"best score={item['best_score_deep']:.2f} | "
            f"one-rule short={item['one_rule_short_count']}"
        )
        print(f"    top missing rules: {top_rules}")
        print(f"    hint: {item.get('action_hint')}")
    print()


def _print_shadow_section(sa: dict[str, Any]) -> None:
    print(_header("13. Shadow Evaluation Results (core_shadow_*)"))
    if not sa.get("available"):
        print(_dim(
            "  No core_shadow_evaluated=True rows found.\n"
            "  Shadow fields are written from the next live session onward."
        ))
        print()
        return

    total = sa["evaluated_count"]
    tg_pass = sa["trend_gate_passed_count"]
    tg_block = sa["trend_gate_blocked_count"]
    sp = sa["shadow_passed_count"]
    sp_rate = sa.get("shadow_pass_rate_pct", "—")

    print(f"  Core deep-eval rows with shadow data : {total}")
    print(f"  Trend pre-gate (score >= 0.25)       : {tg_pass} passed, {tg_block} blocked")
    print(f"  Shadow passed (>= 2/3 rules)         : {sp} of {tg_pass} trend-gate-passed  ({sp_rate})")
    print()

    patterns = sa.get("top_patterns") or []
    if patterns:
        print("  Shadow pattern distribution (trend-gate-passed rows):")
        print(f"  {'pattern':<12} {'count':>6}  passing rules")
        print(_dim("  " + "─" * 58))
        for entry in patterns:
            pat = entry.get("pattern", "")
            cnt = entry.get("count", 0)
            rules = ", ".join(entry.get("passing_rules") or ["(none)"])
            pc = entry.get("pass_count", 0)
            marker = _ok(" ✓") if pc >= 2 else _warn(" ✗")
            print(f"  {pat:<12} {cnt:>6}  [{pc}/3]{marker}  {rules}")
        print()

    reasons = sa.get("shadow_pass_existing_rejection_reasons") or {}
    if reasons:
        print("  Among shadow-passed rows — existing rejection reasons:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {str(reason):<36} {count}")
        print()
        pcount_dist = sa.get("shadow_pass_existing_pcount") or {}
        if pcount_dist:
            print("  Among shadow-passed rows — existing passed_count_deep:")
            for k in sorted(pcount_dist.keys(), key=lambda x: (x is None, x)):
                print(f"    passed_count={k}: {pcount_dist[k]}")
        print()
    else:
        print(_dim("  No shadow-passed rows to analyse yet."))
        print()
