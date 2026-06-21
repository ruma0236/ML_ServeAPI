#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/mlops-lab/ML_ServeAPI/enterprise-vision-mlops}"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"

log() {
  printf '[pycharm-remote-dev] %s\n' "$*"
}

fail() {
  printf '[pycharm-remote-dev] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_path() {
  test -e "$1" || fail "Required path not found: $1"
}

log "Host: $(hostname)"
log "User: $(whoami)"
log "Shell: ${SHELL:-unknown}"
log "Architecture: $(uname -m)"

if [ -n "${SSH_CONNECTION:-}" ]; then
  log "SSH session detected"
else
  log "SSH_CONNECTION is empty; run this through the same SSH path PyCharm will use"
fi

require_cmd git
require_cmd curl
require_cmd tar
require_cmd unzip
require_cmd rsync
require_path "$UV_BIN"
require_path "$PYTHON_BIN"
require_path "$PROJECT_DIR/pyproject.toml"

mkdir -p \
  "$HOME/Library/Caches/JetBrains/RemoteDev" \
  "$HOME/.cache/JetBrains/RemoteDev" \
  "$HOME/.config/JetBrains/RemoteDev"

touch "$HOME/.cache/JetBrains/RemoteDev/.write-test"
rm "$HOME/.cache/JetBrains/RemoteDev/.write-test"

free_kb="$(df -Pk "$HOME" | awk 'NR == 2 {print $4}')"
free_gb="$((free_kb / 1024 / 1024))"
if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
  fail "Free disk space is ${free_gb}GB, below required ${MIN_FREE_GB}GB"
fi
log "Free disk space: ${free_gb}GB"

cd "$PROJECT_DIR"
log "Project: $PROJECT_DIR"
log "Git branch: $(git branch --show-current)"
log "Git commit: $(git rev-parse --short HEAD)"

if [ -n "$(git status --short)" ]; then
  log "Git working tree has local changes:"
  git status --short
else
  log "Git working tree is clean"
fi

"$UV_BIN" --version
"$PYTHON_BIN" --version
"$UV_BIN" run --python 3.11 python -m compileall src scripts >/tmp/pycharm_remote_dev_compile.log
tail -5 /tmp/pycharm_remote_dev_compile.log

log "PyCharm Remote Development prerequisites passed"
