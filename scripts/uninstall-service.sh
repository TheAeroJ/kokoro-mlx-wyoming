#!/bin/zsh
# Stop and remove the launchd agent.
set -euo pipefail

LABEL="com.kokoro-mlx-wyoming"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL"
