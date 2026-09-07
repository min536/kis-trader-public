#!/usr/bin/env bash
# check_eod.sh — run end-of-day diagnostics in one command
#
# Usage:
#   ./scripts/check_eod.sh <ACCOUNT> [DATE]
#   ACCOUNT=mock_12345678_01 ./scripts/check_eod.sh
#   ACCOUNT=mock_12345678_01 DATE=20260409 ./scripts/check_eod.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python3"

if [[ -x "${VENV_PYTHON}" ]]; then
    PYTHON="${VENV_PYTHON}"
else
    PYTHON="python3"
fi

usage() {
    cat <<'EOF'
Usage:
  ./scripts/check_eod.sh <ACCOUNT> [DATE]
  ACCOUNT=<ACCOUNT> ./scripts/check_eod.sh
  ACCOUNT=<ACCOUNT> DATE=<YYYYMMDD> ./scripts/check_eod.sh

Examples:
  ./scripts/check_eod.sh mock_12345678_01
  ./scripts/check_eod.sh mock_12345678_01 20260409
  ACCOUNT=mock_12345678_01 DATE=20260409 ./scripts/check_eod.sh
EOF
}

print_header() {
    local title="$1"
    printf '\n============================================================\n'
    printf '%s\n' "${title}"
    printf '============================================================\n'
}

print_command() {
    printf 'Command:'
    printf ' %q' "$@"
    printf '\n'
}

run_step() {
    local title="$1"
    shift
    print_header "${title}"
    print_command "$@"
    if ! "$@"; then
        printf '\nERROR: %s failed.\n' "${title}" >&2
        exit 1
    fi
}

resolve_latest_date() {
    "${PYTHON}" -c 'from app.tools.eod_health_check import detect_latest_market_date; import sys; print(detect_latest_market_date(sys.argv[1]))' "$1"
}

ACCOUNT="${1:-${ACCOUNT:-}}"
DATE_INPUT="${2:-${DATE:-}}"

if [[ -z "${ACCOUNT}" ]]; then
    usage >&2
    exit 1
fi

