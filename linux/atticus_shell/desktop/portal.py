"""Last-resort backend for Wayland desktops Atticus has no bridge for: xdg-desktop-portal screenshots only."""
import shutil
import urllib.parse
import urllib.request

from atticus_shell.desktop.bridge import Unavailable

REASON = "This desktop doesn't let Atticus move windows or see the pointer. Pictures still work."


def portal_screenshot(timeout=30):
    """Ask org.freedesktop.portal.Screenshot for a picture of every screen; returns its file:// URI or ''."""
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    token = 'atticus%d' % GLib.random_int()
    sender = bus.get_unique_name()[1:].replace('.', '_')
    handle = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'
    loop, result = GLib.MainLoop(), {}

    def response(_c, _s, _p, _i, _sig, params):
        code, results = params.unpack()
        result['uri'] = results.get('uri', '') if code == 0 else ''
        loop.quit()
    sub = bus.signal_subscribe('org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request', 'Response', handle,
                               None, Gio.DBusSignalFlags.NO_MATCH_RULE, response)
    bus.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop', 'org.freedesktop.portal.Screenshot',
                  'Screenshot', GLib.Variant('(sa{sv})', ('', {'handle_token': GLib.Variant('s', token), 'interactive': GLib.Variant('b', False)})),
                  None, Gio.DBusCallFlags.NONE, -1, None)
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()
    bus.signal_unsubscribe(sub)
    return result.get('uri', '')


class PortalBridge(Unavailable):
    def __init__(self, screenshot=None):
        super().__init__(REASON)
        self._screenshot = screenshot or portal_screenshot

    def screenshot_output(self, name, path):
        """The portal pictures the whole desktop; `name` is ignored (callers crop by output geometry)."""
        uri = self._screenshot()
        if not uri.startswith('file://'):
            return False
        src = urllib.request.url2pathname(urllib.parse.urlsplit(uri).path)
        shutil.move(src, path)
        return True
