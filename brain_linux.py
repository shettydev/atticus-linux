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


def _stub(name, task):
    def stub(*args, **kwargs):
        raise NotImplementedError(f'linux: {name} lands in {task}')
    stub.__name__ = name
    return stub


# name -> the task that replaces the stub. Remove a line when its real implementation lands in REPLACED.
STUBS = {
        'background_tab': 'Task 25', 'remember_background_tab': 'Task 25', 'browser_terms': 'Task 25', 'browser_adapter': 'Task 25',
    'close_agent_window': 'Task 25', 'work_page': 'Task 25', 'page_document': 'Task 25', 'work_tab_page': 'Task 25',
    'show_tab': 'Task 25', 'allow_page_js': 'Task 25', 'agent_window_id': 'Task 25',
    'routine_job_path': 'Task 29', 'routine_install': 'Task 29', 'routine_remove': 'Task 29', 'routine_fire': 'Task 29',
    'mac_profile': 'Task 33', 'contacts': 'Task 33', 'playlist': 'Task 34', 'play': 'Task 34', 'queue': 'Task 34',
    'need': 'Task 36', 'automation_hint': 'Task 36',
}

REPLACED = {'osa': osa, 'image_size': image_size, 'screens': screens, **{f.__name__: f for f in (
    thumb, preview_commands, restore_focus, request_screen, placement_window, front, _place, keep_behind, show, installed_apps,
    proc_of, app_id, app_running, quit_app, notify, open_command, launches_program)}, **{name: _stub(name, task) for name, task in STUBS.items()}}

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
    'web': 'Task 25 swaps the transport through browser_terms/browser_adapter/work_page',
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
    _MAC.setdefault('launches_program', ns['launches_program'])
    ns.update(REPLACED)
