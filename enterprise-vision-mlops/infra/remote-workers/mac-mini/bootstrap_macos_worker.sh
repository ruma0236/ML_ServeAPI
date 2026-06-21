#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ruma0236/ML_ServeAPI.git}"
BRANCH="${BRANCH:-codex/mac-mini-worker}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/mlops-lab}"
REPO_DIR="${REPO_DIR:-$WORKSPACE_DIR/ML_ServeAPI}"
PROJECT_DIR="${PROJECT_DIR:-$REPO_DIR/enterprise-vision-mlops}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

log() {
  printf '[mac-mini-bootstrap] %s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$1" >&2
    exit 1
  fi
}

require_cmd git
require_cmd curl

export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv under the current user home"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

require_cmd uv

log "Ensuring Python ${PYTHON_VERSION} is available through uv"
uv python install "$PYTHON_VERSION"

mkdir -p "$WORKSPACE_DIR"

if [ ! -d "$REPO_DIR/.git" ]; then
  log "Cloning ${REPO_URL} into ${REPO_DIR}"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
  log "Updating existing repo at ${REPO_DIR}"
  git -C "$REPO_DIR" fetch origin "$BRANCH"
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
fi

if [ ! -d "$PROJECT_DIR" ]; then
  printf 'Project directory not found: %s\n' "$PROJECT_DIR" >&2
  exit 1
fi

log "Running mac-mini worker smoke checks"
bash "$PROJECT_DIR/infra/remote-workers/mac-mini/run_mac_worker_smoke.sh"
