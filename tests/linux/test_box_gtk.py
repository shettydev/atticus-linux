"""The real box: GTK window, WebKit ui.html and a brain subprocess, end to end (typed ask; no microphone)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import linux

ROOT = Path(__file__).resolve().parents[2]
LIVE = bool(os.environ.get('DISPLAY')) and linux.HAVE_GI

BRAIN = r'''
import json, sys, time
request = json.loads(sys.stdin.readline())
print('▸ Adding it up', flush=True)
time.sleep(0.2)
print('✓ The answer is 4', flush=True)
'''


@unittest.skipUnless(LIVE, 'needs PyGObject and a display (linux/packaging/gui-tests.sh)')
class BoxEndToEnd(unittest.TestCase):
    def test_typed_ask_renders_answer(self):
        from gi.repository import GLib, Gtk
        from atticus_shell import app, web_bridge
        from atticus_shell.desktop.bridge import Output
        from atticus_shell.desktop.env import DesktopEnv
        from tests.linux.fakes import FakeBridge
        brain = Path(tempfile.mkdtemp(), 'brain.py')
        brain.write_text(BRAIN)
        prefs = {'safe': True, 'speak': False, 'boxW': 500, 'talkMode': 'hold'}
        bridge = FakeBridge([Output('screen', 0, 0, 1600, 900)], [], pointer=(100, 100))
        box = app.Box(prefs, DesktopEnv('x11', 'x11'), bridge, brain_command=[sys.executable, str(brain)], engine=lambda: None)
        end = time.time() + 15
        while time.time() < end and not box.view.loaded:
            Gtk.main_iteration_do(False)
        errors = []
        box.view.report_error = errors.append
        box.controller.typed('what is 2 plus 2')   # right away: calls wait for the page's ready
        text = {'value': ''}

        def read():
            web_bridge.eval_js(box.view, 'document.body.innerText', lambda v: text.update(value=v or ''))
            return True
        GLib.timeout_add(100, read)
        while time.time() < end and 'The answer is 4' not in text['value']:
            Gtk.main_iteration_do(False)
            time.sleep(0.002)
        self.assertIn('The answer is 4', text['value'])
        self.assertEqual(errors, [])
        self.assertTrue(box.window.get_visible())
        x, y = box.window.get_position()
        self.assertEqual((x, y), (14, 10))


if __name__ == '__main__':
    unittest.main()
