"""ui/onboarding.html in real WebKitGTK, as the Linux app drives it: Linux words, row progress, row messages."""
import os
import time
import unittest

from tests import linux

LIVE = bool(os.environ.get('DISPLAY')) and linux.HAVE_GI


@unittest.skipUnless(LIVE, 'needs PyGObject and a display (linux/packaging/gui-tests.sh)')
class OnboardingPageLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gi.repository import Gtk
        from atticus_shell import web_bridge
        cls.Gtk, cls.wb = Gtk, web_bridge
        cls.posted, cls.errors = [], []
        cls.view = web_bridge.make_view('onboarding.html', {'ob': cls.posted.append})
        cls.view.report_error = cls.errors.append
        cls.win = Gtk.Window()
        cls.win.add(cls.view)
        cls.win.set_default_size(1000, 760)
        cls.win.show_all()
        end = time.time() + 20
        while time.time() < end and not cls.view.loaded:
            Gtk.main_iteration_do(False)

    @classmethod
    def tearDownClass(cls):
        cls.win.destroy()

    def js(self, script):
        out = {}
        self.wb.eval_js(self.view, script, lambda v: out.update(v=v))
        end = time.time() + 5
        while time.time() < end and 'v' not in out:
            self.Gtk.main_iteration_do(False)
        return out.get('v')

    def call(self, fn, arg):
        self.wb.call_js(self.view, fn, [arg])
        self.js('1')

    def text(self, selector):
        return self.js(f"document.querySelector({selector!r}).textContent")

    def test_linux_words_rows_and_keys(self):
        self.call('ob.platform', {'os': 'linux'})
        self.call('ob.prefs', {'hotkeyLabel': 'Ctrl+Alt+Space', 'dictateHotkeyLabel': 'Ctrl+Super+Space'})
        page = self.js('document.body.innerText')
        self.assertEqual(self.text('#perms h2'), 'Set up Linux access')
        self.assertEqual(self.text('#p-speech b'), 'Speech model')
        self.assertEqual(self.text('#p-speech .allow'), 'Download')
        self.assertEqual(self.text('#p-auto b'), 'Clicks and typing')
        self.assertEqual(self.js("[...document.querySelectorAll('#welkeys .key')].map(k => k.textContent).join(' ')"), 'Ctrl Alt Space')
        for mac in ('Mac', 'System Events', 'Screen & System Audio Recording', 'Speech Recognition', '⌃', '⌥'):
            self.assertNotIn(mac, page, mac)
        self.assertEqual(self.errors, [])

    def test_row_progress_message_and_give_up_timer(self):
        self.call('ob.platform', {'os': 'linux'})
        self.js("document.querySelector('#p-speech .allow').click(); 1")
        end = time.time() + 3
        while time.time() < end and {'do': 'grant', 'id': 'speech'} not in self.posted:   # messages arrive asynchronously
            self.Gtk.main_iteration_do(False)
        self.assertIn({'do': 'grant', 'id': 'speech'}, self.posted)
        self.call('ob.row', {'id': 'speech', 'text': 'Downloading 42%', 'busy': True})
        self.assertEqual(self.text('#p-speech .allow'), 'Downloading 42%')
        self.assertTrue(self.js("document.querySelector('#p-speech .allow').classList.contains('wait')"))
        self.js("document.querySelector('#p-speech .allow').dataset.busy")   # the 12 s give-up must not undo a running download
        self.call('ob.row', {'id': 'speech', 'text': 'Download failed. Check your connection and try again.', 'busy': False})
        self.assertEqual(self.text('#p-speech .allow'), 'Download')
        self.assertEqual(self.text('#p-speech .txt span'), 'Download failed. Check your connection and try again.')
        self.call('ob.row', {'id': 'speech', 'text': '', 'busy': False})
        self.assertIn('300 MB', self.text('#p-speech .txt span'))
        self.call('ob.set', {'mic': True, 'speech': True, 'ax': True, 'screen': True, 'auto': True})
        self.assertTrue(self.js("document.getElementById('p-speech').classList.contains('ok')"))
        self.assertEqual(self.errors, [])

    def test_busy_row_survives_the_give_up_timer(self):
        self.call('ob.platform', {'os': 'linux'})
        self.js("window.__t = window.setTimeout; window.setTimeout = (f, ms) => window.__t(f, ms === 12000 ? 50 : ms); 1")
        self.js("document.querySelector('#p-mic .allow').click(); 1")
        self.call('ob.row', {'id': 'mic', 'text': 'Checking…', 'busy': True})
        end = time.time() + 1
        while time.time() < end:
            self.Gtk.main_iteration_do(False)
        self.assertEqual(self.text('#p-mic .allow'), 'Checking…')
        self.js("window.setTimeout = window.__t; 1")


if __name__ == '__main__':
    unittest.main()
