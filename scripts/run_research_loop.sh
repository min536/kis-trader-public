#!/usr/bin/env bash
# run_research_loop.sh
#
# End-to-end research loop:
#   snapshot → proposal → YAML candidate → (optional backtest) → registry
#
# ─── BACKEND POLICY ─────────────────────────────────────────────────────────
# Backtester calls use the REST API (http://localhost:8002) directly.
# MCP tools (mcp__kis-backtest__*) are intentionally NOT used here.
#
# Why:  MCP returns large payloads (equity_curve, full trades) that bloat
#       LLM conversation context and increase API costs when this loop runs
#       repeatedly. Direct REST calls are zero-context-cost for Claude.
#
# For interactive / manual exploration use MCP directly in Claude Code.
# ─────────────────────────────────────────────────────────────────────────────
#
# Steps:
#   0. Baseline freshness check  → auto-run update_baselines.sh if stale (>7d)
#   1. Snapshot                  → research/snapshots/snapshot_YYYYMMDD.json
#   2. Proposal generation       → research/proposals/proposal_*.json
#   3. Proposal backtest         → research/evaluations/eval_*.json
#   4. Registry                  → research/proposal_registry.json
#   5. Shadow watch (optional)   → research/shadow_watch/ (when shadow_candidates exist)
#
# Usage:
#   bash scripts/run_research_loop.sh --account <ACCOUNT>
#   bash scripts/run_research_loop.sh --account <ACCOUNT> --date 20260410
#   bash scripts/run_research_loop.sh --account <ACCOUNT> --skip-run
#   bash scripts/run_research_loop.sh --account <ACCOUNT> --full-output   # manual inspection
#   bash scripts/run_research_loop.sh --account <ACCOUNT> --skip-baseline-update
#
# Options:
#   --account               (required) Account identifier
#   --date                  Date YYYYMMDD. Defaults to latest available.
#   --session               Optional session filter.
#   --skip-run              Skip backtester execution (candidate YAML is still generated).
#   --full-output           Save complete raw API response alongside compact eval (manual use only).
#   --skip-baseline-update  Skip auto-refresh even when baselines are stale.

set -euo pipefail

ACCOUNT=""
DATE=""
SESSION=""
SKIP_RUN=""
FULL_OUTPUT=""
SKIP_BASELINE_UPDATE=""
BT_URL="${BT_URL:-http://localhost:8002}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)              ACCOUNT="$2";  shift 2 ;;
    --date)                 DATE="$2";     shift 2 ;;
    --session)              SESSION="$2";  shift 2 ;;
    --bt-url)               BT_URL="$2";   shift 2 ;;
    --skip-run)             SKIP_RUN="--skip-run"; shift ;;
    --full-output)          FULL_OUTPUT="--full-output"; shift ;;
    --skip-baseline-update) SKIP_BASELINE_UPDATE="1"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$ACCOUNT" ]]; then
  echo "ERROR: --account is required"
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
PYTHON_BIN="${PYTHON_OVERRIDE:-$PROJECT_ROOT/.venv/bin/python3}"

korean_now() {
  "$PYTHON_BIN" - "$1" <<'PYEOF'
import sys

from app.core.time_utils import get_korean_now

print(get_korean_now().strftime(sys.argv[1]))
PYEOF
}

TODAY=$(korean_now "%Y%m%d")
SNAPSHOT_DIR="research/snapshots"
PROPOSAL_DIR="research/proposals"
EVAL_DIR="research/evaluations"

mkdir -p "$SNAPSHOT_DIR" "$PROPOSAL_DIR" "$EVAL_DIR"

json_field() {
  local file="$1"
  local key="$2"
  "$PYTHON_BIN" - "$file" "$key" <<'PYEOF'
import json
import sys
from pathlib import Path

from app.core.file_read_limits import read_text_bounded

path = Path(sys.argv[1])
key = sys.argv[2]
payload = json.loads(read_text_bounded(path, encoding="utf-8"))
print(payload[key])
PYEOF
}

shadow_candidate_count() {
  local registry_file="$1"
  "$PYTHON_BIN" - "$registry_file" <<'PYEOF'
import json
import sys
from pathlib import Path

from app.core.file_read_limits import read_text_bounded

path = Path(sys.argv[1])
if not path.exists():
    print(0)
    raise SystemExit(0)
registry = json.loads(read_text_bounded(path, encoding="utf-8"))
print(
    sum(
        1
        for entry in registry.get("proposals", {}).values()
        if entry.get("status") == "shadow_candidate"
    )
)
PYEOF
}

# ── 0. Baseline rolling update (if stale) ────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [0/5] Checking baseline freshness"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REPORTS_DIR="$PROJECT_ROOT/backtester/reports"
STALE_FAMILIES=$("$PYTHON_BIN" - "$REPORTS_DIR" <<'PYEOF'
import json, sys
from datetime import datetime
from pathlib import Path

from app.core.file_read_limits import read_text_bounded
from app.core.time_utils import get_korean_now

reports_dir = Path(sys.argv[1])
stale = []
for f in reports_dir.glob("run_bt_custom_kis_trader_*_summary.json"):
    if "_candidate" in f.name:
        continue
    try:
        d = json.loads(read_text_bounded(f, encoding="utf-8"))
        end_date = d.get("end_date", "")
        if not end_date:
            stale.append(f.name)
            continue
        baseline_end = datetime.strptime(end_date, "%Y-%m-%d").date()
        days_old = (get_korean_now().date() - baseline_end).days
        if days_old > 7:
            stale.append(f"{f.stem} (end={end_date}, {days_old}d ago)")
    except Exception:
        stale.append(f.name)
for s in stale:
    print(s)
PYEOF
)

