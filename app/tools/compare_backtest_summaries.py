"""CLI: compare_backtest_summaries

Compare two exported backtest summary JSON files from
`app.tools.export_backtest_result_summary`.

This is research comparison tooling only. It does not change live trading logic.

Examples:
    python3 -m app.tools.compare_backtest_summaries \
        --left backtester/reports/run_bt_custom_kis_trader_core_family_approx_summary.json \
        --right backtester/reports/run_bt_custom_kis_trader_continuation_family_approx_summary.json \
        --left-label core \
        --right-label continuation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BETTER_HIGHER = frozenset(
    {
        "total_return",
        "cagr",
        "sharpe",
        "win_rate",
        "profit_factor",
        "expectancy",
    }
)
_BETTER_LOWER = frozenset({"max_drawdown"})
_NEUTRAL = frozenset({"trade_count"})

_METRICS: tuple[tuple[str, str], ...] = (
    ("total_return", "Total Return"),
    ("cagr", "CAGR"),
    ("max_drawdown", "Max DD"),
    ("sharpe", "Sharpe"),
    ("win_rate", "Win Rate"),
    ("profit_factor", "Profit Factor"),
    ("trade_count", "Trade Count"),
    ("expectancy", "Expectancy"),
)


def _load_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in: {path}")
    return payload


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _metric_winner(metric_key: str, left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return "n/a"
    if metric_key in _BETTER_HIGHER:
        if left > right:
            return "left"
        if right > left:
            return "right"
        return "tie"
    if metric_key in _BETTER_LOWER:
        if left < right:
            return "left"
        if right < left:
            return "right"
        return "tie"
    return "n/a"


def _fmt_metric(metric_key: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    if metric_key in {"total_return", "cagr", "max_drawdown", "win_rate"}:
        return f"{value:.2f}%"
    if metric_key == "trade_count":
        return f"{int(value)}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.2f}"


def _build_rows(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for metric_key, label in _METRICS:
        left_value = _as_float(left.get(metric_key))
        right_value = _as_float(right.get(metric_key))
        winner = _metric_winner(metric_key, left_value, right_value)
        rows.append(
            {
                "metric": label,
                "left": _fmt_metric(metric_key, left_value),
                "right": _fmt_metric(metric_key, right_value),
                "winner": winner,
            }
        )
    return rows


def _score_summary(left: dict[str, Any], right: dict[str, Any]) -> tuple[int, int]:
    left_score = 0
    right_score = 0
    for metric_key, _ in _METRICS:
        winner = _metric_winner(
            metric_key,
            _as_float(left.get(metric_key)),
            _as_float(right.get(metric_key)),
        )
        if winner == "left":
            left_score += 1
        elif winner == "right":
            right_score += 1
    return left_score, right_score


def _build_comparison_read(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_label: str,
    right_label: str,
) -> str:
    left_trades = _as_float(left.get("trade_count"))
    right_trades = _as_float(right.get("trade_count"))
    low_confidence = (
        (left_trades is not None and left_trades < 20)
        or (right_trades is not None and right_trades < 20)
        or left.get("research_read") == "low sample confidence"
        or right.get("research_read") == "low sample confidence"
    )
    if low_confidence:
        return "low-confidence comparison because trade counts are too small"

    left_score, right_score = _score_summary(left, right)
    left_return = _as_float(left.get("total_return"))
    right_return = _as_float(right.get("total_return"))
    left_dd = _as_float(left.get("max_drawdown"))
    right_dd = _as_float(right.get("max_drawdown"))

    if right_score >= left_score + 2:
        if (
            right_return is not None
            and left_return is not None
            and right_return > left_return
            and right_dd is not None
            and left_dd is not None
            and right_dd <= left_dd
        ):
            return f"{right_label} looks stronger as a research baseline"
        return f"{right_label} appears modestly stronger, but this is still comparative research only"

    if left_score >= right_score + 2:
        if (
            left_dd is not None
            and right_dd is not None
            and left_dd < right_dd
            and left_return is not None
            and right_return is not None
            and left_return < right_return
        ):
            return f"{left_label} looks more stable but weaker in return"
        return f"{left_label} appears modestly stronger, but this is still comparative research only"

    return "evidence is mixed"


def render_comparison(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_label: str,
    right_label: str,
) -> str:
    rows = _build_rows(left, right)
    metric_width = max(len("Metric"), *(len(row["metric"]) for row in rows))
    left_width = max(len(left_label), *(len(row["left"]) for row in rows))
    right_width = max(len(right_label), *(len(row["right"]) for row in rows))
    winner_width = len("Better")

    lines = []
    lines.append("compare_backtest_summaries")
    lines.append(f"  left  : {left_label}")
    lines.append(f"  right : {right_label}")
    lines.append("")

    header = (
        f"{'Metric':<{metric_width}}  "
        f"{left_label:<{left_width}}  "
        f"{right_label:<{right_width}}  "
        f"{'Better':<{winner_width}}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for row in rows:
        winner = row["winner"]
        if winner == "left":
            better = left_label
        elif winner == "right":
            better = right_label
        elif winner == "tie":
            better = "tie"
        else:
            better = "n/a"
        lines.append(
            f"{row['metric']:<{metric_width}}  "
            f"{row['left']:<{left_width}}  "
            f"{row['right']:<{right_width}}  "
            f"{better:<{winner_width}}"
        )

    lines.append("")
    lines.append("Comparison Read")
    lines.append(f"  {_build_comparison_read(left, right, left_label=left_label, right_label=right_label)}")

    left_missing = left.get("missing_fields") or []
    right_missing = right.get("missing_fields") or []
    if left_missing or right_missing:
        lines.append("")
        lines.append("Missing Fields")
        lines.append(f"  {left_label}: {', '.join(left_missing) if left_missing else 'none'}")
        lines.append(f"  {right_label}: {', '.join(right_missing) if right_missing else 'none'}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two exported backtest summary JSON files"
    )
    parser.add_argument("--left", required=True, help="Left summary JSON file")
    parser.add_argument("--right", required=True, help="Right summary JSON file")
    parser.add_argument("--left-label", default="left", help="Left label")
    parser.add_argument("--right-label", default="right", help="Right label")
    args = parser.parse_args(argv)

    try:
        left = _load_summary(Path(args.left).expanduser().resolve())
        right = _load_summary(Path(args.right).expanduser().resolve())
        print(
            render_comparison(
                left,
                right,
                left_label=args.left_label,
                right_label=args.right_label,
            )
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
