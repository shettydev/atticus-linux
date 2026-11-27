"""Global shortcuts: hold-to-talk / press-to-toggle logic, and where the key edges come from on this desktop.

Sources: the GlobalShortcuts portal (GNOME 48+, KDE, Hyprland), an X11 passive grab, or external triggers
(`atticus-shell --trigger talk down|up` from a Sway `bindsym`). Every source reports (name, 'down'|'up') edges.
"""
import threading
import time

AUTOREPEAT = 0.035     # a release followed this soon by a press is X11 auto-repeat, not the user letting go
TOGGLE_REVIEW = 60.0   # toggle recordings reach the review screen instead of sending (GUIDE, shortcut behaviour)
MODIFIERS = {'CTRL', 'ALT', 'SHIFT', 'LOGO'}
DESCRIPTIONS = {'talk': 'Talk to Atticus', 'dictate': 'Dictate anywhere', 'handoff': 'Move the running ask to an agent',
                'meeting': 'Meeting Copilot: what should I say?'}
ORDER = ('talk', 'dictate', 'handoff', 'meeting')
SWAY_MODS = {'CTRL': 'Ctrl', 'ALT': 'Mod1', 'SHIFT': 'Shift', 'LOGO': 'Mod4'}


class HoldMachine:
    """Key edges in, intents out: start, send, cancel, review. Call tick() every ~20 ms from the main loop."""

    def __init__(self, mode='hold', now=time.monotonic):
        self.mode, self.now = mode, now
        self.state = 'idle'          # idle | held | toggled
        self.started = 0.0
        self.pending_up = None       # time of a release that may still turn out to be auto-repeat
        self.down_seen = False       # toggle: the press that started it is still down

    def down(self):
        t = self.now()
        if self.pending_up is not None and t - self.pending_up < AUTOREPEAT:
            self.pending_up = None   # auto-repeat pair: the key never left
            return []
        if self.state == 'idle':
            self.state, self.started, self.down_seen = ('held' if self.mode == 'hold' else 'toggled'), t, True
            return ['start']
        if self.state == 'toggled' and not self.down_seen:
            self.state = 'idle'
            return ['send']
        return []

    def up(self):
        if self.state == 'held':
            self.pending_up = self.now()
        elif self.state == 'toggled':
            self.down_seen = False
        return []

    def tick(self):
        t = self.now()
        if self.state == 'held' and self.pending_up is not None and t - self.pending_up >= AUTOREPEAT:
            self.state, self.pending_up = 'idle', None
            return ['send']
        if self.state == 'toggled' and t - self.started >= TOGGLE_REVIEW:
            self.state = 'idle'
            return ['review']
        return []

    def session_locked(self, heard=False):
        """The screen locked mid-listen: a lost release must not leave Atticus listening."""
        if self.state == 'idle':
            return []
        self.state, self.pending_up = 'idle', None
        return ['review' if heard else 'cancel']

    def cancel(self):
        self.state, self.pending_up = 'idle', None


def parse_trigger(trigger):
    """'CTRL+ALT+space' (XDG shortcut format) -> ({'CTRL', 'ALT'}, 'space')."""
    parts = [p for p in trigger.split('+') if p]
    if not parts:
        raise ValueError('empty shortcut')
    *mods, key = parts
    mods = {m.upper() for m in mods}
    if key.upper() in MODIFIERS or not mods <= MODIFIERS:
        raise ValueError(f'not a shortcut: {trigger}')
    return mods, key


def sway_bindings(bindings, shell):
    """The two bindsym lines per shortcut that feed Sway's key edges to Atticus."""
    lines = []
    for name in ORDER:
        if name not in bindings:
            continue
        mods, key = parse_trigger(bindings[name])
        combo = '+'.join([SWAY_MODS[m] for m in ('CTRL', 'ALT', 'SHIFT', 'LOGO') if m in mods] + [key])
        lines += [f'bindsym --no-repeat {combo} exec {shell} --trigger {name} down',
                  f'bindsym --release {combo} exec {shell} --trigger {name} up']
    return lines


def pick_source(env):
    if env.session == 'x11':
        return 'x11'
    if 'GlobalShortcuts' in env.portals:
        return 'portal'
    return 'external'


