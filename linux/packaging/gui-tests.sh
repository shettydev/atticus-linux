#!/bin/sh
# GTK/WebKit tests inside the test image: a session bus and Xvfb. WebKit's bubblewrap sandbox can't nest in a container.
export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
exec dbus-run-session -- xvfb-run -a -s '-screen 0 1600x900x24' python3 -m unittest "${@:-discover -s tests/linux -t .}"
