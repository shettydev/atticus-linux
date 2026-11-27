# Headless GNOME Shell for live extension tests: docker build -f linux/packaging/gnome-test.Dockerfile -t atticus-gnome-test .
FROM atticus-linux-test
RUN apt-get update && apt-get install -y --no-install-recommends gnome-shell gnome-shell-extension-prefs mutter dbus-daemon \
    gnome-session-bin xwayland && rm -rf /var/lib/apt/lists/*
