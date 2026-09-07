"""Shared helpers for llm_cache_builder test modules."""
from __future__ import annotations

import json
from pathlib import Path


def _valid_response(tickers: list[str], *, rm: float = 1.0) -> str:
    """Return a syntactically valid LLM response for the given tickers."""
    ticker_payload = {
        t: {
            "delta": round(0.5 * (i + 1), 2),
            "veto": False,
            "veto_confidence": 0.05,
            "veto_reason": None,
            "rank": i + 1,
        }
        for i, t in enumerate(tickers)
    }
    return json.dumps({"regime_multiplier": rm, "tickers": ticker_payload})


def _veto_response(tickers: list[str], *, veto_ticker: str) -> str:
    """Response where ``veto_ticker`` is vetoed; others are normal."""
    ticker_payload = {}
    rank = 1
    for t in tickers:
        if t == veto_ticker:
            ticker_payload[t] = {
                "delta": -3.0,
                "veto": True,
                "veto_confidence": 0.9,
                "veto_reason": "overheat",
                "rank": len(tickers),  # worst rank
            }
        else:
            ticker_payload[t] = {
                "delta": 1.0,
                "veto": False,
                "veto_confidence": 0.1,
                "veto_reason": None,
                "rank": rank,
            }
            rank += 1
    return json.dumps({"regime_multiplier": 1.05, "tickers": ticker_payload})


def _rec(date: str, ticker: str, **kwargs) -> dict:
    return {
        "date": date,
        "ticker": ticker,
        "base_score": kwargs.get("base_score", 2.0),
        "features": {
            "momentum_quality_score": kwargs.get("momentum", 0.5),
            "trend_quality_score": kwargs.get("trend", 0.4),
            "range_recovery_bonus": kwargs.get("recovery", 0.2),
            "overheat_penalty": kwargs.get("overheat", 0.0),
            "pullback_exhaustion_penalty": kwargs.get("exhaustion", 0.0),
        },
        "decision": "approved",
        "rule_gate_passed": True,
        "score_gate_passed": True,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
