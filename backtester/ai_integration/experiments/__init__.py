"""Thin orchestration layer for AI-integration experiments.

This package is a small, explicit harness — not a new subsystem. Its job
is to run a baseline + several AI-augmented backtests against the same
data/config and summarize the differences. All real work is delegated to
existing modules:

* ``run_backtest`` from ``engine_backtest.runner``
* ``AISignalProvider`` and the three synthetic providers
* ``AIEvaluator`` for post-hoc metrics
* ``AIIntegrationConfig`` for YAML-driven cache-based runs

Public entry points:

* :func:`run_experiment_suite` — run selected presets and write a
  structured result tree.
* :func:`build_forward_returns` — T+K forward-return lookup built from
  the same :class:`BacktestDataProvider` used by the backtest.
"""
from __future__ import annotations

from backtester.ai_integration.experiments.forward_returns import (
    build_forward_returns,
)
from backtester.ai_integration.experiments.runner import (
    PRESETS,
    ExperimentRun,
    build_rule_proxy_features,
    run_experiment_suite,
)

__all__ = [
    "PRESETS",
    "ExperimentRun",
    "build_forward_returns",
    "build_rule_proxy_features",
    "run_experiment_suite",
]
