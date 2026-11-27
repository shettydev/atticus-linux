"""First-run setup (ui/onboarding.html, the `ob` bridge) with Linux's checks in place of macOS's permission panes.

The page's rows keep their macOS keys (Onboarding.perms, Atticus.swift): mic, speech, ax (assistive technologies),
screen (the desktop bridge), auto (input access), calendar, tools, chrome (the browser extension). Each Fix does the
Linux thing or says exactly what to do.
"""
import json
import os
import subprocess
import threading

PROVIDERS = ('claude', 'devin', 'pi')


class Probe:
    """Live checks of this desktop."""

    def __init__(self, bridge, prefs_dir, env=None, uinput=None):
        self._bridge, self.prefs_dir, self.env = bridge, prefs_dir, env
        self._uinput = uinput or (lambda: os.access('/dev/uinput', os.W_OK))

    def a11y(self):
        try:
            r = subprocess.run(['gsettings', 'get', 'org.gnome.desktop.interface', 'toolkit-accessibility'], capture_output=True, text=True, timeout=2)
            if r.stdout.strip() == 'true':
                return True
            r = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.a11y.Bus', '--object-path', '/org/a11y/bus', '--method',
                                'org.freedesktop.DBus.Properties.Get', 'org.a11y.Status', 'IsEnabled'], capture_output=True, text=True, timeout=2)
            return 'true' in r.stdout
        except (OSError, subprocess.SubprocessError):
            return False

    def bridge(self):
        return bool(getattr(self._bridge, 'available', False))

    def bridge_reason(self):
        return getattr(self._bridge, 'unavailable_reason', '')

    def input(self):
        """Clicks and typing: /dev/uinput (any session), or XTest in an X11 session. XWayland's DISPLAY doesn't count:
        XTest there can't reach Wayland apps."""
        session = getattr(self.env, 'session', None) or os.environ.get('XDG_SESSION_TYPE', '')
        return self._uinput() or session == 'x11'

    def mics(self):
        from atticus_shell import audio
        return bool(audio.microphones())

    def speech_model(self):
        from atticus_shell.speech import stt
        models = os.path.join(self.prefs_dir, 'models')
        return stt.ModelStore(models, stt.SHERPA_MODELS).installed('builtin') or \
            any(os.path.exists(os.path.join(self.prefs_dir, 'whisper', m.file)) for m in stt.WHISPER_MODELS.values())

    def speech_engine(self):
        return speech_engine_ok()

    def speech(self):   # a model is useless without the engine that runs it (sherpa-onnx, installed by build-linux.sh)
        return self.speech_model() and self.speech_engine()

    def extension(self):
        try:
            from atticus_shell.web_host import call
            return bool(call('hello', timeout=2))
        except Exception:
            return False

    def calendar(self):
        try:
            r = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus',
                                '--method', 'org.freedesktop.DBus.ListActivatableNames'], capture_output=True, text=True, timeout=2)
            return 'org.gnome.evolution.dataserver.Calendar' in r.stdout
        except (OSError, subprocess.SubprocessError):
            return False


RULE = '/etc/udev/rules.d/60-atticus-uinput.rules'
ENGINE_MISSING = 'The speech engine isn’t installed. Run ./build-linux.sh again, then restart Atticus.'
RELOGIN = 'Log out and back in once to finish.'


def speech_engine_ok():
    import importlib.util
    return importlib.util.find_spec('sherpa_onnx') is not None


def hints(probe, rule_installed=None):
    """What to do for rows one click can't finish, shown under the row before anyone clicks."""
    rule_installed = os.path.exists(RULE) if rule_installed is None else rule_installed
    out = {}
    if not probe.bridge():
        out['screen'] = probe.bridge_reason() or RELOGIN
    if probe.speech_model() and not probe.speech_engine():
        out['speech'] = ENGINE_MISSING
    if not probe.input() and rule_installed:
        out['auto'] = RELOGIN
    return out


def status(probe, prefs):
    return {'mic': probe.mics(), 'speech': probe.speech(), 'ax': probe.a11y(), 'screen': probe.bridge(), 'auto': probe.input(),
            'calendar': probe.calendar(), 'tools': bool(prefs.get('agentUserTools')), 'chrome': probe.extension()}


