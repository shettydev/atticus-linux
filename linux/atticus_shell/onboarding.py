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

    def __init__(self, bridge, prefs_dir):
        self._bridge, self.prefs_dir = bridge, prefs_dir

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
        return os.access('/dev/uinput', os.W_OK) or bool(os.environ.get('DISPLAY'))

    def mics(self):
        from atticus_shell import audio
        return bool(audio.microphones())

    def speech(self):
        from atticus_shell.speech import stt
        models = os.path.join(self.prefs_dir, 'models')
        return stt.ModelStore(models, stt.SHERPA_MODELS).installed('builtin') or \
            any(os.path.exists(os.path.join(self.prefs_dir, 'whisper', m.file)) for m in stt.WHISPER_MODELS.values())

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


def status(probe, prefs):
    return {'mic': probe.mics(), 'speech': probe.speech(), 'ax': probe.a11y(), 'screen': probe.bridge(), 'auto': probe.input(),
            'calendar': probe.calendar(), 'tools': bool(prefs.get('agentUserTools')), 'chrome': probe.extension()}


def detail(probe):
    """Why a row is off, in words for the page."""
    return {'screen': probe.bridge_reason() if not probe.bridge() else ''}


class OnboardingController:
    def __init__(self, js, prefs, set_pref, probe, fix, run, close, open_settings=lambda tab='': None, open_uri=lambda u: None):
        self.js, self.prefs, self.set_pref, self.probe = js, prefs, set_pref, probe
        self.fix, self.run, self.close, self.open_settings, self.open_uri = fix, run, close, open_settings, open_uri

    def push(self):
        self.js('ob.set', status(self.probe, self.prefs))
        self.js('ob.detail', detail(self.probe))
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
