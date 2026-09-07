from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SellWatchCursorPlan:
    start_index: int
    ordered_indices: tuple[int, ...]
    retry_symbol: str
    retry_index: int | None
    used_retry_anchor: bool
    clear_retry_symbol: bool


def build_sell_watch_cursor_plan(
    *,
    symbols: Sequence[str],
    next_start_index: int,
    retry_symbol: str | None,
    risk_first_override: bool,
) -> SellWatchCursorPlan:
    normalized_symbols = tuple(str(symbol or "").strip() for symbol in symbols)
    total = len(normalized_symbols)
    normalized_retry_symbol = str(retry_symbol or "").strip()
    if total <= 0:
        return SellWatchCursorPlan(
            start_index=0,
            ordered_indices=(),
            retry_symbol=normalized_retry_symbol,
            retry_index=None,
            used_retry_anchor=False,
            clear_retry_symbol=bool(normalized_retry_symbol),
        )

    cursor_start = int(next_start_index or 0) % total
    retry_index = next(
        (
            index
            for index, symbol in enumerate(normalized_symbols)
            if symbol == normalized_retry_symbol
        ),
        None,
    )
    if retry_index is not None:
        return SellWatchCursorPlan(
            start_index=retry_index,
            ordered_indices=tuple(range(retry_index, total))
            + tuple(range(0, retry_index)),
            retry_symbol=normalized_retry_symbol,
            retry_index=retry_index,
            used_retry_anchor=True,
            clear_retry_symbol=True,
        )

    if risk_first_override:
        return SellWatchCursorPlan(
            start_index=0,
            ordered_indices=tuple(range(total)),
            retry_symbol=normalized_retry_symbol,
            retry_index=None,
            used_retry_anchor=False,
            clear_retry_symbol=bool(normalized_retry_symbol),
        )

    return SellWatchCursorPlan(
        start_index=cursor_start,
        ordered_indices=tuple(range(cursor_start, total))
        + tuple(range(0, cursor_start)),
        retry_symbol=normalized_retry_symbol,
        retry_index=None,
        used_retry_anchor=False,
        clear_retry_symbol=bool(normalized_retry_symbol),
    )


def retry_anchor_to_restore_after_empty_budget(
    *,
    budget_limit: int,
    retry_symbol: str | None,
    retry_anchor_consumed: bool,
) -> str | None:
    normalized_retry_symbol = str(retry_symbol or "").strip()
    if (
        retry_anchor_consumed
        and int(budget_limit or 0) <= 0
        and normalized_retry_symbol
    ):
        return normalized_retry_symbol
    return None
