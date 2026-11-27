import os
import tempfile
import unittest

from tests import linux  # noqa: F401
from atticus_shell.desktop import bridge as B
from atticus_shell.desktop.env import DesktopEnv
from tests.linux.fakes import FakeBridge, FakeX11Conn


class BridgeContract:
    """Every backend passes these against its own fake transport. make() builds a desktop with:
    'eDP-1' laptop at 0,0 1280x800 and 'HDMI-1' at 1280,0 1920x1080; windows 'Firefox' (focused, top) and 'Terminal'."""
    scale = 1.0

    def make(self):
        raise NotImplementedError

    def setUp(self):
        self.b = self.make()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_outputs_and_builtin(self):
        outs = {o.name: o for o in self.b.outputs()}
        self.assertEqual(set(outs), {'eDP-1', 'HDMI-1'})
        self.assertTrue(outs['eDP-1'].builtin)
        self.assertFalse(outs['HDMI-1'].builtin)
        self.assertEqual((outs['HDMI-1'].x, outs['HDMI-1'].width, outs['HDMI-1'].height), (1280, 1920, 1080))

    def test_windows_top_first_and_focused(self):
        wins = self.b.windows()
        self.assertEqual([w.title for w in wins], ['Firefox', 'Terminal'])
        self.assertEqual(self.b.focused().title, 'Firefox')
        self.assertEqual(wins[0].output, 'eDP-1')

    def test_move_resize_reads_back(self):
        term = next(w for w in self.b.windows() if w.title == 'Terminal')
        self.assertTrue(self.b.move_resize(term.id, 1280, 0, 960, 1080))
        moved = next(w for w in self.b.windows() if w.id == term.id)
        self.assertEqual((moved.x, moved.y, moved.width, moved.height, moved.output), (1280, 0, 960, 1080, 'HDMI-1'))

    def test_activate_brings_to_top(self):
        term = next(w for w in self.b.windows() if w.title == 'Terminal')
        self.assertTrue(self.b.activate(term.id))
        self.assertEqual(self.b.windows()[0].title, 'Terminal')
        self.assertEqual(self.b.focused().id, term.id)

    def test_fullscreen_toggles(self):
        ff = self.b.focused()
        self.assertTrue(self.b.set_fullscreen(ff.id, True))
        self.assertTrue(self.b.focused().fullscreen)
        self.assertTrue(self.b.set_fullscreen(ff.id, False))
        self.assertFalse(self.b.focused().fullscreen)

    def test_bridge_contract_logical_coordinates(self):
        path = os.path.join(self.tmp.name, 'o.png')
        self.assertTrue(self.b.screenshot_output('HDMI-1', path))
        self.assertEqual(B.png_size(path), (round(1920 * self.scale), round(1080 * self.scale)))

    def test_screenshot_window(self):
        path = os.path.join(self.tmp.name, 'w.png')
        self.assertTrue(self.b.screenshot_window(self.b.focused().id, path))
        self.assertGreater(os.path.getsize(path), 0)

    def test_unknown_window_is_false_not_error(self):
        self.assertFalse(self.b.move_resize('nope', 0, 0, 1, 1))
        self.assertFalse(self.b.activate('nope'))


def two_screen_windows():
    return [B.Window('0x1', 'firefox', 10, 'Firefox', 0, 0, 1280, 800, 'eDP-1', '0', focused=True),
            B.Window('0x2', 'terminal', 11, 'Terminal', 100, 100, 600, 400, 'eDP-1', '0')]


class FakeBridgeContract(BridgeContract, unittest.TestCase):
    scale = 1.5

    def make(self):
        return FakeBridge([B.Output('eDP-1', 0, 0, 1280, 800, 1.0, True), B.Output('HDMI-1', 1280, 0, 1920, 1080, 1.5, False)],
                          two_screen_windows())


def x11_conn(**kw):
    return FakeX11Conn([('eDP-1', 0, 0, 1280, 800), ('HDMI-1', 1280, 0, 1920, 1080)],
                       [{'xid': 0x1, 'title': 'Firefox', 'wm_class': ('Navigator', 'firefox'), 'pid': 10, 'x': 0, 'y': 0, 'w': 1280, 'h': 800},
                        {'xid': 0x2, 'title': 'Terminal', 'wm_class': ('gnome-terminal-server', 'Gnome-terminal'), 'pid': 11, 'x': 100, 'y': 100, 'w': 600, 'h': 400}],
                       active=0x1, **kw)


