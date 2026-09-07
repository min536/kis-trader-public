"""Experiment orchestrator — thin layer over ``run_backtest``.

Responsibilities:

1. Materialize a provider for each requested preset (``baseline``,
   ``random``, ``ruleproxy``, ``perfect``, ``cache``) using only
   already-implemented modules.
2. Run each backtest and write ``report.json`` + ``summary.txt`` to a
   per-preset directory.
3. Evaluate every non-baseline run against the baseline with
   :class:`AIEvaluator` and persist ``ai_evaluation.json``.
4. Write a top-level ``experiment_manifest.json`` and
   ``comparison_summary.txt`` ranking runs by Sharpe delta, return
   delta, and signal coverage.

Design notes:

* Presets are plain builder callables — no registry DSL, no plugin
  system. Add a new preset by adding a function and putting it in
  :data:`PRESETS`.
* The orchestrator does **not** import any engine internals beyond
  what the CLI already uses (``run_backtest``, ``compute_metrics``).
* Running ``["baseline"]`` alone is a valid smoke test and will
  produce no evaluator output.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from backtester.ai_integration import (
    AIEvaluator,
    AIIntegrationConfig,
    AISignalProvider,
    PerfectAIProvider,
    RandomAIProvider,
    RuleProxyAIProvider,
)
from backtester.ai_integration.experiments.forward_returns import (
    build_forward_returns,
)
from backtester.engine_backtest.data_provider import BacktestDataProvider
from backtester.engine_backtest.metrics import compute_metrics
from backtester.engine_backtest.runner import BacktestResult, run_backtest
from backtester.engine_backtest.settings_factory import (
    make_settings,
    make_settings_from_yaml,
)


__all__ = [
    "PRESETS",
    "ExperimentRun",
    "build_rule_proxy_features",
    "run_experiment_suite",
]


# ── helpers ───────────────────────────────────────────────────────────────


def _rsi_14(closes: list[int]) -> list[float]:
    """Classic Wilder RSI(14) over a list of closes.

    Emits 50.0 for positions before enough data exists (neutral
    placeholder; downstream rule-proxy uses strict cutoffs).
    """
    n = len(closes)
    out = [50.0] * n
    if n < 15:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, 15):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses += -ch
    avg_gain = gains / 14.0
    avg_loss = losses / 14.0
    out[14] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(15, n):
        ch = closes[i] - closes[i - 1]
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * 13 + gain) / 14.0
        avg_loss = (avg_loss * 13 + loss) / 14.0
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def build_rule_proxy_features(
    data_provider: BacktestDataProvider,
    *,
    momentum_window: int = 10,
    symbols: Sequence[str] | None = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Build the ``features_cache`` consumed by :class:`RuleProxyAIProvider`.

    Output shape: ``{date_iso: {ticker: {"rsi": float, "momentum": float}}}``.
    RSI is Wilder-14; momentum is ``(close_today / close_{t-N}) - 1`` in
    percent over ``momentum_window`` trading days.
    """
    universe = list(symbols) if symbols is not None else data_provider.symbols()
    cache: dict[str, dict[str, dict[str, float]]] = {}
    for symbol in universe:
        dates = data_provider.trading_dates(symbol)
        closes = [
            data_provider.get_row(symbol, d).close_price  # type: ignore[union-attr]
            for d in dates
            if data_provider.get_row(symbol, d) is not None
        ]
        if len(closes) != len(dates):
            continue
        rsi_series = _rsi_14(closes)
        for i, d in enumerate(dates):
            if i < momentum_window:
                momentum = 0.0
            else:
                base = closes[i - momentum_window]
                momentum = 0.0 if base <= 0 else (closes[i] - base) / base * 100.0
            cache.setdefault(d.isoformat(), {})[symbol] = {
                "rsi": round(float(rsi_series[i]), 4),
                "momentum": round(float(momentum), 4),
            }
    return cache


# ── run record ────────────────────────────────────────────────────────────


