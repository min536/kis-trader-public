"""Signal dataset section builders (extracted from analyze_signal_dataset)."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[96m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_RESET  = "\033[0m"
_W      = 72


def _h(t: str)   -> str: return f"{_BOLD}{_CYAN}{t}{_RESET}"
def _ok(t: str)  -> str: return f"{_GREEN}{t}{_RESET}"
def _warn(t: str)-> str: return f"{_YELLOW}{t}{_RESET}"
def _bad(t: str) -> str: return f"{_RED}{t}{_RESET}"
def _dim(t: str) -> str: return f"{_DIM}{t}{_RESET}"
def _fval(row: dict[str, Any], col: str) -> float | None:
    v = row.get(col)
    if v is None or (isinstance(v, str) and v.strip() in ("", "None")):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
def _bool_val(row: dict[str, Any], col: str) -> bool:
    v = row.get(col)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)
def _fvals(rows: list[dict[str, Any]], col: str) -> list[float]:
    out = []
    for r in rows:
        v = _fval(r, col)
        if v is not None:
            out.append(v)
    return out
def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]
def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None
def _pct(n: int, total: int) -> str:
    return f"{round(n / total * 100):3d}%" if total else "  0%"
def _fmt(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "   —"
    return f"{v:>{4 + decimals}.{decimals}f}"
def _delta_fmt(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "    —"
    d = a - b
    s = f"{d:+.2f}"
    if d > 0.05:
        return _ok(f"{s:>7}")
    if d < -0.05:
        return _warn(f"{s:>7}")
    return _dim(f"{s:>7}")
def _minute_of_day(raw_ts: Any) -> int | None:
    if raw_ts is None:
        return None
    ts = str(raw_ts).strip()
    if ts in ("", "None"):
        return None
    normalized = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.hour * 60 + dt.minute
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.hour * 60 + dt.minute
        except ValueError:
            continue
    return None
_TIME_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("09:00–10:00", 9 * 60, 10 * 60),
    ("10:00–11:00", 10 * 60, 11 * 60),
    ("11:00–12:00", 11 * 60, 12 * 60),
    ("12:00–13:00", 12 * 60, 13 * 60),
    ("13:00–14:00", 13 * 60, 14 * 60),
    ("14:00–15:30", 14 * 60, 15 * 60 + 30),
)


def _time_bucket_label(raw_ts: Any) -> str:
    minute = _minute_of_day(raw_ts)
    if minute is None:
        return "(missing/invalid ts)"
    for idx, (label, start_min, end_min) in enumerate(_TIME_BUCKETS):
        if idx == len(_TIME_BUCKETS) - 1:
            if start_min <= minute <= end_min:
                return label
        elif start_min <= minute < end_min:
            return label
    return "(outside 09:00–15:30)"
    # end _time_bucket_label
def _bucket_order(rows: list[dict[str, Any]]) -> list[str]:
    preferred = ["core", "rotating", "exploration"]
    present = {str(r.get("selection_bucket") or "?") for r in rows}
    ordered = [b for b in preferred if b in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered
def _bucket_code(bucket: str) -> str:
    mapping = {
        "core": "C",
        "rotating": "R",
        "exploration": "E",
    }
    return mapping.get(bucket, bucket[:1].upper() or "?")
def _bucket_count_text(
    rows: list[dict[str, Any]],
    buckets: list[str],
    predicate: Any,
) -> str:
    counts = Counter(
        str(r.get("selection_bucket") or "?")
        for r in rows
        if predicate(r)
    )
    return "/".join(f"{counts.get(bucket, 0):>2}" for bucket in buckets)
def _short_bucket_counts(rows: list[dict[str, Any]], buckets: list[str]) -> str:
    counts = Counter(str(r.get("selection_bucket") or "?") for r in rows)
    return " / ".join(f"{_bucket_code(bucket)}:{counts.get(bucket, 0)}" for bucket in buckets)
def _cycle_shortlist_cutoffs(rows: list[dict[str, Any]]) -> dict[str, float]:
    cutoffs: dict[str, float] = {}
    rows_by_cycle: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        cycle_id = str(row.get("cycle_id") or "").strip()
        if cycle_id:
            rows_by_cycle.setdefault(cycle_id, []).append(row)

    for cycle_id, cycle_rows in rows_by_cycle.items():
        selected_scores = [
            float(row["score_shallow"])
            for row in cycle_rows
            if _bool_val(row, "shallow_selected") and _fval(row, "score_shallow") is not None
        ]
        if selected_scores:
            cutoffs[cycle_id] = min(selected_scores)
    return cutoffs
_FEATURE_COLS: tuple[str, ...] = (
    "score_deep",
    "passed_count_deep",
    "trend_alignment_score",
    "macd_momentum_score",
    "pullback_pct",
    "gap_up_open_pct",
    "range_recovery_ratio",
)

_FEATURE_SHORT: dict[str, str] = {
    "score_deep":             "score",
    "passed_count_deep":      "passed",
    "trend_alignment_score":  "trend_aln",
    "macd_momentum_score":    "macd_mom",
    "pullback_pct":           "pullback",
    "gap_up_open_pct":        "gap_up",
    "range_recovery_ratio":   "range_rec",
}


def _section_bucket(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  1. Bucket comparison"))
    print(_h("─" * _W))

    buckets = sorted({str(r.get("selection_bucket") or "?") for r in rows})
    hdr_cols = ["score", "passed", "trend_aln", "macd_mom", "pullback", "gap_up", "range_rec"]
    print(
        f"  {'bucket':<14} {'rows':>5} {'deep':>5} {'final':>6} "
        + "  ".join(f"{c:>9}" for c in hdr_cols)
    )
    print(f"  {'-'*14} {'-'*5} {'-'*5} {'-'*6} " + "  ".join("-" * 9 for _ in hdr_cols))

    for bkt in buckets:
        brows = [r for r in rows if r.get("selection_bucket") == bkt]
        deep  = sum(1 for r in brows if _bool_val(r, "deep_evaluated"))
        final = sum(1 for r in brows if _bool_val(r, "final_candidate"))
        meds  = [_median(_fvals(brows, c)) for c in _FEATURE_COLS]

        final_txt = _ok(f"{final:>6}") if final > 0 else _dim(f"{final:>6}")
        print(
            f"  {bkt:<14} {len(brows):>5} {deep:>5} {final_txt} "
            + "  ".join(_fmt(m) for m in meds),
        )

    print()
def _section_outcome(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  2. final_candidate vs deep_eval_rejected"))
    print(_h("─" * _W))

    final  = [r for r in rows if _bool_val(r, "final_candidate")]
    reject = [r for r in rows
              if str(r.get("selection_outcome") or "") == "deep_eval_rejected"]
    not_sel= [r for r in rows
              if str(r.get("selection_outcome") or "") == "not_selected_after_deep_eval"]

    groups = [
        ("final_candidate",           final),
        ("deep_eval_rejected",         reject),
        ("not_selected_after_deep_eval", not_sel),
    ]

    col_w = 26
    print(f"  {'feature':<{col_w}}", end="")
    for label, grp in groups:
        short = label[:20]
        print(f"  {short:>22}(n={len(grp)})", end="")
    print()
    print(f"  {'-'*col_w}", end="")
    for _ in groups:
        print(f"  {'-'*27}", end="")
    print()

    for col in _FEATURE_COLS:
        meds = [_median(_fvals(grp, col)) for _, grp in groups]
        print(f"  {col:<{col_w}}", end="")
        for i, (m) in enumerate(meds):
            if i == 0:
                print(f"  {_fmt(m):>27}", end="")
            else:
                delta = _delta_fmt(meds[0], m)
                print(f"  {_fmt(m):>18} Δ{delta}", end="")
        print()

    print()
    print(_dim(f"  Δ = final_candidate median minus comparison group median"))
    print()
def _section_rejection(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  3. Rejection breakdown (deep_eval rows only)"))
    print(_h("─" * _W))

    deep_rows = [r for r in rows if _bool_val(r, "deep_evaluated")]
    reasons: dict[str, list[dict[str, Any]]] = {}
    for r in deep_rows:
        reason = str(r.get("rejection_reason") or "").strip() or "(none / selected)"
        reasons.setdefault(reason, []).append(r)

    total = len(deep_rows)
    short_features = ["score", "passed", "trend_aln", "macd_mom", "pullback"]
    full_features  = ["score_deep", "passed_count_deep", "trend_alignment_score",
                      "macd_momentum_score", "pullback_pct"]

    print(
        f"  {'reason':<32} {'n':>5} {'pct':>4} "
        + "  ".join(f"{c:>9}" for c in short_features)
        + "  (median)"
    )
    print(f"  {'-'*32} {'-'*5} {'-'*4} " + "  ".join("-" * 9 for _ in short_features))

    for reason, grp in sorted(reasons.items(), key=lambda x: -len(x[1])):
        meds = [_median(_fvals(grp, c)) for c in full_features]
        reason_txt = reason[:32]
        n = len(grp)
        pct_txt = _pct(n, total)
        print(
            f"  {reason_txt:<32} {n:>5} {pct_txt} "
            + "  ".join(_fmt(m) for m in meds)
        )

    print()
def _section_time_of_day(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  4. Time-of-day analysis"))
    print(_h("─" * _W))

    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in _TIME_BUCKETS}
    grouped["(missing/invalid ts)"] = []
    grouped["(outside 09:00–15:30)"] = []

    for row in rows:
        grouped[_time_bucket_label(row.get("ts"))].append(row)

    ordered_labels = [label for label, _, _ in _TIME_BUCKETS] + [
        "(missing/invalid ts)",
        "(outside 09:00–15:30)",
    ]

    print(
        f"  {'time':<20} {'rows':>5} {'deep':>5} {'final':>6} {'exec':>5} "
        f"{'pass_ins':>8} {'pb_ins':>7} {'budget':>6} {'score':>7} {'passed':>7}"
    )
    print(
        f"  {'-'*20} {'-'*5} {'-'*5} {'-'*6} {'-'*5} "
        f"{'-'*8} {'-'*7} {'-'*6} {'-'*7} {'-'*7}"
    )

    for label in ordered_labels:
        grp = grouped[label]
        if not grp and label.startswith("("):
            continue

        deep = sum(1 for r in grp if _bool_val(r, "deep_evaluated"))
        final = sum(1 for r in grp if _bool_val(r, "final_candidate"))
        executed = sum(1 for r in grp if _bool_val(r, "executed"))
        pass_ins = sum(
            1 for r in grp
            if str(r.get("rejection_reason") or "").strip() == "passed_count_insufficient"
        )
        pb_ins = sum(
            1 for r in grp
            if str(r.get("rejection_reason") or "").strip() == "profit_buffer_insufficient"
        )
        budget = sum(
            1 for r in grp
            if str(r.get("rejection_reason") or "").strip() == "trade_budget_limited"
        )
        score_med = _median(_fvals(grp, "score_deep"))
        passed_med = _median(_fvals(grp, "passed_count_deep"))

        final_txt = _ok(f"{final:>6}") if final > 0 else _dim(f"{final:>6}")
        exec_txt = _ok(f"{executed:>5}") if executed > 0 else _dim(f"{executed:>5}")
        print(
            f"  {label:<20} {len(grp):>5} {deep:>5} {final_txt} {exec_txt} "
            f"{pass_ins:>8} {pb_ins:>7} {budget:>6} {_fmt(score_med):>7} {_fmt(passed_med):>7}"
        )

    print()
def _section_time_bucket_crosstab(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  5. Time x bucket x outcome/rejection"))
    print(_h("─" * _W))

    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in _TIME_BUCKETS}
    grouped["(missing/invalid ts)"] = []
    grouped["(outside 09:00–15:30)"] = []
    for row in rows:
        grouped[_time_bucket_label(row.get("ts"))].append(row)

    ordered_labels = [label for label, _, _ in _TIME_BUCKETS] + [
        "(missing/invalid ts)",
        "(outside 09:00–15:30)",
    ]
    buckets = _bucket_order(rows)
    legend = " / ".join(f"{_bucket_code(bucket)}={bucket}" for bucket in buckets)

    print(f"  bucket legend: {legend}")
    print(
        f"  {'time':<20} {'rows':>5} {'final':>8} {'pass_ins':>8} "
        f"{'pb_ins':>8} {'budget':>8}"
    )
    print(
        f"  {'-'*20} {'-'*5} {'-'*8} {'-'*8} "
        f"{'-'*8} {'-'*8}"
    )

    for label in ordered_labels:
        grp = grouped[label]
        if not grp and label.startswith("("):
            continue

        final_txt = _bucket_count_text(grp, buckets, lambda r: _bool_val(r, "final_candidate"))
        pass_txt = _bucket_count_text(
            grp,
            buckets,
            lambda r: str(r.get("rejection_reason") or "").strip() == "passed_count_insufficient",
        )
        pb_txt = _bucket_count_text(
            grp,
            buckets,
            lambda r: str(r.get("rejection_reason") or "").strip() == "profit_buffer_insufficient",
        )
        budget_txt = _bucket_count_text(
            grp,
            buckets,
            lambda r: str(r.get("rejection_reason") or "").strip() == "trade_budget_limited",
        )

        print(
            f"  {label:<20} {len(grp):>5} {final_txt:>8} {pass_txt:>8} "
            f"{pb_txt:>8} {budget_txt:>8}"
        )

    print()
def _section_trade_budget_limited(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  6. trade_budget_limited analysis"))
    print(_h("─" * _W))

    budget_rows = [
        r for r in rows
        if str(r.get("rejection_reason") or "").strip() == "trade_budget_limited"
    ]
    final_rows = [r for r in rows if _bool_val(r, "final_candidate")]
    deep_rows = [r for r in rows if _bool_val(r, "deep_evaluated")]

    budget_n = len(budget_rows)
    print(
        f"  budget_limited rows : {budget_n} "
        f"({budget_n}/{len(rows)} total, {_pct(budget_n, len(rows))}; {_pct(budget_n, len(deep_rows))} of deep)"
    )

    if not budget_rows:
        print(_dim("  No trade_budget_limited rows in this dataset."))
        print()
        return

    by_bucket = Counter(str(r.get("selection_bucket") or "?") for r in budget_rows)
    buckets = _bucket_order(rows)
    bucket_txt = " / ".join(f"{_bucket_code(bucket)}:{by_bucket.get(bucket, 0)}" for bucket in buckets)
    print(f"  by bucket            : {bucket_txt}")
    print()

    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label, _, _ in _TIME_BUCKETS}
    grouped["(missing/invalid ts)"] = []
    grouped["(outside 09:00–15:30)"] = []
    for row in budget_rows:
        grouped[_time_bucket_label(row.get("ts"))].append(row)

    ordered_labels = [label for label, _, _ in _TIME_BUCKETS] + [
        "(missing/invalid ts)",
        "(outside 09:00–15:30)",
    ]
    legend = " / ".join(f"{_bucket_code(bucket)}={bucket}" for bucket in buckets)

    print(f"  time x bucket        : {legend}")
    print(f"  {'time':<20} {'rows':>5} {'by_bucket':>12}")
    print(f"  {'-'*20} {'-'*5} {'-'*12}")
    for label in ordered_labels:
        grp = grouped[label]
        if not grp and label.startswith("("):
            continue
        print(
            f"  {label:<20} {len(grp):>5} "
            f"{_bucket_count_text(grp, buckets, lambda r: True):>12}"
        )
    print()

    feature_cols = [
        "score_deep",
        "passed_count_deep",
        "trend_alignment_score",
        "macd_momentum_score",
        "pullback_pct",
    ]
    col_w = 22
    print(f"  {'feature':<{col_w}} {'budget(n='+str(len(budget_rows))+')':>14} {'final(n='+str(len(final_rows))+')':>14} {'Δbudget-final':>14}")
    print(f"  {'-'*col_w} {'-'*14} {'-'*14} {'-'*14}")
    for col in feature_cols:
        budget_med = _median(_fvals(budget_rows, col))
        final_med = _median(_fvals(final_rows, col))
        print(
            f"  {col:<{col_w}} {_fmt(budget_med):>14} {_fmt(final_med):>14} {_delta_fmt(budget_med, final_med):>23}"
        )
    print()
def _section_bottleneck_summary(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  7. Bottleneck summary"))
    print(_h("─" * _W))

    buckets = _bucket_order(rows)
    final_budget = [
        r for r in rows
        if _bool_val(r, "final_candidate")
        and str(r.get("rejection_reason") or "").strip() == "trade_budget_limited"
    ]
    other_final = [
        r for r in rows
        if _bool_val(r, "final_candidate")
        and str(r.get("rejection_reason") or "").strip() != "trade_budget_limited"
    ]
    core_shortlisted_not_deep = [
        r for r in rows
        if str(r.get("selection_bucket") or "") == "core"
        and _bool_val(r, "shallow_selected")
        and not _bool_val(r, "deep_evaluated")
    ]
    core_shallow_miss = [
        r for r in rows
        if str(r.get("selection_bucket") or "") == "core"
        and str(r.get("stage_reached") or "") == "shallow_ranked"
        and not _bool_val(r, "shallow_selected")
    ]
    cutoffs = _cycle_shortlist_cutoffs(rows)
    miss_gaps = []
    for row in core_shallow_miss:
        cutoff = cutoffs.get(str(row.get("cycle_id") or "").strip())
        score = _fval(row, "score_shallow")
        if cutoff is not None and score is not None:
            miss_gaps.append(cutoff - score)

    print("  overview")
    print(f"    A blocked finals          : {len(final_budget)}")
    print(f"    B core shortlist no-deep : {len(core_shortlisted_not_deep)}")
    print(f"    C core shallow misses     : {len(core_shallow_miss)}")
    print()

    print("  A. execution-budget blocked finals")
    print(
        f"    count={len(final_budget)}"
        + (
            f" | buckets={_short_bucket_counts(final_budget, buckets)}"
            if final_budget else ""
        )
    )
    if final_budget:
        print(
            "    medians"
            f" | score={_fmt(_median(_fvals(final_budget, 'score_deep'))).strip()}"
            f" | passed={_fmt(_median(_fvals(final_budget, 'passed_count_deep'))).strip()}"
            f" | pullback={_fmt(_median(_fvals(final_budget, 'pullback_pct'))).strip()}"
        )
        if other_final:
            print(
                "    vs other final"
                f" | score Δ{_delta_fmt(_median(_fvals(final_budget, 'score_deep')), _median(_fvals(other_final, 'score_deep')))}"
                f" | passed Δ{_delta_fmt(_median(_fvals(final_budget, 'passed_count_deep')), _median(_fvals(other_final, 'passed_count_deep')))}"
                f" | pullback Δ{_delta_fmt(_median(_fvals(final_budget, 'pullback_pct')), _median(_fvals(other_final, 'pullback_pct')))}"
            )
        else:
            print(_dim("    No non-budget final_candidate rows to compare in this dataset."))
    else:
        print(_dim("    No final_candidate + trade_budget_limited rows in this dataset."))
    print()

    print("  B. core shortlisted-but-not-deep-evaluated")
    print(f"    count={len(core_shortlisted_not_deep)}")
    if core_shortlisted_not_deep:
        outcomes = Counter(
            str(r.get("selection_outcome") or "?") for r in core_shortlisted_not_deep
        )
        stages = Counter(str(r.get("stage_reached") or "?") for r in core_shortlisted_not_deep)
        print(
            "    outcomes="
            + ", ".join(f"{key}:{value}" for key, value in outcomes.most_common(3))
        )
        print(
            "    stages="
            + ", ".join(f"{key}:{value}" for key, value in stages.most_common(2))
        )
        print(_dim("    Exported dataset does not carry API/rate-limit cause flags for these rows."))
    else:
        print(_dim("    No core shortlist rows stalled before deep_eval in this dataset."))
    print()

    print("  C. core shallow-score misses")
    print(f"    count={len(core_shallow_miss)}")
    if core_shallow_miss:
        print(
            "    medians"
            f" | core score={_fmt(_median(_fvals(core_shallow_miss, 'score_shallow'))).strip()}"
            f" | shortlisted non-core={_fmt(_median(_fvals([r for r in rows if str(r.get('selection_bucket') or '') != 'core' and _bool_val(r, 'shallow_selected')], 'score_shallow'))).strip()}"
        )
        if miss_gaps:
            print(
                "    shortlist miss gap"
                f" | median={_fmt(_median(miss_gaps)).strip()}"
                f" | best={_fmt(min(miss_gaps)).strip()}"
                f" | worst={_fmt(max(miss_gaps)).strip()}"
            )
        else:
            print(_dim("    Shortlist cutoff gap is not derivable from this dataset slice."))
    else:
        print(_dim("    No core shallow-ranked misses in this dataset."))
    print()
def _section_core_rescue(rows: list[dict[str, Any]]) -> None:
    print(_h("─" * _W))
    print(_h("  8. Core rescue analysis"))
    print(_h("─" * _W))

    rescue_rows = [r for r in rows if _bool_val(r, "core_rescue_applied")]
    if not rescue_rows:
        print(_dim("  No core_rescue_applied=True rows in this dataset."))
        if not any(
            str(r.get("stage_reached") or "") in {"pre_gate_rejected", "shallow_ranked", "shallow_selected"}
            for r in rows
        ):
            print(_dim("  Note: export with --all-stages to inspect rescued rows that stalled before deep_eval."))
        print()
        return

    buckets = _bucket_order(rows)
    deep_count = sum(1 for r in rescue_rows if _bool_val(r, "deep_evaluated"))
    final_count = sum(1 for r in rescue_rows if _bool_val(r, "final_candidate"))
    executed_count = sum(1 for r in rescue_rows if _bool_val(r, "executed"))

    print(f"  rescued rows          : {len(rescue_rows)}")
    print(f"  buckets               : {_short_bucket_counts(rescue_rows, buckets)}")
    print(
        "  funnel"
        f" | deep={deep_count}"
        f" | final={final_count}"
        f" | executed={executed_count}"
    )
    print(
        "  medians"
        f" | shallow={_fmt(_median(_fvals(rescue_rows, 'score_shallow'))).strip()}"
        f" | deep={_fmt(_median(_fvals(rescue_rows, 'score_deep'))).strip()}"
        f" | passed={_fmt(_median(_fvals(rescue_rows, 'passed_count_deep'))).strip()}"
    )

    time_counts = []
    for label, _, _ in _TIME_BUCKETS:
        cnt = sum(1 for r in rescue_rows if _time_bucket_label(r.get("ts")) == label)
        if cnt > 0:
            time_counts.append(f"{label}:{cnt}")
    if time_counts:
        print("  by time               : " + ", ".join(time_counts))

    reasons = Counter(str(r.get("core_rescue_reason") or "?") for r in rescue_rows)
    if reasons:
        print(
            "  rescue reasons        : "
            + ", ".join(f"{key}:{value}" for key, value in reasons.most_common(3))
        )
    print()
def _section_core_shadow(rows: list[dict[str, Any]]) -> None:
    shadow_rows = [r for r in rows
                   if str(r.get("core_shadow_evaluated") or "").strip().lower() == "true"]
    if not shadow_rows:
        print(_h("─" * _W))
        print(_h("  9. Core shadow fields"))
        print(_h("─" * _W))
        print(_dim("  No core_shadow_evaluated=True rows in this dataset."))
        print(_dim("  (Fields will populate once logs contain core_shadow_* entries.)"))
        print()
        return

    print(_h("─" * _W))
    print(_h("  9. Core shadow analysis"))
    print(_h("─" * _W))

    total = len(shadow_rows)
    gate_passed = [r for r in shadow_rows
                   if str(r.get("core_shadow_trend_gate_passed") or "").strip().lower() == "true"]
    shadow_passed = [r for r in shadow_rows
                     if str(r.get("core_shadow_passed") or "").strip().lower() == "true"]
    final_in_shadow = [r for r in shadow_rows if _bool_val(r, "final_candidate")]

    print(f"  core_shadow evaluated  : {total}")
    print(f"  trend gate passed      : {len(gate_passed)} ({_pct(len(gate_passed), total)})")
    print(f"  shadow passed          : {len(shadow_passed)} ({_pct(len(shadow_passed), total)})")
    print(f"  final_candidate        : {len(final_in_shadow)} ({_pct(len(final_in_shadow), total)})")
    print()

    # pattern breakdown
    patterns = Counter(
        str(r.get("core_shadow_pattern") or "?") for r in shadow_rows
    )
    print(f"  {'pattern':<12} {'n':>5}  (P=pass F=fail: non_overext | macd_mom | intraday_stab)")
    for pat, n in patterns.most_common():
        print(f"  {pat:<12} {n:>5}")
    print()
