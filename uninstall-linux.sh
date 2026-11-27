#!/bin/bash
# Completely stops and removes Atticus for Linux.
set -euo pipefail

echo "Stopping Atticus services and processes…"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now atticus.service 2>/dev/null || true
  systemctl --user disable --now atticus-inputd.service 2>/dev/null || true
  for unit in "$HOME/.config/systemd/user"/atticus-routine-*.timer; do
    [ -e "$unit" ] && systemctl --user disable --now "$(basename "$unit")" 2>/dev/null || true
  done
fi

pkill -f "atticus-host" 2>/dev/null || true
pkill -f "atticus-inputd" 2>/dev/null || true
pkill -f "atticus-browser-host" 2>/dev/null || true

echo "Removing installed binaries, services, desktop entries, and caches…"
rm -f "$HOME/.local/bin/atticus-host" \
      "$HOME/.local/bin/atticus-inputd" \
      "$HOME/.local/bin/atticus-browser-host" \
      "$HOME/.local/share/applications/atticus.desktop" \
      "$HOME/.config/autostart/atticus.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/atticus.svg" \
      "$HOME/.config/systemd/user/atticus.service" \
      "$HOME/.config/systemd/user/atticus-inputd.service" \
      "$HOME/.config/systemd/user"/atticus-routine-*.{service,timer} \
      "$HOME/.mozilla/native-messaging-hosts/com.atticus.browser_bridge.json" \
      "$HOME/.config/google-chrome/NativeMessagingHosts/com.atticus.browser_bridge.json" \
      "$HOME/.config/chromium/NativeMessagingHosts/com.atticus.browser_bridge.json" \
      "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/com.atticus.browser_bridge.json"

rm -rf "$HOME/.local/share/atticus" \
       "$HOME/.cache/atticus" \
       "$HOME/.local/share/gnome-shell/extensions/atticus@atticus.app"

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload 2>/dev/null || true
fi

if [ "${1:-}" = "--purge" ]; then
  echo "Removing user settings, memory, meetings, and models (~/.config/atticus and ~/.atticus)…"
  rm -rf "$HOME/.config/atticus" "$HOME/.atticus"
else
  echo "Kept your config/history in ~/.config/atticus. Run './uninstall-linux.sh --purge' to remove that too."
fi

echo "Atticus Linux has been uninstalled."