@dataclass
class ExperimentRun:
    """Bookkeeping record for a single preset run."""

    preset: str
    provider_type: str | None   # class name, or None for baseline
    is_synthetic: bool
    mode: str | None            # provider.mode, or None for baseline
    output_subdir: str
    ai_config: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] | None = None

    def to_manifest_entry(self) -> dict[str, Any]:
        d = asdict(self)
        # Lift active_mode to the top level using the evaluator's field name.
        # (d["mode"] is provider.mode; the evaluator also calls it active_mode.)
        d["active_mode"] = d.get("mode")
        # Lift cache_dir to top level when present (cache-based runs).
        d["cache_dir"] = (d.get("ai_config") or {}).get("cache_dir") or None
        # metrics/evaluation can be large — manifest keeps a small summary
        d["metrics_summary"] = {
            k: d["metrics"].get(k)
            for k in (
                "total_return_pct",
                "sharpe_ratio",
                "max_drawdown_pct",
                "n_trades",
            )
            if k in d.get("metrics", {})
        }
        d.pop("metrics", None)
        if d["evaluation"] is None:
            d.pop("evaluation", None)
        # Annotate cache runs with provenance from generation_summary.json.
        if d.get("cache_dir"):
            d["cache_provenance"] = _read_cache_provenance(d["cache_dir"])
        return d


# ── cache provenance helper ───────────────────────────────────────────────


def _read_cache_provenance(cache_dir: str) -> dict[str, Any]:
    """Read generation_summary.json from *cache_dir*, if present.

    Returns a compact provenance dict suitable for embedding in the
    experiment manifest.  Fields:

    * ``llm_days``              — days that came from the LLM
    * ``heuristic_days``        — days that fell back to the heuristic
    * ``failed_days``           — days with no output
    * ``total_retries``         — LLM retry count
    * ``total_parse_failures``  — parse error count
    * ``total_api_failures``    — API/network error count
    * ``cache_source_quality``  — "llm" | "mixed" | "heuristic" | "empty" | "unknown"
    * ``interpretation_warning``— human-readable caution string, or null
    """
    summary_path = Path(cache_dir) / "generation_summary.json"
    if not summary_path.exists():
        return {"cache_source_quality": "unknown", "interpretation_warning": None}
    try:
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {"cache_source_quality": "unknown", "interpretation_warning": None}
    return {
        "llm_days": raw.get("days_success", 0),
        "heuristic_days": raw.get("days_heuristic_fallback", 0),
        "failed_days": raw.get("days_failed", 0),
        "total_retries": raw.get("total_retries", 0),
        "total_parse_failures": raw.get("total_parse_failures", 0),
        "total_api_failures": raw.get("total_api_failures", 0),
        "cache_source_quality": raw.get("cache_source_quality", "unknown"),
        "interpretation_warning": raw.get("interpretation_warning"),
    }


# ── preset builders ───────────────────────────────────────────────────────


ProviderOrNone = AISignalProvider | None


def _preset_baseline(
    *, data_provider: BacktestDataProvider, **_: Any
) -> tuple[ProviderOrNone, dict[str, Any]]:
    return None, {"note": "no AI provider"}


def _preset_random(
    *,
    data_provider: BacktestDataProvider,
    symbols: Sequence[str],
    random_seed: int,
    **_: Any,
) -> tuple[ProviderOrNone, dict[str, Any]]:
    dates = [d.isoformat() for d in data_provider.universe_dates(list(symbols))]
    provider = RandomAIProvider(dates, list(symbols), seed=random_seed)
    return provider, {"seed": random_seed, "n_days": len(dates)}


def _preset_ruleproxy(
    *,
    data_provider: BacktestDataProvider,
    symbols: Sequence[str],
    **_: Any,
) -> tuple[ProviderOrNone, dict[str, Any]]:
    features = build_rule_proxy_features(data_provider, symbols=symbols)
    provider = RuleProxyAIProvider(features)
    return provider, {"n_days": len(features)}


def _preset_perfect(
    *,
    data_provider: BacktestDataProvider,
    symbols: Sequence[str],
    forward_horizon: int,
    **_: Any,
) -> tuple[ProviderOrNone, dict[str, Any]]:
    fwd = build_forward_returns(
        data_provider,
        horizon_trading_days=forward_horizon,
        symbols=symbols,
    )
    provider = PerfectAIProvider(fwd)
    return provider, {"horizon_trading_days": forward_horizon, "n_days": len(fwd)}


