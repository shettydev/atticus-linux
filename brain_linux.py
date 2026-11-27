"""Atticus on Linux: replacements for the brain's macOS-only functions and prompt lines.

atticus.py stays one shared file. On Linux (atticus.PLATFORM == 'linux') it calls install(globals()) once, after every
definition, and this module rebinds the names in REPLACED and rewrites the macOS-specific constants. Functions not
listed here either never touch macOS (PORTABLE) or reach it only through osa(), which is inert on Linux (VIA_OSA).
tests/test_linux_seam_ratchet.py keeps the three tables complete.
"""
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------------------------------------------------
# Prompt lines: (macOS text, Linux text). A pair whose macOS text drifted upstream is skipped, and the seam tests fail.
SYSTEM_SWAPS = (
    ('You are Atticus, a Mac helper in a small box.', 'You are Atticus, a Linux desktop helper in a small box.'),
    ('press "cmd+t"', 'press "ctrl+t"'),
    ('- System settings: shell, defaults or AppleScript first;', '- System settings: shell (gsettings, kwriteconfig6, wpctl, nmcli) first;'),
    ('- Files (if needed): mdfind -name "<words>" | head -20; content: mdfind -onlyin ~/Documents "<words>" | head.',
     '- Files (if needed): localsearch search -f "<words>" | head -20, else plocate -i "<words>" | head -20; content: rg -il "<words>" ~/Documents | head.'),
    ('Open Atticus Settings: open -a Atticus.', 'Open Atticus Settings: gtk-launch atticus.'),
)
WORKFLOW_SWAPS = (('open, URL scheme, osascript or hands label/menu path', 'xdg-open, URL scheme, gio or hands label/menu path'),)
AGENT_SWAPS = (('never cmd+l or the address bar', 'never ctrl+l or the address bar'),)

LINUX_DENIED = ('mkfs', 'fdisk', 'parted', 'wipefs', 'systemctl poweroff', 'systemctl reboot', 'systemctl suspend', 'loginctl terminate-session')
LINUX_UNSAFE = r'(^|[;&|(`\s])(mkfs(\.\w+)?|fdisk|parted|wipefs)\s|systemctl\s+(poweroff|reboot|suspend)|loginctl\s+terminate'
SAFE_TOOLS = ('xdg-open', 'gio open', 'localsearch', 'plocate', 'rg', 'curl', 'head', 'tail', 'grep', 'sort', 'wc', 'echo', 'sleep', 'date')
QUIET_MAC = ('open ', 'mdfind ', 'pbpaste')
QUIET_LINUX = ('xdg-open ', 'gio open ', 'localsearch ', 'plocate ', 'wl-paste', 'xclip -o')
LOOKUPS_LINUX = {'localsearch', 'plocate', 'rg', 'fd'}
RUNS_LINUX = ('.desktop', '.AppImage', '.run')
NEEDS = {'accessibility': 'Assistive technologies', 'screen': 'Screen sharing', 'automation': 'the Atticus browser extension',
         'calendar': 'Calendar', 'microphone': 'Microphone', 'page-js': 'page reading'}


def default_app(environ, home):
    """The Linux shell binary the brain's helper commands call (the macOS app path otherwise)."""
    return environ.get('ATTICUS_APP') or os.path.join(home, '.local/share/atticus/atticus-shell')


def app_dirs(home):
    return ('/usr/share/applications/', '/usr/local/share/applications/', os.path.join(home, '.local/share/applications') + '/',
            '/var/lib/flatpak/exports/share/applications/', os.path.join(home, '.local/share/flatpak/exports/share/applications') + '/',
            '/var/lib/snapd/desktop/applications/')


def swap(text, pairs):
    for mac, linux in pairs:
        text = text.replace(mac, linux)
    return text


# ---------------------------------------------------------------------------------------------------------------------
# Functions. Each REPLACED entry keeps the macOS function's name and signature.

def osa(script, *args, timeout=60):
    """AppleScript has no Linux runtime: every caller already treats '' as 'nothing came back'."""
    return ''


def image_size(path):
    """(width, height) from a PNG or JPEG header: no sips on Linux, no Pillow needed."""
    import struct
    try:
        with open(path, 'rb') as f:
            head = f.read(26)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                return struct.unpack('>II', head[16:24])
            if head[:2] != b'\xff\xd8':
                return None
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xff:
                    return None
                if marker[1] in (0xc0, 0xc1, 0xc2):
                    f.read(3)
                    h, w = struct.unpack('>HH', f.read(4))
                    return w, h
                (length,) = struct.unpack('>H', f.read(2))
                f.seek(length - 2, 1)
    except (OSError, struct.error):
        return None


def screens(visible=False):
    """[(name, x, y, w, h, built_in)] from the shell's bridge (layout coordinates, like the macOS version)."""
    rows = _bridge(['screens'] + (['visible'] if visible else [])) or []
    try:
        return [(n, int(x), int(y), int(w), int(h), bool(b)) for n, x, y, w, h, b in rows if isinstance(n, str)]
    except (ValueError, TypeError):
        return []


def _shell_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (here, os.path.join(here, 'linux')):   # installed beside atticus_shell/, or the repo's linux/
        if os.path.isdir(os.path.join(p, 'atticus_shell')) and p not in sys.path:
            sys.path.insert(0, p)


def _bridge(args):
    """JSON from `atticus-shell --bridge …`, or None."""
    import atticus
    try:
        r = subprocess.run([atticus.APP, '--bridge', *args], capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout or 'null')
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _installed():
    _shell_path()
    from atticus_shell import apps
    return apps.installed()


