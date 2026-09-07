"""CLI: export_backtest_result_summary

Export a compact AI-friendly summary from sibling KIS backtester result files.

This is research/export tooling only. It does not change live trading logic.

Examples:
    export OPEN_TRADING_API_ROOT=/path/to/open-trading-api

    python3 -m app.tools.export_backtest_result_summary \
        --input "$OPEN_TRADING_API_ROOT/backtester/.lean-workspace/projects/bt_custom_kis_trader_core_family_approx/backtests/Algorithm.json" \
        --config-file "$OPEN_TRADING_API_ROOT/backtester/.lean-workspace/projects/bt_custom_kis_trader_core_family_approx/config.json" \
        --format both \
        --output backtester/reports/run_bt_custom_kis_trader_core_family_approx_summary

    python3 -m app.tools.export_backtest_result_summary \
        --run-id bt_custom_kis_trader_core_family_approx \
        --format md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.integrations.open_trading_api import (
    OPEN_TRADING_API_ROOT_ENV,
    resolve_backtester_root,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_MISSING = object()


def _default_backtester_root_text() -> str:
    resolved = resolve_backtester_root()
    if resolved.available and resolved.root is not None:
        return str(resolved.root)
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in: {path}")
    return payload


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    cleaned = text.replace("%", "").replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned.lower() in {"nan", "none", "null", "n/a", "-"}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is _MISSING:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _nested_get(payload: Any, *path: str) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return _MISSING
        if key not in current:
            return _MISSING
        current = current[key]
    return current


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _format_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def _format_contextual_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1000:
        return _format_money(value)
    return f"{value:.2f}"


def _extract_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        return text.split("T", 1)[0]
    if len(text) >= 10:
        return text[:10]
    return text


def _extract_symbols(config: dict[str, Any]) -> list[str]:
    raw_symbols = _nested_get(config, "parameters", "symbols")
    if raw_symbols is _MISSING:
        return []
    if isinstance(raw_symbols, list):
        return [str(item).strip().upper() for item in raw_symbols if str(item).strip()]
    text = str(raw_symbols).strip()
    if not text:
        return []
    return [item.strip().upper() for item in text.split(",") if item.strip()]


def _extract_run_id(result_path: Path, config: dict[str, Any]) -> str | None:
    inferred = None
    if result_path.name.lower() == "algorithm.json":
        parent = result_path.parent.parent
        if parent.name and parent.name != ".":
            inferred = parent.name
    return _first_value(
        _nested_get(config, "run_id"),
        _nested_get(config, "strategy_run_id"),
        inferred,
    )


def _extract_strategy_name(config: dict[str, Any], result: dict[str, Any]) -> str | None:
    algorithm_name = _nested_get(result, "algorithmConfiguration", "name")
    if isinstance(algorithm_name, str) and algorithm_name.strip().lower() == "local":
        algorithm_name = None
    return _first_value(
        _nested_get(config, "display_name"),
        _nested_get(config, "strategy_name"),
        _nested_get(config, "strategy_id"),
        algorithm_name,
    )


def _extract_date_range(config: dict[str, Any], result: dict[str, Any]) -> tuple[str | None, str | None]:
    start_date = _first_value(
        _nested_get(config, "parameters", "start_date"),
        _extract_date(_nested_get(result, "algorithmConfiguration", "startDate")),
        _extract_date(_nested_get(result, "totalPerformance", "tradeStatistics", "startDateTime")),
    )
    end_date = _first_value(
        _nested_get(config, "parameters", "end_date"),
        _extract_date(_nested_get(result, "algorithmConfiguration", "endDate")),
        _extract_date(_nested_get(result, "totalPerformance", "tradeStatistics", "endDateTime")),
    )
    return start_date, end_date


def _extract_starting_cash(config: dict[str, Any], stats: dict[str, Any]) -> float | None:
    return _parse_number(
        _first_value(
            _nested_get(config, "parameters", "initial_capital"),
            stats.get("Start Equity"),
        )
    )


def _extract_trade_count(stats: dict[str, Any], total_performance: dict[str, Any]) -> int | None:
    raw = _first_value(
        _nested_get(total_performance, "tradeStatistics", "totalNumberOfTrades"),
        _nested_get(total_performance, "tradeStatistics", "totalTrades"),
        stats.get("Total Orders"),
    )
    number = _parse_number(raw)
    if number is None:
        return None
    return int(number)


def _extract_trade_metric(stats: dict[str, Any], total_performance: dict[str, Any], *, stat_key: str, perf_key: str) -> float | None:
    return _parse_number(
        _first_value(
            stats.get(stat_key),
            _nested_get(total_performance, "tradeStatistics", perf_key),
        )
    )


def _extract_trade_amount_metric(
    stats: dict[str, Any],
    total_performance: dict[str, Any],
    *,
    stat_key: str,
    perf_key: str,
) -> float | None:
    return _parse_number(
        _first_value(
            _nested_get(total_performance, "tradeStatistics", perf_key),
            stats.get(stat_key),
        )
    )


def _normalize_rate_percent(value: float | None) -> float | None:
    if value is None:
        return None
    if 0 <= value <= 1:
        return value * 100.0
    return value


def _extract_benchmark_info(result: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any] | None:
    series = _nested_get(result, "charts", "Benchmark", "series", "Benchmark", "values")
    benchmark_end_value: float | None = None
    if isinstance(series, list) and series:
        last_point = series[-1]
        if isinstance(last_point, list) and len(last_point) >= 2:
            benchmark_end_value = _parse_number(last_point[-1])

    info = {
        "information_ratio": _parse_number(stats.get("Information Ratio")),
        "tracking_error": _parse_number(stats.get("Tracking Error")),
        "treynor_ratio": _parse_number(stats.get("Treynor Ratio")),
        "benchmark_end_value": benchmark_end_value,
    }
    if all(value is None for value in info.values()):
        return None
    return info


def _build_research_read(summary: dict[str, Any]) -> str:
    total_return = summary.get("total_return")
    max_drawdown = summary.get("max_drawdown")
    sharpe = summary.get("sharpe")
    trade_count = summary.get("trade_count")

    if isinstance(trade_count, int) and trade_count < 20:
        return "low sample confidence"
    if isinstance(total_return, (int, float)) and total_return <= 0:
        return "weak baseline"
    if (
        isinstance(total_return, (int, float))
        and isinstance(max_drawdown, (int, float))
        and total_return > 0
        and max_drawdown > max(total_return * 1.2, 15.0)
    ):
        return "high drawdown relative to return"
    if (
        isinstance(total_return, (int, float))
        and isinstance(sharpe, (int, float))
        and total_return > 0
        and sharpe >= 1.0
    ):
        return "favorable baseline"
    return "mixed baseline"


def build_summary(
    *,
    result_path: Path,
    config_path: Path | None = None,
) -> dict[str, Any]:
    result = _load_json(result_path)
    config = _load_json(config_path) if config_path else {}
    stats = result.get("statistics", {})
    if not isinstance(stats, dict):
        stats = {}
    total_performance = result.get("totalPerformance", {})
    if not isinstance(total_performance, dict):
        total_performance = {}

    run_id = _extract_run_id(result_path, config)
    strategy_name = _extract_strategy_name(config, result)
    symbols = _extract_symbols(config)
    start_date, end_date = _extract_date_range(config, result)
    starting_cash = _extract_starting_cash(config, stats)
    final_equity = _parse_number(stats.get("End Equity"))

    summary: dict[str, Any] = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "symbols": symbols,
        "start_date": start_date,
        "end_date": end_date,
        "starting_cash": starting_cash,
        "final_equity": final_equity,
        "total_return": _parse_number(stats.get("Net Profit")),
        "cagr": _parse_number(stats.get("Compounding Annual Return")),
        "max_drawdown": _parse_number(stats.get("Drawdown")),
        "sharpe": _parse_number(stats.get("Sharpe Ratio")),
        "win_rate": _normalize_rate_percent(
            _extract_trade_metric(
                stats,
                total_performance,
                stat_key="Win Rate",
                perf_key="winRate",
            )
        ),
        "profit_factor": _extract_trade_metric(
            stats,
            total_performance,
            stat_key="Profit-Loss Ratio",
            perf_key="profitLossRatio",
        ),
        "trade_count": _extract_trade_count(stats, total_performance),
        "average_win": _extract_trade_amount_metric(
            stats,
            total_performance,
            stat_key="Average Win",
            perf_key="averageProfit",
        ),
        "average_loss": _extract_trade_amount_metric(
            stats,
            total_performance,
            stat_key="Average Loss",
            perf_key="averageLoss",
        ),
        "expectancy": _extract_trade_amount_metric(
            stats,
            total_performance,
            stat_key="Expectancy",
            perf_key="averageProfitLoss",
        ),
        "benchmark_comparison": _extract_benchmark_info(result, stats),
        "notes": [],
        "missing_fields": [],
        "sources": {
            "algorithm_json": str(result_path),
            "config_json": str(config_path) if config_path else None,
        },
    }

    missing_fields: list[str] = []
    for key in (
        "run_id",
        "strategy_name",
        "start_date",
        "end_date",
        "starting_cash",
        "total_return",
        "cagr",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "profit_factor",
        "trade_count",
        "average_win",
        "average_loss",
        "expectancy",
    ):
        value = summary.get(key)
        if value is None or value == []:
            missing_fields.append(key)
    summary["missing_fields"] = missing_fields

    notes: list[str] = []
    if not config_path:
        notes.append("config.json not provided; context fields were inferred from Algorithm.json path/content when possible")
    if not symbols:
        notes.append("symbols were not available from config.json")
    if summary["benchmark_comparison"] is None:
        notes.append("benchmark comparison fields were not available")
    if missing_fields:
        notes.append("some headline fields were unavailable in the source files")
    summary["notes"] = notes
    summary["research_read"] = _build_research_read(summary)
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Backtest Summary", ""]

    lines.append("## Backtest Context")
    lines.append(f"- Run ID: {summary.get('run_id') or 'N/A'}")
    lines.append(f"- Strategy: {summary.get('strategy_name') or 'N/A'}")
    symbols = summary.get("symbols") or []
    lines.append(f"- Symbols: {', '.join(symbols) if symbols else 'N/A'}")
    lines.append(
        f"- Date Range: {(summary.get('start_date') or 'N/A')} to {(summary.get('end_date') or 'N/A')}"
    )
    lines.append(f"- Starting Cash: {_format_money(summary.get('starting_cash'))}")
    lines.append(f"- Final Equity: {_format_money(summary.get('final_equity'))}")
    lines.append("")

    lines.append("## Headline Metrics")
    lines.append(f"- Total Return: {_format_pct(summary.get('total_return'))}")
    lines.append(f"- CAGR: {_format_pct(summary.get('cagr'))}")
    lines.append(f"- Max Drawdown: {_format_pct(summary.get('max_drawdown'))}")
    lines.append(f"- Sharpe: {_format_ratio(summary.get('sharpe'))}")
    lines.append("")

    lines.append("## Risk / Quality Read")
    lines.append(f"- Research Read: {summary.get('research_read') or 'N/A'}")
    benchmark = summary.get("benchmark_comparison") or {}
    if benchmark:
        lines.append(
            "- Benchmark Comparison: "
            f"information ratio {_format_ratio(benchmark.get('information_ratio'))}, "
            f"tracking error {_format_ratio(benchmark.get('tracking_error'))}, "
            f"treynor {_format_ratio(benchmark.get('treynor_ratio'))}"
        )
    else:
        lines.append("- Benchmark Comparison: N/A")
    lines.append("")

    lines.append("## Trade Summary")
    lines.append(f"- Trade Count: {summary.get('trade_count') if summary.get('trade_count') is not None else 'N/A'}")
    lines.append(f"- Win Rate: {_format_pct(summary.get('win_rate'))}")
    lines.append(f"- Profit Factor: {_format_ratio(summary.get('profit_factor'))}")
    lines.append(f"- Average Win: {_format_contextual_number(summary.get('average_win'))}")
    lines.append(f"- Average Loss: {_format_contextual_number(summary.get('average_loss'))}")
    lines.append(f"- Expectancy: {_format_contextual_number(summary.get('expectancy'))}")
    lines.append("")

    lines.append("## Simple Interpretation Notes")
    if summary.get("notes"):
        for note in summary["notes"]:
            lines.append(f"- {note}")
    else:
        lines.append("- No extra notes.")
    lines.append("")

    lines.append("## Missing / Not Available")
    missing_fields = summary.get("missing_fields") or []
    if missing_fields:
        for item in missing_fields:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _infer_config_path(result_path: Path) -> Path | None:
    if result_path.name.lower() == "algorithm.json" and result_path.parent.name == "backtests":
        candidate = result_path.parent.parent / "config.json"
        if candidate.exists():
            return candidate
    return None


def _resolve_paths(
    *,
    input_path: str,
    config_file: str,
    run_id: str,
    backtester_root: Path | None,
) -> tuple[Path, Path | None]:
    if run_id:
        if backtester_root is None:
            raise ValueError(
                f"--run-id requires --backtester-root or {OPEN_TRADING_API_ROOT_ENV}"
            )
        result_path = (
            backtester_root
            / ".lean-workspace"
            / "projects"
            / run_id
            / "backtests"
            / "Algorithm.json"
        )
        config_path = backtester_root / ".lean-workspace" / "projects" / run_id / "config.json"
        return result_path, config_path if config_path.exists() else None

    if not input_path:
        raise ValueError("Provide either --input or --run-id.")

    result_path = Path(input_path).expanduser().resolve()
    if config_file:
        return result_path, Path(config_file).expanduser().resolve()
    return result_path, _infer_config_path(result_path)


def _resolve_output_paths(base_output: str, fmt: str) -> tuple[Path, Path | None]:
    base_path = Path(base_output).expanduser()

    def build_path(suffix: str) -> Path:
        if base_path.suffix.lower() == suffix:
            return base_path
        if base_path.suffix:
            return base_path.with_suffix(suffix)
        return Path(str(base_path) + suffix)

    if fmt == "json":
        return build_path(".json"), None
    if fmt == "md":
        return build_path(".md"), None
    return build_path(".json"), build_path(".md")


def _default_output_base(summary: dict[str, Any]) -> Path:
    run_id = summary.get("run_id") or "unknown_run"
    return _PROJECT_ROOT / "logs" / "backtester" / f"run_{run_id}_summary"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export compact JSON/Markdown summaries from KIS backtester result files"
    )
    parser.add_argument("--input", default="", help="Path to Lean Algorithm.json")
    parser.add_argument("--config-file", default="", help="Optional sibling config.json path")
    parser.add_argument("--run-id", default="", help="Backtester run id under .lean-workspace/projects/")
    parser.add_argument(
        "--backtester-root",
        default=_default_backtester_root_text(),
        help=(
            "Path to open-trading-api/backtester root for --run-id resolution. "
            f"Defaults to {OPEN_TRADING_API_ROOT_ENV}/backtester when configured."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "md", "both"),
        default="both",
        help="Output format",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output path or base path. For --format both, .json and .md will be written.",
    )
    args = parser.parse_args(argv)

    try:
        result_path, config_path = _resolve_paths(
            input_path=args.input,
            config_file=args.config_file,
            run_id=args.run_id,
            backtester_root=(
                Path(args.backtester_root).expanduser().resolve()
                if args.backtester_root
                else None
            ),
        )
        summary = build_summary(result_path=result_path, config_path=config_path)
        markdown = render_markdown(summary)
        json_text = json.dumps(summary, indent=2, ensure_ascii=False)

        if args.output:
            json_path, md_path = _resolve_output_paths(args.output, args.format)
        else:
            default_base = _default_output_base(summary)
            json_path, md_path = _resolve_output_paths(str(default_base), args.format)

        if args.output:
            if args.format in {"json", "both"} and json_path is not None:
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(json_text + "\n", encoding="utf-8")
            if args.format in {"md", "both"}:
                target_md = md_path or json_path
                target_md.parent.mkdir(parents=True, exist_ok=True)
                target_md.write_text(markdown, encoding="utf-8")

            print("export_backtest_result_summary")
            if args.format in {"json", "both"} and json_path is not None:
                print(f"  json : {json_path}")
            if args.format in {"md", "both"}:
                print(f"  md   : {md_path or json_path}")
            return 0

        if args.format == "json":
            print(json_text)
            return 0
        if args.format == "md":
            print(markdown)
            return 0

        print(json_text)
        print()
        print("---")
        print()
        print(markdown)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
