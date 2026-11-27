"""Reading windows through AT-SPI (the reader half of macOS's enum Hands, Atticus.swift:8227).

capture_app() walks one app's front window into the same Map as Hands.Map: numbered controls with macOS role names
(the brain's prompts and replays speak AX roles), visible texts, the focused control. Values are masked like Hands.mask.
Everything works over a small Acc adapter: AtspiAcc for the real bus, FakeAcc for tests.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

from atticus_shell.desktop import ids

BUDGET = 1800          # nodes visited per capture, as Hands.capture's on-screen budget
ROLES = {
    'push button': 'AXButton', 'button': 'AXButton', 'toggle button': 'AXCheckBox', 'check box': 'AXCheckBox',
    'check menu item': 'AXMenuItem', 'radio button': 'AXRadioButton', 'radio menu item': 'AXMenuItem', 'page tab': 'AXRadioButton',
    'entry': 'AXTextField', 'password text': 'AXTextField', 'text': 'AXTextArea', 'spin button': 'AXIncrementor',
    'combo box': 'AXPopUpButton', 'link': 'AXLink', 'menu item': 'AXMenuItem', 'menu': 'AXMenuBarItem', 'slider': 'AXSlider',
    'list item': 'AXRow', 'table cell': 'AXCell', 'tree item': 'AXRow', 'scroll bar': 'AXScrollBar',
}
TEXT_ROLES = {'label', 'static', 'heading', 'paragraph', 'caption', 'status bar', 'section'}
SYSTEM_UI = ('xdg-desktop-portal', 'polkit', 'gcr-prompter', 'pinentry', 'org.gnome.shell', 'gnome-shell', 'plasmashell', 'ksecretd')


def mask(s):
    """Hands.mask: card, SSN and long ID numbers; one-time codes next to their label."""
    s = re.sub(r'\b(?:\d[ -]?){13,19}\b|\b\d{3}[- ]\d{2}[- ]\d{4}\b|\b\d{6,8}\b', '[SECRET]', s)
    if re.search(r'(?i)(one.?time|verification|security|auth|otp|code)\D{0,20}\d{4,8}', s):
        s = re.sub(r'\b\d{4,8}\b', '[SECRET]', s)
    return s


def quote(s):
    return '"' + s.replace('"', '\\"') + '"'


@dataclass
class Node:
    id: int
    role: str
    label: str
    value: str
    frame: list
    path: list
    secret: bool = False

    @property
    def short_role(self):
        return self.role.replace('AX', '').replace('TextField', 'text field')

    @property
    def line(self):
        x, y = int(self.frame[0] + self.frame[2] / 2), int(self.frame[1] + self.frame[3] / 2)
        value = '' if not self.value or self.secret else f' = {quote(self.value[:60])}'
        return f'[{self.id}] {self.short_role} {quote(self.label)}{value} (at {x},{y})'


@dataclass
class Map:
    pid: int
    app: str
    window: str
    windowFrame: list
    nodes: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    page: str = ''
    focused: str = ''
    scroll: list = field(default_factory=list)
    windowID: str = ''
    focusIdentity: str = ''

    def as_dict(self):
        return asdict(self)


# --- adapters -----------------------------------------------------------------------------------------------------------
class FakeAcc:
    def __init__(self, role, name='', value='', rect=(0, 0, 0, 0), states=(), kids=(), text=None):
        self.role, self.name, self.value, self.rect = role, name, value, tuple(rect)
        self.states, self.kids, self.text = set(states), list(kids), text

    def children(self):
        return self.kids


class FakeApp:
    def __init__(self, name, pid, windows):
        self.name, self.pid, self.windows = name, pid, windows


class AtspiAcc:
    """One Atspi.Accessible, read defensively: apps vanish and half-implement interfaces."""

    def __init__(self, obj):
        self.obj = obj

    def _try(self, fn, default):
        try:
            return fn()
        except Exception:
            return default

    @property
    def role(self):
        return self._try(self.obj.get_role_name, '')

    @property
    def name(self):
        return self._try(self.obj.get_name, '') or ''

    @property
    def rect(self):
        from gi.repository import Atspi
        r = self._try(lambda: self.obj.get_extents(Atspi.CoordType.SCREEN), None)
        return (r.x, r.y, r.width, r.height) if r else (0, 0, 0, 0)

    @property
    def states(self):
        from gi.repository import Atspi
        s = self._try(self.obj.get_state_set, None)
        return {Atspi.StateType(v).value_nick.replace('-', ' ') for v in (s.get_states() if s else [])}

    @property
    def text(self):
        from gi.repository import Atspi
        def read():
            n = Atspi.Text.get_character_count(self.obj)
            return Atspi.Text.get_text(self.obj, 0, n) if n else ''
        return self._try(read, None)

    @property
    def value(self):
        from gi.repository import Atspi
        v = self._try(lambda: Atspi.Value.get_current_value(self.obj), None)
        return '' if v is None else (str(int(v)) if float(v).is_integer() else str(v))

    def children(self):
        n = self._try(self.obj.get_child_count, 0)
        return [AtspiAcc(c) for c in (self._try(lambda i=i: self.obj.get_child_at_index(i), None) for i in range(min(n, 5000))) if c]


class AtspiApp:
    def __init__(self, obj):
        self.obj = obj
        self.name = obj.get_name() or ''
        self.pid = obj.get_process_id()

    @property
    def windows(self):
        return AtspiAcc(self.obj).children()


def atspi_apps():
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    desktop = Atspi.get_desktop(0)
    apps = []
    for i in range(desktop.get_child_count()):
        try:
            apps.append(AtspiApp(desktop.get_child_at_index(i)))
        except Exception:
            continue
    return apps


def ensure_a11y_on():
    """Chromium and Electron apps publish their trees only when assistive technologies are on."""
    for cmd in (['gsettings', 'set', 'org.gnome.desktop.interface', 'toolkit-accessibility', 'true'],
                ['gdbus', 'call', '--session', '--dest', 'org.a11y.Bus', '--object-path', '/org/a11y/bus', '--method',
                 'org.freedesktop.DBus.Properties.Set', 'org.a11y.Status', 'IsEnabled', '<true>']):
        try:
            subprocess.run(cmd, capture_output=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass


# --- capture ------------------------------------------------------------------------------------------------------------
def _area(acc):
    return acc.rect[2] * acc.rect[3]


def front_window(app, words=''):
    wins = [w for w in app.windows if not words or words.lower() in (w.name or '').lower()]
    return next((w for w in wins if 'active' in w.states), None) or max(wins, key=_area, default=None)


def _content(acc):
    if acc.value:
        return acc.value
    t = acc.text
    return t if t is not None else ''


def capture_app(app, words=''):
    w = front_window(app, words)
    if w is None:
        return Map(app.pid, app.name, '', [0, 0, 0, 0])
    m = Map(app.pid, app.name, w.name or '', list(w.rect))
    stack, visited, focused = [(w, [])], 0, None   # depth first: controls come out in reading order
    while stack and visited < BUDGET:
        acc, path = stack.pop()
        visited += 1
        kids = acc.children()
        stack.extend((kid, path + [i]) for i, kid in reversed(list(enumerate(kids))))
        if acc is w or 'showing' not in acc.states:
            continue
        role = ROLES.get(acc.role)
        if role:
            secret = acc.role == 'password text'
            if acc.role in ('toggle button', 'check box', 'radio button', 'page tab', 'check menu item', 'radio menu item'):
                value = '1' if ({'checked', 'pressed', 'selected'} & acc.states) else '0'
            else:
                value = '' if secret else mask(_content(acc) or '')
            node = Node(len(m.nodes) + 1, role, mask(acc.name or ''), value, list(acc.rect), path, secret)
            m.nodes.append(node)
            if 'focused' in acc.states:
                focused = node
        elif acc.role in TEXT_ROLES and (acc.name or acc.text):
            m.texts.append(mask(acc.name or acc.text or ''))
        elif acc.role == 'document web' and not m.page:
            m.page = acc.name or ''
    if focused:
        m.focused = f'[{focused.id}] {focused.short_role} {quote(focused.label)}'
        m.focusIdentity = hashlib.sha1(f'{app.pid}:{w.name}:{focused.path}:{focused.role}'.encode()).hexdigest()[:16]
    return m


def map_text(m):
    lines = [n.line for n in m.nodes] + m.texts
    values = [n.value for n in m.nodes if n.value and not n.secret and n.role in ('AXTextArea', 'AXTextField')]
    return '\n'.join(values + lines)


def _find(apps, name):
    name = name.lower()
    return [a for a in apps if (a.name or '').lower() == name] or [a for a in apps if name in (a.name or '').lower()]


def ocr_lines(tsv):
    lines, order = {}, []
    for row in tsv.splitlines()[1:]:
        cols = row.split('\t')
        if len(cols) < 12 or not cols[11].strip() or cols[10] == '-1':
            continue
        key = (cols[2], cols[3], cols[4])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(cols[11].strip())
    return [' '.join(lines[k]) for k in order]


def ocr(png_path):
    try:
        out = subprocess.run(['tesseract', png_path, '-', 'tsv'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ''
    return '\n'.join(ocr_lines(out))


def window_text(apps, app_name, words, ocr=None):
    """--text: the app window's text, masked: accessible text first, OCR of its picture when it has none."""
    for app in _find(apps, app_name)[:4]:
        text = map_text(capture_app(app, words)).strip()
        if text:
            return text
    if ocr:
        pictured = ocr().strip()
        if pictured:
            return '\n'.join(mask(line) for line in pictured.splitlines())
    return ''


