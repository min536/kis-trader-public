"""Pure analysis helpers for core-bucket diagnostics."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

_STRATEGY_ORDER = [
    "intraday_pullback",
    "rebound_from_low",
    "controlled_down_day",
    "gap_down_open",
    "range_recovery",
]


_SHADOW_RULE_ORDER: tuple[str, ...] = (
    "non_overextension",
    "macd_momentum_confirmed",
    "intraday_stability",
)


def _pct(num: int, den: int) -> str:
    if den == 0:
        return "0.0%"
    return f"{(num / den) * 100:.1f}%"


def _safe_mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coalesce_metric(*values: object) -> float | None:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _build_candidate_detail_index(snapshot_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot_row in snapshot_rows:
        cycle_id = str(snapshot_row.get("cycle_id") or "").strip()
        if not cycle_id:
            continue

        candidate_payloads: list[tuple[str, dict[str, Any]]] = []
        selected = snapshot_row.get("selected_buy_candidate")
        if isinstance(selected, dict):
            candidate_payloads.append(("selected_buy_candidate", selected))
        for candidate in snapshot_row.get("scanner_candidates_top") or []:
            if isinstance(candidate, dict):
                candidate_payloads.append(("scanner_candidates_top", candidate))

        for source, candidate in candidate_payloads:
            symbol = str(candidate.get("symbol") or "").strip()
            if not symbol:
                continue
            score_components = candidate.get("score_components")
            score_components = score_components if isinstance(score_components, dict) else {}
            feature_map = candidate.get("feature_map")
            feature_map = feature_map if isinstance(feature_map, dict) else {}
            technical_group = feature_map.get("technical_features")
            technical_group = technical_group if isinstance(technical_group, dict) else {}
            mean_reversion_group = feature_map.get("mean_reversion_features")
            mean_reversion_group = (
                mean_reversion_group if isinstance(mean_reversion_group, dict) else {}
            )

            trend_alignment_score = _coalesce_metric(
                score_components.get("trend_alignment_score"),
                technical_group.get("trend_alignment_score"),
            )
            macd_momentum_score = _coalesce_metric(
                score_components.get("macd_momentum_score"),
                technical_group.get("macd_momentum_score"),
            )
            mean_reversion_bonus = _coalesce_metric(
                score_components.get("mean_reversion_bonus"),
                mean_reversion_group.get("mean_reversion_bonus"),
            )
            overextension_penalty = _coalesce_metric(
                candidate.get("overextension_penalty"),
                score_components.get("overextension_penalty"),
                mean_reversion_group.get("overextension_penalty"),
            )
            technical_total_score = None
            if trend_alignment_score is not None or macd_momentum_score is not None:
                technical_total_score = round(
                    float(trend_alignment_score or 0.0) + float(macd_momentum_score or 0.0),
                    4,
                )

            detail = {
                "detail_source": source,
                "passed_count": _safe_float(candidate.get("passed_count")),
                "enabled_count": _safe_float(candidate.get("enabled_count")),
                "detail_score": _safe_float(candidate.get("score")),
                "net_profit_buffer_bps": _safe_float(candidate.get("net_profit_buffer_bps")),
                "expected_cost_bps": _safe_float(candidate.get("expected_cost_bps")),
                "expected_cost_penalty": _coalesce_metric(
                    candidate.get("expected_cost_penalty"),
                    score_components.get("expected_cost_penalty"),
                ),
                "cost_quality_score": _coalesce_metric(
                    candidate.get("cost_quality_score"),
                    score_components.get("cost_quality_score"),
                ),
                "cost_block_reason": candidate.get("cost_block_reason"),
                "trend_alignment_score": trend_alignment_score,
                "macd_momentum_score": macd_momentum_score,
                "technical_total_score": technical_total_score,
                "mean_reversion_bonus": mean_reversion_bonus,
                "overextension_penalty": overextension_penalty,
                "score_highlights": list(candidate.get("score_highlights") or []),
                "score_penalties": list(candidate.get("score_penalties") or []),
                "final_reason_detail": candidate.get("final_reason"),
            }

            key = (cycle_id, symbol)
            previous = index.get(key)
            if previous is None or source == "selected_buy_candidate":
                index[key] = detail
    return index


def _enrich_rows(rows: list[dict[str, Any]], detail_index: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        cycle_id = str(row.get("cycle_id") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        detail = detail_index.get((cycle_id, symbol), {})
        enriched.append(
            {
                **row,
                **detail,
                "detail_available": bool(detail),
            }
        )
    return enriched


def _metric_stats(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        float(value)
        for value in (row.get(field) for row in rows)
        if isinstance(value, (int, float))
    ]
    return {
        "count": len(values),
        "avg": round(_safe_mean(values), 4),
        "median": round(_safe_median(values), 4),
        "min": round(min(values), 4) if values else 0.0,
        "max": round(max(values), 4) if values else 0.0,
    }


def _top_symbol_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    frequencies = Counter(str(row.get("symbol") or "") for row in rows if str(row.get("symbol") or ""))
    named_rows = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol and symbol not in named_rows:
            named_rows[symbol] = row
    return [
        {
            "symbol": symbol,
            "symbol_name": named_rows[symbol].get("symbol_name"),
            "count": count,
        }
        for symbol, count in frequencies.most_common(limit)
    ]


def _component_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    detail_rows = [row for row in rows if row.get("detail_available")]
    return {
        "row_count": len(rows),
        "detail_coverage_count": len(detail_rows),
        "detail_coverage_pct": _pct(len(detail_rows), len(rows)),
        "score_deep": _metric_stats(rows, "score_deep"),
        "passed_count": _metric_stats(detail_rows, "passed_count"),
        "technical_total_score": _metric_stats(detail_rows, "technical_total_score"),
        "trend_alignment_score": _metric_stats(detail_rows, "trend_alignment_score"),
        "macd_momentum_score": _metric_stats(detail_rows, "macd_momentum_score"),
        "mean_reversion_bonus": _metric_stats(detail_rows, "mean_reversion_bonus"),
        "overextension_penalty": _metric_stats(detail_rows, "overextension_penalty"),
        "net_profit_buffer_bps": _metric_stats(detail_rows, "net_profit_buffer_bps"),
        "expected_cost_bps": _metric_stats(detail_rows, "expected_cost_bps"),
        "expected_cost_penalty": _metric_stats(detail_rows, "expected_cost_penalty"),
        "cost_quality_score": _metric_stats(detail_rows, "cost_quality_score"),
        "cost_block_reasons": dict(
            Counter(
                str(row.get("cost_block_reason"))
                for row in detail_rows
                if row.get("cost_block_reason")
            )
        ),
        "rejection_reasons": dict(
            Counter(
                str(row.get("rejection_reason"))
                for row in rows
                if row.get("rejection_reason") is not None
            )
        ),
        "technical_available_count": sum(
            1
            for row in detail_rows
            if row.get("technical_total_score") is not None
        ),
        "technical_nonzero_count": sum(
            1
            for row in detail_rows
            if isinstance(row.get("technical_total_score"), (int, float))
            and float(row.get("technical_total_score") or 0.0) > 0.0
        ),
        "mean_reversion_nonzero_count": sum(
            1
            for row in detail_rows
            if isinstance(row.get("mean_reversion_bonus"), (int, float))
            and float(row.get("mean_reversion_bonus") or 0.0) > 0.0
        ),
        "top_highlights": dict(
            Counter(
                highlight
                for row in detail_rows
                for highlight in list(row.get("score_highlights") or [])
            ).most_common(5)
        ),
        "top_penalties": dict(
            Counter(
                penalty
                for row in detail_rows
                for penalty in list(row.get("score_penalties") or [])
            ).most_common(5)
        ),
    }


def _sample_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row.get("score_deep") or 0.0),
            str(row.get("symbol") or ""),
        ),
    )[:limit]
    samples: list[dict[str, Any]] = []
    for row in ordered:
        samples.append(
            {
                "cycle_id": row.get("cycle_id"),
                "symbol": row.get("symbol"),
                "symbol_name": row.get("symbol_name"),
                "bucket": row.get("selection_bucket"),
                "score_deep": row.get("score_deep"),
                "passed_count": row.get("passed_count"),
                "rejection_reason": row.get("rejection_reason"),
                "cost_block_reason": row.get("cost_block_reason"),
                "technical_total_score": row.get("technical_total_score"),
                "mean_reversion_bonus": row.get("mean_reversion_bonus"),
                "expected_cost_penalty": row.get("expected_cost_penalty"),
            }
        )
    return samples


def _strategy_hit_rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse strategy_pass_pattern from rows and return per-strategy pass rate.

    strategy_pass_pattern is a string like "P F P F P" where each token maps
    to a fixed strategy in _STRATEGY_ORDER order.
    """
    counts: dict[str, int] = {s: 0 for s in _STRATEGY_ORDER}
    passes: dict[str, int] = {s: 0 for s in _STRATEGY_ORDER}
    parsed_rows = 0

    for row in rows:
        pattern = row.get("strategy_pass_pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        tokens = pattern.strip().split()
        if len(tokens) != len(_STRATEGY_ORDER):
            continue
        parsed_rows += 1
        for i, token in enumerate(tokens):
            strategy = _STRATEGY_ORDER[i]
            counts[strategy] += 1
            if token.upper() == "P":
                passes[strategy] += 1

    return {
        "parsed_rows": parsed_rows,
        "per_strategy": {
            s: {
                "pass_count": passes[s],
                "total": counts[s],
                "pass_pct": _pct(passes[s], counts[s]),
            }
            for s in _STRATEGY_ORDER
        },
    }


def _passed_count_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count how many deep-eval rows land at each passed_count_deep value.

    Uses ``passed_count_deep`` directly from the candidate outcome log so this
    works without cycle-snapshot enrichment.
    """
    dist: dict[int | str, int] = {}
    for row in rows:
        raw = row.get("passed_count_deep")
        if raw is None:
            key: int | str = "null"
        else:
            try:
                key = int(raw)
            except (TypeError, ValueError):
                key = str(raw)
        dist[key] = dist.get(key, 0) + 1
    return dist


def _top_fail_patterns(rows: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most common strategy_pass_pattern values and their counts."""
    counts: Counter[str] = Counter(
        row.get("strategy_pass_pattern", "")
        for row in rows
        if isinstance(row.get("strategy_pass_pattern"), str)
        and row.get("strategy_pass_pattern", "").strip()
    )
    result: list[dict[str, Any]] = []
    for pattern, count in counts.most_common(limit):
        tokens = pattern.strip().split()
        rule_labels = []
        for i, tok in enumerate(tokens):
            if i < len(_STRATEGY_ORDER) and tok.upper() == "P":
                rule_labels.append(_STRATEGY_ORDER[i])
        result.append(
            {
                "pattern": pattern,
                "count": count,
                "passing_rules": rule_labels,
                "pass_count": sum(1 for t in tokens if t.upper() == "P"),
            }
        )
    return result


def _pattern_passing_rules(pattern: object) -> list[str]:
    if not isinstance(pattern, str) or not pattern.strip():
        return []
    rules: list[str] = []
    for idx, token in enumerate(pattern.strip().split()):
        if idx >= len(_STRATEGY_ORDER):
            break
        if token.upper() == "P":
            rules.append(_STRATEGY_ORDER[idx])
    return rules


def _strategy_rate_map(summary: dict[str, Any]) -> dict[str, float]:
    per_strategy = summary.get("per_strategy", {})
    rates: dict[str, float] = {}
    for rule in _STRATEGY_ORDER:
        payload = per_strategy.get(rule, {})
        total = int(payload.get("total", 0) or 0)
        passed = int(payload.get("pass_count", 0) or 0)
        rates[rule] = float(passed) / total if total > 0 else 0.0
    return rates


def _infer_required_pass_count(core_losers: list[dict[str, Any]]) -> int | None:
    """Infer the required_pass_count threshold from which passed_count value
    stops being rejected for passed_count_insufficient.

    Returns the inferred threshold or None if it cannot be determined.
    """
    max_pci_passed: int | None = None
    for row in core_losers:
        if row.get("rejection_reason") == "passed_count_insufficient":
            raw = row.get("passed_count_deep")
            if raw is not None:
                try:
                    val = int(raw)
                    if max_pci_passed is None or val > max_pci_passed:
                        max_pci_passed = val
                except (TypeError, ValueError):
                    pass
    if max_pci_passed is None:
        return None
    return max_pci_passed + 1


def _rule_contrast_interpretation(
    core_shr: dict[str, Any],
    non_core_shr: dict[str, Any],
    core_losers: list[dict[str, Any]],
    required_pass_count: int | None,
) -> dict[str, Any]:
    """Produce a grounded interpretation of the rule-family contrast.

    Classifies the failure mode as 'rule_family_mismatch', 'threshold_only',
    or 'mixed' based on the per-rule pass-rate gap between core losers and
    non-core final candidates.
    """
    core_per = core_shr.get("per_strategy", {})
    non_core_per = non_core_shr.get("per_strategy", {})

    # Rules that almost never fire for core (< 20%) but do fire for non-core (> 60%)
    structural_gaps: list[str] = []
    threshold_candidates: list[str] = []  # moderate gap, might respond to tuning
    shared_rules: list[str] = []  # fire for both — no discriminating power

    for rule in _STRATEGY_ORDER:
        c = core_per.get(rule, {})
        n = non_core_per.get(rule, {})
        c_total = int(c.get("total", 0))
        n_total = int(n.get("total", 0))
        c_rate = float(c.get("pass_count", 0)) / c_total if c_total > 0 else 0.0
        n_rate = float(n.get("pass_count", 0)) / n_total if n_total > 0 else 0.0

        if c_rate < 0.20 and n_rate >= 0.60:
            structural_gaps.append(rule)
        elif c_rate >= 0.70 and n_rate >= 0.70:
            shared_rules.append(rule)
        elif n_rate - c_rate >= 0.25:
            threshold_candidates.append(rule)

    # Determine verdict
    if len(structural_gaps) >= 2:
        verdict = "rule_family_mismatch"
        explanation = (
            f"{len(structural_gaps)} rule(s) almost never fire for core "
            f"but reliably fire for non-core: {structural_gaps}. "
            "Core names cannot reach required_pass_count on these rules structurally."
        )
    elif len(structural_gaps) == 1 and len(threshold_candidates) >= 1:
        verdict = "mixed"
        explanation = (
            f"One structural gap rule ({structural_gaps[0]}) combined with "
            f"{len(threshold_candidates)} threshold-sensitive rules ({threshold_candidates}). "
            "Lowering required_pass_count alone would not resolve the structural gap."
        )
    elif len(threshold_candidates) >= 2 and not structural_gaps:
        verdict = "threshold_only"
        explanation = (
            f"No rules are structurally blocked for core, but {len(threshold_candidates)} "
            f"rules fire at lower rates ({threshold_candidates}). "
            "required_pass_count reduction or threshold relaxation may help."
        )
    else:
        verdict = "insufficient_data"
        explanation = "Not enough contrast to classify. Check parsed_rows count."

    # Dominant failure pattern inference
    fail_pattern_counts: Counter[str] = Counter(
        row.get("strategy_pass_pattern", "")
        for row in core_losers
        if row.get("rejection_reason") == "passed_count_insufficient"
        and isinstance(row.get("strategy_pass_pattern"), str)
    )
    dominant = fail_pattern_counts.most_common(1)
    dominant_pattern = dominant[0][0] if dominant else None
    dominant_count = dominant[0][1] if dominant else 0

    dominant_rules: list[str] = []
    if dominant_pattern:
        for i, tok in enumerate(dominant_pattern.strip().split()):
            if i < len(_STRATEGY_ORDER) and tok.upper() == "P":
                dominant_rules.append(_STRATEGY_ORDER[i])

    gap_needed = (
        (required_pass_count - len(dominant_rules))
        if required_pass_count is not None and dominant_rules is not None
        else None
    )

    return {
        "verdict": verdict,
        "explanation": explanation,
        "structural_gap_rules": structural_gaps,
        "threshold_candidate_rules": threshold_candidates,
        "shared_rules": shared_rules,
        "inferred_required_pass_count": required_pass_count,
        "dominant_fail_pattern": dominant_pattern,
        "dominant_fail_pattern_count": dominant_count,
        "dominant_passing_rules": dominant_rules,
        "gap_to_threshold": gap_needed,
    }


def _threshold_simulation(
    rows: list[dict[str, Any]],
    required_pass_count: int | None,
) -> dict[str, Any]:
    if required_pass_count is None:
        return {
            "available": False,
            "current_required_pass_count": None,
            "scenarios": [],
        }

    deep_rows = [
        row for row in rows
        if isinstance(row.get("passed_count_deep"), (int, float))
    ]
    current_hits = sum(
        1
        for row in deep_rows
        if int(row.get("passed_count_deep") or 0) >= required_pass_count
    )
    minimum_threshold = max(1, required_pass_count - 2)
    scenarios: list[dict[str, Any]] = []

    for threshold in range(required_pass_count, minimum_threshold - 1, -1):
        flipped_rows = [
            row
            for row in deep_rows
            if int(row.get("passed_count_deep") or 0) >= threshold
            and int(row.get("passed_count_deep") or 0) < required_pass_count
        ]
        flipped_symbols = Counter(
            str(row.get("symbol") or "")
            for row in flipped_rows
            if str(row.get("symbol") or "").strip()
        )
        scenarios.append(
            {
                "threshold": threshold,
                "would_meet_threshold_count": sum(
                    1
                    for row in deep_rows
                    if int(row.get("passed_count_deep") or 0) >= threshold
                ),
                "delta_vs_current_threshold": sum(
                    1
                    for row in deep_rows
                    if int(row.get("passed_count_deep") or 0) >= threshold
                )
                - current_hits,
                "flipped_row_count": len(flipped_rows),
                "flipped_symbols": dict(flipped_symbols),
                "flipped_reasons": dict(
                    Counter(
                        str(row.get("rejection_reason") or "none")
                        for row in flipped_rows
                    )
                ),
                "flipped_sample_rows": [
                    {
                        "symbol": row.get("symbol"),
                        "symbol_name": row.get("symbol_name"),
                        "cycle_id": row.get("cycle_id"),
                        "passed_count_deep": row.get("passed_count_deep"),
                        "score_deep": row.get("score_deep"),
                        "strategy_pass_pattern": row.get("strategy_pass_pattern"),
                    }
                    for row in sorted(
                        flipped_rows,
                        key=lambda row: (
                            -float(row.get("score_deep") or 0.0),
                            str(row.get("symbol") or ""),
                        ),
                    )[:5]
                ],
            }
        )

    return {
        "available": True,
        "current_required_pass_count": required_pass_count,
        "deep_eval_row_count": len(deep_rows),
        "current_hits": current_hits,
        "scenarios": scenarios,
    }


def _rule_lift_opportunities(
    rows: list[dict[str, Any]],
    required_pass_count: int | None,
    *,
    non_core_shr: dict[str, Any],
) -> dict[str, Any]:
    if required_pass_count is None:
        return {"available": False}

    non_core_rates = _strategy_rate_map(non_core_shr)
    candidate_counts: Counter[str] = Counter()
    candidate_symbols: dict[str, set[str]] = defaultdict(set)
    near_threshold_rows = 0
    multi_rule_short_rows = 0

    for row in rows:
        raw_passed = row.get("passed_count_deep")
        try:
            passed_count = int(raw_passed)
        except (TypeError, ValueError):
            continue
        gap = required_pass_count - passed_count
        if gap <= 0:
            continue
        passing_rules = set(_pattern_passing_rules(row.get("strategy_pass_pattern")))
        missing_rules = [rule for rule in _STRATEGY_ORDER if rule not in passing_rules]
        symbol = str(row.get("symbol") or "").strip()
        if gap == 1:
            near_threshold_rows += 1
            for rule in missing_rules:
                candidate_counts[rule] += 1
                if symbol:
                    candidate_symbols[rule].add(symbol)
        else:
            multi_rule_short_rows += 1

    ranked_rules = sorted(
        candidate_counts.keys(),
        key=lambda rule: (-non_core_rates.get(rule, 0.0), -candidate_counts[rule], rule),
    )
    ranked_payload = [
        {
            "rule": rule,
            "near_threshold_row_count": candidate_counts[rule],
            "unique_symbol_count": len(candidate_symbols[rule]),
            "symbols": sorted(candidate_symbols[rule]),
            "non_core_final_pass_rate": round(non_core_rates.get(rule, 0.0), 4),
        }
        for rule in ranked_rules
    ]
    return {
        "available": True,
        "required_pass_count": required_pass_count,
        "near_threshold_row_count": near_threshold_rows,
        "multi_rule_short_row_count": multi_rule_short_rows,
        "ranked_rules": ranked_payload,
    }


def _symbol_failure_diagnosis(
    rows: list[dict[str, Any]],
    required_pass_count: int | None,
    *,
    structural_gap_rules: list[str],
    non_core_shr: dict[str, Any],
) -> dict[str, Any]:
    if required_pass_count is None:
        return {"available": False, "symbols": []}

    non_core_rates = _strategy_rate_map(non_core_shr)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            grouped[symbol].append(row)

    diagnoses: list[dict[str, Any]] = []
    for symbol, symbol_rows in grouped.items():
        symbol_name = str(symbol_rows[0].get("symbol_name") or "").strip() or None
        gap_values: list[int] = []
        one_rule_short = 0
        structural_gap_row_count = 0
        all_missing_rules: Counter[str] = Counter()
        dominant_patterns: Counter[str] = Counter()

        for row in symbol_rows:
            try:
                passed_count = int(row.get("passed_count_deep"))
            except (TypeError, ValueError):
                continue
            gap = max(required_pass_count - passed_count, 0)
            gap_values.append(gap)
            if gap == 1:
                one_rule_short += 1
            passing_rules = set(_pattern_passing_rules(row.get("strategy_pass_pattern")))
            missing_rules = [rule for rule in _STRATEGY_ORDER if rule not in passing_rules]
            for rule in missing_rules:
                all_missing_rules[rule] += 1
            if any(rule in structural_gap_rules for rule in missing_rules):
                structural_gap_row_count += 1
            pattern = str(row.get("strategy_pass_pattern") or "").strip()
            if pattern:
                dominant_patterns[pattern] += 1

        avg_passed = round(
            _safe_mean(
                [
                    float(row.get("passed_count_deep"))
                    for row in symbol_rows
                    if isinstance(row.get("passed_count_deep"), (int, float))
                ]
            ),
            3,
        )
        best_score = max(
            (
                float(row.get("score_deep"))
                for row in symbol_rows
                if isinstance(row.get("score_deep"), (int, float))
            ),
            default=0.0,
        )
        top_missing_rules = sorted(
            all_missing_rules.keys(),
            key=lambda rule: (-non_core_rates.get(rule, 0.0), -all_missing_rules[rule], rule),
        )[:3]

        if structural_gap_row_count == len(symbol_rows):
            verdict = "structural_gap"
            action_hint = "core에서 거의 안 뜨는 규칙 보완이 우선입니다."
        elif one_rule_short == len(symbol_rows) and len(symbol_rows) > 0:
            verdict = "threshold_only"
            action_hint = "한 규칙만 더 붙으면 threshold를 넘는 near miss입니다."
        elif one_rule_short > 0:
            verdict = "mixed"
            action_hint = "일부는 near miss이고 일부는 추가 규칙 보완이 더 필요합니다."
        else:
            verdict = "multi_rule_short"
            action_hint = "threshold를 낮추는 것만으로는 부족하고 2개 이상 규칙 개선이 필요합니다."

        diagnoses.append(
            {
                "symbol": symbol,
                "symbol_name": symbol_name,
                "rows": len(symbol_rows),
                "verdict": verdict,
                "action_hint": action_hint,
                "avg_passed_count_deep": avg_passed,
                "best_score_deep": round(best_score, 3),
                "gap_distribution": dict(Counter(gap_values)),
                "one_rule_short_count": one_rule_short,
                "structural_gap_row_count": structural_gap_row_count,
                "top_missing_rules": [
                    {
                        "rule": rule,
                        "count": all_missing_rules[rule],
                        "non_core_final_pass_rate": round(non_core_rates.get(rule, 0.0), 4),
                    }
                    for rule in top_missing_rules
                ],
                "dominant_patterns": [
                    {"pattern": pattern, "count": count}
                    for pattern, count in dominant_patterns.most_common(2)
                ],
            }
        )

    diagnoses.sort(
        key=lambda item: (
            item["verdict"] != "threshold_only",
            -item["one_rule_short_count"],
            item["verdict"] != "mixed",
            -item["best_score_deep"],
            item["symbol"],
        )
    )
    return {"available": True, "symbols": diagnoses}


def _shadow_analysis(core_deep_losers: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise core_shadow_* fields from candidate outcome rows.

    Returns a dict that is empty-safe: all counts are 0 and 'available' is
    False when the shadow fields are not yet present in the log data.
    """
    evaluated = [r for r in core_deep_losers if r.get("core_shadow_evaluated") is True]
    if not evaluated:
        return {"available": False, "evaluated_count": 0}

    trend_gate_passed = [r for r in evaluated if r.get("core_shadow_trend_gate_passed") is True]
    trend_gate_blocked = [r for r in evaluated if r.get("core_shadow_trend_gate_passed") is False]
    shadow_passed = [r for r in evaluated if r.get("core_shadow_passed") is True]

    pattern_counts: Counter[str] = Counter(
        str(r.get("core_shadow_pattern") or "")
        for r in trend_gate_passed
        if r.get("core_shadow_pattern") and r.get("core_shadow_pattern") != "BLOCKED"
    )
    top_patterns: list[dict[str, Any]] = []
    for pattern, count in pattern_counts.most_common(8):
        tokens = pattern.strip().split()
        rule_labels = [
            _SHADOW_RULE_ORDER[i]
            for i, tok in enumerate(tokens)
            if i < len(_SHADOW_RULE_ORDER) and tok.upper() == "P"
        ]
        top_patterns.append({
            "pattern": pattern,
            "count": count,
            "passing_rules": rule_labels,
            "pass_count": sum(1 for t in tokens if t.upper() == "P"),
        })

    shadow_pass_reasons: dict[str, int] = dict(
        Counter(
            str(r.get("rejection_reason") or "none")
            for r in shadow_passed
        )
    )
    shadow_pass_existing_pcount: dict[str | None, int] = dict(
        Counter(r.get("passed_count_deep") for r in shadow_passed)
    )

    return {
        "available": True,
        "evaluated_count": len(evaluated),
        "trend_gate_passed_count": len(trend_gate_passed),
        "trend_gate_blocked_count": len(trend_gate_blocked),
        "shadow_passed_count": len(shadow_passed),
        "shadow_pass_rate_pct": _pct(len(shadow_passed), len(trend_gate_passed)) if trend_gate_passed else "—",
        "top_patterns": top_patterns,
        "shadow_pass_existing_rejection_reasons": shadow_pass_reasons,
        "shadow_pass_existing_pcount": shadow_pass_existing_pcount,
    }


def _build_human_summary(core_profile: dict[str, Any], non_core_profile: dict[str, Any]) -> str:
    points: list[str] = []

    core_pass_avg = float(core_profile["passed_count"]["avg"])
    non_core_pass_avg = float(non_core_profile["passed_count"]["avg"])
    if non_core_pass_avg - core_pass_avg >= 0.5:
        points.append(
            f"passed_count gap {core_pass_avg:.2f} vs {non_core_pass_avg:.2f}"
        )

    core_score_avg = float(core_profile["score_deep"]["avg"])
    non_core_score_avg = float(non_core_profile["score_deep"]["avg"])
    if non_core_score_avg - core_score_avg >= 0.3:
        points.append(
            f"score gap {core_score_avg:.2f} vs {non_core_score_avg:.2f}"
        )

    core_tech_avg = float(core_profile["technical_total_score"]["avg"])
    non_core_tech_avg = float(non_core_profile["technical_total_score"]["avg"])
    if non_core_tech_avg > core_tech_avg + 0.05:
        points.append(
            f"technical contribution gap {core_tech_avg:.2f} vs {non_core_tech_avg:.2f}"
        )

    core_mean_rev = float(core_profile["mean_reversion_bonus"]["avg"])
    non_core_mean_rev = float(non_core_profile["mean_reversion_bonus"]["avg"])
    if non_core_mean_rev > core_mean_rev + 0.05:
        points.append(
            f"mean-reversion bonus gap {core_mean_rev:.2f} vs {non_core_mean_rev:.2f}"
        )

    core_cost_penalty = float(core_profile["expected_cost_penalty"]["avg"])
    non_core_cost_penalty = float(non_core_profile["expected_cost_penalty"]["avg"])
    if core_cost_penalty > non_core_cost_penalty + 0.1:
        points.append(
            f"higher cost penalty {core_cost_penalty:.2f} vs {non_core_cost_penalty:.2f}"
        )

    if core_profile["cost_block_reasons"]:
        points.append(f"core cost blocks present {core_profile['cost_block_reasons']}")

    if not points:
        return (
            "core 억제 원인은 단일 항목보다는 score ranking 전반으로 보이며, "
            "뚜렷한 cost veto 또는 기술 overlay 차이 하나로 설명되지는 않습니다."
        )
    return "core final candidate 부재는 " + ", ".join(points) + " 영향이 커 보입니다."


