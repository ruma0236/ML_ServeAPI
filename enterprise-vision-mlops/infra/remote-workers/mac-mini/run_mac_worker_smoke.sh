#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
UV_BIN="${UV_BIN:-uv}"
RUN_REMOTE_INVENTORY="${RUN_REMOTE_INVENTORY:-0}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${RUN_DIR:-$PROJECT_ROOT/artifacts/runs/mac_mini_worker_smoke/$RUN_ID}"
REPORT_PATH="${REPORT_PATH:-$PROJECT_ROOT/artifacts/reports/mac_mini_worker_smoke.md}"
LOG_PATH="$RUN_DIR/smoke.log"

export PATH="$HOME/.local/bin:$PATH"

if ! command -v "$UV_BIN" >/dev/null 2>&1; then
  printf 'uv is required. Run bootstrap_macos_worker.sh first.\n' >&2
  exit 1
fi

mkdir -p "$RUN_DIR" "$(dirname "$REPORT_PATH")"
cd "$PROJECT_ROOT"

run_step() {
  printf '\n$ %s\n' "$*" | tee -a "$LOG_PATH"
  "$@" 2>&1 | tee -a "$LOG_PATH"
}

time_step() {
  local label="$1"
  local start_ts
  local end_ts
  local elapsed
  shift
  start_ts="$(date +%s)"
  run_step "$@"
  end_ts="$(date +%s)"
  elapsed="$((end_ts - start_ts))"
  printf -- '- %s: passed (%ss)\n' "$label" "$elapsed" >>"$REPORT_PATH"
}

HOSTNAME_VALUE="$(hostname)"
ARCH_VALUE="$(uname -m)"
BRANCH_VALUE="$(git branch --show-current 2>/dev/null || printf 'unknown')"
COMMIT_VALUE="$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"

cat >"$REPORT_PATH" <<EOF
# mac-mini Worker Smoke Report

- Last updated UTC: \`$(date -u +%Y-%m-%dT%H:%M:%SZ)\`
- Host: \`${HOSTNAME_VALUE}\`
- Architecture: \`${ARCH_VALUE}\`
- Branch: \`${BRANCH_VALUE}\`
- Commit: \`${COMMIT_VALUE}\`
- Run directory: \`${RUN_DIR#$PROJECT_ROOT/}\`
- Run remote inventory: \`${RUN_REMOTE_INVENTORY}\`

## Checks

EOF

time_step "compileall" "$UV_BIN" run --python "$PYTHON_VERSION" python -m compileall src scripts
time_step "data-ingest" \
  "$UV_BIN" run --python "$PYTHON_VERSION" python scripts/run_pipeline.py data-ingest --config configs/local.toml
time_step "data-validate" \
  "$UV_BIN" run --python "$PYTHON_VERSION" python scripts/run_pipeline.py data-validate --config configs/local.toml

if [ "$RUN_REMOTE_INVENTORY" = "1" ]; then
  time_step "remote-inventory" \
    "$UV_BIN" run --python "$PYTHON_VERSION" python scripts/run_pipeline.py remote-inventory --config configs/local.toml
else
  printf -- '- remote-inventory: skipped (run from Windows control-plane, or set RUN_REMOTE_INVENTORY=1)\n' >>"$REPORT_PATH"
fi

printf '\nSmoke checks completed. Report: %s\n' "$REPORT_PATH"
