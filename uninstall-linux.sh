#!/bin/bash
# Uninstalls Atticus for Linux. Run it as yourself from your desktop session; it asks for sudo only for the udev rule.
#
#   ./uninstall-linux.sh            remove the app; keep your settings, history and the downloaded source
#   ./uninstall-linux.sh --purge    also delete ~/.config/atticus and ~/.atticus (asks first; can't be undone)
#   ./uninstall-linux.sh --purge --yes    the same without asking
#
# Without a copy of the source:
#   curl -fsSL https://raw.githubusercontent.com/shettydev/atticus-linux/main/uninstall-linux.sh | bash

usage() {
  cat <<'H'
usage: uninstall-linux.sh [--purge] [--yes] [--help]
  (no option)  remove the app; keep your settings, history and the downloaded source
  --purge      also delete your data (~/.config/atticus) and the source (~/.atticus). Asks first; can't be undone.
               The source is kept if it holds work that exists nowhere else.
  --yes        don't ask before --purge deletes
H
}

# Prints why the source folder must stay; prints nothing when it holds only what the remote already has.
keep_source_because() {
  local src=$1 changes unpushed stashed
  [ ! -L "$src" ] || { echo "it is a link to another folder"; return; }
  [ -e "$src/.git" ] && [ -f "$src/atticus.py" ] || { echo "it is not a git checkout of Atticus"; return; }
  command -v git >/dev/null || { echo "git is not installed, so it can't be checked"; return; }
  changes=$(git -C "$src" status --porcelain 2>/dev/null) || { echo "git could not check it"; return; }
  [ -z "$changes" ] || { echo "it has uncommitted changes"; return; }
  unpushed=$(git -C "$src" log HEAD --branches --not --remotes --oneline 2>/dev/null) || { echo "git could not check it"; return; }
  [ -z "$unpushed" ] || { echo "it has commits that were never pushed"; return; }
  stashed=$(git -C "$src" stash list 2>/dev/null) || { echo "git could not check it"; return; }
  [ -z "$stashed" ] || echo "it has stashed changes"
}

main() {
  local purge="" yes="" failed=0 arg answer dir timer why
  for arg in "$@"; do
    case $arg in
      --purge) purge=1;;
      --yes) yes=1;;
      -h|--help) usage; return 0;;
      *) echo "Unknown option: $arg"; usage; return 2;;
    esac
  done
  [ "$(uname)" = Linux ] || [ -n "${ATTICUS_ALLOW_NON_LINUX:-}" ] ||
    { echo "This uninstaller is for Linux. It changes nothing here."; return 1; }
  [ -n "${HOME:-}" ] && [ -d "$HOME" ] || { echo "HOME is not set to a folder. Nothing was changed."; return 1; }
  if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
    echo "Run this without sudo, so it removes your own Atticus. It asks for your password when it needs it."; return 1
  fi

  local data="$HOME/.config/atticus" src="${ATTICUS_DIR_SRC:-$HOME/.atticus}" share="$HOME/.local/share"
  local config="${XDG_CONFIG_HOME:-$HOME/.config}" cache="${XDG_CACHE_HOME:-$HOME/.cache}"
  local rule=/etc/udev/rules.d/60-atticus-uinput.rules

  if [ -n "$purge" ] && [ -z "$yes" ]; then   # ask before touching anything; `curl | bash` has no stdin, so ask the terminal
    if ! { exec 3</dev/tty; } 2>/dev/null; then
      echo "--purge deletes your data and there is no terminal to ask on. Add --yes if you mean it. Nothing was changed."; return 1
    fi
    echo "--purge deletes $data (settings, saved rules, history, chats, speech models)"
    echo "and $src (the source, unless it holds work that exists nowhere else). This can't be undone."
    printf 'Type yes to continue: '
    read -r answer <&3; exec 3<&-
    [ "$answer" = yes ] || { echo "Nothing was changed."; return 1; }
  fi

  echo "Stopping Atticus…"
  systemctl --user disable --now atticus.service 2>/dev/null || true
  local unit_dirs=("$HOME/.config/systemd/user")
  [ "$config" = "$HOME/.config" ] || unit_dirs+=("$config/systemd/user")
  for dir in "${unit_dirs[@]}"; do
    for timer in "$dir"/atticus-routine-*.timer; do
      [ -e "$timer" ] || continue
      systemctl --user disable --now "${timer##*/}" 2>/dev/null || true
    done
  done
  # Narrow patterns: only Atticus's own processes, never an editor or shell that merely has an Atticus path open.
  pkill -u "$(id -u)" -f '[-]m atticus_shell' 2>/dev/null || true
  pkill -u "$(id -u)" -f 'from atticus_shell[.]input import serve_clipboard_stdin' 2>/dev/null || true
  pkill -u "$(id -u)" -f 'python[0-9.]* .*[.]local/share/atticus/atticus[.]py' 2>/dev/null || true
  gnome-extensions disable atticus@atticus.app 2>/dev/null || true

  echo "Removing the app…"
  for dir in "${unit_dirs[@]}"; do
    rm -f "$dir/atticus.service" "$dir"/atticus-routine-*.timer "$dir"/atticus-routine-*.service || failed=1
  done
  systemctl --user daemon-reload 2>/dev/null || true
  rm -rf "$share/atticus" "$share/atticus-venv" "$share/gnome-shell/extensions/atticus@atticus.app" "$cache/atticus" || failed=1
  rm -f "$share/applications/atticus.desktop" || failed=1
  # the files that let browsers start Atticus's native messaging host
  find "$HOME/.config" "$HOME/.mozilla" "$HOME/.librewolf" -name app.atticus.web.json -delete 2>/dev/null || true

  if [ -e "$rule" ]; then
    echo "Removing the click-and-type permission (sudo may ask for your password)…"
    if sudo rm -f "$rule"; then
      sudo udevadm control --reload-rules || true
      sudo udevadm trigger --name-match=uinput || true
    else
      echo "Could not remove $rule. To finish: sudo rm $rule"; failed=1
    fi
  fi

  if [ -n "$purge" ]; then
    echo "Deleting your data…"
    rm -rf "$data" || failed=1
    if [ -e "$src" ] || [ -L "$src" ]; then
      why=$(keep_source_because "$src")
      if [ -n "$why" ]; then
        echo "Kept $src: $why. Delete it yourself once you're sure."
      else
        rm -rf "$src" || failed=1
      fi
    fi
  else
    echo "Kept your data ($data) and the source ($src). Run again with --purge to delete them too."
  fi

  cat <<'H'

Left for you, if you set these up:
  - remove the Atticus extension at chrome://extensions
  - delete custom keyboard shortcuts or Sway bindsym lines that run atticus-shell
  - Atticus turned desktop accessibility on; if nothing else needs it (screen readers do):
      gsettings set org.gnome.desktop.interface toolkit-accessibility false
  - log out and back in, so GNOME unloads the extension and the input permission is dropped
System packages and your AI client (Claude Code, Devin or Pi) were not touched.
H
  if [ $failed = 0 ]; then echo "Atticus is uninstalled."; else echo "Atticus is mostly uninstalled; see the messages above."; fi
  return $failed
}

main "$@"; exit   # one line: bash has read it all before --purge can delete the folder this script lives in