def _preset_cache(
    *,
    cache_ai_config: AIIntegrationConfig | None,
    **_: Any,
) -> tuple[ProviderOrNone, dict[str, Any]]:
    if cache_ai_config is None or not cache_ai_config.is_active:
        raise ValueError(
            "cache preset requested but no active ai_integration config was "
            "supplied (either set ai_integration.enabled=true in the YAML or "
            "pass cache_ai_config=...)."
        )
    provider = cache_ai_config.build_provider()
    return provider, cache_ai_config.to_dict()


PresetBuilder = Callable[..., tuple[ProviderOrNone, dict[str, Any]]]

PRESETS: dict[str, PresetBuilder] = {
    "baseline": _preset_baseline,
    "random": _preset_random,
    "ruleproxy": _preset_ruleproxy,
    "perfect": _preset_perfect,
    "cache": _preset_cache,
}


# ── per-run writers ───────────────────────────────────────────────────────


def _write_report(result: BacktestResult, out_dir: Path, run_label: str) -> dict[str, Any]:
    """Write ``report.json`` + ``summary.txt`` with stable filenames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(result)
    payload = {
        "run_label": run_label,
        "metrics": metrics,
        "equity_curve": [
            {"date": d.isoformat(), "value": v} for d, v in result.equity_curve
        ],
        "trade_log": [t.to_dict() for t in result.trade_log],
        "daily_records": [r.to_dict() for r in result.daily_records],
    }
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.txt").write_text(
        _format_summary(run_label, metrics), encoding="utf-8"
    )
    return metrics


def _format_summary(run_label: str, metrics: Mapping[str, Any]) -> str:
    pf = metrics.get("profit_factor")
    pf_str = "∞ (pure-win)" if pf is None else f"{float(pf):.3f}"
    return "\n".join(
        [
            f"=== {run_label} ===",
            f"initial_cash     : {metrics.get('initial_cash', 0):,} KRW",
            f"final_value      : {metrics.get('final_value', 0):,} KRW",
            f"total_return_pct : {float(metrics.get('total_return_pct', 0.0)):+.2f} %",
            f"cagr_pct         : {float(metrics.get('cagr_pct', 0.0)):+.2f} %",
            f"sharpe_ratio     : {float(metrics.get('sharpe_ratio', 0.0)):.3f}",
            f"max_drawdown_pct : {float(metrics.get('max_drawdown_pct', 0.0)):.2f} %",
            f"n_trades         : {metrics.get('n_trades', 0)}",
            f"profit_factor    : {pf_str}",
        ]
    )


def _write_evaluation(
    *,
    out_dir: Path,
    baseline_result: BacktestResult,
    ai_result: BacktestResult,
    provider: AISignalProvider,
    forward_returns: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    evaluator = AIEvaluator(
        baseline_result=baseline_result,
        ai_result=ai_result,
        ai_provider=provider,
        forward_returns=forward_returns,
    )
    evaluation = evaluator.evaluate().to_dict()
    (out_dir / "ai_evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evaluation


# ── public API ────────────────────────────────────────────────────────────


def run_experiment_suite(
    *,
    data_path: str | Path,
    presets: Sequence[str] = ("baseline", "random", "ruleproxy", "perfect"),
    output_root: str | Path = "results/ai_experiments",
    config_path: str | Path | None = None,
    symbols: Sequence[str] | None = None,
    initial_cash: int = 10_000_000,
    random_seed: int = 1337,
    forward_horizon: int = 5,
    cache_ai_config: AIIntegrationConfig | None = None,
    experiment_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run a baseline + zero-or-more AI-augmented backtests and summarize.

    Parameters
    ----------
    data_path:
        Path to the OHLCV CSV (same format ``run_backtest`` consumes).
    presets:
        Subset of :data:`PRESETS` keys. ``"baseline"`` is always
        prepended if missing — we need it to compute Sharpe deltas.
    output_root:
        Parent directory; the function creates
        ``<root>/<experiment_name>/<preset>/``.
    config_path:
        Optional ``.kis.yaml`` strategy file. If the YAML has an active
        ``ai_integration:`` block, it is used as the default ``cache``
        preset config.
    cache_ai_config:
        Explicit override for the ``cache`` preset (takes precedence
        over anything pulled from the YAML).

    Returns
    -------
    dict
        The manifest that was also written to ``experiment_manifest.json``.
    """
    data_path = Path(data_path)
    if not data_path.exists():
        raise FileNotFoundError(f"data CSV not found: {data_path}")

    # ── Resolve settings + cache config from YAML (if any) ────────────────
    if config_path is not None:
        settings, meta = make_settings_from_yaml(str(config_path))
        yaml_ai_cfg: AIIntegrationConfig | None = meta.get(
            "ai_integration_config"
        )
    else:
        settings = make_settings()
        meta = {"name": Path(data_path).stem, "applied_overrides": {}}
        yaml_ai_cfg = None

    resolved_cache_cfg = cache_ai_config or yaml_ai_cfg

    # ── Resolve preset list ────────────────────────────────────────────────
    ordered_presets = list(dict.fromkeys(presets))  # de-dup, preserve order
    if "baseline" not in ordered_presets:
        ordered_presets.insert(0, "baseline")

    unknown = [p for p in ordered_presets if p not in PRESETS]
    if unknown:
        raise ValueError(
            f"unknown preset(s): {unknown}. Valid: {sorted(PRESETS)}"
        )

    # ── Prepare output tree ───────────────────────────────────────────────
    now = now or datetime.now()
    experiment_name = experiment_name or (
        f"{meta.get('name', data_path.stem)}_{now.strftime('%Y%m%d_%H%M%S')}"
    )
    root = Path(output_root) / experiment_name
    root.mkdir(parents=True, exist_ok=True)

    # ── Load data once ────────────────────────────────────────────────────
    data_provider = BacktestDataProvider.from_csv(data_path)
    run_symbols = list(symbols) if symbols else data_provider.symbols()

    # Forward returns are needed by both the perfect preset and by the
    # evaluator for every non-baseline run; build once.
    forward_returns = build_forward_returns(
        data_provider,
        horizon_trading_days=forward_horizon,
        symbols=run_symbols,
    )

    # ── Run baseline first ────────────────────────────────────────────────
    baseline_result: BacktestResult | None = None
    runs: list[ExperimentRun] = []

    for preset in ordered_presets:
        sub_dir = root / preset
        provider, builder_meta = PRESETS[preset](
            data_provider=data_provider,
            symbols=run_symbols,
            random_seed=random_seed,
            forward_horizon=forward_horizon,
            cache_ai_config=resolved_cache_cfg,
        )
        result = run_backtest(
            data_provider=data_provider,
            settings=settings,
            initial_cash=initial_cash,
            symbols=run_symbols,
            ai_provider=provider,
        )
        metrics = _write_report(result, sub_dir, run_label=preset)

        run_rec = ExperimentRun(
            preset=preset,
            provider_type=type(provider).__name__ if provider else None,
            is_synthetic=preset in ("random", "ruleproxy", "perfect"),
            mode=provider.mode if provider else None,
            output_subdir=preset,
            ai_config=(
                {
                    "mode": provider.mode,
                    "veto_threshold": provider.veto_threshold,
                    "delta_scale": provider.delta_scale,
                    **builder_meta,
                }
                if provider
                else None
            ),
            metrics=metrics,
        )

        if preset == "baseline":
            baseline_result = result
        else:
            assert baseline_result is not None  # baseline always runs first
            run_rec.evaluation = _write_evaluation(
                out_dir=sub_dir,
                baseline_result=baseline_result,
                ai_result=result,
                provider=provider,  # type: ignore[arg-type]
                forward_returns=forward_returns,
            )

        runs.append(run_rec)

    # ── Manifest + comparison summary ─────────────────────────────────────
    manifest = {
        "experiment_name": experiment_name,
        "experiment_time": now.isoformat(),
        "strategy_config": str(config_path) if config_path else None,
        "data_path": str(data_path),
        "initial_cash": initial_cash,
        "symbols": run_symbols,
        "forward_horizon_trading_days": forward_horizon,
        "random_seed": random_seed,
        "runs": [r.to_manifest_entry() for r in runs],
    }
    (root / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "comparison_summary.txt").write_text(
        _format_comparison(runs), encoding="utf-8"
    )

    return manifest


