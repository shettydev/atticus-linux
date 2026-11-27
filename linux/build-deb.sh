#!/bin/bash
# Build the Ubuntu preview packages without installing into the running session.
set -eu
cd "$(dirname "$0")/.."
out=${1:-dist}
mkdir -p "$out"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
version="$(cat VERSION)~linux2"
app="$stage/atticus"
bridge="$stage/atticus-gnome"
mkdir -p "$app/DEBIAN" "$app/usr/bin" "$app/usr/lib/atticus"
install -m644 atticus.py linux_desktop.py linux_app.py VERSION "$app/usr/lib/atticus/"
install -d "$app/usr/lib/atticus/ui"
install -m644 ui/{ui.html,orbs.js,i18n.js,tokens.css} "$app/usr/lib/atticus/ui/"
install -Dm644 linux/org.atticus.Desktop.desktop "$app/usr/share/applications/org.atticus.Desktop.desktop"
install -Dm644 docs/icon.png "$app/usr/share/pixmaps/org.atticus.Desktop.png"
PYTHONPYCACHEPREFIX="$stage/cache" python3 -m py_compile "$app/usr/lib/atticus/"*.py
cat > "$app/usr/bin/atticus" <<'LAUNCHER'
#!/bin/sh
case "$1" in
    --ask) exec /usr/bin/python3 /usr/lib/atticus/linux_desktop.py "$@" ;;
    '') exec /usr/bin/python3 /usr/lib/atticus/linux_app.py "$@" ;;
    --place) exec /usr/bin/python3 /usr/lib/atticus/atticus.py "$@" ;;
    --version) cat /usr/lib/atticus/VERSION ;;
    --help|-h) echo 'atticus opens the typed ask box. Use atticus --ask "open Firefox" or atticus --place APP [left|right|full|fullscreen|center] [current|primary|laptop|monitor|CONNECTOR] [TITLE]' ;;
    *) echo 'This Ubuntu preview supports --ask and --place. See atticus --help.' >&2; exit 2 ;;
esac
LAUNCHER
chmod 755 "$app/usr/bin/atticus"
cat > "$app/DEBIAN/control" <<CONTROL
Package: atticus
Version: $version
Architecture: all
Maintainer: Atticus contributors <noreply@localhost>
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-webkit-6.0
Recommends: atticus-gnome (= $version)
Description: Typed Atticus assistant and verified GNOME window placement
CONTROL
ext="$bridge/usr/share/gnome-shell/extensions/desktop@atticus.local"
mkdir -p "$bridge/DEBIAN" "$ext"
install -m644 linux/gnome/{extension.js,metadata.json} "$ext/"
install -Dm644 linux/gnome/*.gschema.xml "$bridge/usr/share/glib-2.0/schemas/org.gnome.shell.extensions.atticus.gschema.xml"
cat > "$bridge/DEBIAN/control" <<CONTROL
Package: atticus-gnome
Version: $version
Architecture: all
Maintainer: Atticus contributors <noreply@localhost>
Depends: gnome-shell (>= 50), gnome-shell (<< 51)
Description: GNOME Shell 50 window placement bridge for Atticus
CONTROL
install -Dm644 LICENSE-Plip "$app/usr/share/doc/atticus/LICENSE-Plip"
for package in "$app" "$bridge"; do
    install -Dm644 LICENSE "$package/usr/share/doc/$(basename "$package")/copyright"
    dpkg-deb --root-owner-group --build "$package" "$out/"
done
