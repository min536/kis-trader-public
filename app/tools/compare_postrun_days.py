"""CLI: compare_postrun_days

Side-by-side comparison of two trading days (or two log windows) to measure
the effect of settings changes (SELL_CHECK_INTERVAL_SECONDS, etc.).

Usage:
    python3 -m app.tools.compare_postrun_days \\
        --account mock_12345678_01 \\
        --date-a 20260403 \\
        --date-b 20260406 \\
        --session REGULAR
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports — reuse existing budget analysis rather than duplicating it
# ---------------------------------------------------------------------------
from app.core.jsonl import read_jsonl_objects
from app.tools.analyze_budget_bottlenecks import run_analysis as _run_budget
from app.tools.analyze_technical_feature_activation import (
    run_analysis as _run_technical_activation,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _candidate_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"


def _stats_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows, _errors = read_jsonl_objects(path)
    return rows


# ---------------------------------------------------------------------------
# Candidate-outcomes stats
# ---------------------------------------------------------------------------

def _load_candidate_stats(account: str, date: str, session: str) -> dict[str, Any]:
    rows = _load_jsonl(_candidate_path(account, date))
    if session:
        target = session.upper()
        rows = [r for r in rows if str(r.get("session") or "").upper() == target]

    final_count = sum(1 for r in rows if r.get("final_candidate") is True)
    executed_count = sum(1 for r in rows if r.get("executed") is True)

    core_losers = [
        r for r in rows
        if r.get("selection_bucket") == "core"
        and r.get("deep_evaluated") is True
        and r.get("final_candidate") is not True
        and r.get("rejection_reason") is not None
    ]
    core_rej_counter = Counter(str(r["rejection_reason"]) for r in core_losers)
    top_core_rej = core_rej_counter.most_common(1)
    dominant_core_rej = top_core_rej[0][0] if top_core_rej else "—"

    return {
        "final_candidate_count": final_count,
        "executed_count": executed_count,
        "dominant_core_rejection": dominant_core_rej,
        "data_present": bool(rows),
    }


# ---------------------------------------------------------------------------
# Unified per-day metrics dict
# ---------------------------------------------------------------------------

def build_day_metrics(account: str, date: str, session: str) -> dict[str, Any]:
    budget = _run_budget(account=account, date=date, session=session)
    candidate = _load_candidate_stats(account, date, session)
    technical = _run_technical_activation(
        account=account,
        date=date,
        session=session,
        bucket="",
        last_n=0,
    )

    m = budget["meta"]
    f = budget["flags"]
    d = budget.get("drain", {})
    api = budget.get("api", {})
    tech_meta = technical.get("meta", {})
    tech_summary = technical.get("activation_summary", {})
    cycles = int(m["cycles_analyzed"])
    technical_analyzed = int(tech_meta.get("analyzed_rows", 0) or 0)
    technical_history_usable = int(tech_summary.get("history_usable_count", 0) or 0)
    technical_active = int(tech_summary.get("either_nonzero_count", 0) or 0)

    return {
        "date": date,
        "cycles": cycles,
        "data_present": cycles > 0 or candidate["data_present"],
        # pressure
        "rl_triggered": int(f.get("rate_limit_triggered", 0) or 0),
        "buy_skip": int(f.get("skipped_buy_scan_budget_limited", 0) or 0),
        "sell_partial": int(f.get("sell_watch_partial", 0) or 0),
        "avg_requests_per_cycle": float(api.get("avg_requests_per_cycle", 0.0) or 0.0),
        "avg_quotes_per_cycle": float(api.get("avg_quotes_per_cycle", 0.0) or 0.0),
        # drain (0 until backoff-drain fix is active)
        "sw_drain_cycles": int(d.get("sell_watch_drain_count", 0) or 0),
        "sw_drain_avg_ms": float(d.get("sell_watch_drain_avg_ms", 0.0) or 0.0),
        "exec_drain_cycles": int(d.get("exec_tail_drain_count", 0) or 0),
        "exec_drain_avg_ms": float(d.get("exec_tail_drain_avg_ms", 0.0) or 0.0),
        # outcomes
        "final_candidate_count": candidate["final_candidate_count"],
        "executed_count": candidate["executed_count"],
        "dominant_core_rejection": candidate["dominant_core_rejection"],
        "technical_analyzed_rows": technical_analyzed,
        "technical_history_usable_count": technical_history_usable,
        "technical_active_count": technical_active,
        "technical_history_usable_rate": _rate(
            technical_history_usable,
            technical_analyzed,
        ),
        "technical_active_rate": _rate(technical_active, technical_analyzed),
        "technical_load_warning_count": len(tech_meta.get("errors", []) or []),
    }


# ---------------------------------------------------------------------------
# Delta helpers
# ---------------------------------------------------------------------------

# Direction semantics: for each metric, True = higher is better
_HIGHER_IS_BETTER: dict[str, bool] = {
    "cycles": True,               # more cycles = longer trading window (neutral but treat as positive)
    "rl_triggered": False,        # fewer RL events = better
    "buy_skip": False,
    "sell_partial": False,
    "avg_requests_per_cycle": False,
    "avg_quotes_per_cycle": False,
    "sw_drain_cycles": True,      # drain fired = fix is working
    "exec_drain_cycles": True,
    "final_candidate_count": True,
    "executed_count": True,
    "technical_history_usable_rate": True,
    "technical_active_rate": True,
    "technical_load_warning_count": False,
}

# How much absolute change counts as "meaningful" (to filter noise)
_NOISE_FLOOR: dict[str, float] = {
    "cycles": 10,
    "rl_triggered": 2,
    "buy_skip": 2,
    "sell_partial": 3,
    "avg_requests_per_cycle": 0.25,
    "avg_quotes_per_cycle": 0.25,
    "sw_drain_cycles": 1,
    "exec_drain_cycles": 1,
    "final_candidate_count": 1,
    "executed_count": 1,
    "technical_history_usable_rate": 0.05,
    "technical_active_rate": 0.05,
    "technical_load_warning_count": 1,
}


def _direction(key: str, a: float, b: float) -> str:
    """Return ✓ (improved) / ✗ (worsened) / = (unchanged) / ~ (new field)."""
    floor = _NOISE_FLOOR.get(key, 1)
    delta = b - a
    if abs(delta) < floor:
        return "="
    higher_better = _HIGHER_IS_BETTER.get(key, True)
    if (delta > 0) == higher_better:
        return "✓"
    return "✗"


def _delta_str(a: float, b: float) -> str:
    delta = b - a
    sign = "+" if delta >= 0 else ""
    if isinstance(a, int) and isinstance(b, int):
        return f"{sign}{int(delta)}"
    return f"{sign}{delta:.1f}"


def _pct_str(value: int, total: int) -> str:
    if total == 0:
        return "  —  "
    return f"{100.0 * value / total:.1f}%"


# ---------------------------------------------------------------------------
# Operational health summary
# ---------------------------------------------------------------------------

# Verdict tokens
_V_IMPROVED  = "IMPROVED"
_V_WORSENED  = "WORSENED"
_V_MIXED     = "MIXED"
_V_UNCHANGED = "UNCHANGED"
_V_NODATA    = "NO DATA"


def _rate(value: int, cycles: int) -> float:
    return value / cycles if cycles > 0 else 0.0


def _rate_dir(rate_a: float, rate_b: float, *, higher_is_better: bool, noise: float = 0.02) -> str:
    """Compare two rates with a noise floor expressed as a fraction (0.02 = 2 pp)."""
    delta = rate_b - rate_a
    if abs(delta) < noise:
        return _V_UNCHANGED
    return _V_IMPROVED if (delta > 0) == higher_is_better else _V_WORSENED


def _combine_verdicts(*verdicts: str) -> str:
    """Merge sub-verdicts: unanimous = that verdict; mixed = MIXED; all unchanged = UNCHANGED."""
    meaningful = {v for v in verdicts if v not in (_V_UNCHANGED, _V_NODATA)}
    if not meaningful:
        return _V_UNCHANGED
    if len(meaningful) == 1:
        return meaningful.pop()
    return _V_MIXED


def operational_health_summary(ma: dict[str, Any], mb: dict[str, Any]) -> dict[str, Any]:
    ca, cb = ma["cycles"], mb["cycles"]

    # ── 1. Sell pressure ──────────────────────────────────────────────
    # Lower rl% and lower sell_partial% are both improvements.
    rl_a  = _rate(ma["rl_triggered"], ca)
    rl_b  = _rate(mb["rl_triggered"], cb)
    sp_a  = _rate(ma["sell_partial"],  ca)
    sp_b  = _rate(mb["sell_partial"],  cb)

    rl_v = _rate_dir(rl_a, rl_b, higher_is_better=False)
    sp_v = _rate_dir(sp_a, sp_b, higher_is_better=False)

    sell_verdict = _combine_verdicts(rl_v, sp_v)

    # ── 2. Buy survivability ──────────────────────────────────────────
    # Lower buy_skip% is the primary signal.
    # Drain activation (0 → >0) means the fix is working; it is a positive
    # secondary signal but does not override a worsened skip rate.
    bs_a = _rate(ma["buy_skip"], ca)
    bs_b = _rate(mb["buy_skip"], cb)
    skip_v = _rate_dir(bs_a, bs_b, higher_is_better=False)
    req_v = _direction(
        "avg_requests_per_cycle",
        float(ma.get("avg_requests_per_cycle", 0.0) or 0.0),
        float(mb.get("avg_requests_per_cycle", 0.0) or 0.0),
    )
    quote_v = _direction(
        "avg_quotes_per_cycle",
        float(ma.get("avg_quotes_per_cycle", 0.0) or 0.0),
        float(mb.get("avg_quotes_per_cycle", 0.0) or 0.0),
    )

    total_drain_a = ma["exec_drain_cycles"] + ma["sw_drain_cycles"]
    total_drain_b = mb["exec_drain_cycles"] + mb["sw_drain_cycles"]

    # Drain signal: only informational — doesn't override skip_v
    if total_drain_a == 0 and total_drain_b == 0:
        drain_note = "drain fix not yet active in either day"
    elif total_drain_b > 0 and total_drain_a == 0:
        drain_note = f"drain fix now active in B ({total_drain_b} cyc)"
    else:
        drain_note = f"drain A={total_drain_a} B={total_drain_b}"

    buy_verdict = skip_v  # drain is informational only

    # ── 3. Execution completeness ─────────────────────────────────────
    # Per-cycle rates for final_candidate and executed.
    # Tight noise floor: 0.5% per cycle (1 order per 200 cycles).
    fc_a = _rate(ma["final_candidate_count"], ca)
    fc_b = _rate(mb["final_candidate_count"], cb)
    ex_a = _rate(ma["executed_count"], ca)
    ex_b = _rate(mb["executed_count"], cb)

    fc_v = _rate_dir(fc_a, fc_b, higher_is_better=True, noise=0.005)
    ex_v = _rate_dir(ex_a, ex_b, higher_is_better=True, noise=0.003)

    exec_verdict = _combine_verdicts(fc_v, ex_v)

    return {
        "sell_pressure": {
            "verdict": sell_verdict,
            "rl_rate_a": rl_a,   "rl_rate_b": rl_b,
            "sp_rate_a": sp_a,   "sp_rate_b": sp_b,
            "rl_sub": rl_v,      "sp_sub": sp_v,
        },
        "buy_survivability": {
            "verdict": buy_verdict,
            "skip_rate_a": bs_a, "skip_rate_b": bs_b,
            "skip_sub": skip_v,
            "avg_requests_a": float(ma.get("avg_requests_per_cycle", 0.0) or 0.0),
            "avg_requests_b": float(mb.get("avg_requests_per_cycle", 0.0) or 0.0),
            "avg_quotes_a": float(ma.get("avg_quotes_per_cycle", 0.0) or 0.0),
            "avg_quotes_b": float(mb.get("avg_quotes_per_cycle", 0.0) or 0.0),
            "avg_requests_sub": req_v,
            "avg_quotes_sub": quote_v,
            "drain_note": drain_note,
        },
        "execution_completeness": {
            "verdict": exec_verdict,
            "fc_a": ma["final_candidate_count"], "fc_b": mb["final_candidate_count"],
            "ex_a": ma["executed_count"],         "ex_b": mb["executed_count"],
            "fc_sub": fc_v,                       "ex_sub": ex_v,
        },
        "technical_cold_start": {
            "history_rate_a": float(ma.get("technical_history_usable_rate", 0.0) or 0.0),
            "history_rate_b": float(mb.get("technical_history_usable_rate", 0.0) or 0.0),
            "active_rate_a": float(ma.get("technical_active_rate", 0.0) or 0.0),
            "active_rate_b": float(mb.get("technical_active_rate", 0.0) or 0.0),
            "history_sub": _direction(
                "technical_history_usable_rate",
                float(ma.get("technical_history_usable_rate", 0.0) or 0.0),
                float(mb.get("technical_history_usable_rate", 0.0) or 0.0),
            ),
            "active_sub": _direction(
                "technical_active_rate",
                float(ma.get("technical_active_rate", 0.0) or 0.0),
                float(mb.get("technical_active_rate", 0.0) or 0.0),
            ),
        },
    }


# ---------------------------------------------------------------------------
# Tuning suggestion logic
# ---------------------------------------------------------------------------

def _worsened(key: str, ma: dict[str, Any], mb: dict[str, Any]) -> bool:
    return _direction(key, float(ma.get(key, 0) or 0), float(mb.get(key, 0) or 0)) == "✗"


def _pct_of_cycles(value: int, cycles: int) -> float:
    return value / cycles if cycles > 0 else 0.0


# Each suggestion is a tuple of (observation_line, action_line, detail_line_or_None).
_Suggestion = tuple[str, str, str | None]


def suggest_tuning(ma: dict[str, Any], mb: dict[str, Any]) -> list[_Suggestion]:
    suggestions: list[_Suggestion] = []

    cb = mb["cycles"]
    rl_pct_b = _pct_of_cycles(mb["rl_triggered"], cb)
    sp_pct_b = _pct_of_cycles(mb["sell_partial"], cb)
    bs_pct_b = _pct_of_cycles(mb["buy_skip"], cb)

    # Rule 1 — sell_partial + rl_triggered both worsened
    if _worsened("sell_partial", ma, mb) and _worsened("rl_triggered", ma, mb):
        suggestions.append((
            f"sell_partial ({sp_pct_b:.0%}) and rl_triggered ({rl_pct_b:.0%}) both worsened in B",
            "SELL_CHECK_INTERVAL_SECONDS 증가 검토"
            " — sell_watch가 예산을 소진하기 전에 사이클당 평가 횟수를 줄입니다",
            "python3 -m app.tools.analyze_budget_bottlenecks"
            f" --date {mb['date']} --account <account> --session <session>",
        ))

    # Rule 2 — buy_skip worsened AND sell_watch pressure still high in B (>= 10 %)
    if _worsened("buy_skip", ma, mb) and rl_pct_b >= 0.10:
        suggestions.append((
            f"buy_skip worsened ({bs_pct_b:.0%}) and sell_watch RL pressure remains high"
            f" ({rl_pct_b:.0%})",
            "API_BUY_SCAN_MIN_REQUEST_RESERVE 증가 검토"
            " — sell_watch 소진 전에 buy_scan 예산 몫을 선점합니다",
            None,
        ))

    # Rule 3 — drain fix became active in B but executed_count still did not improve
    drain_active_b = mb["exec_drain_cycles"] > 0 or mb["sw_drain_cycles"] > 0
    drain_inactive_a = ma["exec_drain_cycles"] == 0 and ma["sw_drain_cycles"] == 0
    exec_not_improved = _direction("executed_count", float(ma["executed_count"]), float(mb["executed_count"])) != "✓"
    if drain_active_b and drain_inactive_a and exec_not_improved:
        suggestions.append((
            "drain fix is active in B but executed_count did not improve",
            "orderable/order tail 예산 흐름 점검 권장",
            "python3 -m app.tools.postrun_diagnostics"
            f" --date {mb['date']} --account <account> --session <session>",
        ))

    if (
        _worsened("avg_requests_per_cycle", ma, mb)
        or _worsened("avg_quotes_per_cycle", ma, mb)
    ):
        suggestions.append((
            "average API pressure worsened in B",
            "sell_watch / buy_scan 예산 cap이 실제 호출 수를 줄였는지 cycle_stats로 재점검 권장",
            "python3 -m app.tools.analyze_budget_bottlenecks"
            f" --date {mb['date']} --account <account> --session <session>",
        ))

    if _worsened("technical_history_usable_rate", ma, mb):
        suggestions.append((
            "technical history usable rate worsened in B",
            "cycle snapshot 가격 관측 저장 범위와 current snapshot 재사용 경로 점검 권장",
            "python3 -m app.tools.analyze_technical_feature_activation"
            f" --date {mb['date']} --account <account> --session <session>",
        ))

    # Rule 4 — dominant core rejection is passed_count_insufficient in B
    if mb.get("dominant_core_rejection") == "passed_count_insufficient":
        suggestions.append((
            "dominant core rejection reason in B: passed_count_insufficient",
            "core 버킷 전략별 hit율 확인 권장",
            "python3 -m app.tools.analyze_core_bucket"
            f" --date {mb['date']} --account <account> --session <session>",
        ))

    return suggestions


# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _c(col: str, txt: str) -> str:
    return f"{col}{txt}{_RESET}"


def _verdict_badge(v: str) -> str:
    if v == _V_IMPROVED:
        return _c(_GREEN,  "✓ IMPROVED ")
    if v == _V_WORSENED:
        return _c(_RED,    "✗ WORSENED ")
    if v == _V_MIXED:
        return _c(_YELLOW, "~ MIXED    ")
    if v == _V_NODATA:
        return _c(_DIM,    "- NO DATA  ")
    return _c(_DIM,        "= UNCHANGED")


def _print_health_summary(ma: dict[str, Any], mb: dict[str, Any]) -> None:
    h = operational_health_summary(ma, mb)
    da, db = ma["date"], mb["date"]

    print(_c(_BOLD + _CYAN, "  ── Operational Health Summary ─────────────────────────"))

    # ── sell pressure ──
    sp = h["sell_pressure"]
    rl_arrow  = f"rl {sp['rl_rate_a']:.0%}→{sp['rl_rate_b']:.0%}"
    spa_arrow = f"sell_partial {sp['sp_rate_a']:.0%}→{sp['sp_rate_b']:.0%}"
    print(
        f"  sell pressure       {_verdict_badge(sp['verdict'])}   "
        f"{rl_arrow},  {spa_arrow}"
    )

    # ── buy survivability ──
    bv = h["buy_survivability"]
    skip_arrow = f"buy_skip {bv['skip_rate_a']:.0%}→{bv['skip_rate_b']:.0%}"
    api_arrow = (
        f"api req {bv['avg_requests_a']:.2f}→{bv['avg_requests_b']:.2f}, "
        f"quotes {bv['avg_quotes_a']:.2f}→{bv['avg_quotes_b']:.2f}"
    )
    print(
        f"  buy survivability   {_verdict_badge(bv['verdict'])}   "
        f"{skip_arrow},  {api_arrow},  {bv['drain_note']}"
    )

    # ── execution completeness ──
    ev = h["execution_completeness"]
    fc_arrow = f"final {ev['fc_a']}→{ev['fc_b']}"
    ex_arrow = f"executed {ev['ex_a']}→{ev['ex_b']}"
    exec_caveat = "  ⚠ execution count also reflects market conditions" if ev["verdict"] in (_V_WORSENED, _V_MIXED) else ""
    print(
        f"  execution           {_verdict_badge(ev['verdict'])}   "
        f"{fc_arrow},  {ex_arrow}"
    )
    if exec_caveat:
        print(_c(_DIM, exec_caveat))

    tv = h["technical_cold_start"]
    tech_badge = _combine_verdicts(
        _V_IMPROVED if tv["history_sub"] == "✓" else _V_WORSENED if tv["history_sub"] == "✗" else _V_UNCHANGED,
        _V_IMPROVED if tv["active_sub"] == "✓" else _V_WORSENED if tv["active_sub"] == "✗" else _V_UNCHANGED,
    )
    print(
        f"  technical history   {_verdict_badge(tech_badge)}   "
        f"usable {tv['history_rate_a']:.0%}→{tv['history_rate_b']:.0%},  "
        f"active {tv['active_rate_a']:.0%}→{tv['active_rate_b']:.0%}"
    )

    print()


def _print_suggestions(suggestions: list[_Suggestion]) -> None:
    print(_c(_BOLD + _CYAN, "  ── Suggested Next Tuning Action ───────────────────────"))
    if not suggestions:
        print(_c(_DIM, "  no clear tuning recommendation yet — gather more data and re-compare"))
    else:
        for i, (obs, action, detail) in enumerate(suggestions, 1):
            print(f"  {i}. {_c(_YELLOW, obs)}")
            print(f"     → {action}")
            if detail:
                print(_c(_DIM, f"       {detail}"))
    print()


def _dir_colored(d: str) -> str:
    if d == "✓":
        return _c(_GREEN, d)
    if d == "✗":
        return _c(_RED, d)
    if d == "=":
        return _c(_DIM, d)
    return d


# ---------------------------------------------------------------------------
# Print report
# ---------------------------------------------------------------------------

def print_comparison(ma: dict[str, Any], mb: dict[str, Any]) -> None:
    da, db = ma["date"], mb["date"]
    ca, cb = ma["cycles"], mb["cycles"]

    print()
    print(_c(_BOLD + _CYAN, "═" * 74))
    print(_c(_BOLD + _CYAN, "  compare_postrun_days"))
    print(_c(_BOLD + _CYAN, "═" * 74))
    print(f"  A : {da}   B : {db}")
    print()

    # Column widths
    w_metric = 28
    w_val = 20

    header = (
        f"  {'Metric':<{w_metric}} "
        f"{'A  '+da:>{w_val}}   "
        f"{'B  '+db:>{w_val}}   "
        f"{'Δ':>6}"
    )
    print(_c(_BOLD, header))
    print(_c(_DIM, "  " + "─" * 72))

    improved: list[str] = []
    worsened: list[str] = []
    unchanged: list[str] = []
    new_field: list[str] = []

    def _row(label: str, key: str, fmt_a: str, fmt_b: str, *, skip_delta: bool = False) -> None:
        if skip_delta:
            symbol = "~"
            delta_s = "—"
        else:
            va = ma.get(key, 0) or 0
            vb = mb.get(key, 0) or 0
            symbol = _direction(key, float(va), float(vb))
            delta_s = _delta_str(float(va), float(vb))
            if symbol == "✓":
                improved.append(label)
            elif symbol == "✗":
                worsened.append(label)
            else:
                unchanged.append(label)
        colored_sym = _dir_colored(symbol)
        print(
            f"  {label:<{w_metric}} "
            f"{fmt_a:>{w_val}}   "
            f"{fmt_b:>{w_val}}   "
            f"{delta_s:>4}  {colored_sym}"
        )

    _row(
        "cycles",
        "cycles",
        str(ca),
        str(cb),
    )
    _row(
        "rl_triggered",
        "rl_triggered",
        f"{ma['rl_triggered']} ({_pct_str(ma['rl_triggered'], ca)})",
        f"{mb['rl_triggered']} ({_pct_str(mb['rl_triggered'], cb)})",
    )
    _row(
        "buy_skip",
        "buy_skip",
        f"{ma['buy_skip']} ({_pct_str(ma['buy_skip'], ca)})",
        f"{mb['buy_skip']} ({_pct_str(mb['buy_skip'], cb)})",
    )
    _row(
        "sell_partial",
        "sell_partial",
        f"{ma['sell_partial']} ({_pct_str(ma['sell_partial'], ca)})",
        f"{mb['sell_partial']} ({_pct_str(mb['sell_partial'], cb)})",
    )
    _row(
        "avg_requests_per_cycle",
        "avg_requests_per_cycle",
        f"{ma['avg_requests_per_cycle']:.2f}",
        f"{mb['avg_requests_per_cycle']:.2f}",
    )
    _row(
        "avg_quotes_per_cycle",
        "avg_quotes_per_cycle",
        f"{ma['avg_quotes_per_cycle']:.2f}",
        f"{mb['avg_quotes_per_cycle']:.2f}",
    )

    print(_c(_DIM, "  " + "─" * 72))

    # Drain: if both zero it just means the fix hasn't fired yet — mark neutral
    sw_both_zero = ma["sw_drain_cycles"] == 0 and mb["sw_drain_cycles"] == 0
    ex_both_zero = ma["exec_drain_cycles"] == 0 and mb["exec_drain_cycles"] == 0

    if sw_both_zero:
        new_field.append("sw_drain_cycles")
        _row(
            "sw_drain_cycles",
            "sw_drain_cycles",
            "0",
            "0",
            skip_delta=True,
        )
        print(
            f"  {'sw_drain_avg_ms':<{w_metric}} "
            f"{'—':>{w_val}}   {'—':>{w_val}}   {'—':>4}  ~"
        )
    else:
        _row(
            "sw_drain_cycles",
            "sw_drain_cycles",
            str(ma["sw_drain_cycles"]),
            str(mb["sw_drain_cycles"]),
        )
        _row(
            "sw_drain_avg_ms",
            "sw_drain_avg_ms",
            f"{ma['sw_drain_avg_ms']:.0f} ms",
            f"{mb['sw_drain_avg_ms']:.0f} ms",
        )

    if ex_both_zero:
        new_field.append("exec_drain_cycles")
        _row(
            "exec_drain_cycles",
            "exec_drain_cycles",
            "0",
            "0",
            skip_delta=True,
        )
        print(
            f"  {'exec_drain_avg_ms':<{w_metric}} "
            f"{'—':>{w_val}}   {'—':>{w_val}}   {'—':>4}  ~"
        )
    else:
        _row(
            "exec_drain_cycles",
            "exec_drain_cycles",
            str(ma["exec_drain_cycles"]),
            str(mb["exec_drain_cycles"]),
        )
        _row(
            "exec_drain_avg_ms",
            "exec_drain_avg_ms",
            f"{ma['exec_drain_avg_ms']:.0f} ms",
            f"{mb['exec_drain_avg_ms']:.0f} ms",
        )

    print(_c(_DIM, "  " + "─" * 72))

    _row(
        "final_candidate_count",
        "final_candidate_count",
        str(ma["final_candidate_count"]),
        str(mb["final_candidate_count"]),
    )
    _row(
        "executed_count",
        "executed_count",
        str(ma["executed_count"]),
        str(mb["executed_count"]),
    )
    _row(
        "technical_history_usable_rate",
        "technical_history_usable_rate",
        f"{ma['technical_history_usable_rate']:.1%}",
        f"{mb['technical_history_usable_rate']:.1%}",
    )
    _row(
        "technical_active_rate",
        "technical_active_rate",
        f"{ma['technical_active_rate']:.1%}",
        f"{mb['technical_active_rate']:.1%}",
    )
    _row(
        "technical_load_warning_count",
        "technical_load_warning_count",
        str(ma["technical_load_warning_count"]),
        str(mb["technical_load_warning_count"]),
    )

    rej_a = ma["dominant_core_rejection"]
    rej_b = mb["dominant_core_rejection"]
    rej_same = rej_a == rej_b
    if rej_same:
        unchanged.append("dominant_core_rejection")
    print(
        f"  {'dominant_core_rejection':<{w_metric}} "
        f"{rej_a:>{w_val}}   "
        f"{rej_b:>{w_val}}   "
        f"{'—':>4}  {_dir_colored('=' if rej_same else '~')}"
    )

    # Delta summary
    print()
    print(_c(_BOLD + _CYAN, "  ── Delta Summary ──────────────────────────────────────"))
    if improved:
        print(_c(_GREEN, f"  improved  : {', '.join(improved)}"))
    else:
        print(_c(_DIM, "  improved  : (none)"))
    if worsened:
        print(_c(_RED, f"  worsened  : {', '.join(worsened)}"))
    else:
        print(_c(_DIM, "  worsened  : (none)"))
    if unchanged:
        print(_c(_DIM, f"  unchanged : {', '.join(unchanged)}"))
    if new_field:
        print(_c(_YELLOW, f"  no data   : {', '.join(new_field)} — drain fix not yet active in either day"))

    print()
    _print_health_summary(ma, mb)
    _print_suggestions(suggest_tuning(ma, mb))
    print(_c(_BOLD + _CYAN, "═" * 74))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two trading days before/after settings tuning."
    )
    parser.add_argument("--account", required=True)
    parser.add_argument("--date-a", required=True, dest="date_a")
    parser.add_argument("--date-b", required=True, dest="date_b")
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    ma = build_day_metrics(args.account, args.date_a, args.session)
    mb = build_day_metrics(args.account, args.date_b, args.session)

    if args.json:
        print(
            json.dumps(
                {
                    "meta": {
                        "account": args.account,
                        "date_a": args.date_a,
                        "date_b": args.date_b,
                        "session": args.session,
                    },
                    "day_a": ma,
                    "day_b": mb,
                    "health": operational_health_summary(ma, mb),
                    "suggestions": suggest_tuning(ma, mb),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(0)

    print_comparison(ma, mb)
    sys.exit(0)


if __name__ == "__main__":
    main()
