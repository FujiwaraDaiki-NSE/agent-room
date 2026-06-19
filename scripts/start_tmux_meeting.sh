#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 6 ]; then
  echo "usage: $0 <session> <host> <port> <data-dir> <project-root> <codex-auth-file>" >&2
  exit 2
fi

SESSION="$1"
HOST="$2"
PORT="$3"
DATA_DIR="$4"
PROJECT_ROOT="$5"
CODEX_AUTH_FILE="$6"
SERVER_URL="http://${HOST}:${PORT}"

quote() {
  printf "%q" "$1"
}

COMMAND="cd $(quote "$PROJECT_ROOT") && export AGENT_ROOM_SERVER_URL=$(quote "$SERVER_URL") && uv sync && uv run agent-room serve --host $(quote "$HOST") --port $(quote "$PORT") --data-dir $(quote "$DATA_DIR") --project-root $(quote "$PROJECT_ROOT") --codex-auth-file $(quote "$CODEX_AUTH_FILE")"

tmux new-session -d -s "$SESSION" -n meeting "$COMMAND"
tmux attach -t "$SESSION"
