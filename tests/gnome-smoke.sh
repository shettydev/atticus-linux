#!/bin/bash
# Build, install and exercise the Ubuntu packages in an isolated two-monitor Wayland session.
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -i -v "$PWD:/src:ro" -w /src ubuntu:26.04 bash -se <<'CONTAINER'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends gnome-shell dbus-x11 gir1.2-webkit-6.0 python3-gi libgl1-mesa-dri
# Exercise the source installer users run. APT can accept the transaction in this container.
echo 'APT::Get::Assume-Yes "true";' > /etc/apt/apt.conf.d/99-atticus-smoke
# Check readability even in containers where APT suppresses its fallback notice.
apt() {
    for arg in "$@"; do
        if [[ "$arg" == *.deb ]] && ! runuser -u _apt -- test -r "$arg"; then
            echo "APT sandbox user cannot read $arg" >&2
            return 1
        fi
    done
    command apt "$@"
}
export -f apt
./install.sh </dev/null
install -d /run/dbus
dbus-daemon --system --fork
# The container has no logind. Let GNOME use its built-in dummy login manager.
if [ -d /run/systemd/seats ]; then rmdir /run/systemd/seats; fi
install -d -m700 -o ubuntu -g ubuntu /run/user/1000
runuser -u ubuntu -- env HOME=/home/ubuntu XDG_RUNTIME_DIR=/run/user/1000 GTK_A11Y=none \
    GSK_RENDERER=cairo LIBGL_ALWAYS_SOFTWARE=1 WAYLAND_DISPLAY=atticus-test ATTICUS_TEST_GNOME=1 \
    dbus-run-session -- bash -se <<'SESSION'
gsettings set org.gnome.shell enabled-extensions "['desktop@atticus.local']"
gnome-shell --headless --no-x11 --wayland-display atticus-test \
    --virtual-monitor 1280x800 --virtual-monitor 1024x768 >/tmp/gnome.log 2>&1 &
shell_pid=$!
trap 'kill "$shell_pid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 150); do
    if gdbus call --session --dest org.gnome.Shell --object-path /org/atticus/Desktop1 \
        --method org.atticus.Desktop1.Snapshot >/dev/null 2>&1; then break; fi
    if ! kill -0 "$shell_pid" || [ "$attempt" = 150 ]; then cat /tmp/gnome.log; exit 1; fi
    sleep .2
done
python3 tests/gnome_placement.py
# Docker blocks nested proc mounts. Only this local-page WebKit smoke test uses
# the disposable container as its sandbox. The shipped launcher uses WebKit defaults.
WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 python3 tests/gnome_ask.py
gnome-extensions disable desktop@atticus.local
if output=$(atticus --place Missing left 2>&1); then exit 1; fi
[[ "$output" == "NOT DONE: Enable the Atticus Desktop extension"* ]]
SESSION
CONTAINER
