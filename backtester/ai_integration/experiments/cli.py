"""CLI for the AI-integration experiment suite.

Usage
-----
# Quick sanity sweep: baseline + three synthetic providers
python -m backtester.ai_integration.experiments.cli run \\
    --data data/prices.csv \\
    --presets baseline,random,ruleproxy,perfect

# Full sweep including the cache-based real provider (requires an active
# ``ai_integration:`` block in the YAML, or ``--ai-cache-dir``).
python -m backtester.ai_integration.experiments.cli run \\
    --data data/prices.csv \\
    --config backtester/strategies/kis_trader_core_family_approx.kis.yaml \\
    --presets baseline,random,ruleproxy,perfect,cache \\
    --ai-cache-dir data/ai_signals \\
    --ai-mode combined

The tool writes everything under ``--output`` (default
``results/ai_experiments/``) in a timestamped sub-directory; see the
``experiments`` package docstring for the tree layout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backtester.ai_integration import AIIntegrationConfig
from backtester.ai_integration.experiments import run_experiment_suite


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.experiments.cli",
        description="Run baseline + synthetic/cache AI backtests and summarize.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the experiment suite")
    run_p.add_argument("--data", required=True, help="Path to OHLCV CSV")
    run_p.add_argument(
        "--config", default="",
        help="Optional .kis.yaml strategy file (for Settings + ai_integration block)",
    )
    run_p.add_argument(
        "--presets",
        default="baseline,random,ruleproxy,perfect",
        help="Comma-separated subset of: baseline,random,ruleproxy,perfect,cache",
    )
    run_p.add_argument(
        "--output", default="results/ai_experiments",
        help="Output root directory",
    )
    run_p.add_argument("--symbols", default="", help="Comma-separated symbol subset")
    run_p.add_argument("--cash", type=int, default=10_000_000)
    run_p.add_argument("--seed", type=int, default=1337, help="RandomAIProvider seed")
    run_p.add_argument(
        "--forward-horizon", type=int, default=5,
        help="T+K trading days for forward returns (perfect preset + evaluator)",
    )
    run_p.add_argument("--name", default="", help="Explicit experiment name (optional)")

    # cache-preset overrides — take precedence over YAML ai_integration block.
    run_p.add_argument("--ai-cache-dir", default="", help="Override ai_integration.cache_dir")
    run_p.add_argument(
        "--ai-mode", default="",
        choices=["", "disabled", "score_delta", "veto", "rerank", "regime", "combined"],
        help="Override ai_integration.mode",
    )
    run_p.add_argument("--ai-veto-threshold", type=float, default=None)
    run_p.add_argument("--ai-delta-scale", type=float, default=None)
    run_p.add_argument(
        "--ai-fallback", default="",
        choices=["", "passthrough", "reject", "warn"],
    )

    return parser.parse_args(argv)


def _build_cache_override(args: argparse.Namespace) -> AIIntegrationConfig | None:
    """Assemble an AIIntegrationConfig from CLI flags, if any were given."""
    if not any(
        [
            args.ai_cache_dir,
            args.ai_mode,
            args.ai_veto_threshold is not None,
            args.ai_delta_scale is not None,
            args.ai_fallback,
        ]
    ):
        return None
    kwargs: dict = {"enabled": True}
    if args.ai_mode:
        kwargs["mode"] = args.ai_mode
    else:
        kwargs["mode"] = "combined"
    if args.ai_cache_dir:
        kwargs["cache_dir"] = args.ai_cache_dir
    if args.ai_veto_threshold is not None:
        kwargs["veto_threshold"] = float(args.ai_veto_threshold)
    if args.ai_delta_scale is not None:
        kwargs["delta_scale"] = float(args.ai_delta_scale)
    if args.ai_fallback:
        kwargs["fallback"] = args.ai_fallback
    return AIIntegrationConfig(**kwargs)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.command != "run":
        sys.exit(1)

    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    cache_cfg = _build_cache_override(args)

    manifest = run_experiment_suite(
        data_path=args.data,
        presets=presets,
        output_root=args.output,
        config_path=args.config or None,
        symbols=symbols,
        initial_cash=args.cash,
        random_seed=args.seed,
        forward_horizon=args.forward_horizon,
        cache_ai_config=cache_cfg,
        experiment_name=args.name or None,
    )

    root = Path(args.output) / manifest["experiment_name"]
    print(f"Experiment: {manifest['experiment_name']}")
    print(f"Output root: {root}")
    print(f"Runs: {[r['preset'] for r in manifest['runs']]}")
    print(f"Manifest: {root / 'experiment_manifest.json'}")
    print(f"Comparison: {root / 'comparison_summary.txt'}")
    print()
    print((root / "comparison_summary.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    main()