def _apps_mod():
    _shell_path()
    from atticus_shell import apps
    return apps


def _find(name):
    return _apps_mod().find(name, _installed())


def _windows():
    rows = _bridge(['windows'])
    return rows if isinstance(rows, list) else []


def _display_name(window_app_id):
    a = next((a for a in _installed() if _apps_mod().owns(a, window_app_id)), None)
    return a.name if a else window_app_id


def installed_apps():
    return {a.name for a in _installed()}


def proc_of(app):
    return app


def app_id(app):
    a = _find(app)
    return a.id if a else ''


def app_running(bid):
    a = _find(bid)
    if a and any(_apps_mod().owns(a, w.get('app', '')) for w in _windows()):
        return True
    exe = _apps_mod().exe_name(a) if a else bid
    try:
        return subprocess.run(['pgrep', '-x', exe], capture_output=True, timeout=2).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def quit_app(bid):
    """Ask the app to quit (SIGTERM, which lets it save) and confirm it went; a save dialog may keep it open."""
    import time
    if not app_running(bid):
        return True
    a = _find(bid)
    exe = _apps_mod().exe_name(a) if a else bid
    try:
        subprocess.run(['pkill', '-TERM', '-x', exe], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(10):
        if not app_running(bid):
            return True
        time.sleep(0.1)
    return False


def front():
    app = os.environ.get('ATTICUS_FRONT')
    focused = _bridge(['focused']) or {}
    if not app:
        app = _display_name(focused.get('app', '')) if focused else ''
    return app, (focused.get('title', '') if focused else '')


def request_screen():
    if os.environ.get('ATTICUS_SCREEN'):
        return os.environ['ATTICUS_SCREEN']
    f = _bridge(['focused']) or {}
    if f.get('frame'):
        x, y, w, h = f['frame']
        for name, sx, sy, sw, sh, _ in screens():
            if sx <= x + w / 2 < sx + sw and sy <= y + h / 2 < sy + sh:
                return name
    return 'current'


def _matching_windows(proc):
    a = _find(proc)
    mod = _apps_mod()
    return [w for w in _windows() if w.get('app') != 'atticus' and (mod.owns(a, w.get('app', '')) if a else proc.lower() in (w.get('app', '') + ' ' + w.get('title', '')).lower())]


def placement_window(proc, title):
    """(exact title, None) for the one window a move means, else (None, why)."""
    wins = _matching_windows(proc)
    key = lambda s: ' '.join(re.split(r'[\s_-]+', s.casefold())).strip()
    if title:
        want = key(title)
        found = [w for w in wins if key(w.get('title', '')) == want] or [w for w in wins if want in key(w.get('title', ''))]
    else:
        found = wins
    if len(found) == 1:
        return found[0].get('title', ''), None
    if found:
        return None, f'NOT DONE: more than one {proc} window matches "{title or found[0].get("title", "")}"; nothing moved. Ask which one.'
    return None, f'no window found for "{proc}" {title}'


def _target_screen(screen):
    rows = screens(visible=True)
    if screen == 'current':
        screen = request_screen()
    if screen == 'laptop':
        return next((r for r in rows if r[5]), None)
    if screen == 'monitor':
        return next((r for r in rows if not r[5]), None)
    return next((r for r in rows if r[0] == screen), rows[0] if rows and screen == 'current' else None)


def _place(proc, where, screen, title, space, held):
    native = where == 'fullscreen'
    if native:
        where = 'full'
    if where not in ('left', 'right', 'full', 'center'):
        return 'where must be left, right, full, fullscreen or center'
    target = _target_screen(screen)
    if target is None:
        return f'NOT DONE: no {screen} display is connected. Say so.'
    exact, problem = placement_window(proc, title)
    if problem:
        return problem
    w = next(w for w in _matching_windows(proc) if w.get('title', '') == exact)
    n, sx, sy, sw, sh, _ = target
    if native:
        r = _bridge(['move_resize', w['id'], str(sx), str(sy), str(sw), str(sh)]) or {}
        ok = (_bridge(['set_fullscreen', w['id'], '1']) or {}).get('ok')
        return f'{proc} window -> fullscreen of {n}' if ok and r.get('ok') else f'NOT DONE: {proc} did not enter full screen on {n}.'
    ww, wh = w['frame'][2], w['frame'][3]
    box = {'left': (sx, sy, sw // 2, sh), 'right': (sx + sw - sw // 2, sy, sw // 2, sh), 'full': (sx, sy, sw, sh),
           'center': (sx + max(0, (sw - min(ww, sw)) // 2), sy + max(0, (sh - min(wh, sh)) // 2), min(ww, sw), min(wh, sh))}[where]
    if not (_bridge(['move_resize', w['id'], *map(str, box)]) or {}).get('ok'):
        return f'Moved {proc}, but could not check where its window is. Look at the screen before saying it worked or moving it again.'
    now = next((x for x in _windows() if x['id'] == w['id']), None)
    if now is None:
        return f'Moved {proc}, but could not check where its window is. Look at the screen before saying it worked or moving it again.'
    l, t, rw, rh = now['frame']
    bx, by, bw, bh = box
    if abs(l - bx) > 40 or abs(t - by) > 40 or abs(rw - bw) > 60 or abs(rh - bh) > 60:
        return (f'NOT DONE: {proc} window is at {l},{t} size {rw}x{rh}, not the {where} of {n} ({bx},{by} size {bw}x{bh}); '
                'it may not allow that size. Say so; do not report success.')
    return f'{proc} window -> {where} of {n}'


def keep_behind(mine, app, wait=1.5):
    """Give focus back to the user's app if `app` jumped forward by itself (a launch); never undo the user's own switch."""
    import time
    import atticus
    if not mine or not app or atticus.wants_focus() or atticus.pref('workIn', 'back') == 'front' or os.environ.get('ATTICUS_FOCUS_GUARD') == '1':
        return
    a, mod = _find(app), _apps_mod()
    mine_app = _find(mine)
    for _ in range(int(wait * 10)):
        time.sleep(0.1)
        f = _bridge(['focused']) or {}
        if f and a and mod.owns(a, f.get('app', '')):
            back = next((w for w in _windows() if mine_app and mod.owns(mine_app, w.get('app', ''))), None)
            if back:
                _bridge(['activate', back['id']])
            return


def restore_focus(app, task, cmds):
    import atticus
    if atticus.wants_focus(task) or re.search(r'\b(close|quit|exit)\b', task, re.I) or atticus.pref('workIn', 'back') == 'front':
        return
    f = _bridge(['focused']) or {}
    now = _display_name(f.get('app', '')) if f else ''
    if not now or now == app or now.lower() not in ' '.join(cmds).lower():
        return
    a = _find(app)
    back = next((w for w in _windows() if a and _apps_mod().owns(a, w.get('app', ''))), None)
    if back:
        _bridge(['activate', back['id']])


def _opener(t):
    if re.match(r'^[a-z][a-z0-9+.-]*:', t) or os.path.exists(t):
        return ['xdg-open', t], None
    a = _find(t)
    return (['gtk-launch', a.id], a) if a else (None, None)


def show(target, bg=False):
    """Open a URL, file or app; bg opens it behind the user's app (the mini screen shows it)."""
    import atticus
    t = os.path.expanduser(target)
    if re.match(r'^https?://', t) and not atticus.agent_url_allowed(t):
        return atticus.agent_url_denial()
    if os.environ.get('ATTICUS_AGENT') and re.match(r'^https?://', t) and not (atticus.wants_focus() or atticus.pref('workIn', 'back') == 'front' and atticus.wants_focus(None, True)):
        return atticus.web('read', t)
    argv, app = _opener(t)
    if argv is None:
        return f'no app or file called "{target}"'
    mine = front()[0] if bg else ''
    try:
        r = subprocess.run(argv, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return f'could not open {target}'
    if r.returncode:
        return f'no app or file called "{target}"' if not bg else f'could not open {target}'
    if bg:
        ahead = atticus.pref('workIn', 'back') == 'front'
        if not ahead:
            keep_behind(mine, app.name if app else '')
        try:
            atticus.preview_app(t, 'open')
        except Exception:
            pass
        if re.match(r'^https?://', t):
            try:
                status = atticus.web('wait', text=t, fresh=True)
            except Exception:
                status = 'Page readiness unavailable. Read the page before acting.'
            where = 'in front' if ahead else 'in the background'
            return f'opened {target} {where}' + ('' if status == 'Page ready.' else f'\n{status}')
        return f'opened {target} ' + ('in front' if ahead else 'in the background')
    return f'opened {target} in front'


def notify(text):
    try:
        subprocess.run(['notify-send', '--app-name=Atticus', 'Atticus', str(text)[:2000]], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


def thumb(path):
    """An image's own pixels, else the desktop's thumbnail cache (freedesktop spec), as a data URL; '' when none."""
    import base64
    import hashlib
    import urllib.parse
    uri = 'file://' + urllib.parse.quote(os.path.abspath(path))
    cache = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'thumbnails')
    for size in ('large', 'normal'):
        p = os.path.join(cache, size, hashlib.md5(uri.encode()).hexdigest() + '.png')
        if os.path.exists(p):
            with open(p, 'rb') as f:
                return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
    if re.search(r'\.(png|jpe?g|gif|webp)$', path, re.I) and os.path.getsize(path) < 8 << 20:
        _shell_path()
        try:
            from atticus_shell.pen import thumb as small
            return small(path)
        except Exception:
            return ''
    return ''


def preview_commands(cmd, directory=None):
    """The app a shell command opens, so the mini screen can follow it (xdg-open, gio open, gtk-launch)."""
    import atticus
    import shlex
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return
    for i, token in enumerate(tokens):
        base = os.path.basename(token)
        args = tokens[i + 1:]
        if base == 'gtk-launch' and args:
            atticus.preview_app(args[0], 'open', directory=directory)
        elif base in ('xdg-open', 'gio') and args:
            target = args[1] if base == 'gio' and args[0] == 'open' and len(args) > 1 else args[0]
            atticus.preview_app(os.path.expanduser(target), 'open', directory=directory)


_MAC = {}   # the macOS functions a Linux one extends (filled by install)
OPENERS = ('xdg-open', 'exo-open', 'kde-open', 'kde-open5', 'kioclient', 'kioclient5', 'kioclient6')


def open_command(target, app=False, background=False):
    """xdg-open for URLs and files, gtk-launch for an installed app by name."""
    if app:
        a = _find(target)
        return ['gtk-launch', a.id if a else target]
    return ['xdg-open', target]


def launches_program(part):
    """xdg-open / gio open / gio launch of a script, .desktop launcher, AppImage or executable runs it outside the sandbox."""
    import atticus
    import shlex
    try:
        tokens = shlex.split(part)
    except ValueError:
        return False
    if not tokens:
        return False
    base = os.path.basename(tokens[0])
    if base in OPENERS:
        targets = [t for t in tokens[1:] if not t.startswith('-') and t not in ('exec', 'openURL', 'open')]
    elif base == 'gio' and tokens[1:2] in (['open'], ['launch']):
        targets = [t for t in tokens[2:] if not t.startswith('-')]
    elif base == 'gtk-launch':
        return False   # an installed app by its id
    else:
        return _MAC['launches_program'](part)
    return any(map(atticus.runs_path, targets))


# --- the browser, through the Atticus extension (atticus_shell.web_host) ----------------------------------------------------
NO_EXTENSION = ('Install the Atticus browser extension once (Settings → Browser), then ask again. '
                'Background reads never launch or focus a browser.')


def _host(op, **args):
    _shell_path()
    from atticus_shell.web_host import call
    return call(op, **args)


def _browser():
    hello = _host('hello') or {}
    return hello.get('browser') or 'Browser'


def browser_adapter(app):
    try:
        return 'extension' if _host('hello') else ''
    except Exception:
        return ''


def browser_terms(app, adapter, window=None, tab=None):
    return '', ''   # no AppleScript: pages are reached through the extension


def allow_page_js(app, ask=False):
    return True     # the extension's own permission covers it


def page_document(page):
    try:
        v = _host('document', tab=page['tab'])
    except Exception:
        return None
    return tuple(map(str, v)) if isinstance(v, list) and len(v) == 2 and all(v) else None


def _gate(name):
    import atticus
    return os.path.join(atticus.agent_gate_dir(os.environ['ATTICUS_ASK']), name)


def close_agent_window(directory):
    import atticus
    path = os.path.join(atticus.agent_gate_dir(directory), 'browser-window.json')
    try:
        with open(path) as f:
            saved = json.load(f)
        os.remove(path)
        if type(saved.get('window')) is int:
            _host('close_window', window=saved['window'])
    except Exception:
        pass


def background_tab(url, app):
    import atticus
    saved_path = os.path.join(os.environ.get('ATTICUS_ASK', atticus.DIR) if os.environ.get('ATTICUS_AGENT') else atticus.DIR, 'background-page.json')
    try:
        with open(saved_path) as f:
            saved = json.load(f)
        if saved.get('adapter') != 'extension' or not all(type(saved.get(k)) is int for k in ('window', 'tab')):
            return False
        _host('navigate', tab=saved['tab'], expect=saved.get('url', ''), url=url)
        page = {'app': app, 'adapter': 'extension', 'window': saved['window'], 'tab': saved['tab'], 'target': None}
        path = os.path.join(os.environ.get('ATTICUS_ASK', atticus.DIR), 'web.json')
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        atticus.private_json(path, page)
        return True
    except Exception:
        return False


def remember_background_tab(app):
    import atticus
    try:
        with open(os.path.join(os.environ.get('ATTICUS_ASK', atticus.DIR), 'web.json')) as f:
            page = json.load(f)
        url = (_host('tab', tab=page['tab']) or {}).get('url', '')
        if not url:
            return
        path = os.path.join(os.environ.get('ATTICUS_ASK', atticus.DIR) if os.environ.get('ATTICUS_AGENT') else atticus.DIR, 'background-page.json')
        atticus.private_json(path, {'app': app, 'adapter': 'extension', 'window': page['window'], 'tab': page['tab'], 'url': url})
    except Exception:
        pass


def work_tab_page(directory):
    import atticus
    path = os.path.join(atticus.agent_gate_dir(directory), 'work-tab.json')
    try:
        with open(path) as f:
            tab = json.load(f)
        os.remove(path)
        info = _host('tab', tab=tab['tab']) or {}
        url = info.get('url', '')
        if not re.match(r'https?://', url):
            return {}
        return {'title': ' '.join(info.get('title', '').split())[:180], 'url': url[:2000], 'type': '', 'tab': tab}
    except Exception:
        return {}


def show_tab(tab):
    try:
        _host('focus', window=tab['window'], tab=tab['tab'])
    except Exception:
        pass


def agent_window_id(app):
    import atticus
    if not app or not os.environ.get('ATTICUS_AGENT'):
        return ''
    handed = atticus.handed_page()
    if str(handed.get('app', '')).casefold() == app.casefold() and isinstance(handed.get('target'), dict):
        return str(handed['target'].get('windowID', '')) or None
    window = atticus.agent_window(app) or (handed.get('window') if str(handed.get('app', '')).casefold() == app.casefold() and type(handed.get('window')) is int else 0)
    if not window:
        return ''
    try:
        b = _host('window', window=window)
        r = subprocess.run([atticus.APP, '--window-target', app, '', json.dumps([b['left'], b['top'], b['width'], b['height']])],
                           capture_output=True, text=True, timeout=2)
        identifier = str(json.loads(r.stdout)['windowID'])
        return identifier if identifier.isdigit() and int(identifier) > 0 else None
    except Exception:
        return None


def work_page(app='', words='', ask=''):
    """macOS's work_page, with the browser tab found through the extension."""
    found = _MAC['work_page'](app, words, ask)
    if found.get('tab') or not found.get('app') or browser_adapter(found['app']) != 'extension':
        return found
    try:
        tabs = _host('tabs') or []
    except Exception:
        return found
    used = {}
    try:
        with open(os.path.join(ask, 'web.json')) as f:
            used = json.load(f)
    except (OSError, ValueError):
        pass
    pick = (next((t for t in tabs if t['id'] == used.get('tab')), None) if used.get('adapter') == 'extension' and not words else None) or \
        (next((t for t in tabs if words and (words.lower() in t['title'].lower() or words.lower() in t['url'].lower())), None) if words else None) or \
        next((t for t in tabs if t['active'] and t.get('focused')), None)
    if not pick:
        return found
    return {'app': found['app'], 'adapter': 'extension', 'window': pick['windowId'], 'tab': pick['id'], 'title': pick['title'][:200], 'url': pick['url'][:2000]}


def web(cmd, n='', text='', app=None, fresh=False, want='', page=None):
    """macOS's web() over the extension: read a page (numbered controls), click or fill by number, type after an anchor."""
    import atticus as A
    import urllib.parse
    try:
        browser = _browser()
    except Exception:
        return NO_EXTENSION
    if cmd == 'read' and str(n).startswith(('http:', 'https:')):
        try:
            url = urllib.parse.urlsplit(n)
            if not url.hostname or url.username or url.password:
                return 'Use an HTTP(S) page URL without credentials.'
        except ValueError:
            return 'Use a valid HTTP(S) page URL.'
        if not A.agent_url_allowed(n):
            return A.agent_url_denial()
        if A.pref('workIn', 'back') == 'front' and A.wants_focus(None, True):
            status = A.show(n, bg=True)
            if not status.startswith('opened '):
                return status
            return web('read', app=browser, want=n)
        own = A.agent_window(browser) if os.environ.get('ATTICUS_AGENT') else 0
        live = own and A.live_page(browser, 'extension', own, n)
        if live:
            return web('read', app=browser, want=n, page=live)
        try:
            if os.environ.get('ATTICUS_AGENT'):
                r, made = (_host('open', url=n, window=own, active=True), '') if own else (_host('open', url=n, window='agent', active=False), 'new')
            else:
                r, made = _host('open', url=n, window='user', active=False), ''
        except Exception:
            return 'Could not create a background page in an existing window. Requested page was not read.'
        if made:
            A.private_json(_gate('browser-window.json'), {'app': browser, 'window': r['window']})
        page = dict(app=browser, adapter='extension', window=r['window'], tab=r['tab'], target=None)
        status = web('wait', text=n, app=browser, page=page)
        if status != 'Page ready.':
            return status + '\nRequested page not ready; do not substitute another source.'
        document = os.environ.get('ATTICUS_AGENT') and page_document(page)
        if document:
            page.update(opened=n, document=document[1])
        result = web('read', app=browser, want=n, page=page)
        directory = os.environ.get('ATTICUS_ASK', A.DIR)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        A.private_json(os.path.join(directory, 'web.json'), page)
        return result
    path = os.path.join(os.environ.get('ATTICUS_ASK', A.DIR), 'web.json')
    try:
        saved = page
        if saved is None and not fresh and os.path.exists(path) and (cmd != 'read' or os.environ.get('ATTICUS_ASK')):
            with open(path) as f:
                saved = json.load(f)
        if not saved or saved.get('adapter') != 'extension':
            tabs = _host('tabs') or []
            t = next((t for t in tabs if t['active'] and t.get('focused')), None) or next((t for t in tabs if t['active']), None)
            if not t:
                return f'Could not find a scriptable page window. Use {A.HANDS} see.'
            saved = {'app': browser, 'adapter': 'extension', 'window': t['windowId'], 'tab': t['id'], 'target': None}
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            A.private_json(path, saved)
        if os.environ.get('ATTICUS_AGENT') and cmd in ('click', 'fill', 'type-after') and saved['window'] != A.agent_window(browser):
            handed = A.handed_page()
            if any(handed.get(k) != saved.get(k) for k in ('window', 'tab')):
                return f"That page is in the user's own window; agents only read it. To act on it, open its URL in this task's window: {A.WEB} read <url>"
            want = want or handed.get('url', '')
        if cmd in ('read', 'wait') or not saved.get('target'):
            try:
                b = _host('window', window=saved['window'])
                info = _host('tab', tab=saved['tab']) or {}
                saved['target'] = A.preview_app(browser, cmd, title=info.get('title', ''), frame=[b['left'], b['top'], b['width'], b['height']])
            except Exception:
                saved['target'] = None
            if cmd == 'read' and not n:
                A.hands_map(browser, saved['target'])
            if page is None:
                A.private_json(path, saved)
        if isinstance(saved.get('target'), dict):
            A.emit(['▣ ' + json.dumps({**saved['target'], 'action': cmd})])
    except Exception as e:
        return f'Could not use the page: {e}\nUse the screen instead: {A.HANDS} see'

    def js(c, timeout=15):
        try:
            v = _host('run', tab=saved['tab'], args=[c, n, text])
        except Exception as e:
            raise RuntimeError(str(e)[-300:] or 'the page did not answer')
        return v if isinstance(v, str) else json.dumps(v)
    try:
        if cmd == 'wait':
            return 'Page ready.' if A.page_ready(lambda timeout: js('ready', timeout=timeout), text) else 'Page still loading. Read again before acting.'

        def same_source():
            state = json.loads(js('ready'))
            current = str(state.get('signature', '')).split('|')[0]
            host = lambda u: (urllib.parse.urlsplit(u).hostname or '').removeprefix('www.')
            return host(current) == host(want)
        if want and not same_source():
            return 'The browser is not on the requested source. Read it again before continuing.'
        if cmd == 'click':
            try:
                c = json.loads(js('name'))
            except ValueError:
                c = None
            if isinstance(c, dict):
                if c.get('submit') or re.search(A.RISKY_CLICK, c.get('what', ''), re.I) and A.pref('safe', True):
                    if not os.environ.get('ATTICUS_ASK'):
                        return "This click needs the user's confirmation. Ask through Atticus, not a shortcut."
                    if not A.task_allowed('web click'):
                        answer = A.ask_user({'q': f'Click "{c["short"][:40]}"?', 'cmd': f'{browser}: click "{c["what"][:300]}"', 'task': A.managed_agent()}, 60)
                        if answer == 'task' and A.managed_agent():
                            A.allow_for_task('web click')
                        elif answer != 'y':
                            return 'The user said no. Do not try another way; say it was not done.'
                if json.loads(js('name')) != c:
                    return 'The control changed since the check. Read the page again.'
            done = js('click')
            if not done.startswith('Clicked'):
                return done
            A.remember_work_tab(saved)
            try:
                if not A.page_ready(lambda timeout: js('ready', timeout=timeout)):
                    return done + '\nPage still loading. Read again before acting.'
                return done + '\nChanged state:\n' + js('state')
            except (RuntimeError, ValueError):
                return done + '\nPage readiness unavailable. Read again before acting.'
        result = js(cmd if cmd in ('read', 'fill', 'type-after') else 'read')
        if want and not same_source():
            return 'The page left the requested source during the read. Its content was not returned.'
        if cmd == 'fill' and result.startswith('Filled'):
            A.remember_work_tab(saved)
            try:
                if not A.page_ready(lambda timeout: js('ready', timeout=timeout)):
                    return result + '\nPage still loading. Re-read values before claiming success.'
                return result + '\nChanged state:\n' + js('state')
            except (RuntimeError, ValueError):
                return result + '\nChanged state unavailable. Re-read values before claiming success.'
        return result
    except (RuntimeError, ValueError) as e:
        return f'Could not use the page: {e}\nUse the screen instead: {A.HANDS} see'


# --- routines: systemd user timers ---------------------------------------------------------------------------------------
def _units_dir():
    import atticus
    return os.path.join(atticus.DIR, 'systemd') if os.environ.get('ATTICUS_DIR') else \
        os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'systemd', 'user')


def routine_job_path(identifier):
    return os.path.join(_units_dir(), f'atticus-routine-{identifier}.timer')


def _systemctl(*args):
    return subprocess.run(['systemctl', '--user', *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def routine_install(row):
    """A .service that fires the routine and a .timer that wakes it; Persistent=false so a restart never repeats a run."""
    import atticus
    import shlex
    timer = atticus.routine_job_path(row['id'])
    service = timer[:-len('.timer')] + '.service'
    os.makedirs(os.path.dirname(timer), mode=0o700, exist_ok=True)
    if row['kind'] == 'interval':
        when = f"OnActiveSec={row['every']}s\nOnUnitActiveSec={row['every']}s"
    else:
        days = 'Mon..Fri ' if row['days'] == 'weekdays' else ''
        when = f"OnCalendar={days}*-*-* {row['at']}:00"
    files = {service: f"[Unit]\nDescription=Atticus routine {row['id']}\n\n[Service]\nType=oneshot\n"
                      f"ExecStart={shlex.join([sys.executable, atticus.ME, '--routine-fire', row['id']])}\n",
             timer: f"[Unit]\nDescription=Atticus routine {row['id']}\n\n[Timer]\n{when}\nPersistent=false\nAccuracySec=1s\n\n"
                    "[Install]\nWantedBy=timers.target\n"}
    for path, text in files.items():
        with open(path + '.tmp', 'w') as f:
            f.write(text)
        os.chmod(path + '.tmp', 0o600)
        os.replace(path + '.tmp', path)
    if not os.environ.get('ATTICUS_DIR'):
        _systemctl('daemon-reload')
        if _systemctl('enable', '--now', os.path.basename(timer)).returncode:
            for path in files:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            raise OSError('Could not start the routine. Try again.')


def routine_repair():
    """A routine whose timer is missing (a reinstall, a cleared config) gets it back."""
    import atticus
    for row in atticus.routines():
        if not os.path.exists(atticus.routine_job_path(row['id'])):
            try:
                routine_install(row)
            except (OSError, ValueError, KeyError, TypeError):
                pass


def routine_remove(identifier):
    import atticus
    import fcntl
    if not re.fullmatch(r'[a-f0-9]{12}', identifier):
        return False
    os.makedirs(atticus.DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(atticus.DIR, 'routines.lock'), 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = atticus.routines()
        kept = [r for r in rows if r.get('id') != identifier]
        if len(rows) == len(kept):
            return False
        atticus.private_json(os.path.join(atticus.DIR, 'routines.json'), kept)
    timer = atticus.routine_job_path(identifier)
    if not os.environ.get('ATTICUS_DIR'):
        subprocess.run(['systemctl', '--user', 'disable', '--now', os.path.basename(timer)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for path in (timer, timer[:-len('.timer')] + '.service'):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return True


def routine_fire(identifier, now=None):
    """The timer fired: queue the routine once per slot and make sure Atticus is running to start it."""
    import atticus
    import fcntl
    import time
    if not re.fullmatch(r'[a-f0-9]{12}', identifier) or atticus.pref('paused', False):
        return False
    os.makedirs(atticus.DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(atticus.DIR, 'routines.lock'), 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = atticus.routines()
        row = next((r for r in rows if r.get('id') == identifier), None)
        slot = atticus.routine_slot(row, now) if row else None
        if not slot or row.get('lastSlot') == slot:
            return False
        row['lastSlot'], row['lastAt'] = slot, time.time() if now is None else now
        atticus.private_json(os.path.join(atticus.DIR, 'routines.json'), rows)
        fd = os.open(os.path.join(atticus.DIR, 'routine-queue.jsonl'), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps({'id': identifier}) + '\n')
    if not os.environ.get('ATTICUS_DIR'):
        subprocess.run(['systemctl', '--user', 'start', 'atticus.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# --- Spotify over MPRIS --------------------------------------------------------------------------------------------------
MPRIS = ['--session', '--dest', 'org.mpris.MediaPlayer2.spotify', '--object-path', '/org/mpris/MediaPlayer2']


def _gdbus(*args):
    try:
        return subprocess.run(['gdbus', 'call', *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, 1, '', '')


def _spotify_up():
    r = _gdbus('--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus',
               '--method', 'org.freedesktop.DBus.NameHasOwner', 'org.mpris.MediaPlayer2.spotify')
    return 'true' in (r.stdout or '')


def _spotify_prop(name):
    r = _gdbus(*MPRIS, '--method', 'org.freedesktop.DBus.Properties.Get', 'org.mpris.MediaPlayer2.Player', name)
    return r.stdout if r.returncode == 0 else ''


def _spotify_now():
    meta = _spotify_prop('Metadata')
    track = re.search(r"'mpris:trackid': <'([^']*)'>", meta)
    status = re.search(r"<'(\w+)'>", _spotify_prop('PlaybackStatus'))
    return (status.group(1) if status else ''), (track.group(1) if track else '')


def _same_track(uri, trackid):
    """Spotify names tracks spotify:track:ID in old builds and /com/spotify/track/ID in new ones."""
    return bool(trackid) and trackid.replace('/com/spotify/track/', 'spotify:track:') == uri


def playlist(name):
    """The macOS app reads Spotify's local playlist cache; on Linux playlists come from the web lookup instead."""
    return None


def play(name, anything=True, credited=False):
    import time
    import atticus
    p = atticus.spotify_item(name, anything, credited=credited)
    if not p:
        return None
    mine = front()[0]
    if not _spotify_up():
        a = _find('Spotify')
        subprocess.run(['gtk-launch', a.id if a else 'spotify'], capture_output=True, timeout=10)
        for _ in range(100):   # it takes a few seconds to start
            if _spotify_up():
                break
            time.sleep(0.1)
        else:
            return f'Could not start {p[1]} in Spotify: Spotify did not open.'
    _, old = _spotify_now()
    track, ok, state = p[0].startswith('spotify:track:'), False, ''
    for attempt in range(30):
        if attempt in (0, 15):
            if _gdbus(*MPRIS, '--method', 'org.mpris.MediaPlayer2.Player.OpenUri', p[0]).returncode:
                return f'Could not start {p[1]} in Spotify: it did not take the request.'
        time.sleep(0.1)
        state, now = _spotify_now()
        ok = state == 'Playing' and (_same_track(p[0], now) if track else now != old)
        if ok:
            break
    if not ok:
        atticus.report_error('play', stage='not_playing')
        return f'Could not start {p[1]} in Spotify: ' + ('it kept playing another track.' if state == 'Playing' else 'playback did not reach the playing state.')
    atticus.preview_app('Spotify', 'play')
    if not atticus.wants_focus():
        keep_behind(mine, 'Spotify')
    return f'Playing {atticus.on_spotify(p[1])}.'


def queue(name):
    return ('Adding to Spotify’s queue isn’t on Linux yet: Spotify’s desktop app doesn’t offer its queue to other apps. '
            'Say “play …” to start it now instead.')


# --- contacts and the user's own card -----------------------------------------------------------------------------------
def _home():
    return os.path.expanduser('~')


def _gecos():
    import pwd
    try:
        return pwd.getpwuid(os.getuid()).pw_gecos
    except KeyError:
        return ''


def _vcards():
    """vCards from Evolution Data Server address books and ~/.local/share/contacts/*.vcf."""
    import glob
    import sqlite3
    texts = []
    for db in glob.glob(os.path.join(_home(), '.local/share/evolution/addressbook/*/contacts.db')):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            texts += [row[0] for row in con.execute('SELECT vcard FROM folder_id') if row[0]]
            con.close()
        except sqlite3.Error:
            continue
    for path in glob.glob(os.path.join(_home(), '.local/share/contacts/*.vcf')):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                texts.append(f.read())
        except OSError:
            continue
    cards = []
    for text in texts:
        for block in re.findall(r'BEGIN:VCARD(.*?)END:VCARD', re.sub(r'\r?\n[ \t]', '', text), re.S | re.I):
            card = {'name': '', 'nickname': '', 'organization': '', 'emails': [], 'phones': []}
            for line in block.splitlines():
                if ':' not in line:
                    continue
                key, value = line.split(':', 1)
                key = key.split(';')[0].upper()
                if key == 'FN':
                    card['name'] = value.strip()
                elif key == 'NICKNAME':
                    card['nickname'] = value.strip()
                elif key == 'ORG':
                    card['organization'] = value.split(';')[0].strip()
                elif key == 'EMAIL':
                    card['emails'].append(value.strip())
                elif key == 'TEL':
                    card['phones'].append(value.strip())
            if card['name'] or card['emails']:
                cards.append(card)
    return cards


def contacts(query):
    import atticus
    cards = _vcards()
    own = {'name': (_gecos().split(',')[0]).strip(), 'related': []}
    indexed = [{'index': i, 'name': c['name'], 'nickname': c['nickname'], 'organization': c['organization']} for i, c in enumerate(cards)]
    matches = [{k: cards[m['index']][k] for k in ('name', 'emails', 'phones')} for m in atticus.contact_matches(query, indexed, own)]
    if not cards:
        return 'Contacts unavailable. Use the app address book or ask for the recipient.'
    return json.dumps({'matches': matches, 'ownCard': own, 'profile': atticus.profile_context(query),
                       'guidance': "Use related names to resolve relationships, then look up that name. Profile facts are the user's own words and "
                                   'win over card values. Missing/ambiguous recipient: ask, never guess.'})


def mac_profile():
    """The import Settings offers: on Linux, the account's full name (no "my card" or Mail accounts to read)."""
    name = _gecos().split(',')[0].strip()
    return {'contacts': {'name': name, 'pronouns': '', 'birthday': '', 'emails': [], 'phones': [], 'urls': [], 'addresses': [],
                         'organization': '', 'department': '', 'jobTitle': '', 'social': [], 'related': []} if name else None,
            'mail': None, 'errors': ['mail'] + ([] if name else ['contacts'])}


def _stub(name, task):
    def stub(*args, **kwargs):
        raise NotImplementedError(f'linux: {name} lands in {task}')
    stub.__name__ = name
    return stub


# name -> the task that replaces the stub. Remove a line when its real implementation lands in REPLACED.
STUBS = {
    }

REPLACED = {'osa': osa, 'image_size': image_size, 'screens': screens, **{f.__name__: f for f in (
    thumb, preview_commands, restore_focus, request_screen, placement_window, front, _place, keep_behind, show, installed_apps,
    proc_of, app_id, app_running, quit_app, notify, open_command, launches_program, background_tab, remember_background_tab, browser_terms,
    browser_adapter, close_agent_window, work_page, page_document, work_tab_page, show_tab, allow_page_js, agent_window_id, web,
    routine_job_path, routine_install, routine_repair, routine_remove, routine_fire, playlist, play, queue, contacts, mac_profile)}, **{name: _stub(name, task) for name, task in STUBS.items()}}

# Touch macOS words only to classify command text the model wrote; the same rules hold on Linux.
PORTABLE = {
    'risky': 'classifies command text (osascript/JXA rules stay correct and never fire on Linux commands)',
    'focus_command': 'classifies command text for focus changes',
    'shortcut_commands': 'filters learned commands by text; osascript ones never appear on Linux',
    'learn': 'stores verified commands; only inspects text',
    'replay_quit': 'parses a quit command from text',
    'label': 'turns command text into step labels',
    'action_kind': 'classifies interpreter commands by text',
    'hands_replayable': 'classifies hands/osascript commands by text',
    'report_crashes': 'reads ~/Library/Logs/DiagnosticReports, absent on Linux, so it reports nothing',
}
# Reach macOS only through osa() (inert on Linux) or through REPLACED helpers; their Linux branches land later.
VIA_OSA = {
    'fast': 'Task 24 adds Linux instant verbs; its osascript branch returns nothing on Linux',
    'relay': 'Task 15: pick/ask dialogs come from the box (ATTICUS_UI); osa() fallback is inert',
    'ask': 'Task 15: the typed pop-up is the box on Linux; osa() fallback is inert',
    'main': 'Task 24/27: notification and activate branches go through osa(); Linux uses notify()/show()',
}


def install(ns):
    """Rebind the Linux functions and constants in the atticus module namespace `ns`."""
    ns['SYSTEM'] = swap(ns['SYSTEM'], SYSTEM_SWAPS)
    ns['WORKFLOW'] = swap(ns['WORKFLOW'], WORKFLOW_SWAPS)
    ns['AGENT_SYSTEM'] = swap(ns['AGENT_SYSTEM'], AGENT_SWAPS)
    ns['DENIED'] = tuple(ns['DENIED']) + LINUX_DENIED
    ns['DENY'] = [f'Bash({c}:*)' for c in ns['DENIED']]
    ns['UNSAFE'] = re.compile(ns['UNSAFE'].pattern + '|' + LINUX_UNSAFE, ns['UNSAFE'].flags)
    me = ns['ME']
    ns['SAFE_OUT'] = [f'{c} *' for c in SAFE_TOOLS + (me,)] + ['wl-paste', 'xclip -o', 'date'] + [f'orca terminal {c} *' for c in ('list', 'read', 'send')]
    ns['QUIET'] = tuple(q for q in ns['QUIET'] if q not in QUIET_MAC) + QUIET_LINUX
    ns['LOOKUPS'] = (set(ns['LOOKUPS']) - {'mdfind'}) | LOOKUPS_LINUX
    ns['RUNS'] = tuple(ns['RUNS']) + RUNS_LINUX
    ns['APP_DIRS'] = app_dirs(ns['HOME'])
    ns['NEEDS'] = dict(NEEDS)
    ns['APP'] = default_app(os.environ, ns['HOME'])
    for name in ('launches_program', 'work_page'):
        _MAC.setdefault(name, ns[name])
    ns.update(REPLACED)
