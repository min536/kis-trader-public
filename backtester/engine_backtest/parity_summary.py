"""Summary/classification cluster extracted from parity.py (R7-B1)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _normalize_date(raw: str) -> tuple[str, str, date]:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        iso = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text, iso, date.fromisoformat(iso)
    parsed = date.fromisoformat(text[:10])
    return parsed.strftime("%Y%m%d"), parsed.isoformat(), parsed


def _parse_ts(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_count_dicts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        payload = row.get(key)
        if not isinstance(payload, dict):
            continue
        for raw_name, raw_count in payload.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            counter[name] += _int(raw_count) or 0
    return dict(sorted(counter.items()))


def _collect_unique_list_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    values: set[str] = set()
    for row in rows:
        payload = row.get(key)
        if not isinstance(payload, list):
            continue
        for item in payload:
            text = str(item or "").strip()
            if text:
                values.add(text)
    return sorted(values)


def _last_dict_value(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for row in reversed(rows):
        payload = row.get(key)
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _max_count_entry(values: dict[str, int]) -> tuple[str | None, int]:
    best_name: str | None = None
    best_count = 0
    for name, count in sorted(values.items()):
        numeric = _int(count) or 0
        if numeric > best_count:
            best_name = name
            best_count = numeric
    return best_name, best_count


def _build_rule_failure_summary(
    *,
    rule_names: list[str],
    enabled_counts: dict[str, int],
    pass_counts: dict[str, int],
) -> tuple[dict[str, int], dict[str, float | None], dict[str, Any] | None]:
    fail_counts: dict[str, int] = {}
    pass_rates: dict[str, float | None] = {}
    names = sorted({*(rule_names or []), *enabled_counts.keys(), *pass_counts.keys()})

    dominant_name: str | None = None
    dominant_fail_count = -1
    for name in names:
        enabled = _int(enabled_counts.get(name)) or 0
        passed = _int(pass_counts.get(name)) or 0
        failed = max(enabled - passed, 0)
        fail_counts[name] = failed
        pass_rates[name] = round(passed / enabled, 4) if enabled > 0 else None
        if failed > dominant_fail_count:
            dominant_name = name
            dominant_fail_count = failed

    if dominant_name is None or dominant_fail_count <= 0:
        dominant = None
    else:
        dominant = {
            "rule": dominant_name,
            "fail_count": int(fail_counts.get(dominant_name) or 0),
            "enabled_count": int(enabled_counts.get(dominant_name) or 0),
            "pass_count": int(pass_counts.get(dominant_name) or 0),
            "pass_rate": pass_rates.get(dominant_name),
        }
    return fail_counts, pass_rates, dominant


def _build_buy_diagnostics(engine: dict[str, Any]) -> dict[str, Any]:
    funnel = dict(engine.get("buy_funnel") or {})
    rejection_counts = {
        str(name): _int(count) or 0
        for name, count in dict(engine.get("buy_rejection_reason_counts") or {}).items()
    }
    capacity = dict(engine.get("buy_capacity") or {})
    score_stats = dict(engine.get("buy_score_stats") or {})
    sizing = dict(engine.get("buy_sizing") or {})
    dominant_rule_failure = engine.get("dominant_rule_failure")

    primary_rejection_reason, primary_rejection_count = _max_count_entry(rejection_counts)
    positions_before_buy = _int(capacity.get("positions_before_buy_pass")) or 0
    max_positions = _int(capacity.get("max_positions")) or 0
    available_slots = _int(capacity.get("available_slots_before_buy_pass")) or 0

    entered_rule_stage = (_int(funnel.get("decision_evaluated")) or 0) > 0
    entered_scoring_stage = (_int(funnel.get("buy_signal")) or 0) > 0
    entered_sizing_stage = (_int(funnel.get("scored_candidate")) or 0) > 0
    zero_buy = (_int(engine.get("executed_buy_count")) or 0) == 0

    stage = "executed_buy"
    summary = "BUY가 실행되었습니다."
    if zero_buy:
        if (_int(funnel.get("max_positions_blocked")) or 0) > 0 or (
            max_positions > 0 and available_slots <= 0
        ):
            stage = "capacity_blocked"
            summary = (
                "capacity block: buy rule 평가 전에 슬롯이 없어 중단되었습니다 "
                f"(positions_before_buy={positions_before_buy}, max_positions={max_positions}, "
                f"available_slots={available_slots})."
            )
        elif not entered_rule_stage:
            stage = "pre_rule_blocked"
            summary = (
                "pre-rule block: buy rule 평가 전에 후보를 평가하지 못했습니다 "
                f"(symbols_considered={_int(funnel.get('symbols_considered')) or 0}, "
                f"missing_snapshot={_int(funnel.get('no_data')) or 0})."
            )
        elif not entered_scoring_stage:
            stage = "rule_blocked"
            if isinstance(dominant_rule_failure, dict):
                summary = (
                    "rule block: buy rule 단계에서 신호가 0건입니다 "
                    f"(dominant_rule={dominant_rule_failure.get('rule')}, "
                    f"fail_count={dominant_rule_failure.get('fail_count')}, "
                    f"pass_rate={dominant_rule_failure.get('pass_rate')})."
                )
            else:
                summary = "rule block: buy rule 단계에서 신호가 0건입니다."
        elif not entered_sizing_stage and (_int(funnel.get("score_rejected")) or 0) > 0:
            rejected = dict(score_stats.get("score_rejected") or {})
            stage = "score_blocked"
            summary = (
                "score block: buy signal은 있었지만 점수 임계값을 넘지 못했습니다 "
                f"(threshold={score_stats.get('min_score_threshold')}, "
                f"rejected_max={rejected.get('max')})."
            )
        elif (_int(funnel.get("sizing_blocked")) or 0) > 0:
            stage = "sizing_blocked"
            summary = (
                "sizing block: scored candidate는 있었지만 수량 산정이 0주였습니다 "
                f"(block_reason={sizing.get('block_reason_code') or 'unknown'})."
            )
        else:
            stage = "unknown_zero_buy"
            summary = "BUY 0건이지만 현재 계측으로 primary bottleneck을 단정하기 어렵습니다."

    return {
        "zero_buy": zero_buy,
        "stage": stage,
        "entered_rule_stage": entered_rule_stage,
        "entered_scoring_stage": entered_scoring_stage,
        "entered_sizing_stage": entered_sizing_stage,
        "capacity_blocked_before_rule_eval": bool(capacity.get("capacity_blocked_before_rule_eval")),
        "primary_rejection_reason": primary_rejection_reason,
        "primary_rejection_count": primary_rejection_count,
        "dominant_rule_failure": dominant_rule_failure,
        "summary": summary,
    }


def _enrich_engine_summary(engine: dict[str, Any]) -> dict[str, Any]:
    rule_names = list(engine.get("buy_rule_names") or [])
    enabled_counts = {
        str(name): _int(count) or 0
        for name, count in dict(engine.get("buy_rule_enabled_counts") or {}).items()
    }
    pass_counts = {
        str(name): _int(count) or 0
        for name, count in dict(engine.get("buy_rule_pass_counts") or {}).items()
    }
    fail_counts, pass_rates, dominant = _build_rule_failure_summary(
        rule_names=rule_names,
        enabled_counts=enabled_counts,
        pass_counts=pass_counts,
    )
    engine["buy_rule_fail_counts"] = fail_counts
    engine["buy_rule_pass_rates"] = pass_rates
    engine["dominant_rule_failure"] = dominant
    engine["buy_diagnostics"] = _build_buy_diagnostics(engine)
    return engine


def _unique_symbols(rows: list[dict[str, Any]], predicate) -> list[str]:
    return sorted(
        {
            str(row.get("symbol") or "").strip()
            for row in rows
            if str(row.get("symbol") or "").strip() and predicate(row)
        }
    )


def _summarize_live_reference(
    *,
    account: str,
    date_text: str,
    iso_date: str,
    session: str,
    candidate_rows: list[dict[str, Any]],
    stats_rows: list[dict[str, Any]],
    snapshot_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    cycle_max = {
        "pre_gate_passed": max((_int(row.get("pre_gate_passed")) or 0 for row in stats_rows), default=0),
        "shallow_ranked_count": max((_int(row.get("shallow_ranked_count")) or 0 for row in stats_rows), default=0),
        "shallow_shortlist_size": max((_int(row.get("shallow_shortlist_size")) or 0 for row in stats_rows), default=0),
        "deep_eval_count": max((_int(row.get("deep_eval_count")) or 0 for row in stats_rows), default=0),
        "final_candidate_count": max((_int(row.get("final_candidate_count")) or 0 for row in stats_rows), default=0),
        "executed_order_count": max((_int(row.get("executed_order_count")) or 0 for row in stats_rows), default=0),
        "sell_evaluated_count": max((_int(row.get("sell_evaluated_count")) or 0 for row in stats_rows), default=0),
        "sell_triggered_count": max((_int(row.get("sell_triggered_count")) or 0 for row in stats_rows), default=0),
    }

    live_sell_reasons: Counter[str] = Counter()
    for row in snapshot_rows:
        sell = row.get("selected_sell_candidate")
        if not isinstance(sell, dict):
            continue
        reason = str(sell.get("triggered_rule_name") or "").strip()
        if reason:
            live_sell_reasons[reason] += 1

    buy_success_rows = [
        row
        for row in order_rows
        if str(row.get("timestamp") or "").startswith(iso_date)
        and str(row.get("order_type") or "") == "market_buy"
        and str(row.get("action") or "").endswith("succeeded")
        and str(row.get("result") or "") == "success"
    ]
    sell_success_rows = [
        row
        for row in order_rows
        if str(row.get("timestamp") or "").startswith(iso_date)
        and str(row.get("order_type") or "") == "market_sell"
        and str(row.get("action") or "").endswith("succeeded")
        and str(row.get("result") or "") == "success"
    ]

    return {
        "candidate_rows": len(candidate_rows),
        "cycle_rows": len(stats_rows),
        "snapshot_rows": len(snapshot_rows),
        "day_unique_symbols": {
            "observed": _unique_symbols(candidate_rows, lambda row: True),
            "pre_gate_passed": _unique_symbols(candidate_rows, lambda row: _bool(row.get("pre_gate_passed"))),
            "shallow_selected": _unique_symbols(candidate_rows, lambda row: _bool(row.get("shallow_selected"))),
            "deep_evaluated": _unique_symbols(candidate_rows, lambda row: _bool(row.get("deep_evaluated"))),
            "final_candidate": _unique_symbols(candidate_rows, lambda row: _bool(row.get("final_candidate"))),
            "executed_buy": _unique_symbols(candidate_rows, lambda row: _bool(row.get("executed"))),
            "successful_buy_orders": sorted({str(row.get("symbol") or "").strip() for row in buy_success_rows if str(row.get("symbol") or "").strip()}),
            "successful_sell_orders": sorted({str(row.get("symbol") or "").strip() for row in sell_success_rows if str(row.get("symbol") or "").strip()}),
        },
        "cycle_stats_max": cycle_max,
        "buy_success_count": len(buy_success_rows),
        "sell_success_count": len(sell_success_rows),
        "sell_reason_distribution": dict(sorted(live_sell_reasons.items())),
    }


def _summarize_engine_report(
    *,
    report_path: str,
    iso_date: str,
) -> dict[str, Any]:
    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    daily_records = [
        row for row in (payload.get("daily_records") or [])
        if str(row.get("date") or "") == iso_date
    ]
    trade_log = [
        row for row in (payload.get("trade_log") or [])
        if str(row.get("sell_date") or "") == iso_date or str(row.get("buy_date") or "") == iso_date
    ]
    selected_buy_symbols = [str(row.get("buy_symbol") or "").strip() for row in daily_records if str(row.get("buy_symbol") or "").strip()]
    selected_sell_symbols = [str(row.get("sell_symbol") or "").strip() for row in daily_records if str(row.get("sell_symbol") or "").strip()]
    sell_reasons = Counter(
        str(row.get("sell_trigger") or "").strip()
        for row in trade_log
        if str(row.get("sell_trigger") or "").strip()
    )
    return {
        "mode": "engine_report",
        "input_symbol_count": None,
        "initial_cash": payload.get("metrics", {}).get("initial_cash"),
        "seeded_position_count": None,
        "buy_signal_count": sum(_int(row.get("buy_signal_count")) or 0 for row in daily_records),
        "buy_scored_candidate_count": sum(_int(row.get("buy_scored_candidate_count")) or 0 for row in daily_records),
        "buy_candidate_symbols": sorted(
            {
                symbol
                for row in daily_records
                for symbol in (row.get("buy_candidate_symbols") or [])
                if str(symbol).strip()
            }
        ),
        "buy_rule_names": _collect_unique_list_values(daily_records, "buy_rule_names"),
        "buy_rule_enabled_counts": _sum_count_dicts(daily_records, "buy_rule_enabled_counts"),
        "buy_rule_pass_counts": _sum_count_dicts(daily_records, "buy_rule_pass_counts"),
        "buy_rejection_reason_counts": _sum_count_dicts(daily_records, "buy_rejection_reason_counts"),
        "buy_funnel": _sum_count_dicts(daily_records, "buy_funnel"),
        "buy_capacity": _last_dict_value(daily_records, "buy_capacity"),
        "buy_score_stats": _last_dict_value(daily_records, "buy_score_stats"),
        "buy_sizing": _last_dict_value(daily_records, "buy_sizing"),
        "selected_buy_symbols": selected_buy_symbols,
        "selected_buy_score": next(
            (_float(row.get("buy_selected_score")) for row in daily_records if row.get("buy_selected_score") is not None),
            None,
        ),
        "final_candidate_count": len(selected_buy_symbols),
        "executed_buy_count": len(selected_buy_symbols),
        "sell_evaluated_count": sum(_int(row.get("sell_evaluated_count")) or 0 for row in daily_records),
        "sell_triggered_count": sum(_int(row.get("sell_triggered_count")) or 0 for row in daily_records),
        "selected_sell_symbols": selected_sell_symbols,
        "executed_sell_count": len(selected_sell_symbols),
        "sell_reason_distribution": dict(sorted(sell_reasons.items())),
    }


def _overlap(lhs: list[str], rhs: list[str]) -> dict[str, Any]:
    left = {item for item in lhs if item}
    right = {item for item in rhs if item}
    overlap = sorted(left & right)
    union = left | right
    return {
        "left_count": len(left),
        "right_count": len(right),
        "overlap_count": len(overlap),
        "overlap_symbols": overlap,
        "jaccard": round(len(overlap) / len(union), 4) if union else 1.0,
    }


def _classify_parity(
    *,
    source_mode: str,
    buy_executed_overlap: dict[str, Any],
    buy_final_overlap: dict[str, Any],
    sell_overlap: dict[str, Any],
    live_sell_reasons: dict[str, int],
    engine_sell_reasons: dict[str, int],
    warnings: list[str],
) -> tuple[str, list[str], list[str]]:
    matched_signals: list[str] = []
    divergence_notes: list[str] = []
    score = 0
    possible = 0

    if buy_final_overlap["right_count"] > 0:
        possible += 1
        if buy_final_overlap["overlap_count"] > 0:
            score += 1
            matched_signals.append(
                f"engine selected buy가 live final candidate와 겹칩니다: {', '.join(buy_final_overlap['overlap_symbols'])}"
            )
        else:
            divergence_notes.append("engine selected buy가 live final candidate와 겹치지 않았습니다.")

    if buy_executed_overlap["right_count"] > 0:
        possible += 1
        if buy_executed_overlap["overlap_count"] > 0:
            score += 1
            matched_signals.append(
                f"engine selected buy가 live executed buy와 겹칩니다: {', '.join(buy_executed_overlap['overlap_symbols'])}"
            )
        else:
            divergence_notes.append("engine selected buy가 live executed buy와 겹치지 않았습니다.")

    if sell_overlap["right_count"] > 0:
        possible += 1
        if sell_overlap["overlap_count"] > 0:
            score += 1
            matched_signals.append(
                f"engine selected sell이 live successful sell과 겹칩니다: {', '.join(sell_overlap['overlap_symbols'])}"
            )
        else:
            divergence_notes.append("engine selected sell이 live successful sell과 겹치지 않았습니다.")

    live_top_sell = next(iter(sorted(live_sell_reasons.items(), key=lambda item: (-item[1], item[0]))), None)
    engine_top_sell = next(iter(sorted(engine_sell_reasons.items(), key=lambda item: (-item[1], item[0]))), None)
    if live_top_sell is not None:
        possible += 1
        if engine_top_sell is not None and engine_top_sell[0] == live_top_sell[0]:
            score += 1
            matched_signals.append(
                f"주요 sell reason이 일치합니다: {engine_top_sell[0]}"
            )
        else:
            divergence_notes.append(
                f"주요 sell reason 불일치: live={live_top_sell[0]}, engine={engine_top_sell[0] if engine_top_sell else 'none'}"
            )

    if warnings:
        divergence_notes.extend(warnings)

    ratio = (score / possible) if possible else 0.0
    if ratio >= 0.75:
        level = "high"
    elif ratio >= 0.35:
        level = "medium"
    else:
        level = "low"

    if source_mode != "engine_report" and level == "high":
        level = "medium"
        divergence_notes.append("actual engine report 대신 historical_signal_proxy를 사용했으므로 parity_level은 medium을 상한으로 둡니다.")

    return level, matched_signals, divergence_notes
