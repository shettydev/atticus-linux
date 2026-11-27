"""Synthetic input and the clipboard borrow.

Injectors click, drag, scroll, press keys and type when an app offers no accessibility action (Hands' Quartz fallback):
uinput everywhere (an absolute pointer over the whole layout, so it works on Wayland), XTest on X11. Both put the
pointer back where the user had it. ClipboardBorrow (borrowPasteboard, Atticus.swift:1041) takes the clipboard for one
paste and gives back every type the user had copied.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
ABS_X, ABS_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
ABS_MAX = 32767
BUTTONS = {'left': BTN_LEFT, 'right': BTN_RIGHT, 'middle': BTN_MIDDLE}

# linux/input-event-codes.h (an X keycode is this + 8)
KEYS = {'esc': 1, 'escape': 1, '-': 12, '=': 13, 'backspace': 14, 'tab': 15, '[': 26, ']': 27, 'enter': 28, 'return': 28,
        'ctrl': 29, ';': 39, "'": 40, '`': 41, 'shift': 42, '\\': 43, ',': 51, '.': 52, '/': 53, 'alt': 56, 'space': 57,
        'capslock': 58, 'home': 102, 'up': 103, 'pageup': 104, 'left': 105, 'right': 106, 'end': 107, 'down': 108,
        'pagedown': 109, 'insert': 110, 'delete': 111, 'super': 125}
for i, ch in enumerate('1234567890'):
    KEYS[ch] = 2 + i
for row, start in (('qwertyuiop', 16), ('asdfghjkl', 30), ('zxcvbnm', 44)):
    for i, ch in enumerate(row):
        KEYS[ch] = start + i
for i in range(1, 13):
    KEYS[f'f{i}'] = 58 + i if i <= 10 else 76 + i
ALIASES = {'cmd': 'ctrl', 'command': 'ctrl', 'control': 'ctrl', '⌘': 'ctrl', '⌃': 'ctrl', 'option': 'alt', 'opt': 'alt', '⌥': 'alt',
           '⇧': 'shift', 'meta': 'super', 'win': 'super', 'logo': 'super', 'del': 'delete', 'forwarddelete': 'delete',
           'pgup': 'pageup', 'pgdn': 'pagedown', 'arrowup': 'up', 'arrowdown': 'down', 'arrowleft': 'left', 'arrowright': 'right',
           '↑': 'up', '↓': 'down', '←': 'left', '→': 'right', '↩': 'enter', '⏎': 'enter', '⌫': 'backspace', '⎋': 'esc'}
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=',
           '{': '[', '}': ']', ':': ';', '"': "'", '~': '`', '|': '\\', '<': ',', '>': '.', '?': '/'}
TERMINALS = ('terminal', 'konsole', 'kitty', 'alacritty', 'wezterm', 'foot', 'xterm', 'tilix', 'terminator', 'ptyxis', 'ghostty', 'urxvt')


def to_abs(x, y, outputs):
    left, top = min(o.x for o in outputs), min(o.y for o in outputs)
    right, bottom = max(o.x + o.width for o in outputs), max(o.y + o.height for o in outputs)
    return round(ABS_MAX * (x - left) / (right - left)), round(ABS_MAX * (y - top) / (bottom - top))


def parse_combo(combo):
    """'cmd+shift+t', '⌘⇧[', 'Return' -> key codes, modifiers first."""
    text = combo.strip()
    parts = [p for p in text.lower().split('+') if p] if '+' in text and len(text) > 1 else None
    if parts is None:   # symbol-only combos like ⌘⇧[ and plain names like Return
        parts, rest = [], text
        while rest and rest[0] in '⌘⌃⌥⇧':
            parts.append(rest[0])
            rest = rest[1:]
        parts.append(rest.lower())
    codes = []
    for p in parts:
        name = ALIASES.get(p, p)
        if name not in KEYS:
            raise ValueError(f'unknown key {p!r} in {combo!r}')
        codes.append(KEYS[name])
    return codes


def text_keys(text):
    """[(modifiers, key)] that type text on a US layout, or None when it needs characters keys can't make."""
    out = []
    for ch in text:
        if ch == '\n':
            out.append(([], KEYS['enter']))
        elif ch == '\t':
            out.append(([], KEYS['tab']))
        elif ch == ' ':
            out.append(([], KEYS['space']))
        elif ch.isascii() and ch.isalpha():
            out.append(([KEYS['shift']] if ch.isupper() else [], KEYS[ch.lower()]))
        elif ch in SHIFTED:
            out.append(([KEYS['shift']], KEYS[SHIFTED[ch]]))
        elif ch in KEYS and len(ch) == 1:
            out.append(([], KEYS[ch]))
        else:
            return None
    return out