class OnboardingController:
    def __init__(self, js, prefs, set_pref, probe, fix, run, close, open_settings=lambda tab='': None, open_uri=lambda u: None,
                 hints=lambda: {}, busy=lambda: set()):
        self.js, self.prefs, self.set_pref, self.probe = js, prefs, set_pref, probe
        self.fix, self.run, self.close, self.open_settings, self.open_uri = fix, run, close, open_settings, open_uri
        self.hints, self.busy = hints, busy

    def push(self):
        self.js('ob.platform', {'os': 'linux'})
        self.js('ob.set', status(self.probe, self.prefs))
        busy = self.busy()
        for key, text in self.hints().items():
            if key not in busy:   # never over a running download's progress
                self.js('ob.row', {'id': key, 'text': text, 'busy': False})
        self.js('ob.prefs', {k: self.prefs.get(k, '') for k in ('hotkeyLabel', 'dictateHotkeyLabel', 'model', 'agentModel', 'routing')}
                | {'paused': bool(self.prefs.get('paused')), 'dictateHotkeyTaken': False, 'globeTaken': False})
        self.js('ob.server', {'apiBase': self.prefs.get('apiBase', ''), 'apiModel': self.prefs.get('apiModel', '')})

    def check_plan(self):
        from atticus_shell import box
        out = self.run(box.py_command(['--check']))
        try:
            self.js('ob.plan', json.loads(out or '{}'))
        except ValueError:
            self.js('ob.plan', {})

    def handle(self, b):
        if not isinstance(b, dict):
            return
        what = b.get('do')
        if what == 'done':
            self.set_pref('onboarded', True)
            return self.close()
        if what == 'page':
            self.set_pref('setupPage', b.get('id', ''))
            return
        if what == 'permissionsReady':
            return self.push()
        if what == 'kofi':
            return self.open_uri('https://ko-fi.com/saarthr')
        if what == 'feedback':
            return self.open_settings('feedback')
        if what == 'provider':
            if b.get('client') not in PROVIDERS:
                return
            self.set_pref('client', b['client'])
            for k in ('apiBase', 'apiKey', 'apiModel'):
                if isinstance(b.get(k), str):
                    self.set_pref(k, b[k])
            return self.check_plan()
        if what == 'set':
            if b.get('k') == 'improve':
                self.set_pref('improve', b.get('v') == 'true')
            elif b.get('k') == 'model' and b.get('v') in ('sonnet', 'opus'):
                self.set_pref('model', b['v'])
            return self.push()
        if what == 'dictationFocus':
            return
        key = b.get('id', '')
        if key:
            self.fix(key)
            self.push()


class Fixer:
    """What a row's button does on Linux. Messages and progress go on the row itself (ob.row); a download runs once at a time."""

    def __init__(self, env, prefs_dir, probe, row, push, post, store=None, engine_ok=speech_engine_ok, ensure_a11y=None,
                 run=subprocess.run, rule_path=RULE):
        from atticus_shell.speech import stt
        self.env, self.prefs_dir, self.probe, self.row, self.push, self.post = env, prefs_dir, probe, row, push, post
        self.store = store or stt.ModelStore(os.path.join(prefs_dir, 'models'), stt.SHERPA_MODELS)
        self.engine_ok, self.run, self.rule_path = engine_ok, run, rule_path
        if ensure_a11y is None:
            from atticus_shell import a11y
            ensure_a11y = a11y.ensure_a11y_on
        self.ensure_a11y = ensure_a11y
        self.downloading, self.status = False, ''
        self.lock = threading.Lock()

    def busy(self):
        return {'speech'} if self.downloading else set()

    def fix(self, key):
        getattr(self, 'fix_' + key, lambda: None)()

    def fix_ax(self):
        self.ensure_a11y()
        if not self.probe.a11y():
            self.row('ax', 'Couldn’t turn it on here. Run: gsettings set org.gnome.desktop.interface toolkit-accessibility true')

    def fix_mic(self):
        if not self.probe.mics():
            self.row('mic', 'No microphone found. Plug one in, or check Sound in your system settings.')

    def fix_speech(self):
        if not self.engine_ok():
            return self.row('speech', ENGINE_MISSING)
        with self.lock:
            if self.downloading:
                return self.row('speech', self.status or 'Downloading…', busy=True)
            self.downloading, self.status = True, 'Starting…'
        self.row('speech', self.status, busy=True)
        threading.Thread(target=self._download, daemon=True).start()

    def _report(self, text, busy=True):
        self.status = text
        self.post(lambda: self.row('speech', text, busy=busy))

    def _download(self):
        shown = [-1]

        def progress(fraction):
            pct = min(100, int(fraction * 100))
            if pct == shown[0]:
                return
            shown[0] = pct
            self._report('Unpacking…' if pct >= 100 else f'Downloading {pct}%')
        message = ''
        try:
            self.store.ensure('builtin', progress)
        except Exception as e:   # ModelError carries words for the user; anything else is unexpected
            message = str(e) or 'The download failed. Try again.'
        with self.lock:
            self.downloading = False
        self._report(message, busy=False)
        self.post(self.push)

    def fix_auto(self):
        if self.probe.input():
            return
        self.row('auto', RELOGIN if os.path.exists(self.rule_path) else
                 'Run ./build-linux.sh again. It asks for your password once to add this permission. Then log out and back in.')

    def fix_screen(self):
        if self.probe.bridge():
            return
        if getattr(self.env, 'compositor', '') == 'gnome':
            self.run(['gnome-extensions', 'enable', 'atticus@atticus.app'], capture_output=True)
            return self.row('screen', 'Turned on. ' + RELOGIN)
        self.row('screen', self.probe.bridge_reason() or 'Atticus can’t reach this desktop’s window controls.')

    def fix_chrome(self):
        fix_action('chrome', self.env, self.prefs_dir, lambda text: None, lambda: None)

    def fix_calendar(self):
        fix_action('calendar', self.env, self.prefs_dir, lambda text: None, lambda: None)


