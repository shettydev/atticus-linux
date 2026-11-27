"""The panel icon (StatusNotifierItem through AyatanaAppIndicator3) with the macOS menu bar's items (Atticus.swift:6653)."""
import os

NO_WATCHER = ("No panel tray found. On GNOME, turn on the AppIndicator extension (Ubuntu has it on); "
              "everything else works without it, and the Atticus app icon opens Settings.")


def menu_model(state):
    """[(id, title)] and '-' separators for the current state."""
    paused = state.get('paused', False)
    m = [('paused-note', 'Atticus is paused')] if paused else []
    m += [('ask', 'Ask Atticus'), '-', ('agents', 'Agents…'),
          ('shelf', 'Hide Agent Shelf' if state.get('shelf_shown') else 'Show Agent Shelf')]
    if state.get('dictate', True):
        m += [('dictation-history', 'Dictation History…'), ('paste-dictation', 'Paste Last Dictation')]
    m += [('meeting', 'Stop Meeting Copilot' if state.get('meeting_running') else 'Meeting Copilot…'), '-',
          ('resume', 'Resume Atticus') if paused else ('pause', 'Pause Atticus'), ('settings', 'Settings…'),
          ('feedback', 'Report Feedback…'), ('tip', 'Leave a Tip…')]
    if state.get('tips', True) is False:
        m.append(('tips-again', 'Show Tips Again'))
    return m + ['-', ('quit', 'Quit Atticus')]


def icon_state(state):
    return 'paused' if state.get('paused') else 'listening' if state.get('listening') else 'idle'


def watcher_present():
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        r = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                          GLib.Variant('(s)', ('org.kde.StatusNotifierWatcher',)), GLib.VariantType('(b)'), Gio.DBusCallFlags.NONE, 1000, None)
        return r.unpack()[0]
    except Exception:
        return False


class Tray:
    def __init__(self, on_item, state, watcher=watcher_present, indicator=True):
        self.on_item, self.state = on_item, state
        self.available = bool(watcher())
        self.unavailable_reason = '' if self.available else NO_WATCHER
        self.indicator = None
        if self.available and indicator:
            self._indicator()

    def build_menu(self):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk
        menu = Gtk.Menu()
        for item in menu_model(self.state()):
            if item == '-':
                menu.append(Gtk.SeparatorMenuItem())
                continue
            ident, title = item
            mi = Gtk.MenuItem(label=title)
            if ident == 'paused-note':
                mi.set_sensitive(False)
            else:
                mi.connect('activate', lambda _w, ident=ident: self.on_item(ident))
            menu.append(mi)
        menu.show_all()
        return menu

    def _indicator(self):
        import gi
        gi.require_version('AyatanaAppIndicator3', '0.1')
        from gi.repository import AyatanaAppIndicator3 as A
        icon = os.path.join(os.environ.get('ATTICUS_RESOURCES_DIR', ''), 'ui', 'icon.png')
        self.indicator = A.Indicator.new('atticus', icon if os.path.exists(icon) else 'audio-input-microphone', A.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(A.IndicatorStatus.ACTIVE)
        self.indicator.set_title('Atticus')
        self.refresh()

    def refresh(self):
        """Rebuild the menu after a state change (pause, meeting, shelf, listening)."""
        if self.indicator is None:
            return
        self.indicator.set_menu(self.build_menu())
        self.indicator.set_attention_icon_full('audio-input-microphone', 'Listening')
        from gi.repository import AyatanaAppIndicator3 as A
        self.indicator.set_status(A.IndicatorStatus.ATTENTION if icon_state(self.state()) == 'listening' else A.IndicatorStatus.ACTIVE)