if [[ -n "$STALE_FAMILIES" ]]; then
  echo "  stale baselines detected:"
  echo "$STALE_FAMILIES" | while IFS= read -r line; do echo "    $line"; done
  if [[ -z "$SKIP_BASELINE_UPDATE" ]]; then
    echo ""
    echo "  → auto-refreshing baselines (pass --skip-baseline-update to suppress)"
    bash "$PROJECT_ROOT/scripts/update_baselines.sh" --bt-url "$BT_URL" || {
      echo "  WARNING: baseline update failed — continuing with stale data"
    }
  else
    echo "  → skipping baseline update (--skip-baseline-update)"
  fi
else
  echo "  baselines are fresh — skipping update"
fi

# ── 1. Snapshot ──────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [1/5] Building research snapshot"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

SNAPSHOT_ARGS="--account $ACCOUNT"
[[ -n "$DATE" ]] && SNAPSHOT_ARGS="$SNAPSHOT_ARGS --date $DATE"
[[ -n "$SESSION" ]] && SNAPSHOT_ARGS="$SNAPSHOT_ARGS --session $SESSION"

SNAPSHOT_FILE="$SNAPSHOT_DIR/snapshot_${TODAY}.json"
"$PYTHON_BIN" -m app.tools.build_research_snapshot \
  $SNAPSHOT_ARGS \
  --output-file "$SNAPSHOT_FILE"

if [[ ! -f "$SNAPSHOT_FILE" ]]; then
  echo "ERROR: snapshot file not created"
  exit 1
fi

# Extract as_of_date for filenames
AS_OF=$(json_field "$SNAPSHOT_FILE" "as_of_date" 2>/dev/null || echo "$TODAY")
echo "  snapshot date: $AS_OF"

# ── 2. Proposal ──────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [2/5] Generating proposal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

REGISTRY_FILE="$PROJECT_ROOT/research/proposal_registry.json"
PROPOSAL_FILE="$PROPOSAL_DIR/proposal_${AS_OF}_$(korean_now "%H%M%S").json"
GENERATOR_ARGS="--snapshot $SNAPSHOT_FILE --output-file $PROPOSAL_FILE"
[[ -f "$REGISTRY_FILE" ]] && GENERATOR_ARGS="$GENERATOR_ARGS --registry $REGISTRY_FILE"
"$PYTHON_BIN" -m app.tools.proposal_generator $GENERATOR_ARGS

PROPOSAL_ID=$(json_field "$PROPOSAL_FILE" "proposal_id" 2>/dev/null || echo "")
PROPOSAL_STATUS=$(json_field "$PROPOSAL_FILE" "status" 2>/dev/null || echo "")
echo "  proposal_id : $PROPOSAL_ID"
echo "  status      : $PROPOSAL_STATUS"

if [[ "$PROPOSAL_STATUS" == "invalid" ]]; then
  echo "  WARNING: proposal has constraint violations — skipping backtest"
fi

if [[ "$PROPOSAL_STATUS" == "exhausted" ]]; then
  echo "  WARNING: all parameter candidates already tried — no new proposal available"
  echo "  Hint: reject/archive old proposals, or extend parameter range in proposal_constraints.py"
  echo ""
  echo "  Skipping backtest and registry steps."
  echo ""
  exit 0
fi

# ── 3. Backtest evaluation ────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [3/5] Running proposal backtest"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

BT_ARGS="--proposal $PROPOSAL_FILE --output-dir $EVAL_DIR --bt-url $BT_URL"
[[ -n "$SKIP_RUN" ]]    && BT_ARGS="$BT_ARGS $SKIP_RUN"
[[ -n "$FULL_OUTPUT" ]] && BT_ARGS="$BT_ARGS $FULL_OUTPUT"

"$PYTHON_BIN" -m app.tools.run_proposal_backtest $BT_ARGS 2>&1 | grep -v '^{' || true
EVAL_FILE=$(ls "$EVAL_DIR"/eval_${PROPOSAL_ID}.json 2>/dev/null || echo "")

# ── 4. Registry ──────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [4/5] Registering proposal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

"$PYTHON_BIN" -m app.tools.proposal_registry register --file "$PROPOSAL_FILE"

if [[ -n "$EVAL_FILE" && -f "$EVAL_FILE" ]]; then
  "$PYTHON_BIN" -m app.tools.proposal_registry attach-eval \
    --id "$PROPOSAL_ID" \
    --eval-file "$EVAL_FILE"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
"$PYTHON_BIN" -m app.tools.proposal_registry show --id "$PROPOSAL_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 5. Shadow watch (if any shadow_candidates exist) ─────────────────────────
SHADOW_COUNT=$(shadow_candidate_count "$REGISTRY_FILE" 2>/dev/null || echo "0")

if [[ "$SHADOW_COUNT" -gt 0 ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  [5/5] Shadow watch ($SHADOW_COUNT candidate(s))"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  SW_ARGS="--registry $REGISTRY_FILE --output-dir research/shadow_watch --bt-url $BT_URL"
  [[ -n "$SKIP_RUN" ]] && SW_ARGS="$SW_ARGS --skip-run"
  "$PYTHON_BIN" -m app.tools.shadow_watch $SW_ARGS 2>&1 | grep -v '^{' | grep -v '^  "' | grep -v '^\[' || true
fi

echo ""
echo "  Done. To promote to shadow:"
echo "  $PYTHON_BIN -m app.tools.proposal_registry promote --id $PROPOSAL_ID --to shadow_candidate"
echo ""
