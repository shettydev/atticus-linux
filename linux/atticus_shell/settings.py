"""Settings (ui/settings.html, the `st` bridge): the same page and settings.json as macOS (SettingsWindow, Atticus.swift:6850).

SettingsController is GTK-free: push() loads the page, handle() answers each {do: …} message. Actions that need a part
of Atticus not on Linux yet answer with a notice, never silence.
"""
import json
import os
import subprocess

HANDLED = {}
LINKS = {'github': 'https://github.com/shettydev/atticus-linux', 'whatsNew': 'https://github.com/shettydev/atticus-linux/commits/main',
         'kofi': 'https://ko-fi.com/saarthr'}
LATER = 'That part of Settings isn’t on Linux yet.'


def handles(*names):
    def mark(fn):
        for n in names:
            HANDLED[n] = fn.__name__
        return fn
    return mark


class SettingsController:
    def __init__(self, js, prefs, set_pref, data, run, open_uri, copy, routines_path, on_change, version='', mics=lambda: [],
                 in_test=None, speaker=None, open_setup=lambda: None):
        self.js, self.prefs, self.set_pref, self.data, self.run = js, prefs, set_pref, data, run
        self.open_uri, self.copy, self.routines_path, self.on_change = open_uri, copy, routines_path, on_change
        self.version, self.mics, self.speaker, self.open_setup = version, mics, speaker, open_setup
        self.in_test = bool(os.environ.get('ATTICUS_DIR')) if in_test is None else in_test

    def send(self, call, value):
        self.js('st.' + call, value)

    def push(self):
        try:
            with open(self.routines_path) as f:
                routines = json.load(f)
        except (OSError, ValueError):
            routines = []
        self.send('dictation', self.data.state())
        self.send('load', {'prefs': dict(self.prefs), 'mics': self.mics(), 'version': self.version, 'update': '', 'updateRepair': '',
                           'updateChecking': False, 'hotkeyTaken': False, 'dictateHotkeyTaken': False, 'globeTaken': False,
                           'routines': routines if isinstance(routines, list) else [], 'platform': 'linux'})

    def notice(self, text):
        self.send('notice', {'text': text})

    def handle(self, b):
        what = b.get('do') if isinstance(b, dict) else None
        if not isinstance(what, str):
            return
        method = HANDLED.get(what)
        if method:
            return getattr(self, method)(b)
        self.notice(LATER)

    @handles('set')
    def _set(self, b):
        k, v = b.get('k'), b.get('v')
        if not isinstance(k, str) or 'v' not in b:
            return
        if not self.set_pref(k, v):
            self.push()
            return self.notice('Couldn’t save that change, so the old setting is back.')
        if k == 'improve':
            self.set_pref('improveAsked', True)
        if k == 'login':
            if self.in_test:
                self.set_pref('login', False)
                return self.push()
            self.run(['systemctl', '--user', 'enable' if v else 'disable', 'atticus.service'])
        self.on_change(k, v)

    @handles('dictationWord')
    def _word(self, b):
        if isinstance(b.get('word'), str) and self.data.word(b['word'], replacing=b.get('old')):
            self.send('dictation', self.data.state())
        else:
            self.notice('Couldn’t save that word. Use 1–80 characters, up to 500 words and 4,000 characters total.')

    @handles('dictationWordDelete')
    def _word_delete(self, b):
        if isinstance(b.get('word'), str):
            self.data.forget(b['word'])
        self.send('dictation', self.data.state())

    @handles('dictationDelete')
    def _history_delete(self, b):
        ident = b.get('id')
        self.data.archive['history'] = [e for e in self.data.archive['history'] if ident is not None and e.get('id') != ident] if ident else []
        self.data.save()
        self.send('dictation', self.data.state())

    @handles('routineRemove')
    def _routine_remove(self, b):
        from atticus_shell import box
        if isinstance(b.get('id'), str):
            self.run(box.py_command(['--routine', 'remove', b['id']]))
        self.push()

    @handles('github', 'whatsNew', 'kofi')
    def _link(self, b):
        self.open_uri(LINKS[b['do']])

    @handles('copy')
    def _copy(self, b):
        if isinstance(b.get('text'), str):
            self.copy(b['text'])
            self.notice('Copied.')

    @handles('testVoice')
    def _voice(self, b):
        if self.speaker:
            self.speaker('Hi, this is how Atticus sounds.')
        else:
            self.notice('Install a voice: Piper, or speech-dispatcher (spd-say).')

    @handles('setup')
    def _setup(self, b):
        self.open_setup()

    @handles('openLog')
    def _log(self, b):
        base = os.environ.get('ATTICUS_DIR') or os.path.expanduser('~/.config/atticus')
        self.open_uri('file://' + os.path.join(base, 'app.err'))

    @handles('models')
    def _models(self, b):
        from atticus_shell import box
        out = self.run(box.py_command(['--models']))
        try:
            self.send('models', json.loads(out or '[]'))
        except ValueError:
            self.send('models', [])

    @handles('record', 'recordDictate', 'cancelRecord', 'resetHotkey', 'clearDictate', 'globeKey')
    def _shortcuts(self, b):
        self.notice('On Linux, shortcuts are set by your desktop: GNOME and KDE ask when Atticus starts '
                    '(System Settings → Keyboard → Shortcuts to change them); on Sway, add the bindsym lines from Setup.')

    @handles('checkUpdate', 'updateSource')
    def _update(self, b):
        self.on_change('__update', b['do'])


class SettingsWindow:
    """settings.html in a normal window (it takes focus, like any settings window)."""

    def __init__(self, controller_factory):
        from atticus_shell import web_bridge
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import GLib, Gtk
        self.view = web_bridge.make_view('settings.html', {'st': lambda m: self.controller.handle(m)})
        self.controller = controller_factory(lambda fn, arg: web_bridge.call_js(self.view, fn, [arg]))
        self.window = Gtk.Window(title='Atticus Settings')
        self.window.set_wmclass('atticus-settings', 'Atticus')
        self.window.set_default_size(980, 680)
        self.window.add(self.view)
        self.window.connect('delete-event', lambda *_: (self.window.hide(), True)[1])

        def loaded(_v, event):
            from gi.repository import WebKit2
            if event == WebKit2.LoadEvent.FINISHED:
                GLib.timeout_add(150, lambda: (self.controller.push(), False)[1])
        self.view.connect('load-changed', loaded)

    def open(self, tab=''):
        self.window.show_all()
        self.window.present()
        self.controller.push()
        if tab:
            from atticus_shell import web_bridge
            web_bridge.call_js(self.view, 'st.tab', [tab])


def run_text(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ''
