"""AI signal integration layer for the backtester.

Step 1 exposes the feature export hook and the look-ahead audit mixin.
Later steps (AISignalProvider, injection points, synthetic baselines,
evaluator, YAML config) build on these primitives.

When no exporter / provider is supplied, this module adds no behavior:
the backtester runs byte-for-byte identically to before.
"""
from __future__ import annotations

from backtester.ai_integration.config import (
    AI_DOTTED_PREFIX,
    AIIntegrationConfig,
)
from backtester.ai_integration.baselines import (
    PerfectAIProvider,
    RandomAIProvider,
    RuleProxyAIProvider,
)
from backtester.ai_integration.evaluator import (
    AIEvaluation,
    AIEvaluator,
    sharpe_ratio,
)
from backtester.ai_integration.feature_export import (
    CandidateFeatureExporter,
    FeatureAuditMixin,
    LookAheadBiasError,
)
from backtester.ai_integration.provider import (
    AISignalProvider,
    MissingAISignalError,
)

__all__ = [
    "AI_DOTTED_PREFIX",
    "AIEvaluation",
    "AIEvaluator",
    "AIIntegrationConfig",
    "AISignalProvider",
    "CandidateFeatureExporter",
    "FeatureAuditMixin",
    "LookAheadBiasError",
    "MissingAISignalError",
    "PerfectAIProvider",
    "RandomAIProvider",
    "RuleProxyAIProvider",
    "sharpe_ratio",
]
