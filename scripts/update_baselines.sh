#!/usr/bin/env bash
# update_baselines.sh
#
# Refresh the core and continuation family approximate baseline backtest results.
# Run this weekly (or whenever the baseline window needs to move forward) so that
# proposal_generator.py compares against up-to-date performance data.
#
# ─── WHAT IT DOES ────────────────────────────────────────────────────────────
# 1. Calls the backtester REST API (localhost:8002) with each family YAML
# 2. Writes compact summary JSONs to backtester/reports/
# 3. Prints a diff of the key metrics so you can spot regime changes
#
# ─── DOES NOT DO ─────────────────────────────────────────────────────────────
# - Never touches live trading config
# - Never promotes any proposal
# - Only updates the two baseline summary files used by the research loop
#
# Usage:
#   bash scripts/update_baselines.sh
#   bash scripts/update_baselines.sh --end-date 20260412
#   bash scripts/update_baselines.sh --start-date 20251011 --end-date 20260412
#   bash scripts/update_baselines.sh --dry-run      # print params, skip API call

set -euo pipefail

BT_URL="http://localhost:8002"
DRY_RUN=""
# Default window: last ~6 months ending today
END_DATE=$(date +%Y-%m-%d)
START_DATE=$(date -v-6m +%Y-%m-%d 2>/dev/null || date -d "6 months ago" +%Y-%m-%d 2>/dev/null || echo "2025-10-11")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bt-url)      BT_URL="$2"; shift 2 ;;
    --start-date)  START_DATE="$2"; shift 2 ;;
    --end-date)    END_DATE="$2"; shift 2 ;;
    --dry-run)     DRY_RUN="1"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

REPORTS_DIR="$PROJECT_ROOT/backtester/reports"
STRATEGIES_DIR="$PROJECT_ROOT/backtester/strategies"
mkdir -p "$REPORTS_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baseline update"
echo "  window : $START_DATE → $END_DATE"
echo "  api    : $BT_URL"
[[ -n "$DRY_RUN" ]] && echo "  mode   : DRY-RUN (no API call)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Families to refresh
declare -a FAMILIES=("core_family_approx" "continuation_family_approx")

for FAMILY in "${FAMILIES[@]}"; do
  YAML_FILE="$STRATEGIES_DIR/kis_trader_${FAMILY}.kis.yaml"
  RUN_ID="bt_custom_kis_trader_${FAMILY}"
  SUMMARY_FILE="$REPORTS_DIR/run_${RUN_ID}_summary.json"

  echo ""
  echo "  [${FAMILY}]"
  echo "    yaml    : $YAML_FILE"
  echo "    summary : $SUMMARY_FILE"

  if [[ ! -f "$YAML_FILE" ]]; then
    echo "    ERROR: YAML file not found — skipping"
    continue
  fi

  if [[ -n "$DRY_RUN" ]]; then
    echo "    (dry-run: would POST $YAML_FILE to $BT_URL/api/backtest/run-custom)"
    continue
  fi

  # Read old metrics for diff output
  OLD_SHARPE=""
  OLD_RETURN=""
  if [[ -f "$SUMMARY_FILE" ]]; then
    OLD_SHARPE=$(python3 -c "import json; d=json.load(open('$SUMMARY_FILE')); print(d.get('sharpe','—'))" 2>/dev/null || echo "—")
    OLD_RETURN=$(python3 -c "import json; d=json.load(open('$SUMMARY_FILE')); print(d.get('total_return','—'))" 2>/dev/null || echo "—")
  fi

  echo "    running backtest..."
  python3 - <<PYEOF
import json, sys, urllib.request, urllib.error
from pathlib import Path

yaml_content = Path("$YAML_FILE").read_text(encoding="utf-8")

payload = json.dumps({
    "yaml_content": yaml_content,
    "symbols": ["005930", "000660", "035720", "005380", "051910", "035420"],
    "start_date": "$START_DATE",
    "end_date": "$END_DATE",
    "initial_capital": 100000000,
    "commission_rate": 0.00015,
    "tax_rate": 0.0023,
    "slippage": 0.001,
}).encode("utf-8")

req = urllib.request.Request(
    "$BT_URL/api/backtest/run-custom",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=600) as resp:
        r = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"    ERROR: HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"    ERROR: {e}", file=sys.stderr)
    sys.exit(1)

if not r.get("success"):
    print(f"    ERROR: API returned success=false: {r}", file=sys.stderr)
    sys.exit(1)

data = r.get("data") or {}
metrics = data.get("metrics") or {}
basic = metrics.get("basic") or {}
risk = metrics.get("risk") or {}
trading = metrics.get("trading") or {}

def _f(d, k):
    v = d.get(k)
    return float(v) if v is not None else None

total_return = _f(basic, "total_return") or data.get("net_profit_percent")

sharpe_val = _f(risk, "sharpe_ratio")
trade_count_val = trading.get("total_orders")

def _auto_research_read(sharpe, trade_count):
    if sharpe is None:
        return "no data"
    tc = int(trade_count) if trade_count is not None else None
    if tc is not None and tc < 10:
        return "too few trades"
    if sharpe < 0:
        return "weak baseline"
    if tc is not None and tc < 20:
        return "low sample confidence"
    if sharpe >= 1.0:
        return "solid baseline"
    if sharpe >= 0.5:
        return "moderate baseline"
    return "below-average baseline"

summary = {
    "run_id": "$RUN_ID",
    "strategy_name": data.get("strategy_name", "$RUN_ID"),
    "symbols": data.get("symbols", []),
    "start_date": "$START_DATE",
    "end_date": "$END_DATE",
    "starting_cash": data.get("initial_capital"),
    "final_equity": data.get("final_capital"),
    "total_return": round(total_return, 3) if total_return is not None else None,
    "cagr": _f(basic, "annual_return"),
    "sharpe": sharpe_val,
    "max_drawdown": _f(basic, "max_drawdown"),
    "win_rate": _f(trading, "win_rate"),
    "profit_factor": _f(trading, "profit_loss_ratio"),
    "trade_count": trade_count_val,
    "research_read": _auto_research_read(sharpe_val, trade_count_val),
    "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
}

out = Path("$SUMMARY_FILE")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"    saved → {out}")
print(f"    return={summary['total_return']}%  sharpe={summary['sharpe']}  mdd={summary['max_drawdown']}%  trades={summary['trade_count']}")
PYEOF

  # Print diff vs old
  NEW_SHARPE=$(python3 -c "import json; d=json.load(open('$SUMMARY_FILE')); print(d.get('sharpe','—'))" 2>/dev/null || echo "—")
  NEW_RETURN=$(python3 -c "import json; d=json.load(open('$SUMMARY_FILE')); print(d.get('total_return','—'))" 2>/dev/null || echo "—")
  if [[ -n "$OLD_SHARPE" ]]; then
    echo "    diff    : sharpe $OLD_SHARPE → $NEW_SHARPE | return $OLD_RETURN% → $NEW_RETURN%"
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done. Baselines updated in backtester/reports/"
echo "  Next: run the research loop to generate fresh proposals."
echo "    bash scripts/run_research_loop.sh --account <account>"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
