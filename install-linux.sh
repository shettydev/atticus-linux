#!/bin/bash
# Atticus for Linux installer. Paste in a terminal:
#   curl -fsSL https://raw.githubusercontent.com/shettydev/atticus-linux/main/install-linux.sh | bash
# Downloads the source to ~/.atticus (Atticus updates itself from there), installs system packages, builds, starts it.
set -e
DIR="${ATTICUS_DIR_SRC:-$HOME/.atticus}"
REPO="${ATTICUS_REPO:-https://github.com/shettydev/atticus-linux.git}"
echo "Installing Atticus…"
[ "$(uname)" = Linux ] || { echo "This installer is for Linux. On a Mac use install.sh."; exit 1; }
command -v git >/dev/null || { echo "Atticus needs git. Install it with your package manager, then paste the install line again."; exit 1; }
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q --ff-only || { echo "Could not update $DIR. Delete that folder, then paste the install line again."; exit 1; }
else
  git clone -q --depth 1 "$REPO" "$DIR"
fi
"$DIR/linux/packaging/deps.sh"
"$DIR/build-linux.sh"
echo "Done. The Atticus setup window walks you through the rest."
