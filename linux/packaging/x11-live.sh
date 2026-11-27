#!/bin/sh
# Live X11 tests inside the test image: Xvfb + openbox, then the X11 bridge tests.
Xvfb :99 -screen 0 1600x900x24 >/dev/null 2>&1 &
export DISPLAY=:99 ATTICUS_X11_LIVE=1
sleep 1; openbox >/dev/null 2>&1 & sleep 1
exec python3 -m unittest tests.linux.test_x11_live tests.linux.test_hotkeys tests.linux.test_input "$@"
