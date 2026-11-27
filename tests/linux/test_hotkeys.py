import os
import threading
import time
import unittest

from tests import linux  # noqa: F401
from atticus_shell import hotkeys as H
from atticus_shell.desktop.env import DesktopEnv


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class HoldMachineTests(unittest.TestCase):
    def test_a_lost_release_does_not_leave_atticus_stuck(self):   # a desktop dropped the key-up: the next press sends
        clock = [0.0]
        h = H.HoldMachine('hold', now=lambda: clock[0])
        self.assertEqual(h.down(), ['start'])
        clock[0] += 3
        self.assertEqual(h.down(), ['send'])
        self.assertEqual(h.state, 'idle')
        clock[0] += 1
        self.assertEqual(h.down(), ['start'])

    def setUp(self):
        self.clock = Clock()

    def machine(self, mode='hold'):
        return H.HoldMachine(mode, now=self.clock)

    def test_hold_sends_once_on_release(self):
        m = self.machine()
        self.assertEqual(m.down(), ['start'])
        self.clock.t += 2
        self.assertEqual(m.up(), [])          # held back until it can't be auto-repeat
        self.clock.t += 0.05
        self.assertEqual(m.tick(), ['send'])
        self.assertEqual(m.tick(), [])
        self.assertEqual(m.up(), [])

    def test_autorepeat_pairs_do_not_release(self):
        m = self.machine()
        m.down()
        for _ in range(10):                    # X11 without detectable auto-repeat: release+press 30 ms apart
            self.clock.t += 0.03
            self.assertEqual(m.up(), [])
            self.clock.t += 0.001
            self.assertEqual(m.down(), [])
            self.assertEqual(m.tick(), [])
        self.clock.t += 1
        m.up()
        self.clock.t += 0.05
        self.assertEqual(m.tick(), ['send'])

    def test_toggle_press_starts_and_second_press_sends(self):
        m = self.machine('toggle')
        self.assertEqual(m.down(), ['start'])
        self.assertEqual(m.up(), [])
        self.clock.t += 0.05
        self.assertEqual(m.tick(), [])
        self.clock.t += 3
        self.assertEqual(m.down(), ['send'])
        self.assertEqual(m.up(), [])

    def test_toggle_review_after_60s(self):
        m = self.machine('toggle')
        m.down(); m.up()
        self.clock.t += 59
        self.assertEqual(m.tick(), [])
        self.clock.t += 1.5
        self.assertEqual(m.tick(), ['review'])
        self.assertEqual(m.down(), ['start'])  # back to idle

    def test_hold_has_no_time_limit(self):
        m = self.machine()
        m.down()
        self.clock.t += 600
        self.assertEqual(m.tick(), [])

    def test_lock_without_deactivated_ends_hold(self):
        m = self.machine()
        m.down()
        self.assertEqual(m.session_locked(heard=False), ['cancel'])
        m.down()
        self.assertEqual(m.session_locked(heard=True), ['review'])
        self.assertEqual(m.session_locked(heard=True), [])   # idle: nothing to end

    def test_cancel_returns_to_idle(self):
        m = self.machine()
        m.down()
        m.cancel()
        self.clock.t += 1
        self.assertEqual(m.up(), [])
        self.clock.t += 1
        self.assertEqual(m.tick(), [])
        self.assertEqual(m.down(), ['start'])


class TriggerTests(unittest.TestCase):
    def test_parse_trigger(self):
        self.assertEqual(H.parse_trigger('CTRL+ALT+space'), ({'CTRL', 'ALT'}, 'space'))
        self.assertEqual(H.parse_trigger('ctrl+logo+Return'), ({'CTRL', 'LOGO'}, 'Return'))
        with self.assertRaises(ValueError):
            H.parse_trigger('CTRL+ALT')
        with self.assertRaises(ValueError):
            H.parse_trigger('HYPER+x')

    def test_sway_bindings(self):
        lines = H.sway_bindings({'talk': 'CTRL+ALT+space'}, '/p/atticus-shell')
        self.assertEqual(lines, ['bindsym --no-repeat Ctrl+Mod1+space exec /p/atticus-shell --trigger talk down',
                                 'bindsym --release Ctrl+Mod1+space exec /p/atticus-shell --trigger talk up'])

    def test_pick_source(self):
        self.assertEqual(H.pick_source(DesktopEnv('wayland', 'gnome', frozenset({'GlobalShortcuts'}))), 'portal')
        self.assertEqual(H.pick_source(DesktopEnv('x11', 'x11', frozenset({'GlobalShortcuts'}))), 'x11')
        self.assertEqual(H.pick_source(DesktopEnv('wayland', 'sway', frozenset())), 'external')


class FakePortal:
    def __init__(self):
        self.bound, self.handler = None, None

    def create_session(self):
        return '/session/1'

    def bind(self, session, shortcuts):
        self.bound = (session, shortcuts)
        return {s[0]: s[1].get('preferred_trigger') for s in shortcuts}

    def subscribe(self, handler):
        self.handler = handler


class PortalTests(unittest.TestCase):
    def test_portal_binds_all_four(self):
        portal, edges = FakePortal(), []
        bindings = {'talk': 'CTRL+ALT+space', 'dictate': 'CTRL+LOGO+space', 'handoff': 'CTRL+ALT+Return', 'meeting': 'CTRL+Return'}
        H.PortalShortcuts(portal, bindings, lambda *e: edges.append(e)).start()
        session, shortcuts = portal.bound
        self.assertEqual(session, '/session/1')
        self.assertEqual([s[0] for s in shortcuts], ['talk', 'dictate', 'handoff', 'meeting'])
        self.assertEqual(shortcuts[0][1]['preferred_trigger'], 'CTRL+ALT+space')
        self.assertTrue(all(s[1]['description'] for s in shortcuts))
        portal.handler('Activated', '/session/1', 'talk')
        portal.handler('Deactivated', '/session/1', 'talk')
        portal.handler('Activated', '/other/session', 'talk')   # someone else's session
        self.assertEqual(edges, [('talk', 'down'), ('talk', 'up')])


@unittest.skipUnless(os.environ.get('ATTICUS_X11_LIVE') and os.environ.get('DISPLAY'), 'live X11: run linux/packaging/x11-live.sh')
class X11GrabLive(unittest.TestCase):
    def test_grab_reports_press_and_release(self):
        from Xlib import XK, display
        from Xlib.ext import xtest
        edges, got = [], threading.Event()

        def on_edge(which, edge):
            edges.append((which, edge))
            if edge == 'up':
                got.set()
        grab = H.X11Grab({'talk': 'CTRL+ALT+space'}, on_edge)
        grab.start()
        self.addCleanup(grab.stop)
        time.sleep(0.3)
        d = display.Display()
        codes = [d.keysym_to_keycode(XK.string_to_keysym(k)) for k in ('Control_L', 'Alt_L', 'space')]
        for c in codes:
            xtest.fake_input(d, 2, c)       # KeyPress
        d.sync()
        time.sleep(0.2)
        for c in reversed(codes):
            xtest.fake_input(d, 3, c)       # KeyRelease
        d.sync()
        self.assertTrue(got.wait(3), edges)
        self.assertEqual(edges[0], ('talk', 'down'))
        self.assertEqual(edges[-1], ('talk', 'up'))


if __name__ == '__main__':
    unittest.main()
