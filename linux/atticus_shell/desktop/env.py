"""Which Linux desktop Atticus runs on: the session type, the compositor bridge to use, and the portals offered."""
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class DesktopEnv:
    session: str                 # 'wayland' | 'x11' | 'tty'
    compositor: str              # 'gnome' | 'kde' | 'hyprland' | 'sway' | 'x11' | 'other'
    portals: frozenset = frozenset()


PORTAL = re.compile(r'interface org\.freedesktop\.portal\.(\w+)')


def probe_portals() -> set:
    """Interface names (GlobalShortcuts, Screenshot, ScreenCast, ...) xdg-desktop-portal offers on this session bus."""
    out = subprocess.run(['gdbus', 'introspect', '--session', '--dest', 'org.freedesktop.portal.Desktop',
                          '--object-path', '/org/freedesktop/portal/desktop'],
                         capture_output=True, text=True, timeout=2).stdout
    return set(PORTAL.findall(out or ''))


def _session(environ: Mapping[str, str]) -> str:
    kind = (environ.get('XDG_SESSION_TYPE') or '').lower()
    if kind in ('wayland', 'x11'):
        return kind
    if environ.get('WAYLAND_DISPLAY'):
        return 'wayland'
    if environ.get('DISPLAY'):
        return 'x11'
    return 'tty'


def _compositor(session: str, environ: Mapping[str, str]) -> str:
    if session == 'x11':
        return 'x11'   # EWMH works the same under every X11 window manager, GNOME and KDE included
    if environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        return 'hyprland'
    if environ.get('SWAYSOCK'):
        return 'sway'
    desktops = {d.strip().lower() for d in (environ.get('XDG_CURRENT_DESKTOP') or '').split(':')}
    if 'gnome' in desktops:
        return 'gnome'
    if 'kde' in desktops:
        return 'kde'
    return 'other'


def detect(environ: Mapping[str, str] = os.environ, probe: Callable[[], set] = probe_portals) -> DesktopEnv:
    session = _session(environ)
    try:
        portals = frozenset(probe()) if session != 'tty' else frozenset()
    except (OSError, subprocess.SubprocessError):
        portals = frozenset()
    return DesktopEnv(session, _compositor(session, environ) if session != 'tty' else 'other', portals)
