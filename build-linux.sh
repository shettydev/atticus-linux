#!/bin/bash
# Builds and installs Atticus for Linux: compiles atticus-host (+ GUI if GTK4/WebKitGTK6 dev libs
# are present), atticus-inputd, and atticus-browser-host, installs resources into ~/.local/share/atticus,
# binaries into ~/.local/bin, desktop entry + systemd user services, and restarts Atticus.
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(tr -d '\n' < VERSION)
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid VERSION: $VERSION"; exit 1; }
command -v python3 >/dev/null || { echo "Atticus needs Python 3. Install it, then run ./build-linux.sh again."; exit 1; }
command -v cargo >/dev/null || { echo "Atticus needs Rust (cargo). Install via rustup, then run ./build-linux.sh again."; exit 1; }

FEATURES=()
if pkg-config --exists gtk4 webkitgtk-6.0 gtk4-layer-shell-0 2>/dev/null; then
  FEATURES=(--features layer-shell)
  echo "Building atticus-host with GTK4 + WebKitGTK6 + layer-shell GUI..."
elif pkg-config --exists gtk4 webkitgtk-6.0 2>/dev/null; then
  FEATURES=(--features gui)
  echo "Building atticus-host with GTK4 + WebKitGTK6 GUI..."
else
  echo "Note: gtk4 / webkitgtk-6.0 dev libraries not detected; building CLI-only atticus-host."
fi

cargo build --manifest-path linux/Cargo.toml --release -p atticus-host "${FEATURES[@]}"
cargo build --manifest-path linux/Cargo.toml --release -p atticus-inputd -p atticus-browser-host

BIN_DIR="${HOME}/.local/bin"
SHARE_DIR="${HOME}/.local/share/atticus"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
AUTOSTART_DIR="${HOME}/.config/autostart"

mkdir -p "$BIN_DIR" "$SHARE_DIR/ui" "$SHARE_DIR/atticus_platform" "$APP_DIR" "$ICON_DIR" "$SYSTEMD_DIR" "$AUTOSTART_DIR" "${HOME}/.config/atticus"

install -m 755 linux/target/release/atticus-host "$BIN_DIR/atticus-host"
install -m 755 linux/target/release/atticus-inputd "$BIN_DIR/atticus-inputd"
install -m 755 linux/target/release/atticus-browser-host "$BIN_DIR/atticus-browser-host"

install -m 755 atticus.py "$SHARE_DIR/atticus.py"
cp VERSION "$SHARE_DIR/VERSION"
cp atticus_platform/*.py "$SHARE_DIR/atticus_platform/"
cp ui/*.html ui/*.js ui/*.css "$SHARE_DIR/ui/"
cp linux/packaging/atticus.svg "$ICON_DIR/atticus.svg"

sed "s|@BIN_DIR@|$BIN_DIR|g; s|@SHARE_DIR@|$SHARE_DIR|g" linux/packaging/atticus.desktop.in > "$APP_DIR/atticus.desktop"
cp "$APP_DIR/atticus.desktop" "$AUTOSTART_DIR/atticus.desktop"

sed "s|@BIN_DIR@|$BIN_DIR|g; s|@SHARE_DIR@|$SHARE_DIR|g" linux/systemd/atticus.service.in > "$SYSTEMD_DIR/atticus.service"
sed "s|@BIN_DIR@|$BIN_DIR|g" linux/systemd/atticus-inputd.service.in > "$SYSTEMD_DIR/atticus-inputd.service"

# Install Firefox Native Messaging manifest automatically if Mozilla config dir exists
FIREFOX_NM="${HOME}/.mozilla/native-messaging-hosts"
mkdir -p "$FIREFOX_NM"
sed "s|@ATTICUS_BROWSER_HOST_BIN@|$BIN_DIR/atticus-browser-host|g" \
  linux/browser-extension/com.atticus.browser_bridge.firefox.json.in > "$FIREFOX_NM/com.atticus.browser_bridge.json"

if [ -n "${ATTICUS_NO_RESTART:-}" ]; then
  exit 0
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE HYPRLAND_INSTANCE_SIGNATURE SWAYSOCK 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user enable --now atticus-inputd.service 2>/dev/null || true
  systemctl --user enable --now atticus.service 2>/dev/null || true
  systemctl --user restart atticus.service 2>/dev/null || true
fi

echo "Atticus Linux installed to $BIN_DIR/atticus-host. Hold Ctrl+Alt+Space and talk."
