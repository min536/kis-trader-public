"""S5a-1 — native minute-replay backtest CLI (open-trading-api replacement).

docs/slack_backtest_native_pipeline_design_20260707.md §4.1. Assembles a
ParquetMinuteProvider + ScoreV2Artifact from the gate2 manifest, applies the
freshness gate (clamp the requested window to what the parquet store covers,
report the adjustment rather than failing open), runs the native replay via
``app.research.replay.backtest_api.run_backtest`` (the SAME production scan/sell
code the live engine runs — no Lean, no :8002 REST), and emits a one-line
``RESULT:`` summary plus a JSON summary file.

No HTTP server, no broker calls. Runs under launchd's minimal PATH because it is
a plain ``.venv/bin/python -m`` entry point.
"""

from __future__ import annotations

import argparse
import json
import signal
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from app.auth.settings import PROJECT_ROOT, get_settings
from app.research.replay.backtest_api import run_backtest
from app.research.replay.provider_assembly import (
    available_dates,
    available_window,
    build_parquet_provider,
    clamp_window,
    resolve_artifact,
    resolve_parquet_root,
)

# Present so tests can assert the wiring point; the real fn is imported above.
_default_backtest_fn = run_backtest

_DEFAULT_MAX_DAYS = 60


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(str(value).strip(), "%Y%m%d").date()


def _load_symbols(symbols: tuple[str, ...] | None, symbols_file: str | None) -> tuple[str, ...] | None:
    if symbols:
        return tuple(symbols)
    if symbols_file:
        text = Path(symbols_file).read_text(encoding="utf-8")
        parsed = tuple(
            line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")
        )
        return parsed or None
    return None


def run_native_backtest(
    *,
    manifest_path: str | Path,
    base_dir: Path | None = None,
    symbols: tuple[str, ...] | None = None,
    start: date | None = None,
    end: date | None = None,
    initial_capital: float = 100_000_000.0,
    max_days: int = _DEFAULT_MAX_DAYS,
    plan_only: bool = False,
    progress: bool = False,
    should_stop: Any = None,
    _backtest_fn: Any = None,
    _provider_factory: Any = None,
    _settings: Any = None,
) -> dict[str, Any]:
    """Assemble, freshness-gate, run, and normalize a native backtest to a dict.

    Injected ``_backtest_fn`` / ``_provider_factory`` / ``_settings`` keep the
    orchestration testable without a real parquet store.

    ``progress`` (S2) wires a per-day ``on_day_end`` printer into the backtest
    call (``progress: day i/n <date> equity=<eq> elapsed=<s>s``, flushed since
    stdout may be redirected to a log file); ``should_stop`` (S2) is forwarded
    so a caller (e.g. a SIGTERM handler) can request a tick-boundary stop. Both
    are passed to ``backtest_fn`` only when applicable, so the default call
    shape (neither kwarg) is unchanged.
    """
    backtest_fn = _backtest_fn or run_backtest
    provider_factory = _provider_factory or build_parquet_provider

    root = resolve_parquet_root(manifest_path, base_dir=base_dir)
    window = available_window(root)
    if window is None:
        return {
            "ok": False,
            "error": "no parquet partitions in store",
            "parquet_root": str(root),
        }

    req_start = start if start is not None else window[0]
    req_end = end if end is not None else window[1]
    (clamped_start, clamped_end), adjusted = clamp_window(
        start=req_start, end=req_end, available=window
    )
    dates = available_dates(root, start=clamped_start, end=clamped_end)
    if not dates:
        return {
            "ok": False,
            "error": "no parquet partitions in requested window",
            "parquet_root": str(root),
        }

    truncated = False
    if len(dates) > max_days:
        dates = dates[-max_days:]
        truncated = True

    if plan_only:
        return {
            "ok": True,
            "plan_only": True,
            "planned_day_count": len(dates),
            "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat()},
            "window_adjusted": adjusted,
            "truncated": truncated,
            "parquet_root": str(root),
            "symbol_count": len(symbols) if symbols else None,
        }

    artifact = resolve_artifact(manifest_path, base_dir=base_dir)
    settings = _settings if _settings is not None else get_settings()
    provider = provider_factory(
        parquet_root=root,
        symbols=symbols,
        start_date=dates[0],
        end_date=dates[-1],
    )
    optional_kwargs: dict[str, Any] = {}
    if progress:
        t0 = monotonic()
        total_days = len(dates)

        def _print_progress(day_index: int, day: date, equity: float) -> None:
            elapsed = int(monotonic() - t0)
            print(
                f"progress: day {day_index + 1}/{total_days} {day.isoformat()} "
                f"equity={equity:,.0f} elapsed={elapsed}s",
                flush=True,
            )

        optional_kwargs["on_day_end"] = _print_progress
    if should_stop is not None:
        optional_kwargs["should_stop"] = should_stop

    result = backtest_fn(
        artifact=artifact,
        provider=provider,
        dates=dates,
        settings=settings,
        initial_capital=initial_capital,
        **optional_kwargs,
    )

    return {
        "ok": True,
        "total_return_pct": getattr(result, "total_return_pct", None),
        "mdd_pct": getattr(result, "mdd_pct", None),
        "trade_count": getattr(result, "trade_count", None),
        "win_rate": getattr(result, "win_rate", None),
        "day_count": getattr(result, "day_count", len(dates)),
        "final_equity": getattr(result, "final_equity", None),
        "initial_capital": initial_capital,
        "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat()},
        "window_adjusted": adjusted,
        "truncated": truncated,
        "symbol_count": len(symbols) if symbols else None,
        "artifact_version": getattr(artifact, "version", None),
        "partial": getattr(result, "partial", False),
        "completed_days": getattr(result, "completed_days", None),
        "requested_days": len(dates),
    }


