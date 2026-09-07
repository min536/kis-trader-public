"""CLI: run_proposal_backtest

Apply a research proposal's parameter changes to a backtester YAML,
run the backtester via the live API, and compare against baseline.

Flow:
  1. Load proposal JSON
  2. Validate constraints (refuse if invalid)
  3. For each changed family, load the base YAML and apply parameter changes
  4. POST to backtester /api/backtest/run-custom (Docker-based Lean runner)
  5. Parse response and compare candidate vs baseline
  6. Write evaluation JSON (compact summary metrics only — no equity_curve)

This is research-only tooling. It never touches live trading settings.

─────────────────────────────────────────────────────────────────────────────
BACKEND POLICY
─────────────────────────────────────────────────────────────────────────────
DEFAULT (automated research loop): REST API via direct HTTP to localhost:8002
  - POST /api/backtest/run-custom with YAML content
  - response is trimmed to compact summary metrics only
  - large arrays (equity_curve, trades) are NOT stored in the evaluation JSON
  - this keeps evaluation files small and avoids LLM-context bloat

MANUAL / INTERACTIVE: MCP tools (mcp__kis-backtest__*)
  - use for ad-hoc exploration, YAML validation, direct result inspection
  - MCP returns full payloads including equity_curve — NOT suitable for
    automated loops due to LLM-context cost and repeated-call overhead
  - to use MCP interactively: call mcp__kis-backtest__run_backtest_tool
    directly from a Claude Code session

This file MUST NOT import MCP tooling or depend on it at runtime.
─────────────────────────────────────────────────────────────────────────────

Usage:
    python3 -m app.tools.run_proposal_backtest \\
        --proposal research/proposals/proposal_20260412_v1.json

    python3 -m app.tools.run_proposal_backtest \\
        --proposal research/proposals/proposal_20260412_v1.json \\
        --output-dir research/evaluations/ \\
        --bt-url http://localhost:8002

    # Save full raw API response in addition to compact summary
    python3 -m app.tools.run_proposal_backtest \\
        --proposal research/proposals/proposal_20260412_v1.json \\
        --full-output

    # Skip the actual backtester call (offline mode)
    python3 -m app.tools.run_proposal_backtest \\
        --proposal research/proposals/proposal_20260412_v1.json \\
        --skip-run
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.costs import LEGACY_BACKTEST_COST_PARAMS
from app.integrations.open_trading_api import is_loopback_url, resolve_backtester_root
from app.tools.proposal_constraints import (
    get_param,
    validate_proposal_changes,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BT_STRATEGIES = _PROJECT_ROOT / "backtester" / "strategies"
_BT_REPORTS = _PROJECT_ROOT / "backtester" / "reports"

# Backtester API default URL
_DEFAULT_BT_URL = "http://localhost:8002"

# Baseline run-ids per family
_FAMILY_TO_BASE_RUN_ID: dict[str, str] = {
    "core_family_approx": "bt_custom_kis_trader_core_family_approx",
    "continuation_family_approx": "bt_custom_kis_trader_continuation_family_approx",
}

# Baseline project configs: symbols / dates pulled from the external Lean workspace


def _otapi_provenance(*, now, repo_head):
    """E1: run-wide provenance for an eval — auditable proxy origin."""
    return {
        "source": "open-trading-api",
        "repo_head": repo_head,
        "generated_at": now.isoformat(),
    }


def _resolve_otapi_repo_head(*, env=None):
    """Best-effort open-trading-api commit hash for eval provenance; None when the
    sibling repo is unavailable. The adapter helpers do not raise on normal errors."""
    import app.integrations.open_trading_api as otapi

    root = otapi.resolve_open_trading_api_root(env=env)
    if not root.available or root.root is None:
        return None
    result = otapi.run_allowed_open_trading_api_command("repo_head", root=root.root, env=env)
    return result.stdout.strip() if result.ok else None


# ---------------------------------------------------------------------------
# YAML helpers (stdlib-only — no PyYAML dependency)
# ---------------------------------------------------------------------------

import re as _re

_INDEX_RE = _re.compile(r"^(.+?)\[(\d+)\]$")


def _load_yaml_simple(path: Path) -> list[str]:
    """Return raw lines of a YAML file for line-patch approach."""
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _patch_yaml_scalar(
    lines: list[str],
    yaml_path: str,
    new_value: float,
) -> list[str]:
    """
    Patch a single scalar value in a YAML line-list.

    Strategy: use `yaml_path` to resolve which field name to look for,
    then walk the lines to find the right context and replace the value.

    For simple leaf paths like "risk.stop_loss.percent" or
    "strategy.entry.conditions[2].value", we resolve the leaf key and search
    for the first unambiguous occurrence under the relevant parent.

    This approach works for the kis.yaml schemas used in this project.
    Returns a new lines list.
    """
    parts = yaml_path.split(".")
    # Resolve the leaf field name (handling array notation)
    raw_leaf = parts[-1]
    m = _INDEX_RE.match(raw_leaf)
    leaf_key = m.group(1) if m else raw_leaf

    # For array-indexed conditions, we also need the index
    array_parent: str | None = None
    array_idx: int | None = None
    if len(parts) >= 2:
        raw_parent = parts[-2]
        pm = _INDEX_RE.match(raw_parent)
        if pm:
            array_parent = pm.group(1)  # e.g. "conditions"
            array_idx = int(pm.group(2))

    result = list(lines)

    if array_idx is not None and array_parent is not None:
        # Find the Nth list item under `array_parent`, then patch `leaf_key`
        parent_line_idx: int | None = None
        item_count = -1
        for i, line in enumerate(result):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if array_parent + ":" in line and not stripped.startswith("#"):
                parent_line_idx = i
                item_count = -1
                continue
            if parent_line_idx is not None and stripped.startswith("-"):
                item_count += 1
                if item_count == array_idx:
                    # Found the right item block — now find `leaf_key` inside it
                    item_indent = indent
                    for j in range(i + 1, min(i + 20, len(result))):
                        jline = result[j]
                        jstripped = jline.lstrip()
                        jind = len(jline) - len(jstripped)
                        if jind <= item_indent and jstripped.startswith("-"):
                            break
                        if jstripped.startswith(leaf_key + ":"):
                            result[j] = _replace_scalar_in_line(result[j], leaf_key, new_value)
                            return result
                    break
    else:
        # Simple key: find leaf_key: <value> line
        for i, line in enumerate(result):
            stripped = line.lstrip()
            if stripped.startswith(leaf_key + ":") and not stripped.startswith("#"):
                result[i] = _replace_scalar_in_line(result[i], leaf_key, new_value)
                return result

    # No match found — append a warning comment (should not happen with valid yaml_path)
    result.append(f"# WARNING: could not patch {yaml_path} = {new_value}\n")
    return result


def _replace_scalar_in_line(line: str, key: str, value: float) -> str:
    """Replace `key: <old>` with `key: <new>` preserving indentation."""
    # Format: preserve int if whole number, else 2 decimal places
    if value == int(value):
        formatted = str(int(value))
    else:
        formatted = f"{value:.4f}".rstrip("0").rstrip(".")
    # Replace the value part after the colon
    colon_idx = line.index(key + ":")
    prefix = line[:colon_idx + len(key) + 1]
    rest = line[colon_idx + len(key) + 1:]
    # Keep trailing newline / comments if present
    stripped_rest = rest.lstrip()
    leading_space = rest[: len(rest) - len(stripped_rest)]
    # Value ends at first whitespace or end
    parts = stripped_rest.split(None, 1)
    comment_part = (" " + parts[1]) if len(parts) > 1 else ""
    return f"{prefix}{leading_space}{formatted}{comment_part}\n"


# ---------------------------------------------------------------------------
# Candidate YAML generation
# ---------------------------------------------------------------------------


def build_candidate_yaml(
    family: str,
    changes: list[dict[str, Any]],
    proposal_id: str,
) -> tuple[Path, str]:
    """
    Load the base YAML for `family`, apply `changes`, write a new candidate
    YAML file, and return (yaml_path, candidate_run_id).

    Uses a line-patch approach so PyYAML is not required.
    """
    base_run_id = _FAMILY_TO_BASE_RUN_ID.get(family)
    if base_run_id is None:
        raise ValueError(f"Unknown family: {family}")

    base_yaml_path = _BT_STRATEGIES / f"kis_trader_{family}.kis.yaml"
    if not base_yaml_path.exists():
        raise FileNotFoundError(f"Base YAML not found: {base_yaml_path}")

    lines = _load_yaml_simple(base_yaml_path)

    # Apply each change via line-patching
    for change in changes:
        if str(change.get("family") or "") != family:
            continue
        param_id = str(change.get("param_id") or "")
        new_value = change.get("new_value")
        spec = get_param(family, param_id)
        if spec is None:
            raise ValueError(f"Param not found in registry: {family}/{param_id}")
        lines = _patch_yaml_scalar(lines, spec.yaml_path, float(new_value))

    # Update name / description / strategy id lines
    short_id = proposal_id[:32] if len(proposal_id) > 32 else proposal_id
    candidate_strategy_name = f"kis_trader_{family}_candidate"
    candidate_run_id = f"bt_custom_{candidate_strategy_name}"

    patched: list[str] = []
    for line in lines:
        if line.lstrip().startswith("name:") and "metadata" not in line:
            # Only patch the first name: line (under metadata)
            pass
        patched.append(line)

    # Simple text replacements for metadata
    text = "".join(lines)
    old_name_line = f"  name: kis_trader_{family}"
    new_name_line = f"  name: {candidate_strategy_name}"
    text = text.replace(old_name_line, new_name_line, 1)

    old_id_line = f"  id: kis_trader_{family}"
    new_id_line = f"  id: {candidate_strategy_name}"
    text = text.replace(old_id_line, new_id_line, 1)

    # Update description
    import re
    text = re.sub(
        r'(  description: ")[^"]*(")',
        f'\\1Candidate derived from {family} by {short_id}. Research-only.\\2',
        text,
    )

    candidate_yaml_path = _BT_STRATEGIES / f"{candidate_strategy_name}.kis.yaml"
    candidate_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_yaml_path.write_text(text, encoding="utf-8")

    return candidate_yaml_path, candidate_run_id


# ---------------------------------------------------------------------------
# Baseline project config loader
# ---------------------------------------------------------------------------


def _load_baseline_config(family: str) -> dict[str, Any]:
    """Load symbols/dates/capital from the existing lean project config.json."""
    run_id = _FAMILY_TO_BASE_RUN_ID.get(family, "")
    backtester_root = resolve_backtester_root()
    if backtester_root.available and backtester_root.root is not None:
        config_path = (
            backtester_root.root
            / ".lean-workspace"
            / "projects"
            / run_id
            / "config.json"
        )
        if config_path.exists():
            try:
                return json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    # Sensible defaults if config not found
    return {
        "parameters": {
            "symbols": "005930,000660,035720,005380,051910,035420",
            "start_date": "2025-10-11",
            "end_date": "2026-04-11",
            "initial_capital": "100000000",
            # value-preserving single source (was "0.00015" / "0.0023")
            "commission_rate": str(LEGACY_BACKTEST_COST_PARAMS["commission_rate"]),
            "tax_rate": str(LEGACY_BACKTEST_COST_PARAMS["tax_rate"]),
        }
    }


# ---------------------------------------------------------------------------
# Backtester API caller
# ---------------------------------------------------------------------------


def _call_bt_api(
    yaml_content: str,
    family: str,
    bt_url: str,
    timeout: int = 600,
) -> tuple[bool, dict[str, Any]]:
    """
    POST to /api/backtest/run-custom and return (success, response_data).
    Uses only stdlib urllib — no requests dependency.
    """
    # Boundary policy (owned by the adapter): the backtester REST endpoint is a
    # localhost-only research service. Refuse any non-loopback URL before any
    # network access (SSRF / accidental-remote guard).
    if not is_loopback_url(bt_url):
        return False, {"error": f"refusing non-loopback backtester URL: {bt_url}"}

    cfg = _load_baseline_config(family)
    params = cfg.get("parameters") or {}

    symbols = [s.strip() for s in str(params.get("symbols", "005930")).split(",") if s.strip()]
    start_date = str(params.get("start_date", "2025-10-11"))
    end_date = str(params.get("end_date", "2026-04-11"))
    initial_capital = float(params.get("initial_capital", 100_000_000))
    # value-preserving single source (defaults were inline 0.00015 / 0.0023, and
    # slippage 0.001); config overrides preserved. Operator-gated migration to
    # canonical settings policy lives in app/core/costs.py.
    commission_rate = float(
        params.get("commission_rate", LEGACY_BACKTEST_COST_PARAMS["commission_rate"])
    )
    tax_rate = float(params.get("tax_rate", LEGACY_BACKTEST_COST_PARAMS["tax_rate"]))

    payload = {
        "yaml_content": yaml_content,
        "symbols": symbols,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "commission_rate": commission_rate,
        "tax_rate": tax_rate,
        "slippage": LEGACY_BACKTEST_COST_PARAMS["slippage"],
    }

    url = f"{bt_url.rstrip('/')}/api/backtest/run-custom"
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
            return True, response_data
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {"error": str(exc)}
        return False, detail
    except Exception as exc:
        return False, {"error": str(exc)}


def _api_response_to_summary(
    response_data: dict[str, Any],
    candidate_run_id: str,
    *,
    full_output: bool = False,
    raw_result_path: Path | None = None,
) -> dict[str, Any]:
    """Convert backtester API response to compact summary metrics.

    The API returns metrics as a nested dict:
        metrics.basic.total_return / max_drawdown
        metrics.risk.sharpe_ratio
        metrics.trading.win_rate / total_orders / profit_loss_ratio

    By default (full_output=False) this function intentionally discards
    equity_curve, benchmark_curve, trades, and raw_statistics to keep
    the evaluation payload compact and avoid LLM-context bloat in automated
    research loops.

    If full_output=True the caller is expected to have already persisted the
    raw response to disk; raw_result_path should point to that file.
    """
    data = response_data.get("data") or {}
    metrics = data.get("metrics") or {}

    basic = metrics.get("basic") or {}
    risk = metrics.get("risk") or {}
    trading = metrics.get("trading") or {}

    def _f(d: dict, key: str) -> float | None:
        v = d.get(key)
        return float(v) if v is not None else None

    total_return = _f(basic, "total_return")
    if total_return is None:
        total_return = data.get("net_profit_percent")

    # Large arrays that must NOT appear in compact evaluation files
    _OMITTED_KEYS = ("equity_curve", "benchmark_curve", "trades", "raw_statistics")
    omitted = [k for k in _OMITTED_KEYS if k in data]

    summary: dict[str, Any] = {
        "run_id": candidate_run_id,
        "strategy_name": data.get("strategy_name", candidate_run_id),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "starting_cash": data.get("initial_capital"),
        "final_equity": data.get("final_capital"),
        "total_return": round(total_return, 3) if total_return is not None else None,
        "cagr": _f(basic, "annual_return"),
        "sharpe": _f(risk, "sharpe_ratio"),
        "max_drawdown": _f(basic, "max_drawdown"),
        "win_rate": _f(trading, "win_rate"),
        "trade_count": trading.get("total_orders"),
        "profit_factor": _f(trading, "profit_loss_ratio"),
        "research_read": "candidate from proposal",
        "source": "backtester_api",
        # Metadata
        "execution_backend": "rest",
        "payload_mode": "full" if full_output else "compact",
    }

    if omitted:
        summary["warnings"] = [
            f"omitted large fields for compact mode: {', '.join(omitted)}"
        ]

    if raw_result_path is not None:
        summary["raw_result_path"] = str(raw_result_path)

    return summary


# ---------------------------------------------------------------------------
# Summary export (offline fallback) + comparison
# ---------------------------------------------------------------------------


def _export_summary_offline(run_id: str) -> Path | None:
    """Try to export summary from lean output files (offline fallback)."""
    from app.tools.export_backtest_result_summary import main as export_main
    import sys as _sys

    out_path = _BT_REPORTS / f"run_{run_id}_summary.json"
    old_argv = _sys.argv
    try:
        _sys.argv = [
            "export_backtest_result_summary",
            "--run-id", run_id,
            "--format", "json",
            "--output", str(out_path.with_suffix("")),
        ]
        try:
            export_main()
        except SystemExit:
            pass
    finally:
        _sys.argv = old_argv

    return out_path if out_path.exists() else None


def _compare_with_data(
    candidate_data: dict[str, Any],
    baseline_run_id: str,
    candidate_run_id: str,
    proposal: dict[str, Any],
    *,
    execution_backend: str = "rest",
    payload_mode: str = "compact",
    raw_result_path: Path | None = None,
) -> dict[str, Any]:
    """Build evaluation dict comparing candidate data dict vs baseline summary JSON."""

    def _load_baseline() -> dict[str, Any]:
        path = _BT_REPORTS / f"run_{baseline_run_id}_summary.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    baseline = _load_baseline()

    def _perf(d: dict[str, Any]) -> dict[str, Any]:
        return {
            "total_return": d.get("total_return"),
            "sharpe": d.get("sharpe"),
            "max_drawdown": d.get("max_drawdown"),
            "win_rate": d.get("win_rate"),
            "trade_count": d.get("trade_count"),
            "research_read": d.get("research_read", ""),
        }

    cand_p = _perf(candidate_data)
    base_p = _perf(baseline)

    def _delta(key: str) -> float | None:
        c = cand_p.get(key)
        b = base_p.get(key)
        if c is None or b is None:
            return None
        return round(float(c) - float(b), 4)

    sharpe_delta = _delta("sharpe")
    mdd_delta = _delta("max_drawdown")
    sharpe_pass = sharpe_delta is not None and sharpe_delta > 0
    mdd_pass = mdd_delta is None or mdd_delta <= 2.0

    result: dict[str, Any] = {
        "evaluation_type": "proposal_vs_baseline",
        "proposal_id": proposal.get("proposal_id", ""),
        "direction": proposal.get("direction", ""),
        "changes": proposal.get("changes", []),
        "baseline_run_id": baseline_run_id,
        "candidate_run_id": candidate_run_id,
        "baseline_performance": base_p,
        "candidate_performance": cand_p,
        "deltas": {
            "total_return": _delta("total_return"),
            "sharpe": sharpe_delta,
            "max_drawdown": mdd_delta,
            "win_rate": _delta("win_rate"),
        },
        "pass_criteria": {
            "sharpe_improves": sharpe_pass,
            "mdd_acceptable": mdd_pass,
            "overall": sharpe_pass and mdd_pass,
        },
        "verdict": "pass" if (sharpe_pass and mdd_pass) else "fail",
        # Transport / payload metadata
        "execution_backend": execution_backend,
        "payload_mode": payload_mode,
    }

    if raw_result_path is not None:
        result["raw_result_path"] = str(raw_result_path)

    # Propagate warnings from candidate summary
    warnings = candidate_data.get("warnings")
    if warnings:
        result["warnings"] = warnings

    return result


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run_proposal_backtest(
    proposal: dict[str, Any],
    output_dir: Path,
    skip_run: bool = False,
    bt_url: str = _DEFAULT_BT_URL,
    full_output: bool = False,
) -> dict[str, Any]:
    """
    Full pipeline: validate → build YAML → call backtester API → compare.
    Returns the evaluation dict.

    full_output=False (default):
        Compact mode — evaluation JSON contains only summary metrics.
        equity_curve, trades, raw_statistics are intentionally omitted.
        Suitable for automated research loops; keeps files small and avoids
        LLM-context bloat when evaluation results feed back into AI reasoning.

    full_output=True:
        Also saves the complete raw API response to
        <output_dir>/raw_<candidate_run_id>.json and references it via
        raw_result_path in the evaluation. Use for manual inspection only.

    Backend: always REST (direct HTTP to backtester API).
    MCP tools are intentionally NOT used here — use them interactively
    via Claude Code for ad-hoc exploration only.
    """
    if proposal.get("status") == "invalid":
        raise ValueError(
            f"Proposal {proposal.get('proposal_id')} has constraint violations and cannot be run"
        )

    changes = proposal.get("changes") or []
    violations = validate_proposal_changes(changes)
    if violations:
        raise ValueError(
            "Constraint violations: " + "; ".join(v.detail for v in violations)
        )

    # Group changes by family
    families: dict[str, list[dict[str, Any]]] = {}
    for change in changes:
        fam = str(change.get("family") or "")
        families.setdefault(fam, []).append(change)

    evaluations: list[dict[str, Any]] = []
    for family, fam_changes in families.items():
        print(f"[run_proposal_backtest] Building candidate YAML for {family}...", file=sys.stderr)
        try:
            candidate_yaml_path, candidate_run_id = build_candidate_yaml(
                family, fam_changes, proposal["proposal_id"]
            )
        except (ValueError, FileNotFoundError) as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            continue

        print(f"  candidate YAML → {candidate_yaml_path}", file=sys.stderr)

        baseline_run_id = _FAMILY_TO_BASE_RUN_ID[family]
        candidate_summary: dict[str, Any] = {}
        raw_result_path: Path | None = None
        run_ok = False

        if not skip_run:
            yaml_content = candidate_yaml_path.read_text(encoding="utf-8")
            print(
                f"  calling backtester REST API ({bt_url}/api/backtest/run-custom) "
                f"[backend=rest, payload_mode={'full' if full_output else 'compact'}]...",
                file=sys.stderr,
            )
            run_ok, api_response = _call_bt_api(yaml_content, family, bt_url)

            if run_ok and api_response.get("success"):
                # Optionally persist full raw response before trimming
                if full_output:
                    raw_result_path = output_dir / f"raw_{candidate_run_id}.json"
                    raw_result_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_result_path.write_text(
                        json.dumps(api_response, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"  full raw response saved → {raw_result_path}", file=sys.stderr)

                candidate_summary = _api_response_to_summary(
                    api_response,
                    candidate_run_id,
                    full_output=full_output,
                    raw_result_path=raw_result_path,
                )
                # Persist compact summary for future --skip-run reuse
                summary_path = _BT_REPORTS / f"run_{candidate_run_id}_summary.json"
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                summary_path.write_text(
                    json.dumps(candidate_summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  compact summary saved → {summary_path}", file=sys.stderr)
            else:
                err = api_response.get("detail") or api_response.get("error") or "unknown error"
                print(f"  backtester REST API failed: {err}", file=sys.stderr)
                # Try offline fallback (lean output files)
                summary_path = _export_summary_offline(candidate_run_id)
                if summary_path:
                    try:
                        candidate_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
        else:
            # Offline: try to read a previously saved compact summary
            summary_path = _BT_REPORTS / f"run_{candidate_run_id}_summary.json"
            if summary_path.exists():
                try:
                    candidate_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

        evaluation = _compare_with_data(
            candidate_summary,
            baseline_run_id,
            candidate_run_id,
            proposal,
            execution_backend="rest",
            payload_mode="full" if full_output else "compact",
            raw_result_path=raw_result_path,
        )
        evaluation["backtester_run_attempted"] = not skip_run
        evaluation["backtester_run_ok"] = run_ok
        # E1: per-evaluation provenance — which strategy family + data window.
        evaluation["strategy_family"] = family
        _ev_params = _load_baseline_config(family).get("parameters") or {}
        evaluation["data_window"] = {
            "start_date": _ev_params.get("start_date"),
            "end_date": _ev_params.get("end_date"),
        }
        evaluations.append(evaluation)

    result = {
        "proposal_id": proposal.get("proposal_id", ""),
        "direction": proposal.get("direction", ""),
        "evaluations": evaluations,
        "overall_verdict": (
            "pass"
            if evaluations and all(e.get("verdict") == "pass" for e in evaluations)
            else "fail"
        ),
        # E1: auditable proxy origin for downstream screening/evidence.
        "provenance": _otapi_provenance(
            now=datetime.now(timezone.utc), repo_head=_resolve_otapi_repo_head()
        ),
    }

    # Write evaluation file
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_path = output_dir / f"eval_{proposal.get('proposal_id', 'unknown')}.json"
    eval_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"evaluation written → {eval_path}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply a proposal to backtester YAML, run via API, and compare vs baseline"
    )
    parser.add_argument("--proposal", required=True, help="Path to proposal JSON file")
    parser.add_argument(
        "--output-dir",
        default="research/evaluations",
        help="Directory to write evaluation JSON (default: research/evaluations/)",
    )
    parser.add_argument(
        "--bt-url",
        default=_DEFAULT_BT_URL,
        help=f"Backtester API base URL (default: {_DEFAULT_BT_URL})",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Skip the backtester API call; use previously saved candidate summary if available",
    )
    parser.add_argument(
        "--full-output",
        action="store_true",
        help=(
            "Save the complete raw API response (including equity_curve, trades) alongside "
            "the compact evaluation. Use for manual inspection only — do NOT use in automated "
            "research loops as it produces large files and defeats compact-mode cost savings."
        ),
    )
    args = parser.parse_args()

    proposal_path = Path(args.proposal)
    if not proposal_path.exists():
        print(f"ERROR: proposal file not found: {proposal_path}", file=sys.stderr)
        sys.exit(1)

    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    output_dir = _PROJECT_ROOT / args.output_dir

    result = run_proposal_backtest(
        proposal,
        output_dir,
        skip_run=args.skip_run,
        bt_url=args.bt_url,
        full_output=args.full_output,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
