"""Typed Ubuntu desktop host for the bundled Atticus ask box."""
import json
import sys
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit

ROOT = Path(__file__).resolve().parent


class AskWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title='Atticus')
        self.set_default_size(470, 240)
        self.worker = None
        manager = WebKit.UserContentManager()
        manager.connect('script-message-received::c', self.message)
        manager.register_script_message_handler('c', None)
        manager.register_script_message_handler('h', None)
        self.web = WebKit.WebView(user_content_manager=manager)
        self.set_child(self.web)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.key)
        self.add_controller(keys)
        self.connect('close-request', self.closing)
        page = (ROOT / 'ui/ui.html').read_text()
        for marker, name in [('TOKENS', 'tokens.css'), ('I18N', 'i18n.js'), ('ORBS', 'orbs.js')]:
            page = page.replace(f'/*{marker}*/', (ROOT / 'ui' / name).read_text())
        # Only enable controls for capabilities implemented in this host.
        style = '''<style>
            html { background: rgb(var(--gray-11-rgb)); }
            #card { height: 100vh; max-height: 100vh; }
            #comp { margin-top: auto; }
            #mic, #point, #fold, #mode, #x, #grip, .e, .redo, #agents-pill { display: none !important; }
        </style>'''
        self.web.connect('decide-policy', self.policy)
        self.web.load_html(page.replace('</head>', style + '</head>'), None)

    def policy(self, web, decision, kind):
        if kind in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri != 'about:blank' or kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()
                return True
        return False

    def js(self, code):
        self.web.evaluate_javascript(code, -1, None, None, None, None, None)

    def message(self, manager, value):
        if not value.is_object():
            return
        data = json.loads(value.to_json(0))
        if not isinstance(data, dict):
            return
        if data.get('ready'):
            self.js('''ui.visible(true); ui.hover(true); ui.compose(true);
                document.getElementById('ask').placeholder = 'Try “move Firefox to the left”';''')
        elif data.get('close'):
            self.close()
        elif isinstance(data.get('send'), str) and data['send'].strip() and self.worker is None:
            self.submit(data['send'].strip())
        elif isinstance(data.get('copy'), str):
            self.get_clipboard().set(data['copy'])

    def submit(self, text):
        self.js(f'ui.listen(true); ui.words({json.dumps(text)}); ui.think()')
        try:
            worker = Gio.Subprocess.new([sys.executable, str(ROOT / 'linux_desktop.py'), '--ask', text],
                                        Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error as error:
            self.answer(error.message, False)
            return
        self.worker = worker
        worker.communicate_utf8_async(None, None, self.completed)

    def completed(self, worker, result):
        try:
            _, output, _ = worker.communicate_utf8_finish(result)
        except GLib.Error as error:
            output = error.message
        if worker == self.worker:
            self.worker = None
            self.answer((output or '').strip() or 'The request ended before it returned a result.', worker.get_successful())

    def answer(self, text, ok):
        if ok:
            self.js(f'ui.line({json.dumps("✓ " + text)}); ui.finished(); ui.compose(true)')
        else:
            self.js(f'ui.msg({json.dumps(text)}); ui.compose(true)')

    def cancel(self):
        if self.worker is not None:
            worker, self.worker = self.worker, None
            worker.force_exit()
            return True
        return False

    def key(self, controller, keyval, code, state):
        if keyval != Gdk.KEY_Escape:
            return False
        if self.cancel():
            self.js("ui.stopped('Stopped'); ui.compose(true)")
        else:
            self.close()
        return True

    def closing(self, window):
        self.cancel()
        return False


class Application(Gtk.Application):
    def do_activate(self):
        window = self.get_active_window() or AskWindow(self)
        window.present()


if __name__ == '__main__':
    sys.exit(Application(application_id='org.atticus.Desktop').run(sys.argv))
