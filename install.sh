#!/bin/bash
# Atticus installer. Paste in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/SaarthurR/atticus-mac/main/install.sh | bash
# Downloads the source to ~/.atticus (Atticus updates itself from there), builds the app, opens it.
set -e
if [[ $(uname -s) == Linux ]]; then
  src="$(dirname "${BASH_SOURCE[0]:-}")"
  if [[ ! -x "$src/linux/build-deb.sh" ]]; then
    echo "Run ./install.sh from the Atticus Linux source checkout." >&2
    exit 1
  fi
  cd "$src"
  packages=$(mktemp -d /tmp/atticus-packages.XXXXXX)
  trap 'rm -rf "$packages"' EXIT
  ./linux/build-deb.sh "$packages"
  # APT's _apt user needs to traverse the directory and read the finished packages.
  chmod 0644 "$packages"/*.deb
  chmod 0711 "$packages"
  if [[ $EUID -eq 0 ]]; then apt install --reinstall "$packages"/*.deb
  else sudo apt install --reinstall "$packages"/*.deb; fi
  echo "Log out and back in, then enable the bridge with: gnome-extensions enable desktop@atticus.local"
  exit 0
fi
DIR="${ATTICUS_DIR:-$HOME/.atticus}"
echo "Installing Atticus…"
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 26 ] || { echo "Atticus needs macOS 26 or later."; exit 1; }
if ! xcode-select -p >/dev/null 2>&1; then
  echo "First, Apple's command line tools. A macOS window will open: click Install."
  echo "When it finishes, paste the install line again."
  xcode-select --install 2>/dev/null || true
  exit 1
fi
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q --ff-only || { echo "Could not update $DIR. Delete that folder, then paste the install line again."; exit 1; }
else
  git clone -q --depth 1 https://github.com/SaarthurR/atticus-mac.git "$DIR"
fi
"$DIR/build.sh"
echo "Done. The Atticus setup window walks you through the rest."
