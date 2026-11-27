"""GNOME Wayland implementation of the Atticus window placement contract."""
import json
import os
import re
import time

# Load the introspection overrides once, under Python's module import lock.
# A grouped placement can otherwise initialize Gio on two worker threads.
try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None


class Desktop:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Pin the Shell instance for the whole action. Window IDs can recur after restart.
        self.owner = self.bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'GetNameOwner', GLib.Variant('(s)', ('org.gnome.Shell',)), GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    def call(self, method, signature=None, values=()):
        params = GLib.Variant(signature, values) if signature else None
        result = self.bus.call_sync(self.owner, '/org/atticus/Desktop1', 'org.atticus.Desktop1',
                                    method, params, GLib.VariantType('(s)'),
                                    Gio.DBusCallFlags.NONE, 2500, None)
        return json.loads(result.unpack()[0])


def window_for(windows, app, title):
    key = lambda value: re.sub(r'[\s_-]+', ' ', (value or '').casefold()).strip()
    rows = [w for w in windows if any(key(value) == key(app) for value in
            (w['app'], w['wmclass'], (w['desktop'] or '').removesuffix('.desktop')) if value)]
    if title:
        want = key(title)
        stem = lambda name: re.sub(r'\.[a-z0-9]{2,4}$', '', name)
        matches = [w for w in rows if key(w['title']) == want]
        if not matches:
            matches = [w for w in rows if stem(key(w['title'])) == stem(want)
                       and (stem(want) == want or stem(key(w['title'])) == key(w['title']))]
        rows = matches or [w for w in rows if want in key(w['title'])]
        if len(rows) > 1:
            raise ValueError(f'Multiple {app} windows match "{title}". Specify the full title.')
    if not rows:
        raise ValueError(f'No matching window for {app}.')
    return rows[0]  # Shell tab order is most recently used first.


def monitor_for(snapshot, screen):
    monitors = snapshot['monitors']
    for m in monitors:
        if screen == 'current' and m['index'] == snapshot['current']:
            return m
        if screen == 'primary' and m['primary']:
            return m
        if screen == 'laptop' and m['builtin'] or screen == 'monitor' and not m['builtin']:
            return m
        if screen.casefold() in (m['name'].casefold(), f"display {m['index'] + 1}"):
            return m
    raise ValueError(f'Display "{screen}" is unavailable.')


def place(proc, where='full', screen='current', title=''):
    if where not in ('left', 'right', 'full', 'fullscreen', 'center'):
        return 'where must be left, right, full, fullscreen or center'
    attempted = False
    if GLib is None:
        return 'NOT DONE: Install python3-gi and use the Ubuntu system Python.'
    try:
        desktop = Desktop()
        snapshot = desktop.call('Snapshot')
        w = window_for(snapshot['windows'], proc, title)
        monitor = monitor_for(snapshot, os.environ.get('ATTICUS_SCREEN', screen) if screen == 'current' else screen)
        attempted = True
        expected = desktop.call('Place', '(uis)', (w['id'], monitor['index'], where))
        deadline = time.monotonic() + 2
        matched = False
        while True:
            state = desktop.call('Snapshot')
            current = next((item for item in state['windows'] if item['id'] == w['id']), None)
            if current is None:
                break
            observed = (current['monitor'] == monitor['index'] and current['frame'] == expected
                        and current['fullscreen'] == (where == 'fullscreen') and not current['minimized']
                        and any(m == monitor for m in state['monitors']))
            if observed and matched:
                return f"{proc} window -> {where} of {monitor['name']}"
            matched = observed
            if time.monotonic() >= deadline:
                break
            time.sleep(.05)
        if current is not None:
            return (f"Could not confirm the final position of {proc}. "
                    f"Expected {expected} on {monitor['name']}, observed {current['frame']} "
                    f"on display {current['monitor'] + 1}.")
    except ValueError as error:
        if not attempted:
            return f'NOT DONE: {error}'
    except GLib.Error:
        if not attempted:
            return 'NOT DONE: Enable the Atticus Desktop extension in your GNOME Wayland session.'
    return f'Could not confirm the final position of {proc}.'


def ask(text):
    """Handle the offline commands offered by the Linux session app."""
    import atticus
    if Gio is None:
        return 'Install python3-gi and use the Ubuntu system Python.', False
    words = atticus.plain(text)
    opening = re.fullmatch(r'(?:open|launch|start) (.+)', words)
    if opening:
        wanted = atticus.app_words(opening[1])
        apps = [app for app in Gio.AppInfo.get_all() if app.should_show() and wanted in
                {app.get_display_name().casefold(), (app.get_id() or '').removesuffix('.desktop').casefold()}]
        if len(apps) != 1:
            return f'Use the exact name or desktop ID of the installed app to open: {opening[1]}.', False
        try:
            if apps[0].launch([], None):
                return f'Launched {apps[0].get_display_name()}.', True
        except GLib.Error as error:
            return f'Could not launch {apps[0].get_display_name()}: {error.message}', False
        return f'Could not launch {apps[0].get_display_name()}.', False

    hint = 'Try “open Firefox” or “move Firefox to the left”. For a move, name an open app.'
    if not re.search(r'\b(left|right|full|fullscreen|center|middle|maximi[sz]e|laptop|monitor|display|screen)\b', words):
        return hint, False
    try:
        rows = Desktop().call('Snapshot')['windows']
    except GLib.Error:
        return 'Enable the Atticus Desktop extension to move windows.', False
    names = {name.casefold(): name for w in rows for name in
             (w['app'], w['wmclass'], (w['desktop'] or '').removesuffix('.desktop')) if name}
    # Only named windows are supported until the overlay can capture the previous focus.
    move = atticus.move_target(words, resolve_app=names.get, resolve_previous=lambda: None)
    if not move or not move[0]:
        return hint, False
    result = place(*move)
    return result, not atticus.place_failed(result)


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != '--ask':
        sys.exit('Usage: atticus --ask "move Firefox to the left"')
    message, ok = ask(sys.argv[2])
    print(message)
    sys.exit(0 if ok else 1)
