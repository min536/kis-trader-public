"""Score tuning preparation pipeline built on top of parity reports."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backtester.engine_backtest.parity import (
    _build_provider_from_signal_rows,
    _cycle_snapshots_path,
    _filter_snapshots_for_day,
    _load_signal_rows,
    _normalize_date,
    _pick_latest_symbol_rows,
    _read_jsonl,
    _resolve_initial_cash,
    _seed_portfolio_from_snapshot,
    _select_seed_snapshot,
)
from backtester.engine_backtest.parity_batch import (
    build_parity_batch_summary,
    collect_parity_reports,
    render_parity_batch_console,
    write_parity_batch_outputs,
)
from backtester.engine_backtest.portfolio import BacktestPortfolio
from backtester.engine_backtest.runner import DayRecord, collect_buy_pass_samples, _run_sell_pass
from backtester.engine_backtest.settings_factory import make_settings
from backtester.engine_backtest.state import make_backtest_state, reset_daily_state


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    q = min(max(q, 0.0), 1.0)
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return round(ordered[low], 4)
    frac = pos - low
    value = ordered[low] + ((ordered[high] - ordered[low]) * frac)
    return round(value, 4)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "avg": None,
            "max": None,
            "p10": None,
            "p50": None,
            "p90": None,
        }
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "avg": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
        "p10": _percentile(values, 0.10),
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
    }


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            converted[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            converted[key] = value
    return converted


def _write_rows_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("")
        return
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(_jsonable_row(row))


def _write_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _derive_review_band(positive_margins: list[float], negative_margins: list[float]) -> float | None:
    values = [abs(v) for v in [*positive_margins, *negative_margins] if v is not None]
    if len(values) < 4:
        return None
    p75 = _percentile(values, 0.75)
    if p75 is None:
        return None
    return round(min(max(p75, 0.05), 0.5), 4)


def _range(start_iso: float | None, end_iso: float | None) -> dict[str, float | None]:
    if start_iso is None or end_iso is None:
        return {"min": start_iso, "max": end_iso}
    return {"min": start_iso, "max": end_iso}


def build_engine_buy_samples_for_report(
    report: dict[str, Any],
    *,
    initial_cash: int | None = None,
) -> list[dict[str, Any]]:
    account = str(report.get("account") or "").strip()
    date_text = str(((report.get("date_range") or {}).get("start")) or "").strip()
    session = str(report.get("session") or "REGULAR").upper()
    if not account or not date_text:
        return []

    _, iso_date, trading_date = _normalize_date(date_text)
    signal_rows, _, _ = _load_signal_rows(
        account=account,
        date_text=trading_date.strftime("%Y%m%d"),
        session=session,
    )
    latest_signal_rows = _pick_latest_symbol_rows(signal_rows)
    if not latest_signal_rows:
        return []

    snapshot_rows = _filter_snapshots_for_day(
        _read_jsonl(_cycle_snapshots_path(account)),
        iso_date=iso_date,
        session=session,
    )
    settings = make_settings()
    seed_snapshot = _select_seed_snapshot(snapshot_rows)
    provider, prices = _build_provider_from_signal_rows(
        rows=latest_signal_rows,
        trading_date=trading_date,
        seed_snapshot=seed_snapshot,
    )
    resolved_cash = _resolve_initial_cash(seed_snapshot=seed_snapshot, fallback_cash=initial_cash)
    portfolio = BacktestPortfolio(initial_cash=resolved_cash)
    seeded_positions = _seed_portfolio_from_snapshot(
        portfolio,
        seed_snapshot=seed_snapshot,
        trading_date=trading_date,
    )
    state = make_backtest_state(trading_date)
    reset_daily_state(state, trading_date)

    record = DayRecord(
        date=trading_date,
        portfolio_value=portfolio.total_value(prices),
        cash=portfolio.cash,
    )
    record.buy_capacity = {"positions_at_day_start": len(portfolio.positions)}

    if settings.sell_enable:
        _run_sell_pass(
            portfolio=portfolio,
            data_provider=provider,
            settings=settings,
            state=state,
            trading_date=trading_date,
            prices=prices,
            record=record,
        )

    samples = collect_buy_pass_samples(
        portfolio=portfolio,
        data_provider=provider,
        symbols=provider.symbols(),
        settings=settings,
        state=state,
        trading_date=trading_date,
        prices=prices,
    )

    batch_row = None
    engine_summary = dict(((report.get("summary_counts") or {}).get("engine_backtest")) or {})
    stage = str(((engine_summary.get("buy_diagnostics") or {}).get("stage")) or "")
    tuning_label = str(report.get("_score_tuning_label") or "")
    for sample in samples:
        sample["account"] = account
        sample["session"] = session
        sample["day_stage"] = stage
        sample["score_tuning_label"] = tuning_label
        sample["seeded_position_count"] = seeded_positions
        sample["selected_buy_score_reported"] = _safe_float(engine_summary.get("selected_buy_score"))
    return samples


def _rows_for_dates(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row.get("date") or "")].append(row)
    return dict(by_date)


def build_score_tuning_datasets(
    reports: list[dict[str, Any]],
    batch_summary: dict[str, Any],
    *,
    initial_cash: int | None = None,
) -> dict[str, Any]:
    row_by_date = {
        str(row.get("date") or ""): dict(row)
        for row in (batch_summary.get("rows") or [])
    }
    positive_rows: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []
    conditional_rows: list[dict[str, Any]] = []
    date_samples: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []

    for report in reports:
        date_text = str(((report.get("date_range") or {}).get("start")) or "")
        batch_row = row_by_date.get(date_text) or {}
        tuning_label = str(batch_row.get("score_tuning_label") or "")
        stage = str(batch_row.get("stage") or "")
        if tuning_label == "exclude":
            continue

        report["_score_tuning_label"] = tuning_label
        try:
            samples = build_engine_buy_samples_for_report(report, initial_cash=initial_cash)
        except Exception as exc:
            errors.append({"date": date_text, "error": str(exc)})
            continue
        date_samples[date_text] = samples

        if stage == "executed_buy":
            positive_rows.extend(
                sample
                for sample in samples
                if bool(sample.get("executed"))
            )
        elif stage == "score_blocked":
            negative_rows.extend(
                sample
                for sample in samples
                if str(sample.get("stage") or "") == "score_blocked"
                and bool(sample.get("rule_gate_passed"))
            )
        elif stage == "rule_blocked" and tuning_label == "include_conditional":
            conditional_rows.extend(
                sample
                for sample in samples
                if str(sample.get("stage") or "") == "rule_blocked"
            )

    datasets = {
        "positive_rows": positive_rows,
        "negative_rows": negative_rows,
        "conditional_rows": conditional_rows,
        "date_samples": date_samples,
        "errors": errors,
    }
    datasets["summary"] = build_score_tuning_dataset_summary(datasets)
    return datasets


def build_score_tuning_dataset_summary(datasets: dict[str, Any]) -> dict[str, Any]:
    def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [_safe_float(row.get("score")) for row in rows]
        scores = [score for score in scores if score is not None]
        margins = [_safe_float(row.get("score_margin_to_threshold")) for row in rows]
        margins = [margin for margin in margins if margin is not None]
        return {
            "sample_count": len(rows),
            "date_coverage": sorted({str(row.get("date") or "") for row in rows if str(row.get("date") or "")}),
            "symbol_coverage_count": len({str(row.get("symbol") or "") for row in rows if str(row.get("symbol") or "")}),
            "score_distribution": _stats(scores),
            "margin_distribution": _stats(margins),
        }

    positive_rows = list(datasets.get("positive_rows") or [])
    negative_rows = list(datasets.get("negative_rows") or [])
    conditional_rows = list(datasets.get("conditional_rows") or [])
    return {
        "positive_set": _summarize_rows(positive_rows),
        "negative_set": _summarize_rows(negative_rows),
        "conditional_set": _summarize_rows(conditional_rows),
        "dataset_error_count": len(datasets.get("errors") or []),
    }


def build_score_analysis_summary(
    datasets: dict[str, Any],
    *,
    review_band: float | None = None,
) -> dict[str, Any]:
    positive_rows = list(datasets.get("positive_rows") or [])
    negative_rows = list(datasets.get("negative_rows") or [])
    conditional_rows = list(datasets.get("conditional_rows") or [])

    positive_scores = [_safe_float(row.get("score")) for row in positive_rows]
    positive_scores = [value for value in positive_scores if value is not None]
    negative_scores = [_safe_float(row.get("score")) for row in negative_rows]
    negative_scores = [value for value in negative_scores if value is not None]
    positive_margins = [_safe_float(row.get("score_margin_to_threshold")) for row in positive_rows]
    positive_margins = [value for value in positive_margins if value is not None]
    negative_margins = [_safe_float(row.get("score_margin_to_threshold")) for row in negative_rows]
    negative_margins = [value for value in negative_margins if value is not None]

    derived_review_band = review_band if review_band is not None else _derive_review_band(
        positive_margins,
        negative_margins,
    )
    effective_review_band = derived_review_band if derived_review_band is not None else 0.1

    overlap = {
        "max_negative_score": max(negative_scores) if negative_scores else None,
        "min_positive_score": min(positive_scores) if positive_scores else None,
        "positive_below_or_equal_negative_max": 0,
        "negative_above_or_equal_positive_min": 0,
        "score_range_overlap": False,
    }
    if overlap["max_negative_score"] is not None:
        overlap["positive_below_or_equal_negative_max"] = sum(
            1 for score in positive_scores if score <= float(overlap["max_negative_score"])
        )
    if overlap["min_positive_score"] is not None:
        overlap["negative_above_or_equal_positive_min"] = sum(
            1 for score in negative_scores if score >= float(overlap["min_positive_score"])
        )
    if overlap["max_negative_score"] is not None and overlap["min_positive_score"] is not None:
        overlap["score_range_overlap"] = bool(
            float(overlap["max_negative_score"]) >= float(overlap["min_positive_score"])
        )

    by_date: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "date": "",
        "threshold": None,
        "selected_buy_score": None,
        "rejected_max_score": None,
        "selected_vs_rejected_gap": None,
        "near_threshold_sample_count": 0,
        "classification": "insufficient",
    })

    for row in [*positive_rows, *negative_rows]:
        date_text = str(row.get("date") or "")
        item = by_date[date_text]
        item["date"] = date_text
        threshold = _safe_float(row.get("min_score_threshold"))
        if threshold is not None:
            item["threshold"] = threshold
        margin = _safe_float(row.get("score_margin_to_threshold"))
        if margin is not None and abs(margin) <= effective_review_band:
            item["near_threshold_sample_count"] += 1
        if row in positive_rows:
            score = _safe_float(row.get("score"))
            if score is not None:
                current = _safe_float(item.get("selected_buy_score"))
                if current is None or score > current:
                    item["selected_buy_score"] = score
        if row in negative_rows:
            score = _safe_float(row.get("score"))
            if score is not None:
                current = _safe_float(item.get("rejected_max_score"))
                if current is None or score > current:
                    item["rejected_max_score"] = score

    date_rows: list[dict[str, Any]] = []
    borderline_dates: list[str] = []
    stable_dates: list[str] = []
    gaps: list[float] = []
    for date_text, item in sorted(by_date.items()):
        selected = _safe_float(item.get("selected_buy_score"))
        rejected = _safe_float(item.get("rejected_max_score"))
        if selected is not None and rejected is not None:
            gap = round(selected - rejected, 4)
            item["selected_vs_rejected_gap"] = gap
            gaps.append(gap)
        near_count = _safe_int(item.get("near_threshold_sample_count")) or 0
        if near_count > 0:
            item["classification"] = "borderline"
            borderline_dates.append(date_text)
        elif selected is not None or rejected is not None:
            item["classification"] = "stable"
            stable_dates.append(date_text)
        date_rows.append(item)

    review_band_counts = []
    for band in (0.05, 0.10, 0.25, effective_review_band):
        count = sum(
            1
            for margin in [*positive_margins, *negative_margins]
            if abs(margin) <= band
        )
        review_band_counts.append({"band": round(band, 4), "sample_count": count})

    analysis = {
        "generated_at": datetime.now().isoformat(),
        "review_band": {
            "requested": review_band,
            "derived": derived_review_band,
            "effective": effective_review_band,
            "insufficient_data": derived_review_band is None and review_band is None,
        },
        "positive_score_distribution": _stats(positive_scores),
        "negative_score_distribution": _stats(negative_scores),
        "positive_margin_distribution": _stats(positive_margins),
        "negative_margin_distribution": _stats(negative_margins),
        "overlap_metrics": overlap,
        "selected_vs_rejected_gap_distribution": _stats(gaps),
        "date_rows": date_rows,
        "borderline_dates": borderline_dates,
        "stable_dates": stable_dates,
        "review_band_counts": review_band_counts,
        "conditional_rule_sample_count": len(conditional_rows),
        "insufficient_data": not positive_rows or not negative_rows,
    }
    return analysis


def build_tuning_scaffold(
    datasets: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    positive_rows = list(datasets.get("positive_rows") or [])
    negative_rows = list(datasets.get("negative_rows") or [])
    conditional_rows = list(datasets.get("conditional_rows") or [])
    effective_band = _safe_float(((analysis.get("review_band") or {}).get("effective"))) or 0.1

    conservative = round(max(0.05, min(effective_band * 0.5, 0.15)), 4)
    balanced = round(max(0.1, min(effective_band, 0.3)), 4)
    aggressive = round(max(0.2, min(effective_band * 1.5, 0.5)), 4)

    def _scenario(delta: float, name: str, note: str) -> dict[str, Any]:
        negative_hits = [
            row for row in negative_rows
            if (_safe_float(row.get("score_margin_to_threshold")) or -999.0) >= (-delta)
        ]
        positive_near = [
            row for row in positive_rows
            if (_safe_float(row.get("score_margin_to_threshold")) or 999.0) <= delta
        ]
        affected_dates = sorted({
            str(row.get("date") or "")
            for row in [*negative_hits, *positive_near]
            if str(row.get("date") or "")
        })
        return {
            "name": name,
            "review_delta": delta,
            "note": note,
            "negative_score_samples_within_delta": len(negative_hits),
            "positive_reference_samples_within_delta": len(positive_near),
            "affected_dates": affected_dates,
            "conditional_rule_context_dates": sorted({
                str(row.get("date") or "") for row in conditional_rows if str(row.get("date") or "")
            }),
        }

    scaffold = {
        "generated_at": datetime.now().isoformat(),
        "current_thresholds": sorted({
            _safe_float(row.get("min_score_threshold"))
            for row in [*positive_rows, *negative_rows]
            if _safe_float(row.get("min_score_threshold")) is not None
        }),
        "borderline_sample_count": sum(
            int(item.get("sample_count") or 0)
            for item in (analysis.get("review_band_counts") or [])
            if _safe_float(item.get("band")) == _safe_float(((analysis.get("review_band") or {}).get("effective")))
        ),
        "positive_negative_overlap": dict(analysis.get("overlap_metrics") or {}),
        "scenarios": [
            _scenario(conservative, "conservative", "threshold 소폭 검토 band"),
            _scenario(balanced, "balanced", "threshold + normalization 검토 band"),
            _scenario(aggressive, "aggressive", "폭 넓은 threshold 검토 band"),
        ],
        "insufficient_data": bool(analysis.get("insufficient_data")),
    }
    return scaffold


def render_score_analysis_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# Score Analysis",
        "",
        f"- effective_review_band: {((analysis.get('review_band') or {}).get('effective'))}",
        f"- insufficient_data: {analysis.get('insufficient_data')}",
        f"- borderline_dates: {analysis.get('borderline_dates', [])}",
        f"- stable_dates: {analysis.get('stable_dates', [])}",
        "",
        "## Positive",
        "",
        f"- score_distribution: {analysis.get('positive_score_distribution')}",
        f"- margin_distribution: {analysis.get('positive_margin_distribution')}",
        "",
        "## Negative",
        "",
        f"- score_distribution: {analysis.get('negative_score_distribution')}",
        f"- margin_distribution: {analysis.get('negative_margin_distribution')}",
        "",
        "## Overlap",
        "",
        f"- metrics: {analysis.get('overlap_metrics')}",
        f"- selected_vs_rejected_gap_distribution: {analysis.get('selected_vs_rejected_gap_distribution')}",
    ]
    return "\n".join(lines) + "\n"


def render_tuning_scaffold_markdown(scaffold: dict[str, Any]) -> str:
    lines = [
        "# Tuning Scaffold",
        "",
        f"- current_thresholds: {scaffold.get('current_thresholds', [])}",
        f"- borderline_sample_count: {scaffold.get('borderline_sample_count')}",
        f"- insufficient_data: {scaffold.get('insufficient_data')}",
        "",
        "## Scenarios",
        "",
    ]
    for scenario in scaffold.get("scenarios") or []:
        lines.append(
            f"- {scenario.get('name')}: delta={scenario.get('review_delta')} "
            f"negative_hits={scenario.get('negative_score_samples_within_delta')} "
            f"positive_near={scenario.get('positive_reference_samples_within_delta')} "
            f"affected_dates={scenario.get('affected_dates')}"
        )
    return "\n".join(lines) + "\n"


def write_score_pipeline_outputs(
    *,
    output_dir: str | Path,
    batch_summary: dict[str, Any],
    datasets: dict[str, Any],
    analysis: dict[str, Any],
    scaffold: dict[str, Any],
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    batch_json, batch_csv = write_parity_batch_outputs(
        batch_summary,
        output_base=root / "parity_batch_summary",
    )

    _write_json(datasets.get("summary") or {}, root / "score_dataset_summary.json")
    _write_json(datasets.get("positive_rows") or [], root / "score_dataset_positive.json")
    _write_json(datasets.get("negative_rows") or [], root / "score_dataset_negative.json")
    _write_json(datasets.get("conditional_rows") or [], root / "score_dataset_conditional.json")
    _write_rows_csv(list(datasets.get("positive_rows") or []), root / "score_dataset_positive.csv")
    _write_rows_csv(list(datasets.get("negative_rows") or []), root / "score_dataset_negative.csv")
    _write_rows_csv(list(datasets.get("conditional_rows") or []), root / "score_dataset_conditional.csv")

    _write_json(analysis, root / "score_analysis_summary.json")
    (root / "score_analysis_report.md").write_text(
        render_score_analysis_markdown(analysis),
        encoding="utf-8",
    )
    _write_rows_csv(list(analysis.get("date_rows") or []), root / "score_analysis_dates.csv")

    _write_json(scaffold, root / "score_tuning_scaffold.json")
    (root / "score_tuning_scaffold.md").write_text(
        render_tuning_scaffold_markdown(scaffold),
        encoding="utf-8",
    )

    return {
        "batch_json": str(batch_json),
        "batch_csv": str(batch_csv),
        "dataset_summary_json": str(root / "score_dataset_summary.json"),
        "positive_csv": str(root / "score_dataset_positive.csv"),
        "negative_csv": str(root / "score_dataset_negative.csv"),
        "conditional_csv": str(root / "score_dataset_conditional.csv"),
        "analysis_json": str(root / "score_analysis_summary.json"),
        "analysis_markdown": str(root / "score_analysis_report.md"),
        "analysis_dates_csv": str(root / "score_analysis_dates.csv"),
        "scaffold_json": str(root / "score_tuning_scaffold.json"),
        "scaffold_markdown": str(root / "score_tuning_scaffold.md"),
    }


def render_score_pipeline_console(
    *,
    batch_summary: dict[str, Any],
    datasets: dict[str, Any],
    analysis: dict[str, Any],
    scaffold: dict[str, Any],
) -> str:
    lines = [
        render_parity_batch_console(batch_summary),
        "",
        "Score Dataset Summary",
        f"- positive_samples: {((datasets.get('summary') or {}).get('positive_set') or {}).get('sample_count', 0)}",
        f"- negative_samples: {((datasets.get('summary') or {}).get('negative_set') or {}).get('sample_count', 0)}",
        f"- conditional_samples: {((datasets.get('summary') or {}).get('conditional_set') or {}).get('sample_count', 0)}",
        "",
        "Score Analysis Summary",
        f"- effective_review_band: {((analysis.get('review_band') or {}).get('effective'))}",
        f"- borderline_dates: {analysis.get('borderline_dates', [])}",
        f"- stable_dates: {analysis.get('stable_dates', [])}",
        f"- insufficient_data: {analysis.get('insufficient_data')}",
        "",
        "Tuning Scaffold",
        f"- current_thresholds: {scaffold.get('current_thresholds', [])}",
    ]
    for scenario in scaffold.get("scenarios") or []:
        lines.append(
            f"- {scenario.get('name')}: delta={scenario.get('review_delta')} "
            f"neg_hits={scenario.get('negative_score_samples_within_delta')} "
            f"pos_near={scenario.get('positive_reference_samples_within_delta')}"
        )
    return "\n".join(lines)


def run_parity_score_pipeline(
    *,
    account: str | None = None,
    dates: list[str] | None = None,
    session: str = "REGULAR",
    initial_cash: int | None = None,
    report_paths: list[Path] | None = None,
    report_dir: str | Path | None = None,
    review_band: float | None = None,
) -> dict[str, Any]:
    reports, errors = collect_parity_reports(
        account=account,
        dates=dates,
        session=session,
        initial_cash=initial_cash,
        report_paths=report_paths,
        report_dir=report_dir,
    )
    if not reports and errors:
        first = errors[0]
        raise FileNotFoundError(f"No usable parity reports found: {first['error']}")

    batch_summary = build_parity_batch_summary(reports, errors=errors)
    datasets = build_score_tuning_datasets(reports, batch_summary, initial_cash=initial_cash)
    analysis = build_score_analysis_summary(datasets, review_band=review_band)
    scaffold = build_tuning_scaffold(datasets, analysis)
    return {
        "batch_summary": batch_summary,
        "datasets": datasets,
        "analysis": analysis,
        "scaffold": scaffold,
    }


__all__ = [
    "build_engine_buy_samples_for_report",
    "build_score_tuning_datasets",
    "build_score_tuning_dataset_summary",
    "build_score_analysis_summary",
    "build_tuning_scaffold",
    "render_score_pipeline_console",
    "run_parity_score_pipeline",
    "write_score_pipeline_outputs",
]