if [[ $# -gt 2 ]]; then
    usage >&2
    exit 1
fi

cd "${PROJECT_DIR}"

RESOLVED_DATE="${DATE_INPUT}"
if [[ -z "${RESOLVED_DATE}" ]]; then
    print_header "Resolving latest market date"
    print_command "${PYTHON}" -c 'from app.tools.eod_health_check import detect_latest_market_date; import sys; print(detect_latest_market_date(sys.argv[1]))' "${ACCOUNT}"
    if ! RESOLVED_DATE="$(resolve_latest_date "${ACCOUNT}")"; then
        printf '\nERROR: Failed to resolve latest market date for account=%s.\n' "${ACCOUNT}" >&2
        exit 1
    fi
    printf 'Resolved date: %s\n' "${RESOLVED_DATE}"
fi

print_header "EOD verification context"
printf 'Project : %s\n' "${PROJECT_DIR}"
printf 'Python  : %s\n' "${PYTHON}"
printf 'Account : %s\n' "${ACCOUNT}"
printf 'Date    : %s\n' "${RESOLVED_DATE}"

run_step \
    "1. EOD health check" \
    "${PYTHON}" -m app.tools.eod_health_check --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

run_step \
    "2. Export signal dataset" \
    "${PYTHON}" -m app.tools.export_signal_dataset --account "${ACCOUNT}" --date "${RESOLVED_DATE}" --all-stages

run_step \
    "3. Analyze signal dataset" \
    "${PYTHON}" -m app.tools.analyze_signal_dataset --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

run_step \
    "4. Export signal outcome dataset" \
    "${PYTHON}" -m app.tools.export_signal_outcome_dataset --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

run_step \
    "5. Analyze signal outcome dataset" \
    "${PYTHON}" -m app.tools.analyze_signal_outcome_dataset --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

run_step \
    "6. Postrun diagnostics" \
    "${PYTHON}" -m app.tools.postrun_diagnostics --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

run_step \
    "7. Rate-limit audit" \
    "${PYTHON}" -m app.tools.rate_limit_audit --account "${ACCOUNT}" --date "${RESOLVED_DATE}" --days 1

run_step \
    "8. Analyze core bucket" \
    "${PYTHON}" -m app.tools.analyze_core_bucket --account "${ACCOUNT}" --date "${RESOLVED_DATE}"

# ── 9-11. ML data accumulation (export → label → retrain) ─────────────────
ML_DIR="${PROJECT_DIR}/logs/ml"
ML_DATASET="${ML_DIR}/ml_candidate_dataset_${ACCOUNT}_${RESOLVED_DATE}.jsonl"
ML_LABELED="${ML_DIR}/ml_candidate_dataset_${ACCOUNT}_${RESOLVED_DATE}_labeled.jsonl"

run_step \
    "9. Export ML candidate dataset" \
    "${PYTHON}" -m app.tools.export_ml_candidate_dataset \
        --account "${ACCOUNT}" --date "${RESOLVED_DATE}" \
        --format jsonl --output "${ML_DATASET}"

if [[ -f "${ML_DATASET}" ]]; then
    run_step \
        "10. Build ML labels" \
        "${PYTHON}" -m app.tools.build_ml_labels \
            --input "${ML_DATASET}" \
            --format jsonl --output "${ML_LABELED}"
else
    printf '\nWARNING: ML dataset not created — skipping label and retrain steps.\n' >&2
fi

# Step 11: Build (or refresh) the cumulative multi-day labeled dataset.
# Merges all daily _labeled.jsonl files for this account into one growing file.
# More days → more unique trade_dates → more walk-forward folds → better ML confidence.
if [[ -f "${ML_LABELED}" ]]; then
    print_header "11. Building cumulative ML dataset (all days)"
    COMBINED_DATASET=$("${PYTHON}" - "${ML_DIR}" "${ACCOUNT}" <<'PYEOF'
import sys, json, re
from pathlib import Path

ml_dir = Path(sys.argv[1])
account = sys.argv[2]

# Collect all single-day labeled files for this account (exclude already-combined)
pattern = re.compile(r'^ml_candidate_dataset_' + re.escape(account) + r'_(\d{8})_labeled\.jsonl$')
day_files = sorted(
    f for f in ml_dir.glob(f"ml_candidate_dataset_{account}_*_labeled.jsonl")
    if pattern.match(f.name)
)
if not day_files:
    sys.exit(0)

dates = [pattern.match(f.name).group(1) for f in day_files]
start_d, end_d = dates[0], dates[-1]

if start_d == end_d:
    # Only one day — combined == single day file, nothing to merge
    print(day_files[0])
    sys.exit(0)

combined_path = ml_dir / f"ml_candidate_dataset_{account}_{start_d}_{end_d}_labeled.jsonl"

# Re-build only if any source file is newer than the combined file
needs_rebuild = (
    not combined_path.exists()
    or any(f.stat().st_mtime > combined_path.stat().st_mtime for f in day_files)
)
if needs_rebuild:
    seen_row_ids: set[str] = set()
    rows = []
    for f in day_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                rid = row.get("row_id", "")
                if rid and rid in seen_row_ids:
                    continue
                seen_row_ids.add(rid)
                rows.append(line)
            except json.JSONDecodeError:
                continue
    combined_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

print(combined_path)
PYEOF
    )

    if [[ -z "${COMBINED_DATASET}" ]]; then
        COMBINED_DATASET="${ML_LABELED}"
        printf '  (single-day dataset — no merge needed)\n'
    else
        DATES_INFO=$("${PYTHON}" -c "
from pathlib import Path
import re
p = Path('${COMBINED_DATASET}')
dates = re.findall(r'(?:19|20)\d{6}', p.stem)
print(f'{dates[0]} → {dates[-1]}' if len(dates)>=2 else dates[0] if dates else p.stem)
" 2>/dev/null || echo "${COMBINED_DATASET}")
        printf '  combined dataset: %s  (%s)\n' "${COMBINED_DATASET##*/}" "${DATES_INFO}"
    fi
else
    COMBINED_DATASET=""
fi

# Step 12: retrain on combined dataset (more folds as days accumulate)
if [[ -n "${COMBINED_DATASET}" && -f "${COMBINED_DATASET}" ]]; then
    print_header "12. ML retraining (walk-forward baselines + viability report)"
    bash "${PROJECT_DIR}/scripts/run_ml_retraining.sh" \
        --dataset "${COMBINED_DATASET}" \
        || printf '\nWARNING: ML retraining step failed (non-fatal).\n' >&2
fi

print_header "Completed"
printf 'EOD verification finished for account=%s date=%s\n' "${ACCOUNT}" "${RESOLVED_DATE}"
printf '\nKey generated files:\n'
printf '  - logs/signal_dataset_%s_%s.csv\n' "${ACCOUNT}" "${RESOLVED_DATE}"
printf '  - logs/signal_outcomes_%s_%s.csv\n' "${ACCOUNT}" "${RESOLVED_DATE}"
printf '  - logs/ml/ml_candidate_dataset_%s_%s_labeled.jsonl\n' "${ACCOUNT}" "${RESOLVED_DATE}"
printf '  - logs/ml/ml_label_viability_report.json\n'
