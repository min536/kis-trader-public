#!/usr/bin/env bash
# run_ml_retraining.sh
#
# Re-run offline ML baseline experiments on the latest labeled dataset and
# refresh the label viability report.  Call this after each trading day once
# new cycle data has been exported and labeled.
#
# ─── WHAT IT DOES ────────────────────────────────────────────────────────────
# 1. Locates the latest combined labeled dataset in logs/ml/
# 2. Runs walk-forward baseline experiments for all economic labels
# 3. Saves per-label JSON results to logs/ml/ (with date-range suffix)
# 4. Refreshes logs/ml/ml_label_viability_report.json + .md
#
# ─── DOES NOT DO ─────────────────────────────────────────────────────────────
# - Never touches live trading config or parameters
# - Only writes to logs/ml/ (ignored by git)
#
# Usage:
#   bash scripts/run_ml_retraining.sh
#   bash scripts/run_ml_retraining.sh --dataset logs/ml/my_custom_labeled.jsonl
#   bash scripts/run_ml_retraining.sh --dry-run   # print resolved paths, skip training
#   bash scripts/run_ml_retraining.sh --skip-viability  # skip final viability report

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

ML_DIR="$PROJECT_ROOT/logs/ml"
VIABILITY_REPORT="$ML_DIR/ml_label_viability_report.json"
DATASET=""
DRY_RUN=""
SKIP_VIABILITY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset)        DATASET="$2"; shift 2 ;;
    --dry-run)        DRY_RUN="1"; shift ;;
    --skip-viability) SKIP_VIABILITY="1"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

mkdir -p "$ML_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ML retraining"
echo "  ml_dir : $ML_DIR"
[[ -n "$DRY_RUN" ]] && echo "  mode   : DRY-RUN (no training)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Resolve dataset ────────────────────────────────────────────────────────
if [[ -z "$DATASET" ]]; then
  # Find the most recent combined labeled JSONL (prefer multi-day spans like
  # *_YYYYMMDD_YYYYMMDD_labeled.jsonl over single-day files)
  DATASET=$(python3 - <<'PYEOF'
import glob, re
from pathlib import Path

ml_dir = Path("logs/ml")

# Prefer multi-day combined datasets (contain two date groups)
multi = sorted(ml_dir.glob("*_labeled.jsonl"))
# Sort: combined (two dates) before single-date; then by mtime desc
def _key(p):
    dates = re.findall(r'\d{8}', p.stem)
    return (len(dates) >= 2, p.stat().st_mtime)

ranked = sorted(multi, key=_key, reverse=True)
if ranked:
    print(ranked[0])
PYEOF
)
fi

if [[ -z "$DATASET" ]]; then
  echo "  ERROR: no labeled dataset found in $ML_DIR"
  echo "  Run export_ml_candidate_dataset + build_ml_labels first."
  exit 1
fi

if [[ ! -f "$DATASET" ]]; then
  echo "  ERROR: dataset not found: $DATASET"
  exit 1
fi

# Derive date-range suffix from filename for output naming
DATE_RANGE=$(python3 -c "
import re, sys
from pathlib import Path
stem = Path('$DATASET').stem
# Match only YYYYMMDD-style dates (20xx or 19xx), not account numbers
dates = re.findall(r'(?:19|20)\d{6}', stem)
if len(dates) >= 2:
    print(f'{dates[0]}_{dates[-1]}')
elif len(dates) == 1:
    print(dates[0])
else:
    import datetime
    print(datetime.date.today().strftime('%Y%m%d'))
" 2>/dev/null || python3 -c "import datetime; print(datetime.date.today().strftime('%Y%m%d'))")

echo "  dataset    : $DATASET"
echo "  date_range : $DATE_RANGE"
echo ""

# ── 2. Run baseline experiments for each economic label ───────────────────────
declare -a LABELS=(
  "label_positive_30m_net_cost"
  "label_positive_eod_net_cost"
  "label_top_decile_eod"
  "label_top_decile_eod_net_cost"
)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [1/2] Baseline experiments"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for LABEL in "${LABELS[@]}"; do
  # Derive short suffix for filename: strip "label_" prefix
  SUFFIX="${LABEL#label_}"
  OUT_JSON="$ML_DIR/ml_baseline_${SUFFIX}_${DATE_RANGE}.json"
  OUT_MD="${OUT_JSON%.json}.md"

  echo ""
  echo "  [$LABEL]"
  echo "    output : $OUT_JSON"

  if [[ -n "$DRY_RUN" ]]; then
    echo "    (dry-run: would run run_ml_baseline_experiment)"
    continue
  fi

  python3 -m app.tools.run_ml_baseline_experiment \
    --input  "$DATASET" \
    --label  "$LABEL" \
    --model  logistic \
    --output "$OUT_JSON" \
    --markdown-output "$OUT_MD" 2>&1 | grep -E '(read|folds|ERROR|WARNING)' || true
  echo "    done"
done

# ── 3. Refresh viability report ───────────────────────────────────────────────
if [[ -z "$SKIP_VIABILITY" ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  [2/2] Label viability report"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [[ -n "$DRY_RUN" ]]; then
    echo "  (dry-run: would run compare_ml_label_viability)"
  else
    python3 -m app.tools.compare_ml_label_viability \
      --dataset     "$DATASET" \
      --baseline-dir "$ML_DIR" \
      --output      "$VIABILITY_REPORT" \
      --markdown-output "${VIABILITY_REPORT%.json}.md"
    echo "  saved → $VIABILITY_REPORT"
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done."
echo "  Viability report : $VIABILITY_REPORT"
echo "  Next: run the research loop to pick up updated signal."
echo "    bash scripts/run_research_loop.sh --account <account>"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