def format_result_line(summary: dict[str, Any]) -> str:
    if not summary.get("ok", False):
        return f"RESULT: error={summary.get('error', 'unknown')}"
    if summary.get("plan_only"):
        window = summary.get("window") or {}
        return (
            f"RESULT: plan_only days={summary.get('planned_day_count')} "
            f"window={window.get('start')}..{window.get('end')} "
            f"adjusted={summary.get('window_adjusted')} "
            f"truncated={summary.get('truncated')}"
        )
    win = summary.get("win_rate")
    win_pct = f"{float(win) * 100:.1f}%" if isinstance(win, (int, float)) else "-"
    suffix = ""
    if summary.get("window_adjusted") or summary.get("truncated") or summary.get("partial"):
        flags = []
        if summary.get("window_adjusted"):
            flags.append("window_clamped")
        if summary.get("truncated"):
            flags.append(f"truncated_to_{summary.get('day_count')}d")
        if summary.get("partial"):
            flags.append(
                f"partial_{summary.get('completed_days')}/{summary.get('requested_days')}d"
            )
        suffix = f" [{','.join(flags)}]"
    return (
        f"RESULT: return={summary.get('total_return_pct')}% "
        f"mdd={summary.get('mdd_pct')}% "
        f"trades={summary.get('trade_count')} "
        f"win={win_pct} "
        f"days={summary.get('day_count')}{suffix}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Native minute-replay backtest.")
    parser.add_argument("--manifest", default="_workspace/gate2/paths.json")
    parser.add_argument("--base-dir", default=None)
    parser.add_argument("--symbols", default="", help="comma-separated codes")
    parser.add_argument("--symbols-file", default="")
    parser.add_argument("--start", default="", help="YYYYMMDD window start")
    parser.add_argument("--end", default="", help="YYYYMMDD window end")
    parser.add_argument("--initial-capital", type=float, default=100_000_000.0)
    parser.add_argument("--max-days", type=int, default=_DEFAULT_MAX_DAYS)
    parser.add_argument("--plan-only", action="store_true", help="probe window; no replay")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    symbols_arg = (
        tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        if args.symbols
        else None
    )
    symbols = _load_symbols(symbols_arg, args.symbols_file or None)

    # S2: a SIGTERM (e.g. launchd/operator stop) requests a tick-boundary
    # stop rather than a hard kill — run_native_backtest polls this flag via
    # should_stop and returns a partial (but ok=True) result.
    stop_requested = False

    def _handle_sigterm(signum, frame) -> None:  # noqa: ARG001
        nonlocal stop_requested
        stop_requested = True
        print("signal: SIGTERM — stopping at next tick boundary", flush=True)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # gate2 paths.json values are repo-root-relative; resolve against the project
    # root by default so the CLI works from any cwd (launchd, subprocess).
    summary = run_native_backtest(
        manifest_path=args.manifest,
        base_dir=Path(args.base_dir) if args.base_dir else PROJECT_ROOT,
        symbols=symbols,
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        initial_capital=args.initial_capital,
        max_days=args.max_days,
        plan_only=args.plan_only,
        progress=True,
        should_stop=lambda: stop_requested,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"summary: {out_path}")

    print(format_result_line(summary))
    return 0 if summary.get("ok", False) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