class PortalShortcuts:
    """org.freedesktop.portal.GlobalShortcuts: one session, four shortcuts, Activated/Deactivated -> edges."""

    def __init__(self, portal, bindings, on_edge):
        self.portal, self.bindings, self.on_edge = portal, bindings, on_edge
        self.session = None

    def start(self):
        self.session = self.portal.create_session()
        shortcuts = [(n, {'description': DESCRIPTIONS[n], 'preferred_trigger': self.bindings[n]}) for n in ORDER if n in self.bindings]
        self.portal.subscribe(self._signal)
        return self.portal.bind(self.session, shortcuts)

    def _signal(self, kind, session, shortcut_id):
        if session != self.session or shortcut_id not in DESCRIPTIONS:
            return
        self.on_edge(shortcut_id, 'down' if kind == 'Activated' else 'up')


class GioPortal:
    """The real GlobalShortcuts portal over the session bus (request/response handles per the portal spec)."""
    DEST, OBJ, IFACE = 'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop', 'org.freedesktop.portal.GlobalShortcuts'

    def __init__(self):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.n = 0

    def _request(self, method, build):
        GLib, Gio = self.GLib, self.Gio
        self.n += 1
        token = f'atticus{self.n}_{GLib.random_int()}'
        sender = self.bus.get_unique_name()[1:].replace('.', '_')
        handle = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'
        loop, out = GLib.MainLoop(), {}

        def response(*args):
            code, results = args[-1].unpack()
            out.update(results if code == 0 else {})
            out['code'] = code
            loop.quit()
        sub = self.bus.signal_subscribe(self.DEST, 'org.freedesktop.portal.Request', 'Response', handle, None,
                                        Gio.DBusSignalFlags.NO_MATCH_RULE, response)
        self.bus.call_sync(self.DEST, self.OBJ, self.IFACE, method, build(token), None, Gio.DBusCallFlags.NONE, -1, None)
        GLib.timeout_add_seconds(120, loop.quit)   # BindShortcuts waits for the user to confirm in the system dialog
        loop.run()
        self.bus.signal_unsubscribe(sub)
        return out

    def create_session(self):
        V = self.GLib.Variant
        out = self._request('CreateSession', lambda t: V('(a{sv})', ({'handle_token': V('s', t), 'session_handle_token': V('s', 'atticus')},)))
        return out.get('session_handle', '')

    def bind(self, session, shortcuts):
        V = self.GLib.Variant
        rows = [(n, {k: V('s', v) for k, v in o.items()}) for n, o in shortcuts]
        out = self._request('BindShortcuts', lambda t: V('(oa(sa{sv})sa{sv})', (session, rows, '', {'handle_token': V('s', t)})))
        return {n: o.get('trigger_description', '') for n, o in out.get('shortcuts', [])}

    def subscribe(self, handler):
        def on_signal(_c, _s, _p, _i, name, params):
            session, shortcut_id = params.unpack()[:2]
            handler(name, session, shortcut_id)
        for name in ('Activated', 'Deactivated'):
            self.bus.signal_subscribe(self.DEST, self.IFACE, name, self.OBJ, None, self.Gio.DBusSignalFlags.NONE, on_signal)


class X11Grab:
    """Passive key grabs on the X root window, on a thread with its own connection. on_edge runs on that thread."""
    MASKS = {'CTRL': 'ControlMask', 'ALT': 'Mod1Mask', 'SHIFT': 'ShiftMask', 'LOGO': 'Mod4Mask'}

    def __init__(self, bindings, on_edge, display_name=None):
        self.bindings, self.on_edge, self.display_name = bindings, on_edge, display_name
        self.running = False
        self.thread = None

    def start(self):
        from Xlib import X, XK, display
        self.d = display.Display(self.display_name)
        root = self.d.screen().root
        self.keys = {}
        for name, trigger in self.bindings.items():
            mods, key = parse_trigger(trigger)
            code = self.d.keysym_to_keycode(XK.string_to_keysym(key))
            if not code:
                continue
            mask = 0
            for m in mods:
                mask |= getattr(X, self.MASKS[m])
            for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):   # Caps Lock and Num Lock must not break the shortcut
                root.grab_key(code, mask | extra, True, X.GrabModeAsync, X.GrabModeAsync)
            self.keys[code] = name
        self.d.sync()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        from Xlib import X
        while self.running:
            while self.d.pending_events():
                ev = self.d.next_event()
                name = self.keys.get(getattr(ev, 'detail', None))
                if name and ev.type in (X.KeyPress, X.KeyRelease):
                    self.on_edge(name, 'down' if ev.type == X.KeyPress else 'up')
            time.sleep(0.005)

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(1)
        try:
            root = self.d.screen().root
            from Xlib import X
            for code in self.keys:
                root.ungrab_key(code, X.AnyModifier)
            self.d.close()
        except Exception:
            pass