def _format_comparison(runs: Sequence[ExperimentRun]) -> str:
    baseline = next((r for r in runs if r.preset == "baseline"), None)
    base_return = float(baseline.metrics.get("total_return_pct", 0.0)) if baseline else 0.0

    rows: list[dict[str, Any]] = []
    for r in runs:
        metrics = r.metrics or {}
        ev = r.evaluation or {}
        rows.append(
            {
                "preset": r.preset,
                "mode": r.mode or "—",
                "return_pct": float(metrics.get("total_return_pct", 0.0)),
                "return_delta_pct": float(metrics.get("total_return_pct", 0.0)) - base_return,
                "sharpe": float(metrics.get("sharpe_ratio", 0.0)),
                "sharpe_delta": float(ev.get("sharpe_delta", 0.0)) if ev else 0.0,
                "coverage": float(ev.get("signal_coverage_rate", 0.0)) if ev else None,
                # Explicitly preserve None for suppressed metrics so the
                # table can render "—" rather than a misleading 0.000.
                "ndcg3": ev.get("ndcg_at_3") if ev else None,
                "veto_p": ev.get("veto_precision") if ev else None,
                "veto_r": ev.get("veto_recall") if ev else None,
                "ks": ev.get("score_separation_ks") if ev else None,
                "n_trades": int(metrics.get("n_trades", 0)),
            }
        )

    # Ranking: sharpe_delta desc, then return_delta desc, then coverage desc.
    def sort_key(row: dict[str, Any]) -> tuple:
        return (
            -row["sharpe_delta"],
            -row["return_delta_pct"],
            -(row["coverage"] or 0.0),
        )
    ranked = sorted(rows, key=sort_key)

    header = (
        f"{'preset':<11}{'mode':<12}{'return%':>10}{'Δret%':>10}"
        f"{'sharpe':>9}{'Δsharpe':>10}{'cov':>7}"
        f"{'ndcg@3':>9}{'veto_p':>8}{'veto_r':>8}{'ks':>7}{'#tr':>6}"
    )
    lines = [
        "AI experiment comparison (ranked by Δsharpe, Δreturn, coverage)",
        header,
        "-" * len(header),
    ]
    for row in ranked:
        lines.append(
            f"{row['preset']:<11}"
            f"{row['mode']:<12}"
            f"{row['return_pct']:>+10.2f}"
            f"{row['return_delta_pct']:>+10.2f}"
            f"{row['sharpe']:>9.3f}"
            f"{row['sharpe_delta']:>+10.3f}"
            f"{(row['coverage'] if row['coverage'] is not None else 0.0):>7.2f}"
            f"{_fmt_opt(row['ndcg3']):>9}"
            f"{_fmt_opt(row['veto_p']):>8}"
            f"{_fmt_opt(row['veto_r']):>8}"
            f"{_fmt_opt(row['ks']):>7}"
            f"{row['n_trades']:>6}"
        )

    # Provenance footnotes for cache-based runs.
    footnotes: list[str] = []
    for r in runs:
        prov = getattr(r, "_cache_provenance_footnote", None)
        if prov:
            footnotes.append(prov)
    # Build provenance inline from the run records directly.
    for r in runs:
        if r.preset != "cache":
            continue
        manifest_entry = r.to_manifest_entry()
        cp = manifest_entry.get("cache_provenance") or {}
        quality = cp.get("cache_source_quality", "unknown")
        warning = cp.get("interpretation_warning")
        llm_d = cp.get("llm_days")
        heur_d = cp.get("heuristic_days")
        if quality != "unknown":
            note = f"[cache] source quality: {quality}"
            if llm_d is not None:
                note += f"  ({llm_d} LLM days, {heur_d} heuristic days)"
            footnotes.append(note)
        if warning:
            footnotes.append(f"[cache] *** {warning} ***")
    if footnotes:
        lines.append("")
        lines.extend(footnotes)

    return "\n".join(lines) + "\n"


def _fmt_opt(x: Any) -> str:
    if x is None:
        return "   —"
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return str(x)
