"""app.atticus.Shell on the session bus: one running Atticus, reachable by helper calls, Sway key bindings and KWin.

Replaces macOS's DistributedNotificationCenter ("atticus.raise", Atticus.swift:9751) and adds what Linux needs:
Trigger (shortcut edges from `bindsym`), HideOverlays (for --look pictures), BridgeEvent, OpenSettings.
"""
import json
import os
import sys

NAME, PATH, IFACE = 'app.atticus.Shell', '/app/atticus/Shell', 'app.atticus.Shell'
XML = f'''<node><interface name="{IFACE}">
  <method name="Raise"><arg type="b" name="user" direction="in"/></method>
  <method name="Trigger"><arg type="s" name="which" direction="in"/><arg type="s" name="edge" direction="in"/></method>
  <method name="Ping"><arg type="s" name="version" direction="out"/></method>
  <method name="HideOverlays"><arg type="u" name="ms" direction="in"/></method>
  <method name="BridgeEvent"><arg type="s" name="json" direction="in"/></method>
  <method name="OpenSettings"/>
</interface></node>'''
SIGNATURES = {'Raise': '(b)', 'Trigger': '(ss)', 'Ping': None, 'HideOverlays': '(u)', 'BridgeEvent': '(s)', 'OpenSettings': None}
TRIGGERS = ('talk', 'dictate', 'handoff', 'meeting')
EDGES = ('down', 'up')
MAX_HIDE_MS = 2000


class Service:
    def __init__(self, app, version=''):
        self.app, self.version = app, version or os.environ.get('ATTICUS_VERSION', '')

    def handle(self, method, args):
        """Run one call; returns the reply tuple (None for no reply value). Bad arguments raise ValueError."""
        if method == 'Raise':
            self.app.raise_(bool(args[0]))
        elif method == 'Trigger':
            which, edge = args
            if which not in TRIGGERS or edge not in EDGES:
                raise ValueError(f'Trigger takes {"|".join(TRIGGERS)} and down|up')
            self.app.trigger(which, edge)
        elif method == 'Ping':
            return (self.version,)
        elif method == 'HideOverlays':
            self.app.hide_overlays(min(int(args[0]), MAX_HIDE_MS))
        elif method == 'BridgeEvent':
            self.app.bridge_event(json.loads(args[0]))
        elif method == 'OpenSettings':
            self.app.open_settings()
        else:
            raise ValueError(f'unknown method {method}')
        return None


_served = {}


def serve(app, version=''):
    """Own app.atticus.Shell and answer on it. False when another Atticus already owns the name."""
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    DO_NOT_QUEUE, PRIMARY = 4, 1
    code = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'RequestName',
                         GLib.Variant('(su)', (NAME, DO_NOT_QUEUE)), GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    if code != PRIMARY:
        return False
    service = Service(app, version)

    def on_call(_conn, _sender, _path, _iface, method, params, invocation):
        try:
            out = service.handle(method, params.unpack())
            invocation.return_value(GLib.Variant('(s)', out) if out else None)
        except (ValueError, TypeError, KeyError) as e:
            invocation.return_dbus_error('app.atticus.Shell.Error.InvalidArgs', str(e))
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    reg = bus.register_object(PATH, node.interfaces[0], on_call)
    _served.update(bus=bus, reg=reg)
    return True


def stop():
    if _served:
        from gi.repository import GLib, Gio
        bus = _served.pop('bus')
        bus.unregister_object(_served.pop('reg'))
        bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ReleaseName',
                      GLib.Variant('(s)', (NAME,)), GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 2000, None)


def call(method, args=()):
    """Call the running Atticus; returns its reply tuple, or None when it isn't running."""
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    sig = SIGNATURES[method]
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        r = bus.call_sync(NAME, PATH, IFACE, method, GLib.Variant(sig, tuple(args)) if sig else None, None,
                          Gio.DBusCallFlags.NONE, 3000, None)
        return r.unpack() if r else ()
    except GLib.Error:
        return None


def cli_call(args, method):
    """Helper-CLI words -> (method, D-Bus args)."""
    if method == 'Raise':
        return method, (args[:1] == ['user'],)
    if method == 'Trigger':
        if len(args) != 2 or args[0] not in TRIGGERS or args[1] not in EDGES:
            raise ValueError('usage: --trigger talk|dictate|handoff|meeting down|up')
        return method, tuple(args)
    return method, ()


def cli_handler(method):
    def handler(args):
        try:
            m, a = cli_call(args, method)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        if call(m, a) is None:
            print('Atticus is not running.', file=sys.stderr)
            return 1
        return 0
    return handler