# --- windows through the bridge ------------------------------------------------------------------------------------------
def _covered(rect, above):
    x, y, w, h = rect
    points = [(x + w * i / 4, y + h * j / 4) for i in range(1, 4) for j in range(1, 4)]
    return all(any(o.x <= px < o.x + o.width and o.y <= py < o.y + o.height for o in above) for px, py in points)


def user_app(bridge):
    wins = [w for w in bridge.windows() if w.app_id != 'atticus']
    pick = next((w for w in wins if w.focused), wins[0] if wins else None)
    return pick.app_id if pick else ''


def window_target_for(bridge, name, title, frame=None):
    from atticus_shell.screen import window_target
    wins = bridge.windows()
    n = name.lower()
    cands = [w for w in wins if w.app_id != 'atticus' and (w.app_id.lower() == n or n in w.app_id.lower())
             and (not title or title.lower() in w.title.lower())]
    if frame:
        cands = [w for w in cands if all(abs(a - b) < 2 for a, b in zip((w.x, w.y, w.width, w.height), frame))]
        if len(cands) != 1:
            return None   # indistinguishable windows are not a safe target
    if not cands:
        return None
    pick = next((w for w in cands if w.focused), max(cands, key=lambda w: w.width * w.height))
    target = window_target(pick)
    above = wins[:wins.index(pick)]
    target['visible'] = not pick.minimized and not _covered((pick.x, pick.y, pick.width, pick.height), above)
    return target


