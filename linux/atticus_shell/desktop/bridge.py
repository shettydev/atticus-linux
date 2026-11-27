"""The compositor bridge: everything Atticus needs from the desktop that Wayland gives no common API for.

Coordinates are logical (layout) pixels everywhere; Output.scale converts to the physical pixels of a screenshot.
Backends: x11 (EWMH), gnome (Shell extension), kde (KWin script), hyprland, sway, portal (screenshots only).
"""
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Optional, Protocol

BUILTIN = re.compile(r'^(eDP|LVDS|DSI)', re.I)   # a laptop's own panel: "laptop" in --place
RELOGIN = 'Log out and back in once to finish setup.'


@dataclass
class Output:
    name: str
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0
    builtin: bool = False
    work_area: tuple = ()          # (x, y, w, h) without panels and docks; the whole output when unknown

    def __post_init__(self):
        if not self.work_area:
            self.work_area = (self.x, self.y, self.width, self.height)

    def contains(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass
class Window:
    id: str
    app_id: str
    pid: int
    title: str
    x: int
    y: int
    width: int
    height: int
    output: str = ''
    workspace: str = ''
    minimized: bool = False
    fullscreen: bool = False
    focused: bool = False


class PointerUnavailable(Exception):
    """This backend cannot read the global pointer (Sway): the pen uses its own capture surface instead."""


class Bridge(Protocol):
    available: bool
    unavailable_reason: str
    covers_hidden: bool            # screenshot_window works for covered windows

    def outputs(self) -> list: ...
    def windows(self) -> list: ...          # stacking order, top first
    def focused(self) -> Optional[Window]: ...
    def pointer(self) -> tuple: ...
    def move_resize(self, win_id: str, x: int, y: int, w: int, h: int) -> bool: ...
    def set_fullscreen(self, win_id: str, on: bool) -> bool: ...
    def activate(self, win_id: str) -> bool: ...
    def lower(self, win_id: str) -> bool: ...
    def screenshot_output(self, name: str, path: str) -> bool: ...   # PNG, physical pixels
    def screenshot_window(self, win_id: str, path: str) -> bool: ...
    def keep_above(self, win_id: str, on: bool) -> bool: ...


class Unavailable:
    """A bridge that can do nothing and says why; every method answers 'no' instead of raising."""
    available = False
    covers_hidden = False

    def __init__(self, reason):
        self.unavailable_reason = reason

    def outputs(self):
        return []

    def windows(self):
        return []

    def focused(self):
        return None

    def pointer(self):
        raise PointerUnavailable(self.unavailable_reason)

    def move_resize(self, *_):
        return False

    def set_fullscreen(self, *_):
        return False

    def activate(self, *_):
        return False

    def lower(self, *_):
        return False

    def screenshot_output(self, *_):
        return False

    def screenshot_window(self, *_):
        return False

    def keep_above(self, *_):
        return False


def output_at(outputs, x, y):
    """The output containing a point, else the nearest by center distance ('' when there are none)."""
    for o in outputs:
        if o.contains(x, y):
            return o.name
    best = min(outputs, key=lambda o: (o.x + o.width / 2 - x) ** 2 + (o.y + o.height / 2 - y) ** 2, default=None)
    return best.name if best else ''


def write_png(path, width, height, rgba):
    """PNG without Pillow."""
    if len(rgba) != width * height * 4:
        raise ValueError(f'expected {width * height * 4} RGBA bytes, got {len(rgba)}')
    stride = width * 4
    rows = b''.join(b'\x00' + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(rows, 3)) + chunk(b'IEND', b''))


def png_size(path):
    with open(path, 'rb') as f:
        head = f.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    return struct.unpack('>II', head[16:24])


def bgra_to_rgba(data):
    out = bytearray(data)
    out[0::4], out[2::4] = data[2::4], data[0::4]
    out[3::4] = b'\xff' * (len(data) // 4)
    return bytes(out)


def bridge_for(env, **deps):
    """The bridge for this desktop. deps replace transports in tests (conn=, bus=, run=, screenshot=)."""
    if env.compositor == 'x11':
        from atticus_shell.desktop.x11 import X11Bridge
        return X11Bridge(deps.get('conn'))
    if env.compositor == 'gnome':
        from atticus_shell.desktop.gnome import GnomeBridge
        return GnomeBridge(deps.get('bus'))
    if env.compositor == 'kde':
        from atticus_shell.desktop.kwin import KwinBridge
        return KwinBridge(deps.get('bus'))
    if env.compositor == 'hyprland':
        from atticus_shell.desktop.hyprland import HyprlandBridge
        return HyprlandBridge(**({'run': deps['run']} if 'run' in deps else {}))
    if env.compositor == 'sway':
        from atticus_shell.desktop.sway import SwayBridge
        return SwayBridge(**({'run': deps['run']} if 'run' in deps else {}))
    from atticus_shell.desktop.portal import PortalBridge
    return PortalBridge(deps.get('screenshot'))
