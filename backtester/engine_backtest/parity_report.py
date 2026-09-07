"""Parity report writers for JSON and Markdown output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_parity_outputs(report: dict[str, Any], *, output_file: str | Path) -> Path:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    markdown_path = output_path.with_suffix(".md")
    markdown_path.write_text(build_parity_markdown(report), encoding="utf-8")
    return output_path


def build_parity_markdown(report: dict[str, Any]) -> str:
    live = dict((report.get("summary_counts") or {}).get("live") or {})
    engine = dict((report.get("summary_counts") or {}).get("engine_backtest") or {})
    live_symbols = dict(live.get("day_unique_symbols") or {})
    overlaps = dict(report.get("overlap_metrics") or {})
    diagnostics = dict(engine.get("buy_diagnostics") or {})
    capacity = dict(engine.get("buy_capacity") or {})
    score_stats = dict(engine.get("buy_score_stats") or {})
    sizing = dict(engine.get("buy_sizing") or {})

    lines = [
        "# Engine Backtest Parity",
        "",
        f"- parity_level: `{report.get('parity_level', 'unknown')}`",
        f"- account: `{report.get('account', '')}`",
        f"- session: `{report.get('session', '')}`",
        f"- date: `{(report.get('date_range') or {}).get('start', '')}`",
        f"- engine_source: `{(report.get('engine_backtest_source') or {}).get('mode', '')}`",
        "",
        "## Counts",
        "",
        f"- live final_candidate symbols: {len(live_symbols.get('final_candidate') or [])}",
        f"- live executed buy symbols: {len(live_symbols.get('successful_buy_orders') or [])}",
        f"- live successful sell symbols: {len(live_symbols.get('successful_sell_orders') or [])}",
        f"- engine selected buy count: {engine.get('executed_buy_count', 0)}",
        f"- engine selected sell count: {engine.get('executed_sell_count', 0)}",
        f"- engine buy_signal_count: {engine.get('buy_signal_count', 0)}",
        f"- engine buy_scored_candidate_count: {engine.get('buy_scored_candidate_count', 0)}",
        f"- engine buy rules: {engine.get('buy_rule_names', [])}",
        f"- engine buy diagnostic stage: {diagnostics.get('stage', 'unknown')}",
        "",
        "## Overlap",
        "",
        f"- buy vs live final: {((overlaps.get('buy_selected_vs_live_final_candidate') or {}).get('overlap_symbols') or [])}",
        f"- buy vs live executed: {((overlaps.get('buy_selected_vs_live_executed_buy') or {}).get('overlap_symbols') or [])}",
        f"- sell vs live successful sell: {((overlaps.get('sell_selected_vs_live_successful_sell') or {}).get('overlap_symbols') or [])}",
        "",
        "## Engine Buy Diagnosis",
        "",
        f"- stage: {diagnostics.get('stage', 'unknown')}",
        f"- summary: {diagnostics.get('summary', '')}",
        f"- primary_rejection_reason: {diagnostics.get('primary_rejection_reason')} ({diagnostics.get('primary_rejection_count', 0)})",
        f"- entered_rule_stage: {diagnostics.get('entered_rule_stage', False)}",
        f"- entered_scoring_stage: {diagnostics.get('entered_scoring_stage', False)}",
        f"- entered_sizing_stage: {diagnostics.get('entered_sizing_stage', False)}",
        "",
        "## Engine Buy Capacity",
        "",
        f"- positions_at_day_start: {capacity.get('positions_at_day_start')}",
        f"- positions_after_sell_pass: {capacity.get('positions_after_sell_pass')}",
        f"- positions_before_buy_pass: {capacity.get('positions_before_buy_pass')}",
        f"- positions_after_buy_pass: {capacity.get('positions_after_buy_pass')}",
        f"- max_positions: {capacity.get('max_positions')}",
        f"- available_slots_before_buy_pass: {capacity.get('available_slots_before_buy_pass')}",
        f"- capacity_blocked_before_rule_eval: {capacity.get('capacity_blocked_before_rule_eval')}",
        "",
        "## Engine Buy Funnel",
        "",
    ]

    for name, count in (engine.get("buy_funnel") or {}).items():
        lines.append(f"- {name}: {count}")

    lines.extend([
        "",
        "## Engine Buy Rules",
        "",
    ])

    for name, count in (engine.get("buy_rule_enabled_counts") or {}).items():
        passed = ((engine.get("buy_rule_pass_counts") or {}).get(name) or 0)
        failed = ((engine.get("buy_rule_fail_counts") or {}).get(name) or 0)
        pass_rate = ((engine.get("buy_rule_pass_rates") or {}).get(name))
        lines.append(f"- {name}: enabled={count} passed={passed} failed={failed} pass_rate={pass_rate}")

    lines.extend([
        "",
        "## Engine Buy Scores",
        "",
        f"- min_score_threshold: {score_stats.get('min_score_threshold')}",
        f"- signal_scores: {score_stats.get('signal_scores')}",
        f"- score_rejected: {score_stats.get('score_rejected')}",
        f"- scored_candidates: {score_stats.get('scored_candidates')}",
        "",
        "## Engine Buy Sizing",
        "",
        f"- symbol: {sizing.get('symbol')}",
        f"- score: {sizing.get('score')}",
        f"- recommended_qty: {sizing.get('recommended_qty')}",
        f"- block_reason_code: {sizing.get('block_reason_code')}",
        f"- block_reason_label: {sizing.get('block_reason_label')}",
        f"- reason: {sizing.get('reason')}",
    ])

    lines.extend([
        "",
        "## Notes",
        "",
    ])

    notes = list(report.get("divergence_notes") or []) or ["No divergence notes."]
    for note in notes:
        lines.append(f"- {note}")

    matched = list(report.get("matched_signals") or [])
    if matched:
        lines.extend(["", "## Matched", ""])
        for item in matched:
            lines.append(f"- {item}")

    warnings = list(report.get("warnings") or [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- {item}")

    return "\n".join(lines) + "\n"