def paste_combo(app_id):
    a = (app_id or '').lower()
    return 'ctrl+shift+v' if any(t in a for t in TERMINALS) else 'ctrl+v'


class UinputInjector:
    """/dev/uinput: one virtual absolute pointer + keyboard. Needs the udev rule build-linux.sh installs."""

    def __init__(self, outputs, pointer, device=None, sleep=time.sleep):
        self.outputs, self.pointer, self.dev, self.sleep = outputs, pointer, device, sleep

    def _device(self):
        if self.dev is None:
            from evdev import AbsInfo, UInput, ecodes
            keys = sorted(set(KEYS.values()) | set(BUTTONS.values()))
            info = AbsInfo(value=0, min=0, max=ABS_MAX, fuzz=0, flat=0, resolution=0)
            self.dev = UInput({ecodes.EV_KEY: keys, ecodes.EV_ABS: [(ABS_X, info), (ABS_Y, info)], ecodes.EV_REL: [REL_WHEEL, REL_HWHEEL]},
                              name='Atticus virtual input')
            self.sleep(0.15)   # the compositor picks the new device up
        return self.dev

    def _move(self, x, y):
        d = self._device()
        ax, ay = to_abs(x, y, self.outputs())
        d.write(EV_ABS, ABS_X, ax)
        d.write(EV_ABS, ABS_Y, ay)
        d.syn()

    def _restore(self, where):
        if where is not None:
            self._move(*where)

    def _where(self):
        try:
            return self.pointer()
        except Exception:
            return None

    def click(self, x, y, button='left', count=1):
        btn, d, where = BUTTONS[button], self._device(), self._where()
        self._move(x, y)
        for _ in range(count):
            d.write(EV_KEY, btn, 1)
            d.syn()
            d.write(EV_KEY, btn, 0)
            d.syn()
            self.sleep(0.04)
        self._restore(where)
        return True

    def drag(self, a, b, steps=12):
        d, where = self._device(), self._where()
        self._move(*a)
        d.write(EV_KEY, BTN_LEFT, 1)
        d.syn()
        for i in range(1, steps + 1):
            self._move(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
            self.sleep(0.01)
        d.write(EV_KEY, BTN_LEFT, 0)
        d.syn()
        self._restore(where)
        return True

    def scroll(self, dx, dy):
        d = self._device()
        for code, amount in ((REL_HWHEEL, dx), (REL_WHEEL, dy)):
            for _ in range(abs(int(amount))):
                d.write(EV_REL, code, 1 if amount > 0 else -1)
                d.syn()
        return True

    def _press(self, mods, key):
        d = self._device()
        for m in mods:
            d.write(EV_KEY, m, 1)
        d.write(EV_KEY, key, 1)
        d.syn()
        d.write(EV_KEY, key, 0)
        for m in reversed(mods):
            d.write(EV_KEY, m, 0)
        d.syn()

    def key(self, combo, times=1):
        codes = parse_combo(combo)
        for _ in range(times):
            self._press(codes[:-1], codes[-1])
        return True

    def type_text(self, text):
        keys = text_keys(text)
        if keys is None:
            return False
        for mods, key in keys:
            self._press(mods, key)
        return True


class XTestInjector:
    """X11's XTest: real pointer motion in root coordinates; X keycodes are evdev codes + 8."""

    def __init__(self, pointer, display=None, sleep=time.sleep):
        from Xlib import X, display as xdisplay
        from Xlib.ext import xtest
        self.X, self.xtest = X, xtest
        self.d = display or xdisplay.Display()
        self.pointer, self.sleep = pointer, sleep

    def _move(self, x, y):
        self.xtest.fake_input(self.d, self.X.MotionNotify, x=int(x), y=int(y))
        self.d.sync()

    def click(self, x, y, button='left', count=1):
        where = self.pointer()
        self._move(x, y)
        b = {'left': 1, 'middle': 2, 'right': 3}[button]
        for _ in range(count):
            self.xtest.fake_input(self.d, self.X.ButtonPress, b)
            self.xtest.fake_input(self.d, self.X.ButtonRelease, b)
            self.d.sync()
            self.sleep(0.04)
        self._move(*where)
        return True

    def drag(self, a, b, steps=12):
        where = self.pointer()
        self._move(*a)
        self.xtest.fake_input(self.d, self.X.ButtonPress, 1)
        for i in range(1, steps + 1):
            self._move(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps)
        self.xtest.fake_input(self.d, self.X.ButtonRelease, 1)
        self._move(*where)
        return True

    def scroll(self, dx, dy):
        for button, amount in ((7 if dx > 0 else 6, dx), (4 if dy > 0 else 5, dy)):
            for _ in range(abs(int(amount))):
                self.xtest.fake_input(self.d, self.X.ButtonPress, button)
                self.xtest.fake_input(self.d, self.X.ButtonRelease, button)
        self.d.sync()
        return True

    def _press(self, mods, key):
        for m in mods:
            self.xtest.fake_input(self.d, self.X.KeyPress, m + 8)
        self.xtest.fake_input(self.d, self.X.KeyPress, key + 8)
        self.xtest.fake_input(self.d, self.X.KeyRelease, key + 8)
        for m in reversed(mods):
            self.xtest.fake_input(self.d, self.X.KeyRelease, m + 8)
        self.d.sync()

    def key(self, combo, times=1):
        codes = parse_combo(combo)
        for _ in range(times):
            self._press(codes[:-1], codes[-1])
        return True

    def type_text(self, text):
        keys = text_keys(text)
        if keys is None:
            return False
        for mods, key in keys:
            self._press(mods, key)
        return True


def injector_for(env, bridge):
    """uinput when the udev rule gave access (works everywhere), else XTest on X11, else None."""
    if os.access('/dev/uinput', os.W_OK):
        return UinputInjector(bridge.outputs, bridge.pointer)
    if env.session == 'x11' or os.environ.get('DISPLAY'):
        return XTestInjector(bridge.pointer)
    return None


# --- clipboard ---------------------------------------------------------------------------------------------------------
SKIP_TARGETS = {'TARGETS', 'TIMESTAMP', 'MULTIPLE', 'SAVE_TARGETS', 'DELETE', 'INCR'}
TEXT_TYPES = ['text/plain;charset=utf-8', 'UTF8_STRING', 'text/plain', 'STRING', 'TEXT']
MAX_TOTAL = 64 << 20


def best_type(saved):
    """The one type to give back where only one can be served (wl-copy): a picture first, then plain text."""
    for t in ('image/png', 'image/jpeg', 'text/uri-list', *TEXT_TYPES, 'text/html'):
        if t in saved:
            return t
    return next(iter(saved), None)


def _run(args, data=None, timeout=3):
    return subprocess.run(args, input=data, capture_output=True, timeout=timeout)


class X11Clipboard:
    """CLIPBOARD on X11 (and XWayland, which syncs it to Wayland apps). Restoring serves every saved type at once."""

    def read_all(self):
        try:
            targets = _run(['xclip', '-o', '-selection', 'clipboard', '-t', 'TARGETS']).stdout.decode().split()
        except (OSError, subprocess.SubprocessError):
            return {}
        saved, total = {}, 0
        for t in targets:
            if t in SKIP_TARGETS or total > MAX_TOTAL:
                continue
            try:
                data = _run(['xclip', '-o', '-selection', 'clipboard', '-t', t]).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            saved[t] = data
            total += len(data)
        return saved

    def put_text(self, text):
        self.restore({t: text.encode() for t in TEXT_TYPES})

    def restore(self, saved):
        if not saved:
            _run(['xclip', '-selection', 'clipboard', '-i', '/dev/null'])
            return
        payload = json.dumps({t: base64.b64encode(v).decode() for t, v in saved.items()}).encode()
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = subprocess.Popen([sys.executable, '-c', 'from atticus_shell.input import serve_clipboard_stdin; serve_clipboard_stdin()'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={**os.environ, 'PYTHONPATH': here}, start_new_session=True)
        p.stdin.write(payload)
        p.stdin.close()
        p.stdout.readline()   # "ready": the selection is ours before the paste happens


def serve_clipboard_stdin():
    serve_clipboard({t: base64.b64decode(v) for t, v in json.loads(sys.stdin.read()).items()})


def serve_clipboard(types):
    """Own CLIPBOARD and answer for each saved type until another app copies something."""
    from Xlib import X, Xatom, display
    from Xlib.protocol import event
    d = display.Display()
    w = d.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
    clip, targets_atom = d.intern_atom('CLIPBOARD'), d.intern_atom('TARGETS')
    atoms = {d.intern_atom(t): v for t, v in types.items() if len(v) < 200_000}   # bigger needs INCR: handed to xclip below
    big = {t: v for t, v in types.items() if len(v) >= 200_000}
    if big and not atoms:
        t = best_type(big)
        subprocess.Popen(['xclip', '-selection', 'clipboard', '-t', t, '-i'], stdin=subprocess.PIPE).communicate(big[t])
        print('ready', flush=True)
        return
    w.set_selection_owner(clip, X.CurrentTime)
    d.flush()
    print('ready', flush=True)
    while True:
        e = d.next_event()
        if e.type == X.SelectionClear:
            return
        if e.type != X.SelectionRequest:
            continue
        prop = e.property or e.target
        if e.target == targets_atom:
            e.requestor.change_property(prop, Xatom.ATOM, 32, [targets_atom] + list(atoms))
        elif e.target in atoms:
            e.requestor.change_property(prop, e.target, 8, atoms[e.target])
        else:
            prop = X.NONE
        e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection, target=e.target, property=prop))
        d.flush()


