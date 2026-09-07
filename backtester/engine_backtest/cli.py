"""CLI entry point for the full-engine backtester.

Usage examples
--------------
# Run with existing CSV data
python -m backtester.engine_backtest.cli run \\
    --data data/prices.csv \\
    --output results/ \\
    --cash 10000000

# Download data then run
python -m backtester.engine_backtest.cli fetch \\
    --symbols 005930,000660 \\
    --start 2024-01-01 \\
    --end 2026-04-12 \\
    --output data/prices.csv

# Single-parameter sweep
python -m backtester.engine_backtest.cli sweep \\
    --data data/prices.csv \\
    --param sell_stop_loss_pct=-2.0,-3.0,-4.0 \\
    --output results/sweep/

# Multi-parameter grid search (Cartesian product)
python -m backtester.engine_backtest.cli grid \\
    --data data/prices.csv \\
    --param sell_stop_loss_pct=-2.0,-3.0,-4.0 \\
    --param buy_rule_required_pass_count=1,2 \\
    --output results/grid/
"""
from __future__ import annotations

import argparse
import csv
import itertools
import sys
from datetime import date
from pathlib import Path


def _resolve_existing_data_path(raw_path: str) -> Path:
    """Validate that a backtest CSV path exists and looks intentional."""
    path = Path(raw_path).expanduser()
    normalized = path.as_posix()
    if path.exists():
        return path

    hint = (
        "Use a real OHLCV CSV path such as `data/prices.csv`, or create one first with:\n"
        "python -m backtester.engine_backtest.cli fetch --symbols 005930,000660 "
        "--start 2024-01-01 --end 2026-04-14 --output data/prices.csv"
    )
    if normalized.startswith("/path/to/") or "placeholder" in normalized.lower():
        raise SystemExit(f"Placeholder data path detected: {raw_path}\n{hint}")
    raise SystemExit(f"Data CSV not found: {raw_path}\n{hint}")


def _load_settings(config_path: str | None):
    """Load settings from YAML if --config given, else from env."""
    settings, _ai_cfg = _load_settings_and_ai(config_path)
    return settings


def _load_settings_and_ai(config_path: str | None):
    """Load (Settings, AIIntegrationConfig) from YAML if given, else defaults."""
    from backtester.ai_integration import AIIntegrationConfig
    from backtester.engine_backtest.settings_factory import (
        make_settings,
        make_settings_from_yaml,
    )
    if config_path:
        settings, meta = make_settings_from_yaml(config_path)
        ai_cfg: AIIntegrationConfig = meta.get(
            "ai_integration_config", AIIntegrationConfig()
        )
        ai_note = f" ai={ai_cfg.mode}" if ai_cfg.is_active else " ai=off"
        print(
            f"Config: {meta['name']}\n"
            f"  overrides: {meta['applied_overrides'] or '(none)'}{ai_note}"
        )
        return settings, ai_cfg
    return make_settings(), AIIntegrationConfig()