def fix_action(key, env, prefs_dir, notify, post_push):
    """What Fix does for one row on Linux."""
    from atticus_shell import a11y
    if key in ('ax', 'accessibility'):
        a11y.ensure_a11y_on()
    elif key in ('speech',):
        def download():
            from atticus_shell.speech import stt
            try:
                stt.ModelStore(os.path.join(prefs_dir, 'models'), stt.SHERPA_MODELS).ensure('builtin')
                notify('The speech model is ready. Hold Ctrl+Alt+Space and talk.')
            except Exception as e:
                notify(f'The speech model did not download: {e}')
            post_push()
        threading.Thread(target=download, daemon=True).start()
        notify('Downloading the speech model (about 300 MB)…')
    elif key in ('auto',):
        notify('Clicks and typing need one rule: run ./build-linux.sh once more and enter your password, then log out and back in.')
    elif key in ('screen',):
        if env.compositor == 'gnome':
            subprocess.run(['gnome-extensions', 'enable', 'atticus@atticus.app'], capture_output=True)
            notify('Atticus GNOME extension turned on. Log out and back in once to finish setup.')
        else:
            notify('Atticus uses your desktop’s own window controls here; nothing to turn on.')
    elif key in ('chrome',):
        ext = os.path.join(os.environ.get('ATTICUS_RESOURCES_DIR', ''), 'browser-extension')
        subprocess.Popen(['xdg-open', ext], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        notify('In Chrome: open chrome://extensions, turn on Developer mode, click “Load unpacked” and pick this folder.')
    elif key in ('calendar',):
        subprocess.Popen(['gnome-control-center', 'online-accounts'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif key in ('mic', 'microphone'):
        notify('Pick your microphone in Settings → Voice & Sound.')


class OnboardingWindow:
    def __init__(self, controller_factory):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import GLib, Gtk, WebKit2
        from atticus_shell import web_bridge
        self.view = web_bridge.make_view('onboarding.html', {'ob': lambda m: self.controller.handle(m)})
        self.controller = controller_factory(lambda fn, arg: web_bridge.call_js(self.view, fn, [arg]), self.hide)
        self.window = Gtk.Window(title='Set up Atticus')
        self.window.set_wmclass('atticus-setup', 'Atticus')
        self.window.set_default_size(880, 640)
        self.window.add(self.view)
        self.window.connect('delete-event', lambda *_: (self.window.hide(), True)[1])
        self.view.connect('load-changed', lambda _v, e: e == WebKit2.LoadEvent.FINISHED and GLib.timeout_add(150, lambda: (self.controller.push(), self.controller.check_plan(), False)[2]))

    def open(self):
        self.window.show_all()
        self.window.present()

    def hide(self):
        self.window.hide()
