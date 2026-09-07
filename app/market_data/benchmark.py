"""Benchmark snapshot resolution for performance reporting.

Resolves the configured benchmark symbol's price from (in order) the
snapshots already observed this cycle, the cached live-snapshot store, and
finally an optional API lookup that is skipped under adaptive pacing.
Body moved verbatim from app/main.py (R1 core slimming).
"""

from __future__ import annotations

import sys
from typing import Mapping

from app.core.throttle import get_adaptive_pacing_summary
from app.core.time_utils import is_snapshot_fresh
from app.domestic_stock.quote import inquire_price
from app.market_data.schema import build_market_snapshot
from app.reporting.performance import BenchmarkSnapshot
from app.scanner.symbol_names import get_symbol_name


def resolve_benchmark_snapshot(
    *,
    settings,
    token: str | None,
    observed_market_snapshots: dict[str, object],
    cached_market_snapshots: Mapping[str, object] | None = None,
) -> BenchmarkSnapshot | None:
    benchmark_symbol = settings.performance_benchmark_symbol.strip()
    if not benchmark_symbol:
        return None

    observed_snapshot = observed_market_snapshots.get(benchmark_symbol)
    if observed_snapshot is not None:
        return BenchmarkSnapshot(
            symbol=benchmark_symbol,
            name=get_symbol_name(benchmark_symbol),
            current_price=int(observed_snapshot.current_price),
        )

    cached_snapshot = (
        (cached_market_snapshots or {}).get(benchmark_symbol)
        if isinstance(cached_market_snapshots, Mapping)
        else None
    )
    if isinstance(cached_snapshot, Mapping) and is_snapshot_fresh(
        cached_snapshot,
        max_age_seconds=int(getattr(settings, "live_snapshot_ttl_seconds", 420) or 420),
    ):
        cached_price = int(cached_snapshot.get("current_price") or 0)
        if cached_price > 0:
            return BenchmarkSnapshot(
                symbol=benchmark_symbol,
                name=get_symbol_name(benchmark_symbol),
                current_price=cached_price,
            )

    if not token:
        return None

    adaptive_pacing_summary = get_adaptive_pacing_summary()
    if bool(adaptive_pacing_summary.get("active")):
        print(
            "[info] benchmark snapshot API lookup skipped during adaptive pacing",
            file=sys.stderr,
        )
        return None

    try:
        price_data = inquire_price(benchmark_symbol, token=token)
        if price_data.get("rt_cd") != "0":
            return None
        snapshot = build_market_snapshot(price_data["output"])
        return BenchmarkSnapshot(
            symbol=benchmark_symbol,
            name=get_symbol_name(benchmark_symbol),
            current_price=int(snapshot.current_price),
        )
    except Exception as exc:
        print(f"[warn] benchmark snapshot failed ({benchmark_symbol}): {exc}", file=sys.stderr)
        return None