def default_handler(uri):
    kind = f'x-scheme-handler/{uri.split(":", 1)[0]}' if '://' in uri or uri.startswith('mailto:') else None
    try:
        if kind is None:
            kind = subprocess.run(['xdg-mime', 'query', 'filetype', uri], capture_output=True, text=True, timeout=3).stdout.strip()
        out = subprocess.run(['xdg-mime', 'query', 'default', kind], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''
    return out[:-len('.desktop')] if out.endswith('.desktop') else out


def target_app(bridge, name, handler=default_handler):
    if ':' in name or os.path.exists(name):
        return handler(name)
    n = name.lower()
    apps = {w.app_id for w in bridge.windows() if w.app_id != 'atticus' and w.app_id.lower() == n}
    return apps.pop() if len(apps) == 1 else ''


def system_ui(bridge):
    out = {}
    for w in bridge.windows():
        if any(w.app_id.lower().startswith(s) for s in SYSTEM_UI):
            out[ids.public_id(w.id)] = f'{w.app_id} {w.width}×{w.height} on {w.output}'
    return out


# --- CLI ---------------------------------------------------------------------------------------------------------------
def _bridge():
    from atticus_shell.bridge_cli import current_bridge
    return current_bridge()


def text_command(args):
    if not args:
        return 2
    app, words = args[0], (args[1].lower() if len(args) > 1 else '')
    bridge = _bridge()

    def pictured():
        t = window_target_for(bridge, app, words)
        if not t:
            return ''
        import tempfile
        from atticus_shell.desktop.ids import resolve
        wid = resolve(t['windowID'], [w.id for w in bridge.windows()])
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        try:
            return ocr(path) if wid and bridge.screenshot_window(wid, path) else ''
        finally:
            os.unlink(path)
    try:
        apps = atspi_apps()
    except Exception:
        apps = []
    text = window_text(apps, app, words, ocr=pictured)
    if not text:
        return 1
    print(text)
    return 0


def window_target_command(args):
    if not args:
        return 2
    frame = None
    if len(args) > 2 and args[2]:
        try:
            frame = json.loads(args[2])
        except ValueError:
            return 1
        if not (isinstance(frame, list) and len(frame) == 4 and all(isinstance(v, (int, float)) for v in frame)):
            return 1
    t = window_target_for(_bridge(), args[0], args[1] if len(args) > 1 else '', frame)
    if t:
        print(json.dumps(t))
    return 0


def target_app_command(args):
    if not args:
        return 2
    name = target_app(_bridge(), args[0])
    if name:
        print(name)
    return 0


def user_app_command(args):
    print(user_app(_bridge()))
    return 0


def system_ui_command(args):
    print(json.dumps(system_ui(_bridge())))
    return 0
