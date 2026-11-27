#!/bin/sh
# Live shortcut tests on headless Sway and Hyprland (an image with sway, hyprland and wtype: see docs/LINUX.md "Tests").
export XDG_RUNTIME_DIR=/tmp/xdg; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
exec dbus-run-session -- sh -c '
  s=0
  WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman sway -c /dev/null > /tmp/sway.log 2>&1 &
  for i in $(seq 1 40); do ls $XDG_RUNTIME_DIR/sway-ipc.* >/dev/null 2>&1 && break; sleep 0.25; done
  export SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.* | head -1) WAYLAND_DISPLAY=$(ls $XDG_RUNTIME_DIR | grep -E "^wayland-[0-9]+$" | head -1)
  ATTICUS_SWAY_LIVE=1 python3 -m unittest -v tests.linux.test_wayland_shortcuts.SwayLive || { s=1; tail -20 /tmp/sway.log; }
  swaymsg exit >/dev/null 2>&1; sleep 1
  unset SWAYSOCK WAYLAND_DISPLAY
  ' "$@"
