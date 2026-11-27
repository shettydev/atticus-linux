#!/bin/sh
# Live GNOME tests inside atticus-gnome-test: headless GNOME Shell with the Atticus extension enabled.
set -e
export XDG_RUNTIME_DIR=/tmp/xdg HOME=/tmp/home; mkdir -p $XDG_RUNTIME_DIR $HOME/.local/share/gnome-shell/extensions; chmod 700 $XDG_RUNTIME_DIR
cp -R linux/atticus_shell/desktop/gnome-extension/atticus@atticus.app $HOME/.local/share/gnome-shell/extensions/
rm -rf /run/systemd/seats; mkdir -p /run/dbus; dbus-daemon --system --fork 2>/dev/null || true   # no logind in a container: the shell's dummy login manager
exec dbus-run-session -- sh -c '
  gsettings set org.gnome.shell disable-user-extensions false
  gsettings set org.gnome.shell enabled-extensions "[\"atticus@atticus.app\"]"
  ATTICUS_EXTENSION_TEST=1 gnome-shell --headless --wayland --no-x11 --virtual-monitor 1280x800 > /tmp/shell.log 2>&1 &
  for i in $(seq 1 60); do gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner app.atticus.ShellBridge 2>/dev/null | grep -q true && break; sleep 0.5; done
  ATTICUS_GNOME_LIVE=1 python3 -m unittest -v tests.linux.test_gnome.GnomeLive; s=$?
  grep -viE "dbus-daemon|^$" /tmp/shell.log | head -40; exit $s'
