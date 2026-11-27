"""KDE backend: a generated KWin script per request (answers back over D-Bus) and org.kde.KWin.ScreenShot2 for pictures."""
import json
import os
import tempfile

from atticus_shell.desktop.bridge import BUILTIN, Output, PointerUnavailable, Window, output_at, write_png

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kwin-script', 'atticus.js')
NO_KWIN = "KWin did not answer. Atticus needs a KDE Plasma session to move windows."
NO_SHOTS = ('KWin refused a screenshot. Reinstall Atticus (./build-linux.sh) so its atticus.desktop keeps '
            'X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2, then log out and back in.')
BGRA = {4, 5, 6}       # QImage::Format_RGB32, ARGB32, ARGB32_Premultiplied: B,G,R,A in memory on little-endian
RGBA = {16, 17, 18}    # QImage::Format_RGBX8888, RGBA8888, RGBA8888_Premultiplied


class NotAuthorized(Exception):
    pass


def script_for(op, args, service, path):
    with open(SCRIPT) as f:
        src = f.read()
    # json.dumps output is a valid JS literal, so model- or page-supplied ids stay data
    return (src.replace('__OP__', json.dumps(op)).replace('__ARGS__', json.dumps(args))
            .replace('__SERVICE__', json.dumps(service)).replace('__PATH__', json.dumps(path)))


def to_rgba(raw, width, height, stride, fmt):
    out = bytearray()
    for y in range(height):
        row = raw[y * stride:y * stride + width * 4]
        px = bytearray(row)
        if fmt in BGRA:
            px[0::4], px[2::4] = row[2::4], row[0::4]
        elif fmt not in RGBA:
            raise ValueError(f'unsupported QImage format {fmt}')
        px[3::4] = b'\xff' * width
        out += px
    return bytes(out)


class GioKwinBus:
    """Loads a script into KWin, waits for its Reply over this connection, then unloads it."""
    REPLY_PATH = '/app/atticus/KwinReply'
    IFACE = ('<node><interface name="app.atticus.KwinReply"><method name="Reply"><arg type="s" direction="in"/></method>'
             '</interface></node>')

    def __init__(self):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.n = 0

    def _sync(self, name, path, iface, method, params, reply):
        return self.bus.call_sync(name, path, iface, method, params, self.GLib.VariantType(reply) if reply else None,
                                  self.Gio.DBusCallFlags.NONE, 5000, None)

    def kwin_present(self):
        try:
            return self._sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                              self.GLib.Variant('(s)', ('org.kde.KWin',)), '(b)').unpack()[0]
        except self.GLib.Error:
            return False

    def run_script(self, src, timeout=3):
        Gio, GLib = self.Gio, self.GLib
        self.n += 1
        plugin = f'atticus-{os.getpid()}-{self.n}'
        result, loop = {}, GLib.MainLoop()

        def on_call(_conn, _sender, _path, _iface, _method, params, invocation):
            result['value'] = json.loads(params.unpack()[0])
            invocation.return_value(None)
            loop.quit()
        node = Gio.DBusNodeInfo.new_for_xml(self.IFACE)
        reg = self.bus.register_object(self.REPLY_PATH, node.interfaces[0], on_call)
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(src)
            path = f.name
        try:
            sid = self._sync('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', 'loadScript',
                             GLib.Variant('(ss)', (path, plugin)), '(i)').unpack()[0]
            for obj in (f'/Scripting/Script{sid}', f'/{sid}'):   # KWin 6, then KWin 5
                try:
                    self._sync('org.kde.KWin', obj, 'org.kde.kwin.Script', 'run', None, None)
                    break
                except GLib.Error:
                    continue
            GLib.timeout_add(int(timeout * 1000), loop.quit)
            loop.run()
        finally:
            self.bus.unregister_object(reg)
            os.unlink(path)
            try:
                self._sync('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', 'unloadScript', GLib.Variant('(s)', (plugin,)), '(b)')
            except GLib.Error:
                pass
        return result.get('value')

    def service(self):
        return self.bus.get_unique_name()

    def capture(self, method, target, path):
        Gio, GLib = self.Gio, self.GLib
        r, w = os.pipe()
        fds = Gio.UnixFDList.new()
        fds.append(w)
        os.close(w)
        try:
            ret, _ = self.bus.call_with_unix_fd_list_sync(
                'org.kde.KWin', '/org/kde/KWin/ScreenShot2', 'org.kde.KWin.ScreenShot2', method,
                GLib.Variant('(sa{sv}h)', (target, {'include-cursor': GLib.Variant('b', False)}, 0)),
                GLib.VariantType('(a{sv})'), Gio.DBusCallFlags.NONE, 10000, fds, None)
        except GLib.Error as e:
            os.close(r)
            if 'NoAuthorized' in str(e):
                raise NotAuthorized(str(e))
            return False
        info = ret.unpack()[0]
        with os.fdopen(r, 'rb') as f:
            raw = f.read()
        width, height = info['width'], info['height']
        write_png(path, width, height, to_rgba(raw, width, height, info.get('stride', width * 4), info.get('format', 6)))
        return True


class KwinBridge:
    covers_hidden = True

    def __init__(self, bus=None):
        self.bus = bus or GioKwinBus()
        self.available = bool(self.bus.kwin_present())
        self.unavailable_reason = '' if self.available else NO_KWIN
        self.screenshot_reason = ''

    def _op(self, op, **args):
        if not self.available:
            return None
        service = self.bus.service() if hasattr(self.bus, 'service') else ''
        try:
            return self.bus.run_script(script_for(op, args, service, GioKwinBus.REPLY_PATH))
        except Exception:
            return None

    def outputs(self):
        return [Output(r['name'], r['x'], r['y'], r['width'], r['height'], float(r.get('scale') or 1), bool(BUILTIN.match(r['name'])),
                       tuple(r['area'])) for r in self._op('outputs') or []]

    def windows(self):
        outs = self.outputs()
        return [Window(r['id'], r['app_id'], r['pid'], r['title'], r['x'], r['y'], r['width'], r['height'],
                       output_at(outs, r['x'] + r['width'] / 2, r['y'] + r['height'] / 2), r['workspace'],
                       r['minimized'], r['fullscreen'], r['focused']) for r in self._op('windows') or []]

    def focused(self):
        return next((w for w in self.windows() if w.focused), None)

    def pointer(self):
        p = self._op('pointer')
        if not p or 'x' not in p:
            raise PointerUnavailable(self.unavailable_reason or 'KWin did not answer')
        return float(p['x']), float(p['y'])

    def _ok(self, op, **args):
        r = self._op(op, **args)
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

    def _capture(self, method, target, path):
        try:
            return bool(self.bus.capture(method, target, path))
        except NotAuthorized:
            self.screenshot_reason = NO_SHOTS
            return False

    def screenshot_output(self, name, path):
        return self._capture('CaptureScreen', name, path)

    def screenshot_window(self, win_id, path):
        return self._capture('CaptureWindow', win_id, path)