def _cmd_run(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.runner import run_backtest
    from backtester.engine_backtest.report import write_report, print_summary

    data_path = _resolve_existing_data_path(args.data)
    print(f"Loading data from {data_path} ...")
    provider = BacktestDataProvider.from_csv(data_path)

    symbols = args.symbols.split(",") if args.symbols else None
    print(
        f"Symbols: {symbols or provider.symbols()}\n"
        f"Cash: {args.cash:,} KRW"
    )

    settings, ai_cfg = _load_settings_and_ai(args.config or None)
    ai_provider = ai_cfg.build_provider()

    print("Running backtest ...")
    result = run_backtest(
        data_provider=provider,
        settings=settings,
        initial_cash=args.cash,
        symbols=symbols,
        verbose=args.verbose,
        ai_provider=ai_provider,
    )

    run_label = Path(args.config).stem if args.config else Path(args.data).stem
    print_summary(result, run_label=run_label)

    if args.output:
        json_path = write_report(
            result,
            output_dir=args.output,
            run_label=run_label,
        )
        print(f"\nReport saved → {json_path}")


def _cmd_fetch(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.data_fetcher import fetch_symbols_to_csv
    from backtester.engine_backtest.settings_factory import make_settings

    symbols = [s.strip() for s in args.symbols.split(",")]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    settings = make_settings()

    print(f"Fetching {len(symbols)} symbols  {start} → {end} ...")
    fetch_symbols_to_csv(
        symbols=symbols,
        start_date=start,
        end_date=end,
        output_path=args.output,
        settings=settings,
        delay_seconds=args.delay,
        mode=args.mode,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
    )


def _cmd_sweep(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.runner import run_backtest
    from backtester.engine_backtest.report import write_report
    from backtester.engine_backtest.metrics import compute_metrics
    from backtester.engine_backtest.settings_factory import make_settings

    from backtester.ai_integration import AI_DOTTED_PREFIX

    data_path = _resolve_existing_data_path(args.data)
    provider = BacktestDataProvider.from_csv(data_path)
    symbols = args.symbols.split(",") if args.symbols else None
    output_dir = Path(args.output)
    base_settings, base_ai_cfg = _load_settings_and_ai(args.config or None)

    # Parse --param name=v1,v2,v3
    param_name, values_str = args.param.split("=", 1)
    raw_values = [v.strip() for v in values_str.split(",")]

    print(f"Sweeping {param_name} over {raw_values}")
    results = []

    valid_fields = {f.name for f in base_settings.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    base_dict = {f: getattr(base_settings, f) for f in valid_fields}
    is_ai_param = param_name.startswith(AI_DOTTED_PREFIX)

    for raw in raw_values:
        val = _parse_typed(raw)
        if is_ai_param:
            ai_cfg = base_ai_cfg.with_override(param_name, val)
            settings = base_settings
        else:
            overrides = dict(base_dict)
            overrides[param_name] = val
            from app.auth.settings import Settings
            settings = Settings(**overrides)
            ai_cfg = base_ai_cfg
        result = run_backtest(
            data_provider=provider,
            settings=settings,
            initial_cash=args.cash,
            symbols=symbols,
            ai_provider=ai_cfg.build_provider(),
        )
        metrics = compute_metrics(result)
        label = f"{param_name}={val}"
        write_report(result, output_dir=output_dir, run_label=label)
        results.append((label, metrics))
        print(
            f"  {label:40s}  return={metrics['total_return_pct']:+.2f}%  "
            f"sharpe={metrics['sharpe_ratio']:.3f}  mdd={metrics['max_drawdown_pct']:.2f}%  "
            f"trades={metrics['n_trades']}"
        )

    print(f"\nAll sweep results saved → {output_dir}")


def _parse_typed(raw: str):
    """Try int → float → str coercion for a single value string."""
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _cmd_grid(args: argparse.Namespace) -> None:
    """Cartesian-product grid search over multiple parameters.

    Each --param entry is ``name=v1,v2,...``.  All combinations are
    enumerated and run as independent backtests.  Results are written to
    ``--output`` and a summary CSV ``grid_summary.csv`` is appended there.

    Example
    -------
    python -m backtester.engine_backtest.cli grid \\
        --data data/prices.csv \\
        --param sell_stop_loss_pct=-2.0,-3.0,-4.0 \\
        --param buy_rule_required_pass_count=1,2 \\
        --output results/grid/
    """
    from backtester.engine_backtest.data_provider import BacktestDataProvider
    from backtester.engine_backtest.runner import run_backtest
    from backtester.engine_backtest.report import write_report
    from backtester.engine_backtest.metrics import compute_metrics
    from app.auth.settings import Settings

    from backtester.ai_integration import AI_DOTTED_PREFIX

    data_path = _resolve_existing_data_path(args.data)
    provider = BacktestDataProvider.from_csv(data_path)
    symbols = args.symbols.split(",") if args.symbols else None
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_settings, base_ai_cfg = _load_settings_and_ai(args.config or None)
    valid_fields = {f.name for f in base_settings.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    base_dict = {f: getattr(base_settings, f) for f in valid_fields}

    # Parse --param entries → {name: [typed_values, ...]}
    param_axes: list[tuple[str, list]] = []
    for entry in args.param:
        if "=" not in entry:
            raise SystemExit(f"--param must be 'name=v1,v2,...', got: {entry!r}")
        name, values_str = entry.split("=", 1)
        values = [_parse_typed(v.strip()) for v in values_str.split(",")]
        param_axes.append((name, values))

    if not param_axes:
        raise SystemExit("grid requires at least one --param argument")

    # Build Cartesian product
    names = [name for name, _ in param_axes]
    value_lists = [values for _, values in param_axes]
    combos = list(itertools.product(*value_lists))

    total = len(combos)
    print(
        f"Grid search: {' × '.join(f'{n}({len(v)})' for n, v in param_axes)} "
        f"= {total} runs"
    )

    summary_rows: list[dict] = []

    for idx, combo in enumerate(combos, start=1):
        overrides = dict(zip(names, combo))
        label = "  ".join(f"{k}={v}" for k, v in overrides.items())
        run_label = "_".join(f"{k}={v}" for k, v in overrides.items())

        current = dict(base_dict)
        ai_cfg = base_ai_cfg
        for k, v in overrides.items():
            if k.startswith(AI_DOTTED_PREFIX):
                ai_cfg = ai_cfg.with_override(k, v)
            else:
                current[k] = v
        settings = Settings(**current)
        result = run_backtest(
            data_provider=provider,
            settings=settings,
            initial_cash=args.cash,
            symbols=symbols,
            ai_provider=ai_cfg.build_provider(),
        )
        metrics = compute_metrics(result)
        write_report(result, output_dir=output_dir, run_label=run_label)

        row = {**overrides, **metrics}
        summary_rows.append(row)

        print(
            f"  [{idx:>{len(str(total))}}/{total}] {label:60s}"
            f"  return={metrics['total_return_pct']:+.2f}%"
            f"  sharpe={metrics['sharpe_ratio']:.3f}"
            f"  mdd={metrics['max_drawdown_pct']:.2f}%"
            f"  trades={metrics['n_trades']}"
        )

    # Write summary CSV — sorted by total_return_pct descending
    summary_rows.sort(key=lambda r: r.get("total_return_pct", 0.0), reverse=True)
    csv_path = output_dir / "grid_summary.csv"
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)

    print(f"\n{total} runs complete. Summary → {csv_path}")
    if summary_rows:
        best = summary_rows[0]
        best_params = "  ".join(f"{n}={best[n]}" for n in names)
        print(
            f"Best: {best_params}"
            f"  return={best['total_return_pct']:+.2f}%"
            f"  sharpe={best['sharpe_ratio']:.3f}"
        )


def _cmd_parity(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.parity import build_parity_report

    report = build_parity_report(
        account=args.account,
        date=args.date,
        session=args.session,
        output_file=args.output_file or None,
        engine_report=args.engine_report or None,
        initial_cash=args.initial_cash,
    )

    print(f"parity_level: {report['parity_level']}")
    print(f"date        : {report['date_range']['start']}")
    print(f"session     : {report['session']}")
    print(f"engine_mode : {report['engine_backtest_source']['mode']}")

    overlaps = report["overlap_metrics"]
    buy_final = overlaps["buy_selected_vs_live_final_candidate"]["overlap_symbols"]
    buy_exec = overlaps["buy_selected_vs_live_executed_buy"]["overlap_symbols"]
    sell_exec = overlaps["sell_selected_vs_live_successful_sell"]["overlap_symbols"]
    engine = report["summary_counts"]["engine_backtest"]
    diagnostics = engine.get("buy_diagnostics", {})
    capacity = engine.get("buy_capacity", {})
    print(f"buy overlap : final={buy_final} executed={buy_exec}")
    print(f"sell overlap: successful={sell_exec}")
    print(f"buy diag   : {diagnostics.get('stage')} | {diagnostics.get('summary')}")
    print(
        "capacity   : "
        f"positions_before_buy={capacity.get('positions_before_buy_pass')} / "
        f"max_positions={capacity.get('max_positions')} | "
        f"slots={capacity.get('available_slots_before_buy_pass')}"
    )
    print(f"buy rules   : {engine.get('buy_rule_names', [])}")
    print(f"buy funnel  : {engine.get('buy_funnel', {})}")

    if args.output_file:
        print(f"report saved: {args.output_file}")


def _cmd_parity_batch(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.parity_batch import (
        _expand_date_range,
        _parse_dates_arg,
        _parse_paths_arg,
        build_parity_batch_from_sources,
        render_parity_batch_console,
        write_parity_batch_outputs,
    )

    dates = _parse_dates_arg(args.dates)
    if args.date_from and args.date_to:
        dates.extend(_expand_date_range(args.date_from, args.date_to))
    report_files = _parse_paths_arg(args.report_files)
    if not any((dates, report_files, args.report_dir)):
        raise SystemExit("parity-batch requires at least one of --dates, --report-files, or --report-dir")
    if dates and not args.account:
        raise SystemExit("parity-batch requires --account when --dates is provided")

    summary = build_parity_batch_from_sources(
        account=args.account or None,
        dates=dates,
        session=args.session,
        initial_cash=args.initial_cash,
        report_paths=report_files,
        report_dir=args.report_dir or None,
    )
    print(render_parity_batch_console(summary))

    if args.output:
        json_path, csv_path = write_parity_batch_outputs(summary, output_base=args.output)
        print(f"\nsummary saved: {json_path}")
        print(f"csv saved    : {csv_path}")


def _cmd_parity_score_pipeline(args: argparse.Namespace) -> None:
    from backtester.engine_backtest.parity_batch import _expand_date_range, _parse_dates_arg, _parse_paths_arg
    from backtester.engine_backtest.parity_score_pipeline import (
        render_score_pipeline_console,
        run_parity_score_pipeline,
        write_score_pipeline_outputs,
    )

    dates = _parse_dates_arg(args.dates)
    if args.date_from and args.date_to:
        dates.extend(_expand_date_range(args.date_from, args.date_to))
    report_files = _parse_paths_arg(args.report_files)
    if not any((dates, report_files, args.report_dir)):
        raise SystemExit(
            "parity-score-pipeline requires at least one of --dates, --report-files, or --report-dir"
        )
    if dates and not args.account:
        raise SystemExit("parity-score-pipeline requires --account when --dates is provided")

    result = run_parity_score_pipeline(
        account=args.account or None,
        dates=dates,
        session=args.session,
        initial_cash=args.initial_cash,
        report_paths=report_files,
        report_dir=args.report_dir or None,
        review_band=args.review_band,
    )
    print(
        render_score_pipeline_console(
            batch_summary=result["batch_summary"],
            datasets=result["datasets"],
            analysis=result["analysis"],
            scaffold=result["scaffold"],
        )
    )

    if args.output_dir:
        outputs = write_score_pipeline_outputs(
            output_dir=args.output_dir,
            batch_summary=result["batch_summary"],
            datasets=result["datasets"],
            analysis=result["analysis"],
            scaffold=result["scaffold"],
        )
        print("\noutputs:")
        for key, value in outputs.items():
            print(f"- {key}: {value}")


# ── argparse setup ─────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backtester.engine_backtest.cli",
        description="Full-engine backtester CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="Run backtest on CSV data")
    run_p.add_argument("--data", required=True, help="Path to OHLCV CSV")
    run_p.add_argument("--config", default="", help="Path to .kis.yaml strategy file (overrides env defaults)")
    run_p.add_argument("--output", default="", help="Output directory for report")
    run_p.add_argument("--cash", type=int, default=10_000_000, help="Initial cash KRW")
    run_p.add_argument("--symbols", default="", help="Comma-separated symbol subset")
    run_p.add_argument("--verbose", action="store_true")

    # fetch
    fetch_p = sub.add_parser("fetch", help="Download OHLCV data from KIS API")
    fetch_p.add_argument("--symbols", required=True, help="Comma-separated symbols")
    fetch_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    fetch_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    fetch_p.add_argument("--output", required=True, help="Output CSV path")
    fetch_p.add_argument("--delay", type=float, default=0.5, help="Delay between API calls (s)")
    fetch_p.add_argument(
        "--mode",
        choices=["strict", "best-effort"],
        default="strict",
        help="strict: abort on first failing symbol, best-effort: continue after recording failures",
    )
    fetch_p.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries per failing batch request after the initial attempt",
    )
    fetch_p.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=1.0,
        help="Base backoff for retry sleeps (1.0 -> 1s, 2s, 4s ...)",
    )

    # sweep
    sweep_p = sub.add_parser("sweep", help="Single-parameter sensitivity sweep")
    sweep_p.add_argument("--data", required=True)
    sweep_p.add_argument("--config", default="", help="Base .kis.yaml strategy file")
    sweep_p.add_argument("--param", required=True, help="name=v1,v2,v3")
    sweep_p.add_argument("--output", required=True)
    sweep_p.add_argument("--cash", type=int, default=10_000_000)
    sweep_p.add_argument("--symbols", default="")

    # grid
    grid_p = sub.add_parser(
        "grid",
        help="Multi-parameter grid search (Cartesian product of --param entries)",
    )
    grid_p.add_argument("--data", required=True, help="Path to OHLCV CSV")
    grid_p.add_argument("--config", default="", help="Base .kis.yaml strategy file")
    grid_p.add_argument(
        "--param",
        action="append",
        required=True,
        metavar="name=v1,v2,...",
        help="Parameter axis (repeat for multiple params)",
    )
    grid_p.add_argument("--output", required=True, help="Output directory for per-run reports and grid_summary.csv")
    grid_p.add_argument("--cash", type=int, default=10_000_000, help="Initial cash KRW")
    grid_p.add_argument("--symbols", default="", help="Comma-separated symbol subset")

    parity_p = sub.add_parser("parity", help="Compare engine_backtest parity vs historical logs")
    parity_p.add_argument("--account", required=True, help="Account identifier")
    parity_p.add_argument("--date", required=True, help="Date YYYYMMDD or YYYY-MM-DD")
    parity_p.add_argument("--session", default="REGULAR", help="Session filter (default: REGULAR)")
    parity_p.add_argument("--output-file", default="", help="JSON output path")
    parity_p.add_argument("--engine-report", default="", help="Existing engine_backtest report JSON path")
    parity_p.add_argument("--initial-cash", type=int, default=None, help="Optional initial cash override for proxy replay")

    parity_batch_p = sub.add_parser("parity-batch", help="Batch summarize parity reports across dates")
    parity_batch_p.add_argument("--account", default="", help="Account identifier when building reports from dates")
    parity_batch_p.add_argument("--dates", default="", help="Comma-separated dates (YYYYMMDD or YYYY-MM-DD)")
    parity_batch_p.add_argument("--date-from", default="", help="Inclusive scan start date (YYYYMMDD or YYYY-MM-DD)")
    parity_batch_p.add_argument("--date-to", default="", help="Inclusive scan end date (YYYYMMDD or YYYY-MM-DD)")
    parity_batch_p.add_argument("--report-files", default="", help="Comma-separated parity JSON files to read")
    parity_batch_p.add_argument("--report-dir", default="", help="Directory containing parity_*.json files")
    parity_batch_p.add_argument("--session", default="REGULAR", help="Session filter when building from dates")
    parity_batch_p.add_argument("--initial-cash", type=int, default=None, help="Optional initial cash override for proxy replay")
    parity_batch_p.add_argument("--output", default="", help="Output base path for JSON/CSV summary")

    pipeline_p = sub.add_parser("parity-score-pipeline", help="Run score tuning preparation pipeline from parity data")
    pipeline_p.add_argument("--account", default="", help="Account identifier when building reports from dates")
    pipeline_p.add_argument("--dates", default="", help="Comma-separated dates (YYYYMMDD or YYYY-MM-DD)")
    pipeline_p.add_argument("--date-from", default="", help="Inclusive scan start date (YYYYMMDD or YYYY-MM-DD)")
    pipeline_p.add_argument("--date-to", default="", help="Inclusive scan end date (YYYYMMDD or YYYY-MM-DD)")
    pipeline_p.add_argument("--report-files", default="", help="Comma-separated parity JSON files to read")
    pipeline_p.add_argument("--report-dir", default="", help="Directory containing parity_*.json files")
    pipeline_p.add_argument("--session", default="REGULAR", help="Session filter when building from dates")
    pipeline_p.add_argument("--initial-cash", type=int, default=None, help="Optional initial cash override for proxy replay")
    pipeline_p.add_argument("--review-band", type=float, default=None, help="Optional manual review band override")
    pipeline_p.add_argument("--output-dir", default="", help="Directory for pipeline outputs")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        _cmd_run(args)
    elif args.command == "fetch":
        _cmd_fetch(args)
    elif args.command == "sweep":
        _cmd_sweep(args)
    elif args.command == "grid":
        _cmd_grid(args)
    elif args.command == "parity":
        _cmd_parity(args)
    elif args.command == "parity-batch":
        _cmd_parity_batch(args)
    elif args.command == "parity-score-pipeline":
        _cmd_parity_score_pipeline(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
