"""Slice F2 — scanner replay driver over the REAL scanner with store seams.

Drives ``app.scanner.service.scan_target_symbols`` from replay minute data by
writing the two runtime-state stores the scanner reads through
(``live_snapshot.json`` and the cycle-snapshots history JSONL) and synthesizing
KIS quote payloads (F1). No network, no broker/settings imports — research →
runtime imports are pure-calculation only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.portfolio.schema import build_portfolio_snapshot
from app.research.replay.payload_synth import synth_quote_payload
from app.scanner.service import scan_target_symbols

# Generous TTL so the reader's wall-clock age gate (_fresh_snapshot,
# live_snapshot.py:276-292) always passes and never enters the worker-health /
# PID branch (:218-244). ``updated_at`` is stamped at write() time with the wall
# clock NOW for the same reason.
_LIVE_SNAPSHOT_TTL_SECONDS = 3600


class ReplayLiveSnapshotWriter:
    """Writer for seam A — the live-rank ``live_snapshot.json`` store.

    Produces exactly the JSON shape ``get_live_snapshot_signal`` reads
    (live_snapshot.py:316-353): top-level ``updated_at`` / ``ttl_seconds`` /
    ``top_symbols`` plus ``source_symbols`` as ORDERED LISTS keyed by
    ``volume_rank`` / ``fluctuation_rank`` / ``volume_power_rank``. Rank is the
    1-based position of the symbol in each list; ``combined_rank`` is the
    position in ``top_symbols``.
    """

    def __init__(self, tmp_dir) -> None:
        self._path = Path(tmp_dir) / "live_snapshot.json"
        self._snapshot: dict | None = None
        self._rank_maps: dict[str, dict[str, int]] = {
            "combined": {},
            "volume": {},
            "fluctuation": {},
            "volume_power": {},
        }

    @property
    def path(self) -> Path:
        return self._path

    def write(self, t: datetime, ordered_symbols: dict[str, list[str]]) -> None:
        volume = list(ordered_symbols.get("volume_rank") or [])
        fluctuation = list(ordered_symbols.get("fluctuation_rank") or [])
        volume_power = list(ordered_symbols.get("volume_power_rank") or [])
        # combined rank list == volume-rank ordering (single-proxy limitation,
        # documented §9). All three source lists share that ordering upstream.
        top_symbols = volume
        payload = {
            "updated_at": datetime.now().isoformat(),
            "ttl_seconds": _LIVE_SNAPSHOT_TTL_SECONDS,
            "top_symbols": top_symbols,
            "source_symbols": {
                "volume_rank": volume,
                "fluctuation_rank": fluctuation,
                "volume_power_rank": volume_power,
            },
        }
        self._snapshot = payload
        self._rank_maps = {
            "combined": self._rank_map(top_symbols),
            "volume": self._rank_map(volume),
            "fluctuation": self._rank_map(fluctuation),
            "volume_power": self._rank_map(volume_power),
        }
        self._path.write_text(json.dumps(payload))

    @staticmethod
    def _rank_map(symbols: list[str]) -> dict[str, int]:
        return {str(symbol): index for index, symbol in enumerate(symbols, start=1)}

    def load_snapshot(self) -> dict | None:
        return self._snapshot

    def get_signal(self, symbol: str, max_age_seconds: int | None = None):
        del max_age_seconds
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol or self._snapshot is None:
            return None
        source_ranks = (
            self._rank_maps["volume"].get(normalized_symbol),
            self._rank_maps["fluctuation"].get(normalized_symbol),
            self._rank_maps["volume_power"].get(normalized_symbol),
        )
        from app.market_data.live_snapshot import LiveSnapshotSignal

        return LiveSnapshotSignal(
            symbol=normalized_symbol,
            updated_at=str(self._snapshot.get("updated_at") or "") or None,
            combined_rank=self._rank_maps["combined"].get(normalized_symbol),
            volume_rank=source_ranks[0],
            fluctuation_rank=source_ranks[1],
            volume_power_rank=source_ranks[2],
            ranked_source_count=sum(rank is not None for rank in source_ranks),
        )


class ReplayHistoryWriter:
    """Writer for seam B — the per-cycle price-history JSONL store.

    Runtime history is a per-cycle (~per-minute) observation timeline, NOT daily
    rollups (see plan §2.4 seam B / §6 F2-b). Each replay tick appends ONE JSONL
    record — top-level ``timestamp`` plus ``observed_market_snapshots``, a list
    of flat dicts ``{symbol, current_price, open_price, low_price,
    prev_day_change_pct}`` — which the REAL
    ``app.math_models.history._build_symbol_histories`` parses via the
    ``observed_cycle`` source (history.py:182-212).

    Records are kept in an in-memory buffer and flushed to the JSONL by
    rewriting the file. ``history_refresh_ticks`` (default 1) controls how often
    the file is rewritten/synced so per-tick I/O + lru_cache invalidation cost
    can be tuned. At each new trading day, ``reseed_for_new_day`` truncates the
    buffer/file to the previous day's tail ``reseed_tail_records`` (covering the
    technical limit of 3000) before new-day appends continue.
    """

    def __init__(
        self,
        jsonl_path,
        *,
        reseed_tail_records: int = 3000,
        history_refresh_ticks: int = 1,
    ) -> None:
        self._path = Path(jsonl_path)
        self._reseed_tail_records = int(reseed_tail_records)
        self._history_refresh_ticks = max(1, int(history_refresh_ticks))
        self._records: list[dict] = []
        self._ticks_since_flush = 0
        self._version = 0
        self._histories_by_symbol: dict[str, list[dict]] = {}
        self._histories_cache: dict[int, dict[str, list[dict]]] = {}

    @property
    def path(self) -> Path:
        return self._path

    @property
    def version(self) -> int:
        return self._version

    def append_tick(self, t: datetime, observations) -> None:
        snapshots = [
            {
                "symbol": str(row["symbol"]),
                "current_price": row["current_price"],
                "open_price": row["open_price"],
                "low_price": row["low_price"],
                "prev_day_change_pct": row["prev_day_change_pct"],
            }
            for row in observations
        ]
        self._records.append(
            {
                "timestamp": t.isoformat(),
                "observed_market_snapshots": snapshots,
            }
        )
        self._append_observed_histories(t.isoformat(), snapshots)
        self._invalidate_histories()
        self._ticks_since_flush += 1
        if self._ticks_since_flush >= self._history_refresh_ticks:
            self._flush()

    def reseed_for_new_day(self) -> None:
        if self._reseed_tail_records <= 0:
            self._records = []
        else:
            self._records = self._records[-self._reseed_tail_records :]
        self._rebuild_observed_histories()
        self._invalidate_histories()
        self._flush()

    def _flush(self) -> None:
        self._ticks_since_flush = 0
        self._path.write_text(
            "".join(json.dumps(record) + "\n" for record in self._records)
        )

    def _invalidate_histories(self) -> None:
        self._version += 1
        self._histories_cache.clear()

    def _append_observed_histories(self, timestamp: str, snapshots: list[dict]) -> None:
        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol", "")).strip()
            price = snapshot.get("current_price")
            if not symbol or price is None:
                continue
            self._histories_by_symbol.setdefault(symbol, []).append(
                {
                    "timestamp": timestamp,
                    "price": int(price),
                    "open_price": (
                        int(snapshot.get("open_price", 0) or 0)
                        if snapshot.get("open_price") is not None
                        else None
                    ),
                    "low_price": (
                        int(snapshot.get("low_price", 0) or 0)
                        if snapshot.get("low_price") is not None
                        else None
                    ),
                    "prev_day_change_pct": (
                        float(snapshot.get("prev_day_change_pct", 0.0) or 0.0)
                        if snapshot.get("prev_day_change_pct") is not None
                        else None
                    ),
                    "source": "observed_cycle",
                }
            )

    def _rebuild_observed_histories(self) -> None:
        self._histories_by_symbol = {}
        for record in self._records:
            timestamp = str(record.get("timestamp", "")).strip()
            snapshots = record.get("observed_market_snapshots")
            snapshots = snapshots if isinstance(snapshots, list) else []
            self._append_observed_histories(timestamp, snapshots)

    def build_symbol_histories(self, limit: int) -> dict[str, list[dict]]:
        """Return scanner-compatible histories from the in-memory replay buffer.

        Runtime reads histories from JSONL, but replay owns the same records in
        memory before flushing them for observability. Using that buffer avoids
        reparsing a growing JSONL on every replay scan while preserving the same
        per-tick visibility semantics.
        """
        normalized_limit = max(int(limit), 1)
        cached = self._histories_cache.get(normalized_limit)
        if cached is not None:
            return cached
        histories = {
            symbol: list(rows[-normalized_limit:])
            for symbol, rows in self._histories_by_symbol.items()
        }
        self._histories_cache[normalized_limit] = histories
        return histories


# --- replay settings -------------------------------------------------------
#
# Replicated from tests/test_lane_scheduler_no_hooks.py::_Settings defaults
# (research must not import from tests/). Any attribute the scanner reads that is
# absent here resolves to ``False`` via __getattr__ — byte-identical to the test
# helper's fallback.
_REPLAY_SETTINGS_DEFAULTS: dict[str, object] = {
    "run_mode": "mock",
    "confirm_buy": "NO",
    "lane_scheduler_enabled": True,
    "order_gate_enabled": True,
    "enable_sell_guard_selftest": False,
    "enable_sell_test_scenarios": False,
    "sell_test_mode": "off",
    "session_cycle_hard_budget_seconds": 60.0,
    "sell_check_interval_seconds": 30,
    "buy_scan_interval_seconds": 60,
    "buy_scan_min_remaining_budget_seconds": 5.0,
    "buy_scan_quote_prefetch_deadline_seconds": 18.0,
    "buy_scan_quote_request_timeout_seconds": 2.0,
    "buy_scan_quote_max_attempts": 1,
    "buy_scan_total_budget_seconds": 25.0,
    "scan_symbols_max_per_cycle": 200,
    "target_symbols": ("005930",),
    "buy_scan_top_k_candidates": 5,
    "enable_daily_pnl_brake": False,
    "buy_max_budget_per_trade_krw": 1_000_000,
    "buy_max_account_exposure_pct": 100.0,
    "buy_max_qty_per_trade": 10,
    "rebuy_cooldown_minutes": 0,
    "same_symbol_max_buys_per_day": 10,
    "buy_daily_max_order_submissions": 10,
    "buy_enable_risk_guards": False,
    "sell_daily_max_order_submissions": 10,
    "sell_daily_max_notional_krw": 10_000_000,
    "sell_enable": True,
    "sell_stop_loss_pct": 5.0,
    "sell_take_profit_pct": 50.0,
    "sell_trailing_stop_pct": 50.0,
    "sell_rule_enable_live_leadership_loss": False,
    "sell_rule_enable_live_power_breakdown": False,
    "sell_exit_required_pass_count": 1,
    "block_resell_symbols_sold_today": False,
    "enable_sell_cooldown": False,
    "allow_one_sell_trigger_per_symbol_per_day": False,
    "sell_blocked_cooldown_minutes": 0,
    "order_cooldown_minutes": 0,
    "buy_fee_bps": 0.0,
    "buy_slippage_bps": 0.0,
    "sell_fee_bps": 0.0,
    "sell_tax_bps": 0.0,
    "sell_slippage_bps": 0.0,
    "use_cost_aware_pnl": False,
    "qty": 1,
    "buy_block_on_blocked_preview": False,
    "enable_rebalance_sell": False,
    "strict_sell_first": False,
    "target_symbols_source": "test",
    "target_symbols_raw": "005930",
    "target_symbols_split_items": ("005930",),
}

# --- buy-rule / scoring defaults ----------------------------------------
# Runtime defaults from app/auth/settings_fields.py (the os.getenv fallbacks),
# then OVERRIDDEN by the ``config/regular_session.env`` pins below so the replay
# reproduces the regular-session profile that produced the parity snapshots.
_RUNTIME_BUY_RULE_DEFAULTS: dict[str, object] = {
    "buy_rule_enable_intraday_pullback": True,
    "buy_rule_enable_rebound_from_low": True,
    "buy_rule_enable_controlled_down_day": True,
    "buy_rule_enable_range_recovery": True,
    "buy_rule_enable_live_volume_rank": True,
    "buy_rule_enable_live_volume_power_rank": True,
    "buy_rule_rebound_from_low_pct": 0.01,
    "buy_rule_controlled_down_day_min": -6.0,
    "buy_rule_controlled_down_day_max": 0.0,
    "buy_rule_gap_down_open_min_pct": 0.3,
    "buy_rule_gap_down_open_max_pct": 5.0,
    "buy_rule_range_recovery_min_ratio": 0.2,
    "buy_rule_required_pass_count": 3,
    "buy_rule_required_pass_count_core": 3,
    "buy_min_passed_count": 3,
    "buy_min_score": 3.20,
    "buy_min_score_core": 3.00,
    "expected_slippage_bps_base": 0.0,
}

# config/regular_session.env pins (these WIN over the runtime defaults above).
# Named constants so the pinned key -> value mapping is explicit and auditable.
_REGULAR_SESSION_ENV_PINS: dict[str, object] = {
    # Selective buy gate (regular session profile).
    "buy_min_passed_count": 2,          # BUY_MIN_PASSED_COUNT=2
    "buy_min_score": 3.50,              # BUY_MIN_SCORE=3.50
    "buy_min_score_core": 3.40,         # BUY_MIN_SCORE_CORE=3.40
    "min_net_profit_buffer_bps": 20.0,  # MIN_NET_PROFIT_BUFFER_BPS=20.0
    "min_net_edge_bps": 5.0,            # MIN_NET_EDGE_BPS=5.0
    "expected_cost_block_bps": 35.0,    # EXPECTED_COST_BLOCK_BPS=35.0
    # Surge-market buy-rule tuning.
    "buy_rule_controlled_down_day_max": 5.0,     # BUY_RULE_CONTROLLED_DOWN_DAY_MAX=5.0
    "buy_rule_rebound_from_low_pct": 0.005,      # BUY_RULE_REBOUND_FROM_LOW_PCT=0.005
    "buy_rule_range_recovery_min_ratio": 0.10,   # BUY_RULE_RANGE_RECOVERY_MIN_RATIO=0.10
    "buy_rule_enable_gap_down_open": False,       # BUY_RULE_ENABLE_GAP_DOWN_OPEN=false
    # Stop-loss / scan breadth.
    "sell_stop_loss_pct": -8.0,          # SELL_STOP_LOSS_PCT=-8.0
    "scan_symbols_max_per_cycle": 200,   # SCAN_SYMBOLS_MAX_PER_CYCLE=200
    "buy_excluded_symbols": ("252710",),  # BUY_EXCLUDED_SYMBOLS=252710
}


class _ReplaySettings(SimpleNamespace):
    """Settings stand-in mirroring the test _Settings fallback behavior."""

    def __getattr__(self, name: str):
        if name in _REPLAY_SETTINGS_DEFAULTS:
            return _REPLAY_SETTINGS_DEFAULTS[name]
        return False


def replay_settings(**overrides) -> _ReplaySettings:
    """Build the replay Settings stand-in.

    Layering (later wins): _Settings defaults (replicated) -> runtime buy-rule
    defaults -> config/regular_session.env pins -> caller overrides. The result
    is a plain namespace so the REAL scanner reads it exactly like production
    ``Settings``; unknown attributes fall back to ``False`` (test-helper parity).
    """
    merged: dict[str, object] = {}
    merged.update(_RUNTIME_BUY_RULE_DEFAULTS)
    merged.update(_REGULAR_SESSION_ENV_PINS)
    merged.update(overrides)
    return _ReplaySettings(**merged)


# Live-rank proxy depth (§9: 200-symbol universe proxy, not full market).
_LIVE_RANK_TOP_N = 200
# Per-tick history density: gate1-pass top N + sim-held (runtime-density
# fidelity; recording all 200 would be denser than runtime — a fidelity delta).
_HISTORY_GATE1_TOP_N = 40


def _gate1_passed(result) -> bool:
    """A soft gate1 pass: at least one buy rule passed for this symbol."""
    return float(getattr(result, "passed_count", 0) or 0) >= 1.0


def _observation_row(obs, prev_close) -> dict | None:
    """Build one flat history row from an observation (seam-B record shape)."""
    if obs is None or not prev_close:
        return None
    return {
        "symbol": obs.symbol,
        "current_price": int(obs.close),
        "open_price": int(obs.day_open),
        "low_price": int(obs.day_low),
        "prev_day_change_pct": round((obs.close / prev_close - 1) * 100, 2),
    }


def run_scan_at(
    *,
    t: datetime,
    provider,
    sim,
    settings,
    history_writer: ReplayHistoryWriter,
    live_writer: ReplayLiveSnapshotWriter,
):
    """Run ONE replay scan tick through the REAL scanner.

    Writes the two runtime-state stores the scanner reads through (seam A live
    ranks + seam B price history), synthesizes KIS quote payloads (F1), builds
    the portfolio snapshot from the sim's holdings, and calls the REAL
    ``scan_target_symbols`` with inline quote fetch disabled (fully prefetched).

    The two store redirects are held for the duration of the scanner call via
    context managers (§10-① pre-approved seams): ``live_snapshot.SNAPSHOT_PATH``
    + ``LIVE_SNAPSHOT_DIR_ENV`` in parallel, and
    ``history._cycle_snapshots_file``. Seam B is a look-back timeline, so this
    tick's observations are appended AFTER the scan (future ticks read them);
    the scanner here sees only prior ticks' history.
    """
    # ① entry guard — a truthy BUY_SCAN_QUOTE_KIS_ENV would trigger live token
    # issuance inside scan_target_symbols (service.py:649 -> quote_account.py).
    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError("replay requires BUY_SCAN_QUOTE_KIS_ENV unset")

    # ② universe observable at t.
    universe = provider.universe_at(t)
    day = t.date()

    # ③ synthesize prefetched KIS payloads (+ retain observations for history).
    payloads: dict[str, dict] = {}
    obs_by_symbol: dict[str, object] = {}
    prev_close_by_symbol: dict[str, float | None] = {}
    for symbol in universe:
        obs = provider.observable_snapshot(symbol, t)
        prev_close = provider.prev_close(symbol, day)
        obs_by_symbol[symbol] = obs
        prev_close_by_symbol[symbol] = prev_close
        payload = synth_quote_payload(obs, prev_close=prev_close) if obs else None
        if payload is not None:
            payloads[symbol] = payload

    # ④ live ranks (single-proxy: same volume-rank ordering across all lists).
    ranked = list(provider.volume_rank_at(t, _LIVE_RANK_TOP_N))
    live_writer.write(
        t,
        {
            "volume_rank": ranked,
            "fluctuation_rank": ranked,
            "volume_power_rank": ranked,
        },
    )

    # ⑤ sim holdings -> KIS balance-response shape -> portfolio snapshot.
    portfolio_snapshot = build_portfolio_snapshot(
        _balance_response_from_sim(sim, obs_by_symbol)
    )

    # ⑥ call the REAL scanner with the two stores redirected to our tmp files.
    import app.market_data.live_snapshot as live_snapshot
    import app.math_models.history as history_module

    scan_symbols = tuple(payloads)
    def _replay_build_symbol_histories(limit: int, file_marker: int):
        return history_writer.build_symbol_histories(limit)

    def _replay_file_marker(path: Path) -> int:
        del path
        return history_writer.version

    with mock.patch.object(
        live_snapshot, "SNAPSHOT_PATH", live_writer.path
    ), mock.patch.object(
        live_snapshot,
        "load_live_snapshot",
        live_writer.load_snapshot,
    ), mock.patch.object(
        live_snapshot,
        "get_live_snapshot_signal",
        live_writer.get_signal,
    ), mock.patch.dict(
        os.environ,
        {live_snapshot.LIVE_SNAPSHOT_DIR_ENV: str(live_writer.path.parent)},
    ), mock.patch.object(
        history_module,
        "_cycle_snapshots_file",
        lambda: history_writer.path,
    ), mock.patch.object(
        history_module,
        "_file_marker",
        _replay_file_marker,
    ), mock.patch.object(
        history_module,
        "_build_symbol_histories",
        _replay_build_symbol_histories,
    ):
        results = scan_target_symbols(
            settings=settings,
            token="replay",
            portfolio_snapshot=portfolio_snapshot,
            symbols=scan_symbols,
            price_data_by_symbol=payloads,
            allow_inline_quote_fetch=False,
        )

    # ⑦ append THIS tick's observations for future ticks (post-scan): gate1-pass
    # top N (by score) + sim-held symbols only.
    _append_tick_history(
        t=t,
        results=results,
        sim=sim,
        obs_by_symbol=obs_by_symbol,
        prev_close_by_symbol=prev_close_by_symbol,
        history_writer=history_writer,
    )
    return results


def _balance_response_from_sim(sim, obs_by_symbol) -> dict:
    """Render the sim's holdings + cash as the minimal KIS balance-response
    shape (replicated from tests/test_lane_scheduler_no_hooks.py::_balance_response
    — research must not import from tests/). ``output1`` = positions,
    ``output2[0]`` = deposit summary."""
    positions = getattr(sim, "positions", {}) if sim is not None else {}
    cash = int(getattr(sim, "cash", 0) or 0) if sim is not None else 0
    output1: list[dict] = []
    total_market_value = 0
    for symbol, position in positions.items():
        obs = obs_by_symbol.get(symbol)
        current_price = int(obs.close) if obs is not None else int(position.avg_price)
        qty = int(position.qty)
        market_value = current_price * qty
        cost_value = int(position.avg_price) * qty
        total_market_value += market_value
        output1.append(
            {
                "pdno": symbol,
                "hldg_qty": str(qty),
                "pchs_avg_pric": str(int(position.avg_price)),
                "prpr": str(current_price),
                "evlu_amt": str(market_value),
                "evlu_pfls_amt": str(market_value - cost_value),
                "evlu_pfls_rt": (
                    f"{(current_price / position.avg_price - 1) * 100:.2f}"
                    if position.avg_price
                    else "0.0"
                ),
            }
        )
    return {
        "rt_cd": "0",
        "output1": output1,
        "output2": [
            {
                "dnca_tot_amt": str(cash),
                "prvs_rcdl_excc_amt": str(cash),
                "nxdy_excc_amt": str(cash),
                "tot_evlu_amt": str(cash + total_market_value),
            }
        ],
    }


def _append_tick_history(
    *,
    t: datetime,
    results,
    sim,
    obs_by_symbol,
    prev_close_by_symbol,
    history_writer: ReplayHistoryWriter,
) -> None:
    held = set(getattr(sim, "positions", {}) or {}) if sim is not None else set()
    gate1 = sorted(
        (r for r in results if _gate1_passed(r)),
        key=lambda r: float(getattr(r, "score", 0.0) or 0.0),
        reverse=True,
    )
    recorded: dict[str, object] = {}
    for result in gate1[:_HISTORY_GATE1_TOP_N]:
        recorded[result.symbol] = obs_by_symbol.get(result.symbol)
    for symbol in held:
        recorded.setdefault(symbol, obs_by_symbol.get(symbol))

    rows: list[dict] = []
    for symbol, obs in recorded.items():
        row = _observation_row(obs, prev_close_by_symbol.get(symbol))
        if row is not None:
            rows.append(row)
    history_writer.append_tick(t, rows)
