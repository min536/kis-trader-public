"""CLI: shadow_watch

Monitor shadow_candidate proposals by re-running their backtests on the
current date window and comparing against the original evaluation.

Purpose:
  After a proposal is promoted to shadow_candidate (human approval), market
  conditions may drift.  This tool detects "regime drift" — whether the
  candidate still looks promising relative to the baseline, or whether the
  original eval was a statistical fluke.

Flow:
  1. Load registry → find all shadow_candidate proposals
  2. For each: re-run candidate YAML + baseline YAML on a RECENT date window
  3. Compare new sharpe/mdd/win_rate deltas vs original evaluation
  4. Flag drift if the new delta diverges significantly from the original
  5. Emit a compact per-proposal report + overall recommendation

Output:
  - Prints a compact terminal report
  - Writes JSON report to research/shadow_watch/ by default

This is research-only tooling. It never changes live parameters.
Backend: REST API (http://localhost:8002) — MCP not used here.

Usage:
    python3 -m app.tools.shadow_watch

    python3 -m app.tools.shadow_watch \\
        --registry research/proposal_registry.json \\
        --output-dir research/shadow_watch \\
        --window-days 30 \\
        --bt-url http://localhost:8002

    # Skip backtester calls (use cached summaries if available)
    python3 -m app.tools.shadow_watch --skip-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.costs import LEGACY_BACKTEST_COST_PARAMS

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REGISTRY = _PROJECT_ROOT / "research" / "proposal_registry.json"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "research" / "shadow_watch"
_DEFAULT_BT_URL = "http://localhost:8002"
_BT_STRATEGIES = _PROJECT_ROOT / "backtester" / "strategies"
_BT_REPORTS = _PROJECT_ROOT / "backtester" / "reports"

# Drift thresholds
_SHARPE_DRIFT_THRESHOLD = 0.4   # absolute change in sharpe_delta triggers drift flag
_MDD_DRIFT_THRESHOLD = 3.0      # absolute change in mdd_delta (pp) triggers drift flag

# Minimum rerun count before declaring consider_reject
# (avoids snap-judgements from a single bad-luck window)
_MIN_RERUNS_FOR_REJECT = 3

# Days in shadow before escalating "watch" → "consider_reject"
_MAX_WATCH_DAYS_BEFORE_ESCALATE = 14


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _shadow_candidates(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e for e in (registry.get("proposals") or {}).values()
        if e.get("status") == "shadow_candidate"
    ]


def _promoted_at(entry: dict[str, Any]) -> str:
    """Return ISO timestamp when the proposal was promoted to shadow_candidate."""
    for h in reversed(entry.get("status_history") or []):
        if h.get("status") == "shadow_candidate":
            return h.get("at", "")
    return entry.get("registered_at", "")


def _shadow_days(entry: dict[str, Any]) -> int:
    promoted = _promoted_at(entry)
    if not promoted:
        return 0
    try:
        dt = datetime.fromisoformat(promoted)
        return (datetime.now() - dt).days
    except ValueError:
        return 0


def _past_rerun_count(proposal_id: str, output_dir: Path) -> int:
    """Count how many previous shadow_watch JSON reports mention this proposal_id."""
    if not output_dir.exists():
        return 0
    count = 0
    for path in sorted(output_dir.glob("shadow_report_*.json")):
        try:
            results = json.loads(path.read_text(encoding="utf-8"))
            for r in results:
                if r.get("proposal_id") == proposal_id and r.get("current_rerun", {}).get("run_ok"):
                    count += 1
                    break
        except (json.JSONDecodeError, OSError, TypeError):
            continue
    return count


# ---------------------------------------------------------------------------
# Backtester REST call
# ---------------------------------------------------------------------------


def _run_bt_api(
    yaml_content: str,
    start_date: str,
    end_date: str,
    bt_url: str,
    timeout: int = 600,
    initial_capital: int = 100_000_000,
) -> tuple[bool, dict[str, Any]]:
    payload = json.dumps({
        "yaml_content": yaml_content,
        "symbols": ["005930", "000660", "035720", "005380", "051910", "035420"],
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": int(initial_capital),
        # value-preserving single source (was inline 0.00015 / 0.0023 / 0.001);
        # operator-gated migration to canonical settings policy — see costs.py
        **LEGACY_BACKTEST_COST_PARAMS,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{bt_url.rstrip('/')}/api/backtest/run-custom",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"error": str(exc)}
        return False, detail
    except Exception as exc:
        return False, {"error": str(exc)}


def _extract_metrics(api_response: dict[str, Any]) -> dict[str, Any]:
    """Pull compact metrics from API response."""
    data = api_response.get("data") or {}
    metrics = data.get("metrics") or {}
    basic = metrics.get("basic") or {}
    risk = metrics.get("risk") or {}
    trading = metrics.get("trading") or {}

    def _f(d: dict, k: str) -> float | None:
        v = d.get(k)
        return round(float(v), 3) if v is not None else None

    total_return = _f(basic, "total_return")
    if total_return is None:
        r = data.get("net_profit_percent")
        total_return = round(float(r), 3) if r is not None else None

    return {
        "total_return": total_return,
        "sharpe": _f(risk, "sharpe_ratio"),
        "max_drawdown": _f(basic, "max_drawdown"),
        "win_rate": _f(trading, "win_rate"),
        "trade_count": trading.get("total_orders"),
    }


# ---------------------------------------------------------------------------
# Per-proposal shadow evaluation
# ---------------------------------------------------------------------------


def _evaluate_shadow_proposal(
    entry: dict[str, Any],
    start_date: str,
    end_date: str,
    bt_url: str,
    skip_run: bool,
    output_dir: Path,
    initial_capital: int = 100_000_000,
) -> dict[str, Any]:
    proposal_id = entry["proposal_id"]
    changes = entry.get("changes") or []
    families = list({c["family"] for c in changes})

    if not families:
        return _error_result(proposal_id, "no changes in proposal")

    family = families[0]
    candidate_yaml_name = f"kis_trader_{family}_candidate.kis.yaml"
    baseline_yaml_name = f"kis_trader_{family}.kis.yaml"
    candidate_yaml_path = _BT_STRATEGIES / candidate_yaml_name
    baseline_yaml_path = _BT_STRATEGIES / baseline_yaml_name

    if not candidate_yaml_path.exists():
        return _error_result(proposal_id, f"candidate YAML not found: {candidate_yaml_path}")
    if not baseline_yaml_path.exists():
        return _error_result(proposal_id, f"baseline YAML not found: {baseline_yaml_path}")

    # Original evaluation deltas
    orig_eval = entry.get("evaluation") or {}
    orig_evals = orig_eval.get("evaluations") or []
    orig_deltas = (orig_evals[0].get("deltas") or {}) if orig_evals else {}
    orig_verdict = orig_eval.get("overall_verdict", "—")

    # Cache paths for current window
    cache_key = f"shadow_{proposal_id}_{start_date}_{end_date}"
    candidate_cache = output_dir / f"{cache_key}_candidate.json"
    baseline_cache = output_dir / f"{cache_key}_baseline.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_ok = False
    candidate_metrics: dict[str, Any] = {}
    baseline_metrics: dict[str, Any] = {}

    if skip_run:
        # Try to load from cache
        if candidate_cache.exists():
            candidate_metrics = _load_json(candidate_cache)
        if baseline_cache.exists():
            baseline_metrics = _load_json(baseline_cache)
    else:
        print(f"    running candidate backtest ({start_date}→{end_date})...", file=sys.stderr)
        ok, resp = _run_bt_api(
            candidate_yaml_path.read_text(encoding="utf-8"),
            start_date, end_date, bt_url,
            initial_capital=initial_capital,
        )
        if ok and resp.get("success"):
            candidate_metrics = _extract_metrics(resp)
            candidate_cache.write_text(json.dumps(candidate_metrics, indent=2), encoding="utf-8")
        else:
            err = (resp.get("detail") or resp.get("error") or "unknown")
            print(f"    candidate API error: {err}", file=sys.stderr)

        print(f"    running baseline backtest ({start_date}→{end_date})...", file=sys.stderr)
        ok2, resp2 = _run_bt_api(
            baseline_yaml_path.read_text(encoding="utf-8"),
            start_date, end_date, bt_url,
            initial_capital=initial_capital,
        )
        if ok2 and resp2.get("success"):
            baseline_metrics = _extract_metrics(resp2)
            baseline_cache.write_text(json.dumps(baseline_metrics, indent=2), encoding="utf-8")
            run_ok = True
        else:
            err = (resp2.get("detail") or resp2.get("error") or "unknown")
            print(f"    baseline API error: {err}", file=sys.stderr)

    # Compute current deltas
    def _delta(key: str) -> float | None:
        c = candidate_metrics.get(key)
        b = baseline_metrics.get(key)
        if c is None or b is None:
            return None
        return round(float(c) - float(b), 3)

    current_sharpe_delta = _delta("sharpe")
    current_mdd_delta = _delta("max_drawdown")
    current_return_delta = _delta("total_return")
    current_win_delta = _delta("win_rate")

    # Pass/fail on current window
    sharpe_pass = current_sharpe_delta is not None and current_sharpe_delta > 0
    mdd_pass = current_mdd_delta is None or current_mdd_delta <= 2.0
    current_verdict = "pass" if (sharpe_pass and mdd_pass) else "fail"
    if not candidate_metrics or not baseline_metrics:
        current_verdict = "no_data"

    # Drift detection
    orig_sharpe_delta = orig_deltas.get("sharpe")
    orig_mdd_delta = orig_deltas.get("max_drawdown")
    drift_flags: list[str] = []

    if (orig_sharpe_delta is not None and current_sharpe_delta is not None
            and abs(current_sharpe_delta - orig_sharpe_delta) > _SHARPE_DRIFT_THRESHOLD):
        direction = "improved" if current_sharpe_delta > orig_sharpe_delta else "worsened"
        drift_flags.append(
            f"sharpe_delta {direction}: {orig_sharpe_delta:+.3f} → {current_sharpe_delta:+.3f}"
        )

    if (orig_mdd_delta is not None and current_mdd_delta is not None
            and abs(current_mdd_delta - orig_mdd_delta) > _MDD_DRIFT_THRESHOLD):
        direction = "worsened" if current_mdd_delta > orig_mdd_delta else "improved"
        drift_flags.append(
            f"mdd_delta {direction}: {orig_mdd_delta:+.3f} → {current_mdd_delta:+.3f}"
        )

    # Evidence quality: how many successful reruns have we accumulated?
    past_reruns = _past_rerun_count(proposal_id, output_dir)
    shadow_age = _shadow_days(entry)
    evidence_sufficient = past_reruns >= _MIN_RERUNS_FOR_REJECT

    # Recommendation — gated by evidence quality
    if current_verdict == "no_data":
        recommendation = "rerun"
    elif current_verdict == "pass" and orig_verdict == "fail":
        recommendation = "consider_promote"   # was fail, now pass — regime improved
    elif current_verdict == "pass":
        if shadow_age >= _MAX_WATCH_DAYS_BEFORE_ESCALATE and evidence_sufficient:
            recommendation = "consider_promote"   # long enough watch + sufficient evidence
        else:
            recommendation = "watch"              # still passing — continue observing
    elif orig_verdict == "pass" and current_verdict == "fail":
        if evidence_sufficient:
            recommendation = "consider_reject"    # enough reruns confirm regime drift
        else:
            recommendation = "watch"              # single failure — not enough data yet
    else:
        if shadow_age >= _MAX_WATCH_DAYS_BEFORE_ESCALATE and evidence_sufficient:
            recommendation = "consider_reject"    # long time, always failing
        else:
            recommendation = "watch"              # both fail but evidence thin — keep watching

    return {
        "proposal_id": proposal_id,
        "status": "shadow_candidate",
        "direction": entry.get("direction", ""),
        "changes": changes,
        "shadow_days": shadow_age,
        "past_reruns": past_reruns,
        "evidence_sufficient": evidence_sufficient,
        "promoted_at": _promoted_at(entry),
        "observation_window": {"start": start_date, "end": end_date},
        "original_eval": {
            "verdict": orig_verdict,
            "sharpe_delta": orig_sharpe_delta,
            "mdd_delta": orig_mdd_delta,
            "return_delta": orig_deltas.get("total_return"),
        },
        "current_rerun": {
            "run_ok": run_ok or bool(candidate_metrics and baseline_metrics),
            "candidate": candidate_metrics,
            "baseline": baseline_metrics,
            "deltas": {
                "sharpe": current_sharpe_delta,
                "max_drawdown": current_mdd_delta,
                "total_return": current_return_delta,
                "win_rate": current_win_delta,
            },
            "verdict": current_verdict,
        },
        "drift_detected": bool(drift_flags),
        "drift_flags": drift_flags,
        "recommendation": recommendation,
    }


def _error_result(proposal_id: str, reason: str) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "status": "shadow_candidate",
        "error": reason,
        "recommendation": "rerun",
    }


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------

_VERDICT_ICON = {"pass": "✓", "fail": "✗", "no_data": "?", "—": "—"}
_RECOM_ICON = {
    "consider_promote": "▲ consider_promote",
    "consider_reject": "▼ consider_reject",
    "watch": "◉ watch",
    "rerun": "↺ rerun",
}


def _print_report(results: list[dict[str, Any]]) -> None:
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Shadow Watch Report  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"  {len(results)} shadow candidate(s)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    for r in results:
        pid = r["proposal_id"]
        print(f"\n  {pid}")
        if r.get("error"):
            print(f"    ERROR: {r['error']}")
            continue

        changes_str = ", ".join(
            f"{c['param_id']}:{c['current_value']}→{c['new_value']}"
            for c in r.get("changes", [])
        )
        print(f"    direction : {r.get('direction','')}  [{changes_str}]")
        reruns = r.get("past_reruns", 0)
        sufficient = "✓" if r.get("evidence_sufficient") else f"✗ (need {_MIN_RERUNS_FOR_REJECT})"
        print(f"    shadow    : {r.get('shadow_days', 0)} days  reruns={reruns} evidence={sufficient}  (promoted {r.get('promoted_at','')[:10]})")

        window = r.get("observation_window") or {}
        print(f"    window    : {window.get('start','')} → {window.get('end','')}")

        orig = r.get("original_eval") or {}
        cur = r.get("current_rerun") or {}
        deltas = cur.get("deltas") or {}

        def _fmt(v: Any) -> str:
            if v is None:
                return "—"
            return f"{v:+.3f}" if isinstance(v, float) else str(v)

        orig_v = _VERDICT_ICON.get(orig.get("verdict", "—"), "?")
        cur_v = _VERDICT_ICON.get(cur.get("verdict", "—"), "?")
        print(f"    orig eval : {orig_v}  Δsharpe={_fmt(orig.get('sharpe_delta'))}  "
              f"Δmdd={_fmt(orig.get('mdd_delta'))}")
        print(f"    current   : {cur_v}  Δsharpe={_fmt(deltas.get('sharpe'))}  "
              f"Δmdd={_fmt(deltas.get('max_drawdown'))}  "
              f"Δreturn={_fmt(deltas.get('total_return'))}")

        if r.get("drift_detected"):
            print(f"    ⚠ drift   :", end="")
            for flag in r.get("drift_flags", []):
                print(f" {flag}", end="")
            print()

        recom = _RECOM_ICON.get(r.get("recommendation", ""), r.get("recommendation", ""))
        print(f"    → {recom}")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_shadow_watch(
    registry_path: Path,
    output_dir: Path,
    window_days: int,
    bt_url: str,
    skip_run: bool,
    initial_capital: int = 100_000_000,
) -> list[dict[str, Any]]:
    registry = _load_json(registry_path)
    candidates = _shadow_candidates(registry)

    if not candidates:
        print("  No shadow_candidate proposals found in registry.", file=sys.stderr)
        return []

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")

    results: list[dict[str, Any]] = []
    for entry in candidates:
        pid = entry["proposal_id"]
        print(f"\n[shadow_watch] evaluating {pid}...", file=sys.stderr)
        result = _evaluate_shadow_proposal(
            entry, start_date, end_date, bt_url, skip_run, output_dir,
            initial_capital=initial_capital,
        )
        results.append(result)

    # Write combined report
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"shadow_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport written → {report_path}", file=sys.stderr)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor shadow_candidate proposals via periodic re-backtesting"
    )
    parser.add_argument(
        "--registry",
        default=str(_DEFAULT_REGISTRY),
        help=f"Path to proposal registry JSON (default: {_DEFAULT_REGISTRY})",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=f"Directory for shadow watch reports (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Number of recent days to use for re-backtest window (default: 30)",
    )
    parser.add_argument(
        "--bt-url",
        default=_DEFAULT_BT_URL,
        help=f"Backtester REST API base URL (default: {_DEFAULT_BT_URL})",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Skip backtester API calls; use cached results from output-dir if available",
    )
    parser.add_argument(
        "--initial-capital",
        type=int,
        default=100_000_000,
        help=(
            "Backtest seed capital in KRW (default: 100,000,000). Set this to the "
            "current competition seed so the shadow comparison's capital base "
            "matches the live account (see docs/competition_reset_design §RR-4)."
        ),
    )
    args = parser.parse_args()

    results = run_shadow_watch(
        registry_path=Path(args.registry),
        output_dir=Path(args.output_dir),
        window_days=args.window_days,
        bt_url=args.bt_url,
        skip_run=args.skip_run,
        initial_capital=args.initial_capital,
    )

    _print_report(results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
