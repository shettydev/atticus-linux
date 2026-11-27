"""Linux platform backend for Atticus (pure Python stdlib, zero pip dependencies).

Provides Linux equivalents for macOS AppleScript/JXA/Spotlight/launchd/QuickLook/Spotify/Browser
calls in atticus.py:
- XDG .desktop app discovery & launching (gio/gtk-launch/xdg-open)
- Display & window management delegation to atticus-host (X11/Hyprland/Sway/KWin/GNOME) with CLI fallbacks
- Pure-Python JPEG/PNG dimension parsing (replaces sips)
- XDG / poppler / ffmpegthumbnailer thumbnails (replaces qlmanage)
- Spotify playlist cache scan & MPRIS2 D-Bus playback control
- Browser automation bridge via UNIX socket (MV3 WebExtension Native Messaging) + CDP fallback
- Routines via systemd --user timers + crontab fallback
- Notifications via Freedesktop Notifications D-Bus / notify-send
- Contacts & profile import via atticus-host EDS + local vCard (.vcf) fallback
- Linux system prompt substitutions within the strict prompt character budget
"""
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


HOME = os.path.expanduser('~')


def default_app_binary(me_path):
    """Locate the Linux native host binary (atticus-host)."""
    env_app = os.environ.get('ATTICUS_APP')
    if env_app:
        return env_app
    base = Path(me_path).resolve().parent
    candidates = [
        base / 'atticus-host',
        base / 'linux' / 'target' / 'release' / 'atticus-host',
        base / 'linux' / 'target' / 'debug' / 'atticus-host',
        Path(HOME) / '.local' / 'bin' / 'atticus-host',
        Path('/usr/local/bin/atticus-host'),
        Path('/usr/bin/atticus-host'),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    found = shutil.which('atticus-host')
    return found or str(Path(HOME) / '.local' / 'bin' / 'atticus-host')


def curl_bin():
    return shutil.which('curl') or '/usr/bin/curl'


def shell_bin():
    for candidate in (os.environ.get('SHELL'), '/bin/bash', '/bin/sh'):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return '/bin/sh'


# ---------------------------------------------------------------------------
# Pure-Python image dimensions (replaces macOS `sips`)
# ---------------------------------------------------------------------------

def image_dimensions(path):
    """Return (width, height) for a JPEG or PNG file without external binaries, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if len(head) >= 24 and head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR':
                w, h = struct.unpack('>II', head[16:24])
                return (w, h) if w > 0 and h > 0 else None
            if len(head) >= 2 and head[:2] == b'\xff\xd8':
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        return None
                    while b == b'\xff':
                        b = f.read(1)
                        if not b:
                            return None
                    marker = b[0]
                    if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                        continue
                    seg_len_bytes = f.read(2)
                    if len(seg_len_bytes) < 2:
                        return None
                    seg_len = struct.unpack('>H', seg_len_bytes)[0]
                    if seg_len < 2:
                        return None
                    # SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                  0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        sof = f.read(5)
                        if len(sof) < 5:
                            return None
                        h, w = struct.unpack('>HH', sof[1:5])
                        return (w, h) if w > 0 and h > 0 else None
                    f.seek(seg_len - 2, os.SEEK_CUR)
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# XDG .desktop App Discovery, Launching & Process Control
# ---------------------------------------------------------------------------

DESKTOP_DIRS = (
    os.path.join(HOME, '.local/share/applications'),
    os.path.join(HOME, '.local/share/flatpak/exports/share/applications'),
    '/var/lib/flatpak/exports/share/applications',
    '/var/lib/snapd/desktop/applications',
    '/usr/local/share/applications',
    '/usr/share/applications',
)

_DESKTOP_INDEX_CACHE = (0.0, {})


def _parse_desktop_file(path):
    """Parse [Desktop Entry] section of a .desktop file."""
    in_entry = False
    fields = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('['):
                    if in_entry:
                        break
                    in_entry = (line == '[Desktop Entry]')
                    continue
                if not in_entry or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                k = k.strip()
                if '[' not in k and k not in fields:
                    fields[k] = v.strip()
    except OSError:
        return None
    if fields.get('Type') != 'Application':
        return None
    if fields.get('NoDisplay', '').lower() == 'true' or fields.get('Hidden', '').lower() == 'true':
        return None
    name = fields.get('Name', '').strip()
    exec_cmd = fields.get('Exec', '').strip()
    if not name or not exec_cmd:
        return None
    desktop_id = os.path.basename(path)
    wm_class = fields.get('StartupWMClass', '').strip()
    # Strip desktop field codes (%f, %F, %u, %U, %i, %c, %k, etc.)
    cleaned_exec = re.sub(r'\s+%[a-zA-Z]', '', exec_cmd).strip()
    try:
        argv = shlex.split(cleaned_exec)
    except ValueError:
        argv = cleaned_exec.split()
    exe_base = os.path.basename(argv[0]) if argv else ''
    return {
        'name': name,
        'desktop_id': desktop_id,
        'path': path,
        'exec': cleaned_exec,
        'argv': argv,
        'exe': exe_base,
        'wm_class': wm_class,
        'generic': fields.get('GenericName', '').strip(),
    }


def desktop_index(force=False):
    """Map display name -> .desktop metadata, cached for 1 hour."""
    global _DESKTOP_INDEX_CACHE
    now = time.monotonic()
    if not force and _DESKTOP_INDEX_CACHE[0] and now - _DESKTOP_INDEX_CACHE[0] < 3600:
        return _DESKTOP_INDEX_CACHE[1]
    extra_dirs = []
    xdg_data = os.environ.get('XDG_DATA_DIRS', '')
    for d in xdg_data.split(':'):
        if d:
            extra_dirs.append(os.path.join(d, 'applications'))
    seen_ids = set()
    index = {}
    for root in dict.fromkeys((*DESKTOP_DIRS, *extra_dirs)):
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith('.desktop') or fname in seen_ids:
                continue
            info = _parse_desktop_file(os.path.join(root, fname))
            if not info:
                continue
            seen_ids.add(fname)
            index.setdefault(info['name'], info)
    _DESKTOP_INDEX_CACHE = (now, index)
    return index


def installed_apps():
    """Return set of installed Linux desktop application names."""
    return set(desktop_index().keys())


def find_desktop_app(name):
    """Resolve a human app name, desktop ID, or WM class to its .desktop entry."""
    if not name:
        return None
    idx = desktop_index()
    if name in idx:
        return idx[name]
    low = name.casefold().strip()
    key = lambda s: re.sub(r'[^a-z0-9]', '', s.casefold())
    k = key(name)
    for app_name, info in idx.items():
        if app_name.casefold() == low or key(app_name) == k:
            return info
        if info['desktop_id'].removesuffix('.desktop').casefold() == low:
            return info
        if info['wm_class'] and key(info['wm_class']) == k:
            return info
        if info['exe'] and info['exe'].casefold() == low:
            return info
    return None


def app_id(app):
    """Linux equivalent of macOS bundle ID: desktop ID (e.g. google-chrome.desktop) or executable."""
    info = find_desktop_app(app)
    if info:
        return info['desktop_id']
    return app or ''


def proc_of(app):
    """Linux process / WM class identifier for an app."""
    info = find_desktop_app(app)
    if info:
        return info['wm_class'] or info['exe'] or info['name']
    return app or ''


def _proc_matches(bid_or_name):
    """Return list of PIDs owned by current user matching desktop_id, wm_class, or executable name."""
    if not bid_or_name:
        return []
    info = find_desktop_app(bid_or_name)
    needles = set()
    if info:
        for cand in (info['exe'], info['wm_class'], info['desktop_id'].removesuffix('.desktop'), info['name']):
            if cand:
                needles.add(cand.casefold())
    else:
        needles.add(bid_or_name.removesuffix('.desktop').casefold())
    needles = {n for n in needles if len(n) >= 2}
    if not needles:
        return []
    uid = os.getuid()
    pids = []
    if not os.path.isdir('/proc'):
        return []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pdir = os.path.join('/proc', entry)
        try:
            if os.stat(pdir).st_uid != uid:
                continue
            comm = Path(pdir, 'comm').read_text(errors='ignore').strip().casefold()
            cmdline = Path(pdir, 'cmdline').read_bytes().replace(b'\x00', b' ').decode('utf-8', 'ignore').strip().casefold()
            exe0 = os.path.basename(cmdline.split()[0]) if cmdline else ''
            if comm in needles or exe0 in needles or any(n in cmdline for n in needles if len(n) >= 4):
                pids.append(int(entry))
        except OSError:
            continue
    return pids


def app_running(bid):
    return bool(_proc_matches(bid))


def quit_app(bid, app_bin):
    """Gracefully close an app via atticus-host WM close, falling back to SIGTERM on user PIDs."""
    if not app_running(bid):
        return True
    try:
        subprocess.run([app_bin, '--quit-app', bid], capture_output=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for _ in range(5):
        if not app_running(bid):
            return True
        time.sleep(0.1)
    pids = _proc_matches(bid)
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    for _ in range(10):
        if not app_running(bid):
            return True
        time.sleep(0.1)
    return not app_running(bid)


def launch_target(target, bg=False):
    """Launch an app, URL, or file path on Linux without blocking. Returns (ok, app_name)."""
    t = os.path.expanduser(target)
    is_url = bool(re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', t))
    if is_url or os.path.exists(t):
        opener = shutil.which('xdg-open') or shutil.which('gio')
        if not opener:
            return False, ''
        cmd = [opener, 'open', t] if os.path.basename(opener) == 'gio' else [opener, t]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True, default_handler_app(t)
        except OSError:
            return False, ''
    info = find_desktop_app(t)
    if info:
        gio = shutil.which('gio')
        gtk_launch = shutil.which('gtk-launch')
        try:
            if gio and os.path.exists(info['path']):
                subprocess.Popen([gio, 'launch', info['path']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True, info['name']
            if gtk_launch:
                subprocess.Popen([gtk_launch, info['desktop_id']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True, info['name']
            if info['argv']:
                subprocess.Popen(info['argv'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True, info['name']
        except OSError:
            return False, ''
    exe = shutil.which(t) or shutil.which(t.lower())
    if exe:
        try:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True, os.path.basename(exe)
        except OSError:
            return False, ''
    return False, ''


def default_handler_app(target):
    """Resolve the default app name for a URL or file via xdg-mime."""
    xdg_mime = shutil.which('xdg-mime')
    if not xdg_mime:
        return ''
    try:
        if re.match(r'^https?://', target):
            mime = 'x-scheme-handler/https' if target.startswith('https:') else 'x-scheme-handler/http'
        elif re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*):', target):
            scheme = target.split(':', 1)[0].lower()
            mime = f'x-scheme-handler/{scheme}'
        else:
            r = subprocess.run([xdg_mime, 'query', 'filetype', target], capture_output=True, text=True, timeout=1)
            mime = r.stdout.strip()
        if not mime:
            return ''
        r = subprocess.run([xdg_mime, 'query', 'default', mime], capture_output=True, text=True, timeout=1)
        desktop_id = r.stdout.strip()
        info = find_desktop_app(desktop_id)
        return info['name'] if info else desktop_id.removesuffix('.desktop')
    except (OSError, subprocess.TimeoutExpired):
        return ''


# ---------------------------------------------------------------------------
# Displays & Window Management (atticus-host + compositor CLI fallbacks)
# ---------------------------------------------------------------------------

def screens(app_bin, visible=False):
    """Return [(name, x, y, w, h, built_in)] in top-left global logical coordinates."""
    # 1. Native host (supports X11/XRandR, Hyprland, Sway, KWin, GNOME)
    try:
        args = [app_bin, '--screens'] + (['--visible'] if visible else [])
        r = subprocess.run(args, capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            rows = json.loads(r.stdout)
            if isinstance(rows, list):
                return [(str(n), int(x), int(y), int(w), int(h), bool(b)) for n, x, y, w, h, b in rows]
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    # 2. Hyprland CLI fallback
    if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') and shutil.which('hyprctl'):
        try:
            r = subprocess.run(['hyprctl', 'monitors', '-j'], capture_output=True, text=True, timeout=1)
            mons = json.loads(r.stdout)
            out = []
            for m in mons:
                name = m.get('name', '')
                scale = float(m.get('scale', 1.0) or 1.0)
                w = int(round(m.get('width', 0) / scale))
                h = int(round(m.get('height', 0) / scale))
                x, y = int(m.get('x', 0)), int(m.get('y', 0))
                if visible:
                    res = m.get('reserved') or [0, 0, 0, 0]
                    if len(res) == 4:
                        x += int(res[0]); y += int(res[1])
                        w -= int(res[0]) + int(res[2]); h -= int(res[1]) + int(res[3])
                builtin = name.startswith(('eDP', 'LVDS', 'DSI'))
                out.append((name, x, y, max(1, w), max(1, h), builtin))
            if out:
                return out
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            pass
    # 3. Sway CLI fallback
    if os.environ.get('SWAYSOCK') and shutil.which('swaymsg'):
        try:
            r = subprocess.run(['swaymsg', '-t', 'get_outputs'], capture_output=True, text=True, timeout=1)
            outs = json.loads(r.stdout)
            res = []
            for o in outs:
                if not o.get('active', True):
                    continue
                rect = o.get('rect') or {}
                name = o.get('name', '')
                builtin = name.startswith(('eDP', 'LVDS', 'DSI'))
                res.append((name, int(rect.get('x', 0)), int(rect.get('y', 0)),
                            int(rect.get('width', 0)), int(rect.get('height', 0)), builtin))
            if res:
                return res
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            pass
    # 4. X11 xrandr fallback
    if os.environ.get('DISPLAY') and shutil.which('xrandr'):
        try:
            r = subprocess.run(['xrandr', '--query'], capture_output=True, text=True, timeout=1)
            res = []
            for line in r.stdout.splitlines():
                m = re.match(r'^(\S+)\s+connected\s+(primary\s+)?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', line)
                if m:
                    name, prim, w, h, x, y = m.groups()
                    builtin = name.startswith(('eDP', 'LVDS', 'DSI'))
                    row = (name, int(x), int(y), int(w), int(h), builtin)
                    if prim:
                        res.insert(0, row)
                    else:
                        res.append(row)
            if res:
                return res
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return []


def front_window(app_bin):
    """Return (app_name, window_title) of the currently focused user window."""
    env_app = os.environ.get('ATTICUS_FRONT')
    try:
        r = subprocess.run([app_bin, '--front-window'], capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            return env_app or data.get('app', ''), data.get('window', '')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    app = env_app or ''
    if not app:
        try:
            app = subprocess.run([app_bin, '--user-app'], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            app = ''
    return app, ''


def focus_app(app_bin, app_name):
    if not app_name:
        return False
    try:
        r = subprocess.run([app_bin, '--focus', app_name], capture_output=True, text=True, timeout=2)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def place_window(app_bin, proc, where='full', screen='current', title=''):
    """Place a window via atticus-host, returning the standard PLACED receipt or error."""
    if where not in ('left', 'right', 'full', 'fullscreen', 'center'):
        return 'where must be left, right, full, fullscreen or center'
    try:
        r = subprocess.run(
            [app_bin, '--place', proc, where, screen, title],
            capture_output=True, text=True, timeout=10,
        )
        out = (r.stdout or '').strip()
        if out:
            return out
        err = (r.stderr or '').strip()
        return err or f'no window found for "{proc}" {title}'
    except (OSError, subprocess.TimeoutExpired) as e:
        return f'NOT DONE: could not move {proc}: {e}'


# ---------------------------------------------------------------------------
# Thumbnails (replaces macOS `qlmanage`)
# ---------------------------------------------------------------------------

def thumb(path, app_bin=None):
    """Generate a 480px PNG data URL thumbnail for images, PDFs, or videos on Linux."""
    if not path or not os.path.isfile(path):
        return ''
    # 1. Check existing Freedesktop XDG thumbnail cache first (instant)
    try:
        uri = Path(path).resolve().as_uri()
        digest = hashlib.md5(uri.encode('utf-8')).hexdigest()
        for size_dir in ('large', 'x-large', 'normal'):
            cached = os.path.join(HOME, '.cache', 'thumbnails', size_dir, digest + '.png')
            if os.path.isfile(cached):
                return 'data:image/png;base64,' + base64.b64encode(Path(cached).read_bytes()).decode()
    except OSError:
        pass
    ext = os.path.splitext(path)[1].lower()
    # 2. Small PNG/JPEG/WebP image directly
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        try:
            raw = Path(path).read_bytes()
            if len(raw) <= 2_500_000:
                mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}[ext[1:]]
                return f'data:{mime};base64,' + base64.b64encode(raw).decode()
        except OSError:
            pass
    # 3. PDF via pdftoppm (poppler-utils)
    if ext == '.pdf' and shutil.which('pdftoppm'):
        out = tempfile.mkdtemp()
        try:
            prefix = os.path.join(out, 'page')
            subprocess.run(['pdftoppm', '-png', '-singlefile', '-f', '1', '-l', '1', '-scale-to', '480', path, prefix],
                           capture_output=True, timeout=4)
            png = prefix + '.png'
            if os.path.isfile(png):
                return 'data:image/png;base64,' + base64.b64encode(Path(png).read_bytes()).decode()
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            shutil.rmtree(out, True)
    # 4. Video via ffmpegthumbnailer
    if ext in ('.mp4', '.mkv', '.webm', '.mov', '.avi') and shutil.which('ffmpegthumbnailer'):
        out = tempfile.mkdtemp()
        try:
            png = os.path.join(out, 'thumb.png')
            subprocess.run(['ffmpegthumbnailer', '-i', path, '-o', png, '-s', '480'],
                           capture_output=True, timeout=4)
            if os.path.isfile(png):
                return 'data:image/png;base64,' + base64.b64encode(Path(png).read_bytes()).decode()
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            shutil.rmtree(out, True)
    return ''


# ---------------------------------------------------------------------------
# Notifications (replaces AppleScript `display notification`)
# ---------------------------------------------------------------------------

def notify(text, app_bin=None):
    """Send a desktop notification via atticus-host, notify-send, or D-Bus."""
    if app_bin:
        try:
            r = subprocess.run([app_bin, '--notify', text], capture_output=True, timeout=2)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    if shutil.which('notify-send'):
        try:
            subprocess.run(['notify-send', '-a', 'Atticus', 'Atticus', text], capture_output=True, timeout=2)
            return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    if shutil.which('gdbus'):
        try:
            subprocess.run([
                'gdbus', 'call', '--session',
                '--dest', 'org.freedesktop.Notifications',
                '--object-path', '/org/freedesktop/Notifications',
                '--method', 'org.freedesktop.Notifications.Notify',
                'Atticus', '0', 'atticus', 'Atticus', text, '[]', '{}', '5000',
            ], capture_output=True, timeout=2)
            return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


# ---------------------------------------------------------------------------
# Spotify on Linux (Cache scan + MPRIS2 D-Bus playback)
# ---------------------------------------------------------------------------

def spotify_cache_dirs():
    return [
        os.path.join(HOME, '.cache', 'spotify'),
        os.path.join(HOME, '.config', 'spotify'),
        os.path.join(HOME, '.var', 'app', 'com.spotify.Client', 'cache', 'spotify'),
        os.path.join(HOME, '.var', 'app', 'com.spotify.Client', 'config', 'spotify'),
    ]


def _mpris_status():
    """Query org.mpris.MediaPlayer2.spotify via gdbus/busctl. Returns (state, trackid, title, artist) or None."""
    gdbus = shutil.which('gdbus')
    if not gdbus:
        return None
    try:
        st = subprocess.run([
            gdbus, 'call', '--session',
            '--dest', 'org.mpris.MediaPlayer2.spotify',
            '--object-path', '/org/mpris/MediaPlayer2',
            '--method', 'org.freedesktop.DBus.Properties.Get',
            'org.mpris.MediaPlayer2.Player', 'PlaybackStatus',
        ], capture_output=True, text=True, timeout=2)
        if st.returncode != 0:
            return None
        state_m = re.search(r"'(Playing|Paused|Stopped)'", st.stdout)
        state = state_m.group(1).lower() if state_m else ''
        md = subprocess.run([
            gdbus, 'call', '--session',
            '--dest', 'org.mpris.MediaPlayer2.spotify',
            '--object-path', '/org/mpris/MediaPlayer2',
            '--method', 'org.freedesktop.DBus.Properties.Get',
            'org.mpris.MediaPlayer2.Player', 'Metadata',
        ], capture_output=True, text=True, timeout=2)
        meta = md.stdout if md.returncode == 0 else ''
        tid_m = re.search(r"'mpris:trackid':\s*<(?:'|\")([^'\"]+)(?:'|\")>", meta)
        title_m = re.search(r"'xesam:title':\s*<(?:'|\")([^'\"]*)(?:'|\")>", meta)
        artist_m = re.search(r"'xesam:artist':\s*<\[(?:'|\")([^'\"]*)(?:'|\")", meta)
        trackid = tid_m.group(1) if tid_m else ''
        # Normalize /com/spotify/track/<id> -> spotify:track:<id>
        if trackid.startswith('/com/spotify/'):
            parts = trackid.strip('/').split('/')
            if len(parts) >= 4:
                trackid = f'spotify:{parts[2]}:{parts[3]}'
        return (state, trackid, title_m.group(1) if title_m else '', artist_m.group(1) if artist_m else '')
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mpris_open_uri(uri):
    gdbus = shutil.which('gdbus')
    if gdbus:
        r = subprocess.run([
            gdbus, 'call', '--session',
            '--dest', 'org.mpris.MediaPlayer2.spotify',
            '--object-path', '/org/mpris/MediaPlayer2',
            '--method', 'org.mpris.MediaPlayer2.Player.OpenUri',
            uri,
        ], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True
    busctl = shutil.which('busctl')
    if busctl:
        r = subprocess.run([
            busctl, '--user', 'call',
            'org.mpris.MediaPlayer2.spotify', '/org/mpris/MediaPlayer2',
            'org.mpris.MediaPlayer2.Player', 'OpenUri', 's', uri,
        ], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True
    return False


def _mpris_play():
    gdbus = shutil.which('gdbus')
    if gdbus:
        subprocess.run([
            gdbus, 'call', '--session',
            '--dest', 'org.mpris.MediaPlayer2.spotify',
            '--object-path', '/org/mpris/MediaPlayer2',
            '--method', 'org.mpris.MediaPlayer2.Player.Play',
        ], capture_output=True, timeout=3)


def spotify_play(uri, title, app_bin=None):
    """Start a Spotify URI via MPRIS2 D-Bus (launching Spotify if needed) and verify playback.
    Returns (ok, error_msg, [track_name, artist_name])."""
    status = _mpris_status()
    if status is None:
        launch_target('Spotify', bg=True)
        for _ in range(30):
            time.sleep(0.2)
            status = _mpris_status()
            if status is not None:
                break
    if status is None:
        return False, 'Spotify is not running or does not expose MPRIS D-Bus.', ['', '']
    is_track = uri.startswith('spotify:track:')
    old_track = status[1]
    for attempt in range(20):
        if attempt in (0, 10):
            if old_track == uri and is_track:
                _mpris_play()
            elif not _mpris_open_uri(uri):
                return False, 'MPRIS OpenUri failed.', ['', '']
        time.sleep(0.15)
        cur = _mpris_status()
        if not cur:
            continue
        state, now_id, t_name, t_artist = cur
        ok = state == 'playing' and (now_id == uri if is_track else now_id != old_track)
        if ok:
            return True, '', [t_name, t_artist]
    return False, 'playback did not reach the playing state.', ['', '']


# ---------------------------------------------------------------------------
# Browser Automation Bridge (WebExtension UNIX Socket + CDP fallback)
# ---------------------------------------------------------------------------

def browser_socket_path(atticus_dir):
    return os.path.join(atticus_dir, 'browser.sock')


def browser_rpc(atticus_dir, payload, timeout=5.0):
    """Send a JSON request to atticus-browser-host over UNIX socket and return parsed JSON response."""
    sock_path = browser_socket_path(atticus_dir)
    if not os.path.exists(sock_path):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        wire = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
        s.sendall(wire)
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b'\n' in chunk:
                break
        line = b''.join(chunks).split(b'\n', 1)[0]
        if not line:
            return None
        return json.loads(line.decode('utf-8'))
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def browser_available(atticus_dir, app=''):
    """Return 'webext' if the Atticus Browser Extension bridge is connected, else ''."""
    resp = browser_rpc(atticus_dir, {'cmd': 'ping', 'app': app}, timeout=0.4)
    if isinstance(resp, dict) and resp.get('ok'):
        return 'webext'
    return ''


def browser_active_tab(atticus_dir, app=''):
    resp = browser_rpc(atticus_dir, {'cmd': 'active_tab', 'app': app}, timeout=2.0)
    if isinstance(resp, dict) and resp.get('ok'):
        return resp
    return None


def browser_exec_js(atticus_dir, window_id, tab_id, code, timeout=15.0):
    resp = browser_rpc(atticus_dir, {
        'cmd': 'exec_js',
        'window': window_id,
        'tab': tab_id,
        'code': code,
    }, timeout=timeout)
    if not isinstance(resp, dict):
        raise RuntimeError('Atticus browser extension is not connected. Install/enable it in Settings.')
    if not resp.get('ok'):
        raise RuntimeError(str(resp.get('error') or 'the page did not answer'))
    val = resp.get('result', '')
    return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)


def browser_open_page(atticus_dir, url, agent=False, own_window=0):
    resp = browser_rpc(atticus_dir, {
        'cmd': 'open_page',
        'url': url,
        'agent': bool(agent),
        'ownWindow': int(own_window or 0),
    }, timeout=5.0)
    if isinstance(resp, dict) and resp.get('ok'):
        return resp
    return None


def browser_navigate_tab(atticus_dir, window_id, tab_id, expected_url, new_url):
    resp = browser_rpc(atticus_dir, {
        'cmd': 'navigate_tab',
        'window': window_id,
        'tab': tab_id,
        'expectedUrl': expected_url,
        'url': new_url,
    }, timeout=2.0)
    return bool(isinstance(resp, dict) and resp.get('ok'))


def browser_tab_info(atticus_dir, window_id, tab_id):
    resp = browser_rpc(atticus_dir, {
        'cmd': 'tab_info',
        'window': window_id,
        'tab': tab_id,
    }, timeout=2.0)
    if isinstance(resp, dict) and resp.get('ok'):
        return resp
    return None


def browser_close_window(atticus_dir, window_id):
    browser_rpc(atticus_dir, {'cmd': 'close_window', 'window': window_id}, timeout=2.0)


def browser_show_tab(atticus_dir, window_id, tab_id):
    browser_rpc(atticus_dir, {'cmd': 'show_tab', 'window': window_id, 'tab': tab_id}, timeout=2.0)


def browser_find_tab(atticus_dir, words='', window_id=0, tab_id=0):
    resp = browser_rpc(atticus_dir, {
        'cmd': 'find_tab',
        'words': words,
        'window': int(window_id or 0),
        'tab': int(tab_id or 0),
    }, timeout=3.0)
    if isinstance(resp, dict) and resp.get('ok'):
        return resp
    return None


# ---------------------------------------------------------------------------
# Routines via systemd --user timers (with crontab fallback)
# ---------------------------------------------------------------------------

def routine_unit_dir(atticus_dir):
    if os.environ.get('ATTICUS_DIR'):
        return os.path.join(atticus_dir, 'systemd')
    return os.path.join(HOME, '.config', 'systemd', 'user')


def routine_timer_path(atticus_dir, identifier):
    return os.path.join(routine_unit_dir(atticus_dir), f'atticus-routine-{identifier}.timer')


def routine_service_path(atticus_dir, identifier):
    return os.path.join(routine_unit_dir(atticus_dir), f'atticus-routine-{identifier}.service')


def routine_install(atticus_dir, me_path, row):
    """Install a systemd --user service + timer for a routine (or cron fallback)."""
    unit_dir = routine_unit_dir(atticus_dir)
    os.makedirs(unit_dir, mode=0o700, exist_ok=True)
    ident = row['id']
    svc_path = routine_service_path(atticus_dir, ident)
    tmr_path = routine_timer_path(atticus_dir, ident)
    exec_cmd = f'{shlex.quote(sys.executable)} {shlex.quote(me_path)} --routine-fire {ident}'
    service_txt = (
        '[Unit]\n'
        f'Description=Atticus routine {ident}\n\n'
        '[Service]\n'
        'Type=oneshot\n'
        f'ExecStart={exec_cmd}\n'
    )
    if row['kind'] == 'interval':
        secs = int(row['every'])
        schedule = f'OnBootSec=60s\nOnUnitActiveSec={secs}s\n'
    else:
        hhmm = row['at']
        cal = f'Mon..Fri *-*-* {hhmm}:00' if row['days'] == 'weekdays' else f'*-*-* {hhmm}:00'
        schedule = f'OnCalendar={cal}\nPersistent=false\n'
    timer_txt = (
        '[Unit]\n'
        f'Description=Atticus routine timer {ident}\n\n'
        '[Timer]\n'
        f'{schedule}'
        'AccuracySec=15s\n\n'
        '[Install]\n'
        'WantedBy=timers.target\n'
    )
    for path, content in ((svc_path, service_txt), (tmr_path, timer_txt)):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    if not os.environ.get('ATTICUS_DIR'):
        if shutil.which('systemctl'):
            subprocess.run(['systemctl', '--user', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            r = subprocess.run(['systemctl', '--user', 'enable', '--now', f'atticus-routine-{ident}.timer'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return
        # Fallback: install a tagged crontab line if systemd --user isn't available
        _cron_install(me_path, row)


def routine_remove(atticus_dir, identifier):
    if not os.environ.get('ATTICUS_DIR') and shutil.which('systemctl'):
        subprocess.run(['systemctl', '--user', 'disable', '--now', f'atticus-routine-{identifier}.timer'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for path in (routine_timer_path(atticus_dir, identifier), routine_service_path(atticus_dir, identifier)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    if not os.environ.get('ATTICUS_DIR'):
        if shutil.which('systemctl'):
            subprocess.run(['systemctl', '--user', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _cron_remove(identifier)


def routine_wake(app_bin):
    """Wake atticus-host so it drains routine-queue.jsonl."""
    if shutil.which('systemctl'):
        r = subprocess.run(['systemctl', '--user', 'start', 'atticus.service'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return
    if not _proc_matches('atticus-host') and os.path.isfile(app_bin):
        try:
            subprocess.Popen([app_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass


def _cron_tag(identifier):
    return f'# atticus-routine:{identifier}'


def _cron_install(me_path, row):
    if not shutil.which('crontab'):
        return
    ident = row['id']
    tag = _cron_tag(ident)
    if row['kind'] == 'interval':
        mins = max(1, int(row['every']) // 60)
        spec = f'*/{mins} * * * *' if mins < 60 else f'0 */{max(1, mins // 60)} * * *'
    else:
        hh, mm = map(int, row['at'].split(':'))
        dow = '1-5' if row['days'] == 'weekdays' else '*'
        spec = f'{mm} {hh} * * {dow}'
    line = f'{spec} {shlex.quote(sys.executable)} {shlex.quote(me_path)} --routine-fire {ident} >/dev/null 2>&1 {tag}'
    cur = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    lines = [l for l in (cur.stdout.splitlines() if cur.returncode == 0 else []) if tag not in l]
    lines.append(line)
    subprocess.run(['crontab', '-'], input='\n'.join(lines) + '\n', text=True, capture_output=True)


def _cron_remove(identifier):
    if not shutil.which('crontab'):
        return
    tag = _cron_tag(identifier)
    cur = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if cur.returncode != 0:
        return
    lines = [l for l in cur.stdout.splitlines() if tag not in l]
    subprocess.run(['crontab', '-'], input=('\n'.join(lines) + '\n') if lines else '', text=True, capture_output=True)


# ---------------------------------------------------------------------------
# Contacts & Profile Import (EDS via atticus-host + local vCard fallback)
# ---------------------------------------------------------------------------

def _parse_vcards(text):
    """Minimal RFC 6350 / vCard 3.0+ parser returning list of contact dicts."""
    cards = []
    cur = None
    unfolded = re.sub(r'\r?\n[ \t]', '', text)
    for line in unfolded.splitlines():
        upper = line.upper()
        if upper == 'BEGIN:VCARD':
            cur = {'name': '', 'nickname': '', 'organization': '', 'emails': [], 'phones': []}
        elif upper == 'END:VCARD':
            if cur and (cur['name'] or cur['emails'] or cur['phones']):
                cards.append(cur)
            cur = None
        elif cur is not None and ':' in line:
            key, _, val = line.partition(':')
            prop = key.split(';', 1)[0].upper()
            val = val.strip()
            if prop == 'FN' and val:
                cur['name'] = val
            elif prop == 'NICKNAME' and val:
                cur['nickname'] = val
            elif prop == 'ORG' and val:
                cur['organization'] = val.split(';', 1)[0]
            elif prop == 'EMAIL' and val:
                cur['emails'].append(val)
            elif prop == 'TEL' and val:
                cur['phones'].append(val)
    return cards


def contacts_data(app_bin):
    """Return {'own': {...}, 'cards': [...]} from atticus-host EDS backend or local .vcf files."""
    if app_bin:
        try:
            r = subprocess.run([app_bin, '--contacts'], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                if isinstance(data, dict) and isinstance(data.get('cards'), list):
                    return data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    cards = []
    for folder in (
        os.path.join(HOME, '.contacts'),
        os.path.join(HOME, '.local', 'share', 'contacts'),
        os.path.join(HOME, '.local', 'share', 'kpeoplevcard'),
    ):
        if not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for fname in files:
                if fname.lower().endswith('.vcf'):
                    try:
                        cards.extend(_parse_vcards(Path(root, fname).read_text(encoding='utf-8', errors='ignore')))
                    except OSError:
                        pass
    indexed = [{**c, 'index': i} for i, c in enumerate(cards)]
    return {'own': {}, 'cards': indexed}


def profile_import_snapshot(app_bin):
    """Read Linux profile snapshot from atticus-host or git/env/vCard fallback."""
    if app_bin:
        try:
            r = subprocess.run([app_bin, '--profile-import'], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    name, email = '', ''
    try:
        name = subprocess.run(['git', 'config', '--get', 'user.name'], capture_output=True, text=True, timeout=1).stdout.strip()
        email = subprocess.run(['git', 'config', '--get', 'user.email'], capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not name and not email:
        return {'contacts': None, 'mail': None, 'errors': ['contacts', 'mail']}
    return {
        'contacts': {
            'name': name,
            'emails': [{'label': 'personal', 'value': email}] if email else [],
            'phones': [], 'urls': [], 'addresses': [], 'social': [], 'related': [],
            'pronouns': '', 'birthday': '', 'organization': '', 'department': '', 'jobTitle': '',
        },
        'mail': [{'name': 'Git / Local', 'emails': [email]}] if email else [],
        'errors': [],
        'errorReasons': {},
    }


# ---------------------------------------------------------------------------
# System Info & Feedback & Meeting Demo Speech
# ---------------------------------------------------------------------------

def feedback_system_lines():
    """Return Linux OS/desktop/hardware lines for Feedback issue reports."""
    os_name = 'Linux'
    try:
        for line in Path('/etc/os-release').read_text(errors='ignore').splitlines():
            if line.startswith('PRETTY_NAME='):
                os_name = line.split('=', 1)[1].strip().strip('"')
                break
    except OSError:
        pass
    session = os.environ.get('XDG_SESSION_TYPE', 'unknown')
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')
    hw = ''
    for p in ('/sys/devices/virtual/dmi/id/product_name', '/proc/device-tree/model'):
        try:
            hw = Path(p).read_text(errors='ignore').replace('\x00', '').strip()
            if hw:
                break
        except OSError:
            pass
    return f'OS: {os_name}\nSession: {session} ({desktop})\nHardware: {hw or "unknown"}\n'


def speak_demo_line(app_bin, voice_idx, line):
    """Spawn a subprocess speaking one Meeting Demo line on Linux."""
    if app_bin and os.path.isfile(app_bin):
        return subprocess.Popen([app_bin, '--speak', 'female' if voice_idx % 2 == 0 else 'male', line])
    if shutil.which('spd-say'):
        return subprocess.Popen(['spd-say', '--wait', '-t', 'female1' if voice_idx % 2 == 0 else 'male1', line])
    if shutil.which('espeak-ng'):
        return subprocess.Popen(['espeak-ng', '-s', '145', '-v', 'en+f2' if voice_idx % 2 == 0 else 'en+m3', line])
    return subprocess.Popen(['/bin/true'])


# ---------------------------------------------------------------------------
# Linux System Prompt Substitutions (strictly <= macOS length!)
# ---------------------------------------------------------------------------

LINUX_SYSTEM_REPLACEMENTS = (
    ('You are Atticus, a Mac helper in a small box.',
     'You are Atticus, a Linux helper in a small box.'),
    ('press "cmd+t"',
     'press "ctrl+t"'),
    ('System settings: shell, defaults or AppleScript first; else open the pane with --bg and use hands on its labelled control; verify.',
     'System settings: shell (gsettings/kwriteconfig6/wpctl/nmcli/bluetoothctl) first; else open settings with --bg and use hands; verify.'),
    ('full maximizes; fullscreen enters a Space.',
     'full maximizes; fullscreen enters WM fullscreen.'),
    ('Files (if needed): mdfind -name "<words>" | head -20; content: mdfind -onlyin ~/Documents "<words>" | head.',
     'Files (if needed): fd -i "<words>" ~ | head -20; content: rg -li "<words>" ~/Documents | head.'),
    ('Open Atticus Settings: open -a Atticus.',
     'Open Atticus Settings: atticus-host --settings.'),
)

LINUX_NEVER_REPLACEMENTS = (
    ('Files: trash "<path>", never rm.',
     'Files: gio trash "<path>", never rm.'),
)

LINUX_WORKFLOW_REPLACEMENTS = (
    ('open, URL scheme, osascript or hands label/menu path',
     'xdg-open, URL scheme, shell or hands label/menu path'),
)


def adapt_prompt(text, pairs):
    out = text
    for old, new in pairs:
        out = out.replace(old, new)
    return out
