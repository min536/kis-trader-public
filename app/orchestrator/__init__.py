"""Minimal deterministic runtime orchestrator (read-only workflows).

This package mirrors the validated `.claude/` ops-orchestrator harness as a
deterministic, no-LLM runtime skeleton. It currently exposes a single read-only
``postrun_audit`` workflow. There are no broker/API calls and no config mutation.
"""
