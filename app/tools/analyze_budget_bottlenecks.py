"""CLI: analyze_budget_bottlenecks

Diagnostic tool for analyzing API rate-limit and budget bottlenecks
across trading cycles.

Usage:
    python3 -m app.tools.analyze_budget_bottlenecks --date 20260403 --account mock_12345678_01
    python3 -m app.tools.analyze_budget_bottlenecks --date 20260403 --account mock_12345678_01 --session REGULAR
    python3 -m app.tools.analyze_budget_bottlenecks --date 20260403 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.jsonl import read_jsonl_objects

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _stats_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"

def _snapshots_path(account: str) -> Path:
    return _PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"

def _ts_date(ts: str | None) -> str:
    if not ts: return ""
    return str(ts)[:10].replace("-", "")

# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

def _c(color: str, text: str) -> str: return f"{color}{text}{_RESET}"
def _header(text: str) -> str: return _c(_BOLD + _CYAN, text)
def _subheader(text: str) -> str: return _c(_BOLD, text)
def _ok(text: str) -> str: return _c(_GREEN, text)
def _warn(text: str) -> str: return _c(_YELLOW, text)
def _bad(text: str) -> str: return _c(_RED, text)
def _dim(text: str) -> str: return _c(_DIM, text)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows, _errors = read_jsonl_objects(path)
    return rows

def run_analysis(account: str, date: str, session: str) -> dict[str, Any]:
    stats_p = _stats_path(account, date)
    snaps_p = _snapshots_path(account)
    
    stats_rows = _load_jsonl(stats_p)
    snap_rows = _load_jsonl(snaps_p)

    # Filter snapshots to target date since file is monolithic
    snap_rows = [r for r in snap_rows if _ts_date(r.get("ts") or r.get("timestamp")) == date]

    # Filter by session
    if session:
        t_ses = session.upper()
        stats_rows = [r for r in stats_rows if str(r.get("session") or "").upper() == t_ses]
        snap_rows = [r for r in snap_rows if str((r.get("market_session") or {}).get("session") or "").upper() == t_ses]

    # The user asked for counts of certain properties. We can use snapshots as they contain the budget flags.
    # Note: Some metrics are purely in snapshots, some might be in stats.
    # Let's rely heavily on snapshots for budget as they have rate_limit_source, buy_scan_skipped_reason, etc.
    
    total_cycles = len(snap_rows)
    
    # 1. Budget cycle flags
    sbsbl = sum(1 for r in snap_rows if r.get("buy_scan_skipped_reason") in ("rate_limit_detected", "rate_limit_after_sell_watch"))
    bspb = sum(1 for r in snap_rows if r.get("buy_scan_partial_budget"))
    bsbr = sum(1 for r in snap_rows if r.get("buy_scan_budget_reserved"))
    rlt = sum(1 for r in snap_rows if r.get("rate_limit_triggered"))
    swpb = sum(1 for r in snap_rows if r.get("sell_watch_partial"))

    # 1b. Drain metrics (new fields added by backoff-drain fix)
    sw_drain_vals = [float(r["sell_watch_backoff_drain_ms"]) for r in snap_rows if (r.get("sell_watch_backoff_drain_ms") or 0) > 0]
    exec_drain_vals = [float(r["execution_tail_backoff_drain_ms"]) for r in snap_rows if (r.get("execution_tail_backoff_drain_ms") or 0) > 0]
    sw_drain_count = len(sw_drain_vals)
    exec_drain_count = len(exec_drain_vals)
    sw_drain_avg_ms = round(sum(sw_drain_vals) / sw_drain_count, 1) if sw_drain_count else 0.0
    exec_drain_avg_ms = round(sum(exec_drain_vals) / exec_drain_count, 1) if exec_drain_count else 0.0

    # 2. Aggregate API usage
    api_requests = sum((r.get("api_request_count") or 0) for r in stats_rows)
    api_quotes = sum((r.get("api_quote_request_count") or 0) for r in stats_rows)
    sr_len = max(len(stats_rows), 1)
    
    # 3. BUY scan bottleneck
    deep_eval_cycles = sum(1 for r in stats_rows if (r.get("deep_eval_count") or 0) > 0)
    executed_cycles = sum(1 for r in stats_rows if (r.get("executed_order_count") or 0) > 0)

    # 4. SELL watch pressure
    sell_evaluated = sum((r.get("sell_evaluated_count") or 0) for r in stats_rows)
    rl_src = Counter(r.get("rate_limit_source") for r in snap_rows if r.get("rate_limit_source"))

    # 5. Derive actionable recommendations
    recommendations: list[str] = []
    sell_watch_rl_pct = float(rl_src.get("sell_watch", 0)) / max(total_cycles, 1) * 100
    buy_skip_pct = float(sbsbl) / max(total_cycles, 1) * 100
    sell_partial_pct = float(swpb) / max(total_cycles, 1) * 100

    if sell_watch_rl_pct > 15.0:
        recommendations.append(
            f"sell_watch가 rate limit 원인의 {sell_watch_rl_pct:.0f}%를 차지합니다. "
            "SELL_CHECK_INTERVAL_SECONDS 를 늘리거나 "
            "API_BUY_SCAN_MIN_REQUEST_RESERVE / API_BUY_SCAN_MIN_QUOTE_RESERVE 를 "
            "높여 buy_scan 예산을 보호하세요."
        )
    if buy_skip_pct > 20.0:
        recommendations.append(
            f"buy_scan이 {buy_skip_pct:.0f}%의 사이클에서 예산 부족으로 skip됩니다. "
            "API_BUY_SCAN_MIN_REQUEST_RESERVE 값을 현재보다 높이면 "
            "sell_watch가 예산을 다 쓰기 전에 buy_scan 몫을 확보할 수 있습니다."
        )
    if sell_partial_pct > 40.0:
        recommendations.append(
            f"sell_watch가 {sell_partial_pct:.0f}%의 사이클에서 partial로 종료됩니다. "
            "보유 종목 수 대비 SELL_CHECK_INTERVAL_SECONDS 가 짧은 경우가 많습니다. "
            "partial 자체는 다음 사이클에서 이어서 평가하므로 커버리지는 유지되나, "
            "rate limit 압박이 심하면 간격 조정을 검토하세요."
        )
    if not recommendations:
        recommendations.append("현재 데이터 기준으로 즉각적인 조정이 필요한 이상 패턴은 없습니다.")

    return {
        "meta": {
            "account": account,
            "date": date,
            "session": session or "ALL",
            "stats_file": str(stats_p),
            "snapshots_file": str(snaps_p),
            "cycles_analyzed": total_cycles
        },
        "flags": {
            "skipped_buy_scan_budget_limited": sbsbl,
            "buy_scan_partial_budget": bspb,
            "buy_scan_budget_reserved": bsbr,
            "rate_limit_triggered": rlt,
            "sell_watch_partial": swpb
        },
        "api": {
            "total_requests": api_requests,
            "total_quotes": api_quotes,
            "avg_requests_per_cycle": float(api_requests) / sr_len if len(stats_rows) > 0 else 0,
            "avg_quotes_per_cycle": float(api_quotes) / sr_len if len(stats_rows) > 0 else 0
        },
        "buy_scan": {
            "cycles_with_deep_eval": deep_eval_cycles,
            "cycles_with_execution": executed_cycles,
            "cycles_skipped_budget_rl": sbsbl
        },
        "sell_watch": {
            "total_evaluated_count": sell_evaluated,
            "partial_frequency": swpb,
            "rate_limit_sources": dict(rl_src)
        },
        "drain": {
            "sell_watch_drain_count": sw_drain_count,
            "sell_watch_drain_avg_ms": sw_drain_avg_ms,
            "exec_tail_drain_count": exec_drain_count,
            "exec_tail_drain_avg_ms": exec_drain_avg_ms,
        },
        "recommendations": recommendations,
    }


def _pct(part: int, total: int) -> str:
    if total == 0: return "0.0%"
    return f"{100.0 * part / total:.1f}%"

def _bar(value: int, total: int, width: int = 20) -> str:
    if total == 0: return " " * width
    filled = max(1, round(width * value / total)) if value > 0 else 0
    return "█" * filled + "░" * (width - filled)

def print_terminal_report(result: dict[str, Any]) -> None:
    m = result["meta"]
    print()
    print(_header("═" * 72))
    print(_header("  analyze_budget_bottlenecks"))
    print(_header("═" * 72))
    print(f"  account : {m['account']}")
    print(f"  date    : {m['date']}")
    print(f"  session : {m['session']}")
    print(f"  cycles  : {m['cycles_analyzed']}")
    print()

    # 1. Flags
    f = result["flags"]
    tot = m["cycles_analyzed"]
    print(_header("  ── 1. Budget-Limited Cycles ──────────────────────────"))
    for k, v in f.items():
        print(f"    {k:<35} {v:>5}  {_pct(v, tot):>6}  {_bar(v, tot)}")
    print()

    # 2. API usage
    a = result["api"]
    print(_header("  ── 2. Aggregate API Usage ────────────────────────────"))
    print(f"    Total API Requests : {a['total_requests']}")
    print(f"    Total Quotes       : {a['total_quotes']}")
    print(f"    Avg Requests/Cycle : {a['avg_requests_per_cycle']:.2f}")
    print(f"    Avg Quotes/Cycle   : {a['avg_quotes_per_cycle']:.2f}")
    print()

    # 3. BUY Bottleneck
    b = result["buy_scan"]
    print(_header("  ── 3. BUY Scan Bottleneck ────────────────────────────"))
    print(f"    Cycles w/ deep_eval > 0    : {b['cycles_with_deep_eval']}")
    print(f"    Cycles w/ executed > 0     : {b['cycles_with_execution']}")
    print(f"    Cycles skipped (budget/RL) : {_bad(str(b['cycles_skipped_budget_rl']))}")
    print()

    # 4. SELL Pressure
    s = result["sell_watch"]
    print(_header("  ── 4. SELL Watch Pressure ────────────────────────────"))
    print(f"    Total sell_evaluated_count : {s['total_evaluated_count']}")
    print(f"    Partial sell watch cycles  : {s['partial_frequency']}")
    print(_subheader("    Rate Limit Sources:"))
    if not s["rate_limit_sources"]:
        print(_dim("      None"))
    for src, cnt in sorted(s["rate_limit_sources"].items(), key=lambda x: -x[1]):
        print(f"      {src:<25} {cnt:>5}")
    print()

    # 5. Drain stats
    d = result.get("drain", {})
    print(_header("  ── 5. Backoff-Drain Stats ────────────────────────────"))
    print(f"    sell_watch drain cycles    : {d.get('sell_watch_drain_count', 0)}  avg {d.get('sell_watch_drain_avg_ms', 0):.0f} ms")
    print(f"    exec_tail drain cycles     : {d.get('exec_tail_drain_count', 0)}  avg {d.get('exec_tail_drain_avg_ms', 0):.0f} ms")
    print()

    # 6. Recommendations
    recs = result.get("recommendations", [])
    print(_header("  ── 6. Recommendations ────────────────────────────────"))
    if not recs:
        print(_dim("    No recommendations."))
    else:
        for i, rec in enumerate(recs, 1):
            print(f"    {i}. {rec}")
    print()
    print(_header("═" * 72))
    print()

def print_json_report(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze API rate-limit bottlenecks")
    parser.add_argument("--date", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_analysis(account=args.account, date=args.date, session=args.session)

    if args.json:
        print_json_report(result)
    else:
        print_terminal_report(result)
    sys.exit(0)

if __name__ == "__main__":
    main()