class X11Contract(BridgeContract, unittest.TestCase):
    def make(self):
        from atticus_shell.desktop.x11 import X11Bridge
        self.conn = x11_conn()
        return X11Bridge(self.conn)


class X11Specifics(unittest.TestCase):
    def test_move_uses_net_moveresize(self):
        from atticus_shell.desktop.x11 import X11Bridge
        conn = x11_conn()
        X11Bridge(conn).move_resize('0x2', 10, 20, 300, 200)
        kinds = [m[1] for m in conn.messages]
        self.assertIn('_NET_MOVERESIZE_WINDOW', kinds)
        self.assertEqual(conn.messages[kinds.index('_NET_MOVERESIZE_WINDOW')][2][1:5], [10, 20, 300, 200])

    def test_move_leaves_maximized_first(self):
        from atticus_shell.desktop.x11 import X11Bridge
        conn = x11_conn()
        X11Bridge(conn).move_resize('0x2', 10, 20, 300, 200)
        self.assertEqual([m[1] for m in conn.messages][:1], ['_NET_WM_STATE'])
        self.assertEqual(conn.messages[0][2][0], 0)   # remove

    def test_x11_screenshot_covered_window_needs_composite(self):
        from atticus_shell.desktop.x11 import X11Bridge
        b = X11Bridge(x11_conn(composite=False))
        self.assertFalse(b.covers_hidden)
        self.assertFalse(b.screenshot_window('0x2', os.path.join(tempfile.mkdtemp(), 'w.png')))

    def test_work_area_clipped_to_output(self):
        from atticus_shell.desktop.x11 import X11Bridge
        b = X11Bridge(x11_conn(workarea=(0, 32, 3200, 1048)))
        outs = {o.name: o for o in b.outputs()}
        self.assertEqual(outs['eDP-1'].work_area, (0, 32, 1280, 768))
        self.assertEqual(outs['HDMI-1'].work_area, (1280, 32, 1920, 1048))

    def test_app_id_from_wm_class(self):
        from atticus_shell.desktop.x11 import X11Bridge
        self.assertEqual(X11Bridge(x11_conn()).windows()[1].app_id, 'gnome-terminal')


class UnavailableTests(unittest.TestCase):
    def test_unavailable_answers_no_with_reason(self):
        u = B.Unavailable('Log out and back in once to finish setup.')
        self.assertFalse(u.available)
        self.assertEqual((u.windows(), u.outputs(), u.focused()), ([], [], None))
        self.assertFalse(u.move_resize('x', 0, 0, 1, 1))
        with self.assertRaises(B.PointerUnavailable):
            u.pointer()

    def test_portal_bridge_screenshots_only(self):
        from atticus_shell.desktop.portal import PortalBridge
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, 'shot.png')
        B.write_png(src, 2, 1, b'\x00' * 8)
        p = PortalBridge(lambda: 'file://' + src)
        self.assertFalse(p.available)
        self.assertIn('window', p.unavailable_reason.lower())
        out = os.path.join(tmp, 'out.png')
        self.assertTrue(p.screenshot_output('', out))
        self.assertEqual(B.png_size(out), (2, 1))

    def test_bridge_for_picks_x11(self):
        from atticus_shell.desktop.x11 import X11Bridge
        self.assertIsInstance(B.bridge_for(DesktopEnv('x11', 'x11'), conn=x11_conn()), X11Bridge)

    def test_bridge_for_unknown_wayland_is_portal(self):
        from atticus_shell.desktop.portal import PortalBridge
        self.assertIsInstance(B.bridge_for(DesktopEnv('wayland', 'other'), screenshot=lambda: ''), PortalBridge)


class PngTests(unittest.TestCase):
    def test_round_trip_size(self):
        path = os.path.join(tempfile.mkdtemp(), 'a.png')
        B.write_png(path, 3, 2, b'\xff' * 24)
        self.assertEqual(B.png_size(path), (3, 2))

    def test_wrong_byte_count_rejected(self):
        with self.assertRaises(ValueError):
            B.write_png(os.path.join(tempfile.mkdtemp(), 'a.png'), 3, 2, b'\xff' * 23)

    def test_bgra_to_rgba(self):
        self.assertEqual(B.bgra_to_rgba(b'\x01\x02\x03\x00'), b'\x03\x02\x01\xff')

    def test_output_at_nearest(self):
        outs = [B.Output('a', 0, 0, 100, 100), B.Output('b', 100, 0, 100, 100)]
        self.assertEqual((B.output_at(outs, 150, 50), B.output_at(outs, 500, 50)), ('b', 'b'))


if __name__ == '__main__':
    unittest.main()