class WaylandClipboard:
    """wl-clipboard (wlroots and KDE data-control): reads every type; wl-copy can give back only the best one."""

    def read_all(self):
        try:
            types = _run(['wl-paste', '--list-types']).stdout.decode().split('\n')
        except (OSError, subprocess.SubprocessError):
            return {}
        saved, total = {}, 0
        for t in (t.strip() for t in types):
            if not t or t in SKIP_TARGETS or total > MAX_TOTAL:
                continue
            try:
                data = _run(['wl-paste', '--no-newline', '--type', t]).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            saved[t] = data
            total += len(data)
        return saved

    def put_text(self, text):
        _run(['wl-copy'], text.encode())

    def restore(self, saved):
        t = best_type(saved)
        if t is None:
            _run(['wl-copy', '--clear'])
        else:
            _run(['wl-copy', '--type', t], saved[t])


def clipboard_for(env):
    if os.environ.get('DISPLAY') and (env.session == 'x11' or env.compositor == 'gnome') and shutil.which('xclip'):
        return X11Clipboard()
    return WaylandClipboard()


class ClipboardBorrow:
    """with ClipboardBorrow(cb) as b: b.put(text); paste … — the user's clipboard comes back, every type, even on error."""

    def __init__(self, backend):
        self.backend, self.saved = backend, {}

    def __enter__(self):
        self.saved = self.backend.read_all()
        return self

    def put(self, text):
        self.backend.put_text(text)

    def __exit__(self, *exc):
        self.backend.restore(self.saved)
        return False
