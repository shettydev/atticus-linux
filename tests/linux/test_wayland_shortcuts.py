"""Live: Atticus's shortcuts on real Sway and Hyprland (linux/packaging/wl-live.sh). wtype presses the keys like a person."""
import os
import subprocess
import time
import unittest

from tests import linux  # noqa: F401


def serve_edges():
    from atticus_shell import ipc
    edges = []

    class App:
        def trigger(self, which, edge):
            edges.append((which, edge))
    assert ipc.serve(App())
    return edges


def spin(until, seconds=6):
    from gi.repository import GLib
    end = time.time() + seconds
    while time.time() < end and not until():
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def press(hold_ms):
    subprocess.run(['wtype', '-M', 'ctrl', '-M', 'alt', '-P', 'space', '-s', str(hold_ms), '-p', 'space', '-m', 'alt', '-m', 'ctrl'],
                   check=True, timeout=10)


class LiveBase:
    bind = None

    def test_hold_to_talk_press_and_release(self):
        from atticus_shell import ipc, shortcuts
        edges = serve_edges()
        self.addCleanup(ipc.stop)
        result = getattr(shortcuts, self.bind)({'talk': 'CTRL+ALT+space'})
        self.assertEqual((result.bound, result.error), ({'talk': True}, ''))
        press(400)
        spin(lambda: ('talk', 'up') in edges)
        self.assertEqual(edges, [('talk', 'down'), ('talk', 'up')])
        edges.clear()
        result = getattr(shortcuts, self.bind)({'talk': 'CTRL+ALT+space'})   # Atticus restarted: no double binds
        self.assertEqual(result.bound, {'talk': True})
        press(400)
        spin(lambda: ('talk', 'up') in edges)
        time.sleep(0.3)
        spin(lambda: False, 0.3)
        self.assertEqual(edges, [('talk', 'down'), ('talk', 'up')])


@unittest.skipUnless(os.environ.get('ATTICUS_SWAY_LIVE'), 'live Sway: linux/packaging/wl-live.sh')
class SwayLive(LiveBase, unittest.TestCase):
    bind = 'bind_sway'


@unittest.skipUnless(os.environ.get('ATTICUS_HYPR_LIVE'), 'live Hyprland: linux/packaging/wl-live.sh')
class HyprlandLive(LiveBase, unittest.TestCase):
    bind = 'bind_hyprland'


if __name__ == '__main__':
    unittest.main()
