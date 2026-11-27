"""Installed apps from freedesktop .desktop entries (system, user, Flatpak, Snap): names, ids, launching, quitting."""
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass

_CACHE = (0.0, None, [])


@dataclass(frozen=True)
class DesktopApp:
    id: str          # desktop file id without .desktop: org.gnome.TextEditor
    name: str
    exec: str
    wm_class: str = ''
    icon: str = ''
    path: str = ''


def app_dirs(home=None, environ=os.environ):
    home = home or os.path.expanduser('~')
    data = [environ.get('XDG_DATA_HOME') or os.path.join(home, '.local/share')]
    data += (environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':')
    data += [os.path.join(home, '.local/share/flatpak/exports/share'), '/var/lib/flatpak/exports/share', '/var/lib/snapd/desktop']
    seen, out = set(), []
    for d in data:
        p = os.path.join(d, 'applications')
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _parse(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    entry, inside = {}, False
    for line in lines:
        line = line.strip()
        if line.startswith('['):
            inside = line == '[Desktop Entry]'
            continue
        if inside and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            entry.setdefault(k.strip(), v.strip())
    return entry


def installed(dirs=None, cache=True):
    global _CACHE
    if cache and dirs is None and _CACHE[1] is not None and time.monotonic() - _CACHE[0] < 60:
        return _CACHE[2]
    apps, seen = [], set()
    for d in dirs if dirs is not None else app_dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith('.desktop') or n in seen:
                continue
            e = _parse(os.path.join(d, n))
            seen.add(n)   # the first directory wins, as the spec says
            if not e or e.get('Type') != 'Application' or e.get('NoDisplay') == 'true' or e.get('Hidden') == 'true' or not e.get('Name'):
                continue
            apps.append(DesktopApp(n[:-len('.desktop')], e['Name'], e.get('Exec', ''), e.get('StartupWMClass', ''), e.get('Icon', ''),
                                   os.path.join(d, n)))
    if dirs is None:
        _CACHE = (time.monotonic(), True, apps)
    return apps


def _key(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def find(name, apps=None):
    apps = installed() if apps is None else apps
    k = _key(name)
    return (next((a for a in apps if a.id.lower() == (name or '').lower()), None) or next((a for a in apps if _key(a.name) == k), None)
            or next((a for a in apps if _key(a.id.split('.')[-1]) == k), None))


def exe_name(app):
    """The process name of an app: its Exec binary, or a Flatpak's app id."""
    try:
        argv = [a for a in shlex.split(app.exec) if not a.startswith('%')]
    except ValueError:
        argv = app.exec.split()
    while argv and (argv[0] == 'env' or '=' in argv[0]):
        argv = argv[1:]
    if argv and os.path.basename(argv[0]) == 'flatpak' and 'run' in argv:
        rest = [a for a in argv[argv.index('run') + 1:] if not a.startswith('-')]
        return rest[0] if rest else app.id
    return os.path.basename(argv[0]) if argv else app.id


def owns(app, window_app_id):
    """Does a window's app id (Wayland app_id / X11 WM_CLASS) belong to this app?"""
    w = (window_app_id or '').lower()
    if not w or app is None:
        return False
    candidates = {app.id.lower(), app.id.split('.')[-1].lower(), exe_name(app).lower(), app.wm_class.lower(), _key(app.name)}
    return w in candidates or _key(w) in {_key(c) for c in candidates if c}


def launch(app, uris=()):
    return subprocess.run(['gtk-launch', app.id, *uris], capture_output=True, timeout=10).returncode == 0
