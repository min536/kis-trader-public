"""Slice F2-d — replay ↔ live parity gate (operator-run, CI-skipped).

Purpose: prove the replay driver reconstructs the SAME ``score_components`` the
live runtime recorded, so gate2 weight search (R1+) tunes on faithful features.

This test is a **gate**, not a unit test: it can only run against a real
account's cycle-snapshots JSONL, which is not present in CI. The operator points
``GATE2_PARITY_SNAPSHOT_PATH`` at that JSONL and runs it ONCE, recording the
resulting table in ``docs/gate2_minute_backtest_plan_20260702.md`` ("패리티
결과"). For long accumulated JSONL files, the gate defaults to the latest
intraday date with candidates; operators can pin a specific session with
``GATE2_PARITY_DATE=YYYY-MM-DD``. Without the path env var the test SKIPS
cleanly (import-safe, no real-data dependency) — see plan §6 F2-d / §10-②.

Reconstruction fidelity (plan §6 F2-d tolerances):

- **Exact match** — the price-pattern strength scores (pullback / rebound /
  controlled-down / gap-down / range-recovery) and the gap/range ratios are pure
  functions of the recorded ``market_snapshot`` (open / low / current /
  prev_day_change), so a store-free replay reproduces them byte-for-byte.
- **±5 or discrepancy table (deliverable, NOT a failure)** — history-dependent
  (trend / macd / mean_reversion / velocity) and portfolio scores, plus the two
  LIVE-rank strength scores, depend on runtime stores (price history, portfolio,
  live_snapshot ranks) that the serialized snapshot does NOT carry, so a
  store-free replay cannot reproduce them. Per the plan tolerance ("±5 이내
  **또는** 불일치 사유 표") the table is the ALTERNATIVE ACCEPTANCE path:
  entries beyond ±5 do NOT fail this test — they are written to
  ``results/gate2_backtest/parity_table.txt`` and printed to stdout (run with
  ``-s``). Under store-free reconstruction a NON-EMPTY table is the expected
  healthy outcome for these keys (live nonzero vs replay 0.0), so failing on it
  would make the operator run structurally unpassable. The operator records the
  table in the plan doc's "패리티 결과" section; §10-②'s report-and-wait happens
  at the process level (R1 start is gated on review of that table, not on an
  assertion here).

Fixture path (plan §6 F2-d): the account-scope resolution
(``get_cycle_snapshots_path`` needs a base_url) is NOT reproduced here — the
operator supplies the resolved JSONL path directly via the env var.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

_PARITY_ENV = "GATE2_PARITY_SNAPSHOT_PATH"
_PARITY_DATE_ENV = "GATE2_PARITY_DATE"

# Intraday window (KST) — accumulated cycle-snapshots files can span multiple
# tuning profiles, so parity uses one recent intraday session instead of the
# oldest rows in the file.
_SESSION_OPEN = "09:00"
_SESSION_CLOSE = "15:30"
_MAX_ROWS = 50  # M ≤ 50

# Exact-match score_components: pure functions of the recorded snapshot.
_EXACT_KEYS: tuple[str, ...] = (
    "intraday_pullback_strength_score",
    "rebound_from_low_strength_score",
    "controlled_down_strength_score",
    "gap_down_open_strength_score",
    "range_recovery_strength_score",
    "pullback_pct",
    "rebound_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
    "gap_up_open_pct",
)

# Tolerance score_components: depend on runtime stores the serialized snapshot
# does not carry (history / portfolio / live-rank). Overflow beyond ±5 is
# TABLED as a deliverable (file + stdout), never a test failure — only the
# _EXACT_KEYS above are the hard gate.
_TOLERANCE_KEYS: tuple[str, ...] = (
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "mean_reversion_bonus",
    "velocity_bonus",
    "velocity_penalty",
    "diversification_bonus",
    "portfolio_correlation_penalty",
    "variance_increase_penalty",
    # Live-rank strength scores — snapshot drops live ranks, so a store-free
    # replay yields 0.0 here; a live 100.0 is an expected reconstruction gap.
    "live_volume_rank_strength_score",
    "live_volume_power_rank_strength_score",
)
_TOLERANCE = 5.0


def _hhmm(dt_text: str) -> str:
    """Return the ``HH:MM`` of an ISO-ish timestamp string ('...T09:31:00')."""
    time_part = dt_text.split("T", 1)[1] if "T" in dt_text else dt_text.split(" ", 1)[-1]
    return time_part[:5]


def _date_text(dt_text: str) -> str:
    """Return the ``YYYY-MM-DD`` of an ISO-ish timestamp string."""
    return dt_text.split("T", 1)[0].split(" ", 1)[0]


def _is_intraday_candidate_record(record: dict) -> bool:
    timestamp = str(record.get("timestamp") or "")
    if not timestamp:
        return False
    hhmm = _hhmm(timestamp)
    if not (_SESSION_OPEN <= hhmm <= _SESSION_CLOSE):
        return False
    candidates = record.get("scanner_candidates_top")
    return isinstance(candidates, list) and bool(candidates)


def _load_jsonl_record(raw_line: str) -> dict | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    return record


def _latest_intraday_candidate_date(path: Path) -> str | None:
    """Return the latest date that has intraday scanner candidates.

    The file is streamed and never loaded wholesale; assigning as we walk keeps
    the latest chronological date for normal append-only snapshots, and still
    works for mixed files because date strings sort lexicographically.
    """
    latest: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            record = _load_jsonl_record(raw_line)
            if record is None or not _is_intraday_candidate_record(record):
                continue
            date_text = _date_text(str(record.get("timestamp") or ""))
            if date_text and (latest is None or date_text > latest):
                latest = date_text
    return latest


def _resolve_parity_date(path: Path) -> str | None:
    requested = os.getenv(_PARITY_DATE_ENV, "").strip()
    if requested:
        return requested
    return _latest_intraday_candidate_date(path)


def _iter_intraday_candidate_rows(path: Path, *, limit: int, target_date: str | None):
    """Stream up to ``limit`` intraday candidate records for ``target_date``.

    Passing a date keeps the gate on one live-session profile. This matters for
    long accumulated JSONL files because older sessions may have been produced
    under different scoring knobs.
    """
    if not target_date:
        return
    taken = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            record = _load_jsonl_record(raw_line)
            if record is None or not _is_intraday_candidate_record(record):
                continue
            timestamp = str(record.get("timestamp") or "")
            if _date_text(timestamp) != target_date:
                continue
            yield record
            taken += 1
            if taken >= limit:
                return


def _synth_payload_from_snapshot(snapshot: dict) -> dict | None:
    """Build a KIS payload from a serialized ``market_snapshot`` dict.

    ``stck_hgpr`` is not recorded and is not read by the exact-match scores, so
    it is filled with ``max(current, open)`` (harmless for parity)."""
    symbol = str(snapshot.get("symbol") or "").strip()
    current = int(snapshot.get("current_price") or 0)
    open_price = int(snapshot.get("open_price") or 0)
    low_price = int(snapshot.get("low_price") or 0)
    if not symbol or current <= 0 or open_price <= 0:
        return None
    return {
        "rt_cd": "0",
        "output": {
            "stck_shrn_iscd": symbol,
            "stck_prpr": str(current),
            "stck_oprc": str(open_price),
            "stck_hgpr": str(max(current, open_price)),
            "stck_lwpr": str(low_price),
            "prdy_ctrt": f"{float(snapshot.get('prev_day_change_pct') or 0.0):.2f}",
        },
    }


def _reconstruct_score_components(payload: dict, tmp_path: Path) -> dict:
    """Run the REAL scanner for the single candidate with all runtime stores
    empty (no live snapshot, no history, empty portfolio), returning its
    ``score_components``. Store-free reconstruction isolates the store-independent
    (exact-match) categories."""
    import app.market_data.live_snapshot as live_snapshot
    import app.math_models.history as history_module
    from app.portfolio.schema import build_portfolio_snapshot
    from app.research.replay.scan_driver import replay_settings
    from app.scanner.service import scan_target_symbols

    empty_dir = tmp_path / "empty_live"
    empty_dir.mkdir(exist_ok=True)
    empty_history = tmp_path / "empty_history.jsonl"
    empty_history.write_text("")
    symbol = payload["output"]["stck_shrn_iscd"]

    history_module._build_symbol_histories.cache_clear()
    with mock.patch.object(
        live_snapshot, "SNAPSHOT_PATH", empty_dir / "live_snapshot.json"
    ), mock.patch.dict(
        os.environ, {live_snapshot.LIVE_SNAPSHOT_DIR_ENV: str(empty_dir)}
    ), mock.patch.object(
        history_module, "_cycle_snapshots_file", lambda: empty_history
    ):
        results = scan_target_symbols(
            settings=replay_settings(),
            token="parity",
            portfolio_snapshot=build_portfolio_snapshot({"output1": [], "output2": []}),
            symbols=(symbol,),
            price_data_by_symbol={symbol: payload},
            allow_inline_quote_fetch=False,
        )
    for result in results:
        if result.symbol == symbol:
            return dict(result.score_components or {})
    return {}


def test_parity_date_selection_defaults_to_latest_intraday_candidate_date(tmp_path):
    path = tmp_path / "cycle_snapshots.jsonl"
    records = [
        {"timestamp": "2026-06-04T09:00:00+09:00", "scanner_candidates_top": [{}]},
        {"timestamp": "2026-06-30T08:59:59+09:00", "scanner_candidates_top": [{}]},
        {"timestamp": "2026-06-29T09:00:00+09:00", "scanner_candidates_top": [{}]},
        {"timestamp": "2026-06-30T15:29:59+09:00", "scanner_candidates_top": [{}]},
        {"timestamp": "2026-06-30T15:31:00+09:00", "scanner_candidates_top": [{}]},
        {"timestamp": "2026-07-01T09:00:00+09:00", "scanner_candidates_top": []},
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    assert _latest_intraday_candidate_date(path) == "2026-06-30"
    selected_rows = list(
        _iter_intraday_candidate_rows(path, limit=10, target_date="2026-06-30")
    )
    assert [row["timestamp"] for row in selected_rows] == [
        "2026-06-30T15:29:59+09:00"
    ]


@pytest.mark.skipif(
    not os.getenv(_PARITY_ENV),
    reason=f"parity gate is operator-run: set {_PARITY_ENV} to a real cycle-snapshots JSONL",
)
def test_replay_reconstructs_live_score_components(tmp_path):
    """Compare replay-reconstructed ``score_components`` to the live-recorded
    ones for the first ≤50 intraday candidate rows.

    HARD GATE (assertion): at least one candidate compared, and the exact-match
    keys (price-pattern strengths + gap/range ratios) are byte-identical.

    TABLED DELIVERABLE (no assertion): store-dependent keys beyond ±5 are
    collected into a discrepancy table written to
    ``results/gate2_backtest/parity_table.txt`` and printed to stdout. This
    table is EXPECTED to be non-empty for the history/portfolio/live-rank keys
    under store-free reconstruction — it is the plan's alternative acceptance
    path ("±5 이내 또는 불일치 사유 표"), reviewed by the operator before R1
    starts (§10-② report-and-wait at the process level, not here).
    """
    path = Path(os.environ[_PARITY_ENV]).expanduser()
    assert path.exists(), f"{_PARITY_ENV} does not exist: {path}"
    target_date = _resolve_parity_date(path)

    exact_mismatches: list[str] = []
    tolerance_table: list[str] = []
    compared_candidates = 0

    for record in _iter_intraday_candidate_rows(
        path,
        limit=_MAX_ROWS,
        target_date=target_date,
    ):
        timestamp = str(record.get("timestamp") or "")
        for candidate in record["scanner_candidates_top"]:
            if not isinstance(candidate, dict):
                continue
            snapshot = candidate.get("market_snapshot")
            recorded = candidate.get("score_components")
            if not isinstance(snapshot, dict) or not isinstance(recorded, dict):
                continue
            payload = _synth_payload_from_snapshot(snapshot)
            if payload is None:
                continue
            replayed = _reconstruct_score_components(payload, tmp_path)
            if not replayed:
                continue
            compared_candidates += 1
            symbol = str(candidate.get("symbol") or snapshot.get("symbol") or "")

            for key in _EXACT_KEYS:
                if key not in recorded or key not in replayed:
                    continue
                if abs(float(recorded[key]) - float(replayed[key])) > 1e-6:
                    exact_mismatches.append(
                        f"{timestamp} {symbol} {key}: "
                        f"live={recorded[key]} replay={replayed[key]}"
                    )
            for key in _TOLERANCE_KEYS:
                if key not in recorded or key not in replayed:
                    continue
                delta = abs(float(recorded[key]) - float(replayed[key]))
                if delta > _TOLERANCE:
                    tolerance_table.append(
                        f"{timestamp} {symbol} {key}: "
                        f"live={recorded[key]} replay={replayed[key]} Δ={delta:.2f}"
                    )

    assert compared_candidates > 0, (
        "no intraday candidate rows compared — check the JSONL path / contents"
    )

    # --- tolerance table: DELIVERABLE, not a failure. Written for the operator
    # to record in the plan doc's "패리티 결과" section; expected NON-EMPTY for
    # the history/portfolio/live-rank keys under store-free reconstruction
    # (live nonzero vs replay 0.0). R1 start is gated on review of this table
    # at the process level (§10-②), not on an assertion here.
    table_path = (
        Path(__file__).resolve().parents[1]
        / "results"
        / "gate2_backtest"
        / "parity_table.txt"
    )
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_lines = [
        f"F2-d parity run — source={path} date={target_date or '(none)'}",
        f"compared_candidates={compared_candidates} "
        f"exact_mismatches={len(exact_mismatches)} "
        f"tolerance_exceeded(±{_TOLERANCE:.0f})={len(tolerance_table)}",
        "",
        f"±{_TOLERANCE:.0f} DISCREPANCY TABLE (alternative acceptance path — "
        "record in plan '패리티 결과', do not tune):",
    ]
    if tolerance_table:
        table_lines.extend(f"  {line}" for line in tolerance_table)
    else:
        table_lines.append("  (no tolerance-exceeded entries)")
    if exact_mismatches:
        table_lines.append("")
        table_lines.append("EXACT-MATCH MISMATCHES (hard-gate failures):")
        table_lines.extend(f"  {line}" for line in exact_mismatches)
    table_text = "\n".join(table_lines) + "\n"
    table_path.write_text(table_text)
    print(f"\n[parity] tolerance table -> {table_path}\n{table_text}")

    # --- hard gate: exact-match categories only.
    assert not exact_mismatches, (
        f"replay/live parity EXACT-match failed over {compared_candidates} "
        "candidates (store-independent scores must be byte-identical):\n"
        + "\n".join(f"  {line}" for line in exact_mismatches)
    )
