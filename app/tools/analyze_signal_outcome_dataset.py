"""CLI: analyze_signal_outcome_dataset

Reads a flat signal outcome dataset (CSV or JSONL) and prints compact
event-study style summaries.

Usage:
    python3 -m app.tools.analyze_signal_outcome_dataset \
        --date 20260408 --account mock_12345678_01

    python3 -m app.tools.analyze_signal_outcome_dataset \
        --file logs/signal_outcomes_mock_12345678_01_20260408.csv
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_RETURN_COLS: tuple[str, ...] = ("ret_5m_bps", "ret_30m_bps", "ret_eod_bps")
_RETURN_LABELS: dict[str, str] = {
    "ret_5m_bps": "5m",
    "ret_30m_bps": "30m",
    "ret_eod_bps": "eod",
}
_PRICE_BANDS: tuple[tuple[str, int | None, int | None], ...] = (
    ("<100k", None, 100_000),
    ("100k–300k", 100_000, 300_000),
    ("300k–700k", 300_000, 700_000),
    ("700k–1.0m", 700_000, 1_000_000),
    ("1.0m–1.5m", 1_000_000, 1_500_000),
    ("1.5m+", 1_500_000, None),
)
_TIME_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("09:00–10:00", 9 * 60, 10 * 60),
    ("10:00–11:00", 10 * 60, 11 * 60),
    ("11:00–12:00", 11 * 60, 12 * 60),
    ("12:00–13:00", 12 * 60, 13 * 60),
    ("13:00–14:00", 13 * 60, 14 * 60),
    ("14:00–15:30", 14 * 60, 15 * 60 + 30),
)

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _ok(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _locate(account: str, date: str) -> Path:
    csv_path = _PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.csv"
    if csv_path.exists():
        return csv_path
    jsonl_path = _PROJECT_ROOT / "logs" / f"signal_outcomes_{account}_{date}.jsonl"
    if jsonl_path.exists():
        return jsonl_path
    return csv_path


def _bool_val(row: dict[str, Any], col: str) -> bool:
    value = row.get(col)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _fval(row: dict[str, Any], col: str) -> float | None:
    value = row.get(col)
    if value is None or (isinstance(value, str) and value.strip() in ("", "None")):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fvals(rows: list[dict[str, Any]], col: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        parsed = _fval(row, col)
        if parsed is not None:
            values.append(parsed)
    return values


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _positive_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value > 0) / len(values) * 100.0, 1)


def _fmt_num(value: float | None, *, decimals: int = 2, width: int = 7) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{width}.{decimals}f}"


def _fmt_pct(value: float | None, *, width: int = 6) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{width}.1f}"


def _ret_summary(rows: list[dict[str, Any]], col: str) -> tuple[int, float | None, float | None, float | None]:
    values = _fvals(rows, col)
    return len(values), _median(values), _mean(values), _positive_rate(values)


def _covered_count(rows: list[dict[str, Any]], col: str) -> int:
    return sum(1 for row in rows if _fval(row, col) is not None)


def _price_band_label(row: dict[str, Any], *, col: str = "entry_price") -> str | None:
    price = _fval(row, col)
    if price is None or price <= 0:
        return None
    for label, lower, upper in _PRICE_BANDS:
        if lower is None and price < upper:
            return label
        if upper is None and lower <= price:
            return label
        if lower is not None and upper is not None and lower <= price < upper:
            return label
    return None


def _band_share_text(count: int, total: int) -> str:
    if total <= 0:
        return "  —"
    return f"{count / total * 100:>4.0f}%"


def _minute_of_day(raw_ts: Any) -> int | None:
    ts = str(raw_ts or "").strip()
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.hour * 60 + dt.minute
    except ValueError:
        return None


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


def _bucket_order(rows: list[dict[str, Any]]) -> list[str]:
    preferred = ["core", "rotating", "exploration"]
    present = {str(row.get("selection_bucket") or "?") for row in rows}
    ordered = [bucket for bucket in preferred if bucket in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _is_deep_eval_rejected(row: dict[str, Any]) -> bool:
    outcome = str(row.get("selection_outcome") or "").strip()
    if outcome == "deep_eval_rejected":
        return True
    return _bool_val(row, "deep_evaluated") and not _bool_val(row, "final_candidate")


def _print_header(path: Path, rows: list[dict[str, Any]]) -> None:
    covered = sum(1 for row in rows if _fval(row, "entry_price") is not None)
    print()
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(_h("  analyze_signal_outcome_dataset"))
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print(f"  file    : {path.name}")
    print(f"  rows    : {len(rows)}")
    print(f"  covered : {covered}/{len(rows)} with entry_price")
    print()


def _print_comparison(rows: list[dict[str, Any]]) -> None:
    finals = [row for row in rows if _bool_val(row, "final_candidate")]
    rejected = [row for row in rows if _is_deep_eval_rejected(row)]
    print(_h("  1. final_candidate vs deep_eval_rejected"))
    print("  horizon            final(n)   final_med  reject(n)  reject_med    delta")
    print("  ---------------- ----------- ---------- ---------- ---------- --------")
    for col in _RETURN_COLS:
        final_n, final_med, _, _ = _ret_summary(finals, col)
        reject_n, reject_med, _, _ = _ret_summary(rejected, col)
        delta = None if final_med is None or reject_med is None else final_med - reject_med
        delta_text = _fmt_num(delta, width=8)
        if delta is not None:
            if delta > 5:
                delta_text = _ok(delta_text)
            elif delta < -5:
                delta_text = _warn(delta_text)
            else:
                delta_text = _dim(delta_text)
        print(
            f"  {_RETURN_LABELS[col]:<16}"
            f"{final_n:>11} {_fmt_num(final_med)}"
            f"{reject_n:>10} {_fmt_num(reject_med)} {delta_text}"
        )
    print()


def _print_reason_summary(rows: list[dict[str, Any]], reason: str, title: str) -> None:
    subset = [row for row in rows if str(row.get("rejection_reason") or "").strip() == reason]
    print(_h(f"  {title}"))
    if not subset:
        print(_dim(f"  No `{reason}` rows with outcome fields in this dataset."))
        print()
        return

    print(f"  rows={len(subset)}")
    print("  horizon               n   median      avg   pos%")
    print("  ---------------- ---- ---- -------- -------- ------")
    for col in _RETURN_COLS:
        n, med, avg, pos = _ret_summary(subset, col)
        print(
            f"  {_RETURN_LABELS[col]:<16}"
            f"{n:>5} {_fmt_num(med)} {_fmt_num(avg)} {_fmt_pct(pos)}"
        )

    eod_vals = _fvals(subset, "ret_eod_bps")
    if eod_vals:
        label = "missed-opportunity leaning" if _median(eod_vals) and _median(eod_vals) > 0 else "not obviously missed-opportunity"
        label_text = _ok(label) if label.startswith("missed") else _warn(label)
        print(f"  read: {label_text}")
    else:
        print(_dim("  read: no `ret_eod_bps` coverage"))
    print()


def _print_budget_rescue_analysis(rows: list[dict[str, Any]]) -> None:
    rescue_rows = [row for row in rows if _bool_val(row, "budget_rescue_applied")]
    budget_limited_rows = [
        row for row in rows
        if str(row.get("rejection_reason") or "").strip() == "trade_budget_limited"
    ]
    ordinary_final_rows = [
        row for row in rows
        if _bool_val(row, "final_candidate")
        and not _bool_val(row, "budget_rescue_applied")
        and str(row.get("rejection_reason") or "").strip() != "trade_budget_limited"
    ]

    print(_h("  4. budget rescue analysis"))
    print("  group                 rows      5m     30m     eod")
    print("  ------------------ ------- ------- ------- -------")
    groups = (
        ("budget_rescue", rescue_rows),
        ("trade_budget_limited", budget_limited_rows),
        ("ordinary_final", ordinary_final_rows),
    )
    for label, subset in groups:
        print(
            f"  {label:<18} {len(subset):>7}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_5m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_30m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_eod_bps')))}"
        )

    if rescue_rows:
        eod = _median(_fvals(rescue_rows, "ret_eod_bps"))
        if eod is not None and eod > 0:
            print(f"  read: {_ok('rescued rows are retaining positive forward payoff')}")
        elif eod is not None:
            print(f"  read: {_warn('rescued rows are not clearly outperforming yet')}")
        else:
            print(_dim("  read: rescued rows exist but outcome coverage is incomplete"))
    else:
        print(_dim("  No `budget_rescue_applied=True` rows are present in this dataset yet."))
    print()


def _print_core_rescue_analysis(rows: list[dict[str, Any]]) -> None:
    rescue_rows = [row for row in rows if _bool_val(row, "core_rescue_applied")]
    ordinary_core_rows = [
        row for row in rows
        if str(row.get("selection_bucket") or "").strip() == "core"
        and not _bool_val(row, "core_rescue_applied")
    ]
    rejected_rows = [row for row in rows if _is_deep_eval_rejected(row)]
    final_rows = [row for row in rows if _bool_val(row, "final_candidate")]

    print(_h("  5. core_rescue_applied outcome analysis"))
    if not rescue_rows:
        print(_dim("  No `core_rescue_applied=True` rows are present in this outcome dataset yet."))
        print()
        return

    shallow_count = sum(1 for row in rescue_rows if _bool_val(row, "shallow_selected"))
    deep_count = sum(1 for row in rescue_rows if _bool_val(row, "deep_evaluated"))
    final_count = sum(1 for row in rescue_rows if _bool_val(row, "final_candidate"))
    executed_count = sum(1 for row in rescue_rows if _bool_val(row, "executed"))
    covered_any = sum(
        1
        for row in rescue_rows
        if any(_fval(row, col) is not None for col in _RETURN_COLS)
    )

    print(
        "  funnel"
        f" | shallow={shallow_count}"
        f" | deep={deep_count}"
        f" | final={final_count}"
        f" | executed={executed_count}"
    )
    print(
        "  outcome coverage"
        f" | any={covered_any}/{len(rescue_rows)}"
        f" | 5m={_covered_count(rescue_rows, 'ret_5m_bps')}/{len(rescue_rows)}"
        f" | 30m={_covered_count(rescue_rows, 'ret_30m_bps')}/{len(rescue_rows)}"
        f" | eod={_covered_count(rescue_rows, 'ret_eod_bps')}/{len(rescue_rows)}"
    )
    print()

    print("  rescued core forward returns")
    print("  horizon               n   median      avg   pos%")
    print("  ---------------- ---- ---- -------- -------- ------")
    for col in _RETURN_COLS:
        n, med, avg, pos = _ret_summary(rescue_rows, col)
        print(
            f"  {_RETURN_LABELS[col]:<16}"
            f"{n:>5} {_fmt_num(med)} {_fmt_num(avg)} {_fmt_pct(pos)}"
        )
    print()

    print("  comparison            rows   eod_cov      5m     30m     eod")
    print("  ------------------ ------- -------- ------- ------- -------")
    groups = (
        ("core_rescue", rescue_rows),
        ("ordinary_core", ordinary_core_rows),
        ("deep_eval_rejected", rejected_rows),
        ("final_candidate", final_rows),
    )
    for label, subset in groups:
        print(
            f"  {label:<18} {len(subset):>7}"
            f" {_covered_count(subset, 'ret_eod_bps'):>8}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_5m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_30m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_eod_bps')))}"
        )

    rescue_eod = _median(_fvals(rescue_rows, "ret_eod_bps"))
    reject_eod = _median(_fvals(rejected_rows, "ret_eod_bps"))
    final_eod = _median(_fvals(final_rows, "ret_eod_bps"))
    rescue_any = any(_covered_count(rescue_rows, col) > 0 for col in _RETURN_COLS)

    if not rescue_any:
        print(_dim("  read: insufficient evidence (rescued rows exist but no reconstructable forward returns yet)"))
    elif rescue_eod is None:
        print(_dim("  read: partial evidence only (5m/30m available, but no eod coverage yet)"))
    elif final_eod is not None and rescue_eod >= final_eod - 20:
        print(f"  read: {_ok('rescued core looks promising relative to finals')}")
    elif reject_eod is not None and rescue_eod > reject_eod:
        print(f"  read: {_ok('rescued core looks better than ordinary deep-eval rejects')}")
    elif final_eod is not None and rescue_eod < final_eod and rescue_eod <= 0:
        print(f"  read: {_warn('rescued core still weak vs finals')}")
    elif reject_eod is not None and rescue_eod <= reject_eod:
        print(f"  read: {_warn('rescued core is not outperforming ordinary rejects yet')}")
    else:
        print(_dim("  read: insufficient evidence"))
    print()


def _print_price_band_analysis(rows: list[dict[str, Any]]) -> None:
    budget_rows = [
        row for row in rows
        if str(row.get("rejection_reason") or "").strip() == "trade_budget_limited"
        and _price_band_label(row) is not None
    ]
    rescue_rows = [
        row for row in rows
        if _bool_val(row, "budget_rescue_applied")
        and _price_band_label(row) is not None
    ]

    print(_h("  6. Price-band analysis"))
    budget_high = sum(1 for row in budget_rows if (_fval(row, "entry_price") or 0) >= 1_000_000)
    rescue_high = sum(1 for row in rescue_rows if (_fval(row, "entry_price") or 0) >= 1_000_000)
    print(
        "  based on entry_price"
        f" | budget rows with price={len(budget_rows)}"
        f" | rescue rows with price={len(rescue_rows)}"
    )
    print(
        "  high-price share (1.0m+)"
        f" | budget={budget_high}/{len(budget_rows)} ({_band_share_text(budget_high, len(budget_rows)).strip()})"
        f" | rescue={rescue_high}/{len(rescue_rows)} ({_band_share_text(rescue_high, len(rescue_rows)).strip()})"
    )
    print("  band              budget   pct  rescue   pct  budget_eod  rescue_eod")
    print("  --------------- -------- ----- ------- ----- ---------- ----------")
    for label, _, _ in _PRICE_BANDS:
        budget_band = [row for row in budget_rows if _price_band_label(row) == label]
        rescue_band = [row for row in rescue_rows if _price_band_label(row) == label]
        print(
            f"  {label:<15}"
            f"{len(budget_band):>8} {_band_share_text(len(budget_band), len(budget_rows))}"
            f"{len(rescue_band):>8} {_band_share_text(len(rescue_band), len(rescue_rows))}"
            f" {_fmt_num(_median(_fvals(budget_band, 'ret_eod_bps')), width=10)}"
            f" {_fmt_num(_median(_fvals(rescue_band, 'ret_eod_bps')), width=10)}"
        )
    if not budget_rows and not rescue_rows:
        print(_dim("  No budget-limited or rescued rows with reconstructable entry_price exist in this dataset yet."))
    elif not rescue_rows:
        print(_dim("  No `budget_rescue_applied=True` rows with entry_price are present yet."))
    print()


def _print_bucket_comparison(rows: list[dict[str, Any]]) -> None:
    print(_h("  7. Bucket comparison"))
    print("  bucket          rows      5m     30m     eod")
    print("  -------------- ----- ------- ------- -------")
    for bucket in _bucket_order(rows):
        subset = [
            row for row in rows
            if str(row.get("selection_bucket") or "").strip() == bucket
            and _fval(row, "entry_price") is not None
        ]
        print(
            f"  {bucket:<14} {len(subset):>5}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_5m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_30m_bps')))}"
            f" {_fmt_num(_median(_fvals(subset, 'ret_eod_bps')))}"
        )
    print()


def _print_time_summary(rows: list[dict[str, Any]]) -> None:
    covered = [row for row in rows if _fval(row, "entry_price") is not None]
    if not covered:
        return
    print(_h("  8. Time-of-day outcome summary"))
    print("  time                  rows  final   budget  pb_ins     eod")
    print("  -------------------- ----- ------ -------- ------- -------")
    for label, _, _ in _TIME_BUCKETS:
        bucket_rows = [row for row in covered if _time_bucket_label(row.get("entry_ts") or row.get("ts")) == label]
        finals = sum(1 for row in bucket_rows if _bool_val(row, "final_candidate"))
        budget = sum(1 for row in bucket_rows if str(row.get("rejection_reason") or "").strip() == "trade_budget_limited")
        pb_ins = sum(1 for row in bucket_rows if str(row.get("rejection_reason") or "").strip() == "profit_buffer_insufficient")
        print(
            f"  {label:<20} {len(bucket_rows):>5} {finals:>6} {budget:>8} {pb_ins:>7}"
            f" {_fmt_num(_median(_fvals(bucket_rows, 'ret_eod_bps')))}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a signal outcome dataset")
    parser.add_argument("--file", default="", help="Input signal outcome dataset CSV or JSONL")
    parser.add_argument("--date", default="", help="Date YYYYMMDD for auto-locating the outcome dataset")
    parser.add_argument("--account", default="", help="Account identifier for auto-locating the dataset")
    parser.add_argument("--session", default="", help="Optional session filter")
    args = parser.parse_args()

    if not args.file and (not args.date or not args.account):
        print("Provide either --file or both --date and --account.", file=sys.stderr)
        sys.exit(1)

    path = Path(args.file) if args.file else _locate(args.account, args.date)
    rows = _load(path)
    if args.session:
        rows = [
            row for row in rows
            if str(row.get("session") or "").upper() == args.session.upper()
        ]
    if not rows:
        # P0-c: an empty-but-valid outcome dataset is not a failure — exit 0.
        print(
            f"No rows matched the requested dataset/filter in {path.name} "
            "— empty dataset, nothing to analyze.",
            file=sys.stderr,
        )
        return

    _print_header(path, rows)
    _print_comparison(rows)
    _print_reason_summary(rows, "trade_budget_limited", "  2. trade_budget_limited")
    _print_reason_summary(rows, "profit_buffer_insufficient", "  3. profit_buffer_insufficient")
    _print_budget_rescue_analysis(rows)
    _print_core_rescue_analysis(rows)
    _print_price_band_analysis(rows)
    _print_bucket_comparison(rows)
    _print_time_summary(rows)
    print(_h("════════════════════════════════════════════════════════════════════════"))
    print()


if __name__ == "__main__":
    main()
