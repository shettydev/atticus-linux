import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests import linux
from atticus_shell import ipc

ROOT = Path(__file__).resolve().parents[2]


class FakeApp:
    def __init__(self):
        self.events = []

    def raise_(self, user):
        self.events.append(('raise', user))

    def trigger(self, which, edge):
        self.events.append(('trigger', which, edge))

    def hide_overlays(self, ms):
        self.events.append(('hide', ms))

    def bridge_event(self, data):
        self.events.append(('bridge', data))

    def open_settings(self):
        self.events.append(('settings',))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.s = ipc.Service(self.app, version='2.0.2')

    def test_trigger_routes_to_hotkeys(self):
        self.s.handle('Trigger', ('dictate', 'down'))
        self.assertEqual(self.app.events, [('trigger', 'dictate', 'down')])

    def test_press_edge_for_desktops_without_release_events(self):
        self.s.handle('Trigger', ('talk', 'press'))
        self.assertEqual(self.app.events, [('trigger', 'talk', 'press')])
        self.assertEqual(ipc.cli_call(['talk', 'press'], 'Trigger'), ('Trigger', ('talk', 'press')))

    def test_trigger_rejects_unknown_names(self):
        for args in (('nope', 'down'), ('talk', 'sideways')):
            with self.assertRaises(ValueError):
                self.s.handle('Trigger', args)
        self.assertEqual(self.app.events, [])

    def test_raise_user_flag(self):
        self.s.handle('Raise', (True,))
        self.s.handle('Raise', (False,))
        self.assertEqual(self.app.events, [('raise', True), ('raise', False)])

    def test_ping_reports_version(self):
        self.assertEqual(self.s.handle('Ping', ()), ('2.0.2',))

    def test_hide_overlays_and_bridge_event(self):
        self.s.handle('HideOverlays', (120,))
        self.s.handle('BridgeEvent', ('{"kind": "x"}',))
        self.assertEqual(self.app.events, [('hide', 120), ('bridge', {'kind': 'x'})])

    def test_hide_is_capped(self):
        self.s.handle('HideOverlays', (999999,))
        self.assertEqual(self.app.events, [('hide', 2000)])   # a stray caller can't hide the box for long

    def test_cli_args(self):
        self.assertEqual(ipc.cli_call(['user'], 'Raise'), ('Raise', (True,)))
        self.assertEqual(ipc.cli_call([], 'Raise'), ('Raise', (False,)))
        self.assertEqual(ipc.cli_call(['talk', 'up'], 'Trigger'), ('Trigger', ('talk', 'up')))
        with self.assertRaises(ValueError):
            ipc.cli_call(['talk'], 'Trigger')


@linux.needs_gi
@unittest.skipUnless(os.environ.get('DBUS_SESSION_BUS_ADDRESS'), 'needs a session bus (dbus-run-session)')
class LiveBusTests(unittest.TestCase):
    def test_second_instance_raises_first(self):
        from gi.repository import GLib
        app = FakeApp()
        first = ipc.serve(app, version='2.0.2')
        self.assertTrue(first)
        self.addCleanup(ipc.stop)
        self.assertFalse(ipc.serve(FakeApp(), version='2.0.2'))   # the name is taken
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'linux')}
        p = subprocess.Popen([sys.executable, '-m', 'atticus_shell', '--raise', 'user'], env=env)
        ctx, end = GLib.MainContext.default(), time.time() + 10
        while p.poll() is None and time.time() < end:
            ctx.iteration(False)
            time.sleep(0.01)
        for _ in range(50):
            ctx.iteration(False)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(app.events, [('raise', True)])


if __name__ == '__main__':
    unittest.main()
