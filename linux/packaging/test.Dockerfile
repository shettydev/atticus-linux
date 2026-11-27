# Linux test image: the GI stack the shell needs, plus headless X11 and Wayland for overlay tests.
# docker build -f linux/packaging/test.Dockerfile -t atticus-linux-test .
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-gi python3-gi-cairo python3-xlib python3-evdev python3-dbus \
    gir1.2-gtk-3.0 gir1.2-webkit2-4.1 gir1.2-gtklayershell-0.1 gir1.2-ayatanaappindicator3-0.1 \
    gir1.2-atspi-2.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-ecal-2.0 gir1.2-ebook-1.2 gir1.2-secret-1 \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-pipewire \
    at-spi2-core dbus-x11 xvfb weston xclip wl-clipboard tesseract-ocr pipewire-bin pulseaudio-utils \
    git ca-certificates bubblewrap \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# An EWMH window manager for live X11 bridge tests (tests/linux/test_x11_live.py).
RUN apt-get update && apt-get install -y --no-install-recommends openbox x11-utils && rm -rf /var/lib/apt/lists/*
