#!/bin/zsh
# Install (or reinstall) the server as a launchd agent for the current user.
# Usage: scripts/install-service.sh [port] [voice]
set -euo pipefail

PORT="${1:-10200}"
VOICE="${2:-af_heart}"
PROJECT_DIR="${0:A:h:h}"
LOG_DIR="$HOME/Library/Logs"
LABEL="com.kokoro-mlx-wyoming"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -x "$PROJECT_DIR/.venv/bin/kokoro-mlx-wyoming" ]]; then
  echo "Run 'uv sync' in $PROJECT_DIR first." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
    -e "s#__PORT__#$PORT#g" \
    -e "s#__VOICE__#$VOICE#g" \
    -e "s#__LOG_DIR__#$LOG_DIR#g" \
    "$PROJECT_DIR/launchd/$LABEL.plist" > "$TARGET"

# Replace any running copy.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "Installed $TARGET"
echo "Listening on port $PORT with voice $VOICE"
echo "Logs: tail -f $LOG_DIR/kokoro-mlx-wyoming.log"
