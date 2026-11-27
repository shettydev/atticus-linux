#!/bin/bash
# System packages Atticus for Linux needs, per package manager.
# usage: deps.sh            install with the detected manager (asks for sudo)
#        deps.sh --print <apt|dnf|pacman|zypper>   list them    deps.sh --namespaces   GI namespaces they provide
#        deps.sh --ci       apt install without prompts (GitHub Actions)
set -e
NAMESPACES=('Gtk 3.0' 'WebKit2 4.1' 'GtkLayerShell 0.1' 'AyatanaAppIndicator3 0.1' 'Atspi 2.0' 'Gst 1.0' 'ECal 2.0' 'EBook 1.2')
pkgs() {
  case $1 in
    apt) echo python3 python3-gi python3-gi-cairo python3-xlib python3-evdev gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
      gir1.2-gtklayershell-0.1 gir1.2-ayatanaappindicator3-0.1 gir1.2-atspi-2.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
      gir1.2-ecal-2.0 gir1.2-ebook-1.2 gir1.2-secret-1 gstreamer1.0-plugins-good gstreamer1.0-pipewire at-spi2-core libglib2.0-bin \
      tesseract-ocr wl-clipboard xclip bubblewrap pipewire-bin pulseaudio-utils git;;
    dnf) echo python3 python3-gobject python3-xlib python3-evdev gtk3 webkit2gtk4.1 gtk-layer-shell libayatana-appindicator-gtk3 \
      at-spi2-core gstreamer1 gstreamer1-plugins-good pipewire-gstreamer evolution-data-server libsecret glib2 tesseract \
      wl-clipboard xclip bubblewrap pipewire-utils pulseaudio-utils git;;
    pacman) echo python python-gobject python-xlib python-evdev gtk3 webkit2gtk-4.1 gtk-layer-shell libayatana-appindicator \
      at-spi2-core gstreamer gst-plugins-good gst-plugin-pipewire evolution-data-server libsecret glib2 tesseract tesseract-data-eng \
      wl-clipboard xclip bubblewrap pipewire libpulse git;;
    zypper) echo python3 python3-gobject python3-gobject-cairo python3-python-xlib python3-evdev typelib-1_0-Gtk-3_0 \
      typelib-1_0-WebKit2-4_1 typelib-1_0-GtkLayerShell-0_1 typelib-1_0-AyatanaAppIndicator3-0_1 typelib-1_0-Atspi-2_0 \
      typelib-1_0-Gst-1_0 gstreamer-plugins-good pipewire-gstreamer typelib-1_0-ECal-2_0 typelib-1_0-EBook-1_2 typelib-1_0-Secret-1 \
      glib2-tools tesseract-ocr wl-clipboard xclip bubblewrap pipewire-tools pulseaudio-utils git;;
    *) return 1;;
  esac
}
detect() { for m in apt-get:apt dnf:dnf pacman:pacman zypper:zypper; do command -v "${m%%:*}" >/dev/null && { echo "${m#*:}"; return; }; done; return 1; }
case ${1:-} in
  --print) pkgs "$2" | tr ' ' '\n' | grep .; exit;;
  --namespaces) printf '%s\n' "${NAMESPACES[@]}"; exit;;
  --ci) sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $(pkgs apt); exit;;
  "") ;;
  *) sed -n 3,6p "$0"; exit 2;;
esac
m=$(detect) || { echo "No apt, dnf, pacman or zypper found. Install these GI typelibs yourself: ${NAMESPACES[*]}"; exit 1; }
echo "Atticus needs these system packages (sudo asks for your password once):"; pkgs "$m"
case $m in
  apt) sudo apt-get install -y $(pkgs apt);;
  dnf) sudo dnf install -y $(pkgs dnf);;
  pacman) sudo pacman -S --needed --noconfirm $(pkgs pacman);;
  zypper) sudo zypper --non-interactive install $(pkgs zypper);;
esac
