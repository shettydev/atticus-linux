#!/bin/bash
# Installs Atticus for Linux: stages the shell, brain and UI, swaps them in atomically, starts it with the session.
# Run it again after any change. Env: ATTICUS_PREFIX (default ~/.local/share/atticus), ATTICUS_NO_RESTART=1 (updater),
# ATTICUS_SKIP_SYSTEM=1 (no sudo, udev or systemctl), ATTICUS_SKIP_DEPS=1, ATTICUS_ALLOW_NON_LINUX=1 (tests only).
set -e; cd "$(dirname "$0")"
VERSION=$(tr -d '\n' < VERSION)
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid VERSION: $VERSION"; exit 1; }
[ "$(uname)" = Linux ] || [ -n "$ATTICUS_ALLOW_NON_LINUX" ] || { echo "build-linux.sh builds Atticus for Linux. On macOS use ./build.sh."; exit 1; }
command -v python3 >/dev/null || { echo "Atticus needs Python 3.12 or later."; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "Atticus needs Python 3.12 or later."; exit 1; }
if [ -z "$ATTICUS_SKIP_DEPS" ] && ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1")' 2>/dev/null; then
  echo "Missing GTK/WebKit for Python. Run: linux/packaging/deps.sh   then run ./build-linux.sh again."; exit 1
fi
command -v claude >/dev/null || [ -x ~/.local/bin/claude ] || echo "Note: Claude Code is not installed yet. Atticus's setup window can install it, or you can pick Devin or Pi."
PREFIX=${ATTICUS_PREFIX:-$HOME/.local/share/atticus}
APPS=${ATTICUS_APPLICATIONS:-$HOME/.local/share/applications}
UNITS=${ATTICUS_SYSTEMD_DIR:-$HOME/.config/systemd/user}
COMMIT=$(git rev-parse HEAD 2>/dev/null || echo unknown)
# sherpa-onnx (built-in speech, wake word) is pip-only: a venv that still sees the system's GTK/WebKit bindings.
VENV=${ATTICUS_VENV:-$HOME/.local/share/atticus-venv}
if [ -z "$ATTICUS_SKIP_DEPS" ]; then
  [ -x "$VENV/bin/python3" ] || python3 -m venv --system-site-packages "$VENV" || { echo "Could not create $VENV. Run linux/packaging/deps.sh, then ./build-linux.sh again."; exit 1; }
  "$VENV/bin/python3" -m pip install -q --disable-pip-version-check --upgrade sherpa-onnx ||
    echo "Note: sherpa-onnx did not install, so built-in speech is off. Whisper (Settings → Voice & Sound) still works."
fi
PY=python3; [ -x "$VENV/bin/python3" ] && PY="$VENV/bin/python3"
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/atticus"
mkdir -p "$APP/ui"
cp atticus.py brain_linux.py "$APP/"
cp -R linux/atticus_shell "$APP/"
find "$APP/atticus_shell" -name __pycache__ -prune -exec rm -rf {} +
cp ui/*.html ui/*.js ui/*.css "$APP/ui/"
[ -d ui/sounds ] && cp -R ui/sounds "$APP/ui/"
cp docs/icon.png "$APP/ui/icon.png"
cat > "$APP/atticus-shell" <<L
#!/bin/sh
# Atticus for Linux $VERSION ($COMMIT). No arguments: the app. --flags: the helper CLI the brain calls.
export ATTICUS_RESOURCES_DIR="$PREFIX" ATTICUS_VERSION="$VERSION" ATTICUS_COMMIT="$COMMIT" ATTICUS_SOURCE="$(pwd)"
export PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PY" -m atticus_shell "\$@"
L
chmod +x "$APP/atticus-shell" "$APP/atticus.py"
mkdir -p "$(dirname "$PREFIX")"
[ ! -e "$PREFIX" ] || mv "$PREFIX" "$STAGE/previous"
if ! mv "$APP" "$PREFIX"; then
  [ ! -e "$STAGE/previous" ] || mv "$STAGE/previous" "$PREFIX"
  exit 1
fi
mkdir -p "$APPS" "$UNITS" ~/.config/atticus
sed "s|@PREFIX@|$PREFIX|g" linux/packaging/atticus.desktop > "$APPS/atticus.desktop"
sed "s|@PREFIX@|$PREFIX|g" linux/packaging/atticus.service > "$UNITS/atticus.service"
EXT=linux/atticus_shell/desktop/gnome-extension/atticus@atticus.app
if [ -d "$EXT" ] && [ -z "$ATTICUS_SKIP_SYSTEM" ]; then
  mkdir -p ~/.local/share/gnome-shell/extensions && rm -rf ~/.local/share/gnome-shell/extensions/atticus@atticus.app &&
    cp -R "$EXT" ~/.local/share/gnome-shell/extensions/
  command -v gnome-extensions >/dev/null && gnome-extensions enable atticus@atticus.app 2>/dev/null || true
fi
if [ -z "$ATTICUS_SKIP_SYSTEM" ] && [ ! -w /dev/uinput ]; then
  echo "Atticus clicks and types for you through /dev/uinput. One rule gives your login session access (sudo asks once):"
  sudo install -m 0644 linux/packaging/60-atticus-uinput.rules /etc/udev/rules.d/60-atticus-uinput.rules &&
    sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput || echo "Skipped: clicks and typing then need the uinput rule (Settings shows how)."
fi
[ -n "$ATTICUS_NO_RESTART" ] && exit 0
systemctl --user daemon-reload
systemctl --user enable atticus.service >/dev/null 2>&1 || true
systemctl --user restart atticus.service || { echo "Could not start the Atticus service; opening it directly instead."; nohup "$PREFIX/atticus-shell" >/dev/null 2>&1 & }
echo "Atticus is running. Hold Ctrl+Alt+Space and talk."
