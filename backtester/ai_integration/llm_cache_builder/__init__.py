"""Offline LLM batch cache generation for AISignalProvider.

Reads exported candidate feature JSONL, calls an LLM once per trading day,
archives raw responses, and writes validated per-day JSON cache files.

Typical offline workflow::

    # 1. Export features from a backtest run:
    #    (CandidateFeatureExporter writes candidate_features.jsonl)

    # 2. Generate LLM-based cache:
    python -m backtester.ai_integration.llm_cache_builder \\
        --features data/candidate_features.jsonl \\
        --output-dir data/ai_signals \\
        --archive-dir data/llm_archive \\
        --limit-days 5   # smoke-test first

    # 3. Run experiment suite using the new cache:
    python -m backtester.ai_integration.experiments.cli run \\
        --data-csv data/prices.csv \\
        --ai-cache-dir data/ai_signals \\
        --ai-mode combined \\
        --presets baseline,cache

The heuristic builder (``cache_builder.py``) remains available as a
fallback and debug baseline.
"""
from __future__ import annotations

from backtester.ai_integration.llm_cache_builder.archive import ResponseArchive
from backtester.ai_integration.llm_cache_builder.batch import (
    BatchCacheGenerator,
    GenerationStats,
)
from backtester.ai_integration.llm_cache_builder.client import (
    DEFAULT_MODEL,
    KNOWN_MODELS,
    PILOT_MODEL,
    PRODUCTION_MODEL,
    SMOKE_TEST_MODEL,
    AnthropicClient,
    LLMClient,
    MockLLMClient,
    model_note,
)
from backtester.ai_integration.llm_cache_builder.parser import (
    ParseError,
    parse_response,
)
from backtester.ai_integration.llm_cache_builder.prompt import (
    build_correction_prompt,
    build_system_prompt,
    build_user_prompt,
)
from backtester.ai_integration.llm_cache_builder.replay import (
    ArchiveReplayer,
    ReplayStats,
)

__all__ = [
    "DEFAULT_MODEL",
    "KNOWN_MODELS",
    "PILOT_MODEL",
    "PRODUCTION_MODEL",
    "SMOKE_TEST_MODEL",
    "AnthropicClient",
    "ArchiveReplayer",
    "BatchCacheGenerator",
    "GenerationStats",
    "LLMClient",
    "MockLLMClient",
    "ParseError",
    "ReplayStats",
    "ResponseArchive",
    "build_correction_prompt",
    "build_system_prompt",
    "build_user_prompt",
    "model_note",
    "parse_response",
]
