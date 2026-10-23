#!/bin/sh
# Installs (or reinstalls) the bridge as a LaunchAgent that starts at login.
# Usage: launchd/install.sh            uninstall: launchd/install.sh --uninstall
set -eu
project="$(cd "$(dirname "$0")/.." && pwd)"
label="com.$(id -un).smalltv-openpets"
target="$HOME/Library/LaunchAgents/$label.plist"

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$target"
  echo "uninstalled $label"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__LABEL__|$label|g" -e "s|__PROJECT__|$project|g" -e "s|__HOME__|$HOME|g" \
  "$project/launchd/smalltv-openpets.plist.template" > "$target"
plutil -lint "$target" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$target"
echo "installed $label -> $target (log: ~/Library/Logs/smalltv-openpets.log)"
