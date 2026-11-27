#!/bin/bash
# Atticus Linux installer.
# Clones/updates ~/.atticus, checks distro dependencies, builds atticus-host + helpers, and enables systemd user units.
set -euo pipefail

DIR="${ATTICUS_DIR_SRC:-$HOME/.atticus}"
echo "Installing Atticus for Linux…"

if [ "$(uname -s)" != "Linux" ]; then
  echo "This installer is for Linux. On macOS, run ./install.sh instead."
  exit 1
fi

check_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! check_cmd cargo; then
  echo "Rust toolchain (cargo) is required. Install with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
  exit 1
fi
if ! check_cmd python3; then
  echo "Python 3 is required. Install python3 with your package manager."
  exit 1
fi

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q --ff-only || { echo "Could not update $DIR."; exit 1; }
elif [ -f "$(dirname "$0")/build-linux.sh" ]; then
  DIR="$(cd "$(dirname "$0")" && pwd)"
else
  git clone -q --depth 1 https://github.com/SaarthurR/atticus-mac.git "$DIR"
fi

"$DIR/build-linux.sh"

echo ""
echo "Optional recommended setup:"
echo "  • For global Wayland hold-to-talk & modifier dictation keys, add yourself to the 'input' group:"
echo "      sudo usermod -aG input \"\$USER\" && sudo cp \"$DIR/linux/udev/99-atticus-input.rules\" /etc/udev/rules.d/"
echo "  • For browser automation in Chrome/Firefox, load the extension in: $DIR/linux/browser-extension/"
echo "  • On GNOME Wayland, copy $DIR/linux/gnome-extension to ~/.local/share/gnome-shell/extensions/atticus@atticus.app"
echo "Done."
