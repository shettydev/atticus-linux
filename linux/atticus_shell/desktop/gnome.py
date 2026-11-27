"""GNOME backend: the bundled Shell extension (app.atticus.ShellBridge) for windows, pointer and pictures; Mutter's
DisplayConfig D-Bus API for outputs. Without the extension loaded, outputs still work and the rest says how to fix it."""
import json
import os

from atticus_shell.desktop.bridge import RELOGIN, Output, PointerUnavailable, Window, output_at

UUID = 'atticus@atticus.app'
NAME, PATH, IFACE = 'app.atticus.ShellBridge', '/app/atticus/ShellBridge', 'app.atticus.ShellBridge'
TURN_ON = 'Turn on the Atticus GNOME extension: Settings → Desktop integration.'


def extension_installed():
    dirs = [os.path.expanduser('~/.local/share/gnome-shell/extensions'), '/usr/share/gnome-shell/extensions']
    return any(os.path.isdir(os.path.join(d, UUID)) for d in dirs)


def parse_display_state(state):
    """Outputs in logical coordinates from DisplayConfig.GetCurrentState (serial, monitors, logical_monitors, properties)."""
    _, monitors, logical, props = state
    physical_layout = props.get('layout-mode') == 2
    by_connector = {}
    for spec, modes, mprops in monitors:
        mode = next((m for m in modes if m[6].get('is-current')), None)
        if mode:
            by_connector[spec[0]] = (mode[1], mode[2], bool(mprops.get('is-builtin')))
    outs = []
    for x, y, scale, _transform, _primary, specs, _ in logical:
        for spec in specs:
            if spec[0] not in by_connector:
                continue
            pw, ph, builtin = by_connector[spec[0]]
            w, h = (pw, ph) if physical_layout else (round(pw / scale), round(ph / scale))
            outs.append(Output(spec[0], x, y, w, h, float(scale), builtin))
    return outs


class GioBus:
    """The session bus: the extension's Call method and Mutter's display state."""

    def __init__(self):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def _call(self, name, path, iface, method, params, reply, timeout=3000):
        r = self.bus.call_sync(name, path, iface, method, params, self.GLib.VariantType(reply), self.Gio.DBusCallFlags.NONE, timeout, None)
        return r.unpack()

    def name_owned(self):
        try:
            return self._call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                              self.GLib.Variant('(s)', (NAME,)), '(b)')[0]
        except self.GLib.Error:
            return False

    def display_state(self):
        return self._call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig',
                          'GetCurrentState', None, '(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})')

    def call(self, method, args):
        out = self._call(NAME, PATH, IFACE, 'Call', self.GLib.Variant('(ss)', (method, json.dumps(args))), '(s)', timeout=10000)[0]
        return json.loads(out)


class GnomeBridge:
    covers_hidden = True   # window actors paint offscreen, covered or not

    def __init__(self, bus=None, installed=extension_installed, now=None):
        import time
        self.bus, self.installed, self.now = bus or GioBus(), installed, now or time.monotonic
        self.checked = None
        self.available, self.unavailable_reason = False, ''
        self._check()

    def _check(self):
        """The extension can load after Atticus starts (first login, a GNOME restart): look again, at most every 2 s."""
        if self.available or (self.checked is not None and self.now() - self.checked < 2):
            return self.available
        self.checked = self.now()
        self.available = bool(self.bus.name_owned())
        self.unavailable_reason = '' if self.available else (RELOGIN if self.installed() else TURN_ON)
        return self.available

    def _call(self, method, **args):
        if not self._check():
            return None
        try:
            return self.bus.call(method, args)
        except Exception:   # a reloading shell or a vanished window: answer "no", never raise into the box
            return None

    def outputs(self):
        try:
            outs = parse_display_state(self.bus.display_state())
        except Exception:
            return []
        for a in self._call('work_areas') or []:
            for o in outs:
                if (o.x, o.y) == (a['x'], a['y']):
                    o.work_area = tuple(a['area'])
        return outs

    def windows(self):
        rows = self._call('windows') or []
        outs = self.outputs()
        return [Window(str(r['id']), r['app_id'] or '', r['pid'] or 0, r['title'] or '', r['x'], r['y'], r['width'], r['height'],
                       output_at(outs, r['x'] + r['width'] / 2, r['y'] + r['height'] / 2), str(r['workspace']),
                       bool(r['minimized']), bool(r['fullscreen']), bool(r['focused'])) for r in rows]

    def focused(self):
        return next((w for w in self.windows() if w.focused), None)

    def pointer(self):
        p = self._call('pointer')
        if not p:
            raise PointerUnavailable(self.unavailable_reason or 'GNOME did not answer')
        return float(p['x']), float(p['y'])

    def _ok(self, method, **args):
        r = self._call(method, **args)
        return bool(r and r.get('ok'))

    def move_resize(self, win_id, x, y, w, h):
        return self._ok('move_resize', id=win_id, x=x, y=y, width=w, height=h)

    def set_fullscreen(self, win_id, on):
        return self._ok('set_fullscreen', id=win_id, on=bool(on))

    def activate(self, win_id):
        return self._ok('activate', id=win_id)

    def lower(self, win_id):
        return self._ok('lower', id=win_id)

    def keep_above(self, win_id, on):
        return self._ok('keep_above', id=win_id, on=bool(on))

    def screenshot_output(self, name, path):
        o = next((o for o in self.outputs() if o.name == name), None)
        return bool(o) and self._ok('screenshot_area', x=o.x, y=o.y, width=o.width, height=o.height, path=path)

    def screenshot_window(self, win_id, path):
        return self._ok('screenshot_window', id=win_id, path=path)
