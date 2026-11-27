"""X11Bridge against a real X server and window manager. Runs in the test image: ATTICUS_X11_LIVE=1 with Xvfb + openbox up."""
import os
import tempfile
import time
import unittest

from tests import linux  # noqa: F401
from atticus_shell.desktop import bridge as B


@unittest.skipUnless(os.environ.get('ATTICUS_X11_LIVE') and os.environ.get('DISPLAY'), 'live X11: run linux/packaging/x11-live.sh')
class X11Live(unittest.TestCase):
    def setUp(self):
        from Xlib import X, display
        from atticus_shell.desktop.x11 import X11Bridge, XlibConn
        self.d = display.Display()
        screen = self.d.screen()
        self.win = screen.root.create_window(50, 60, 320, 200, 0, screen.root_depth, X.InputOutput, X.CopyFromParent,
                                             background_pixel=screen.white_pixel)
        self.win.set_wm_name('Atticus live test')
        self.win.set_wm_class('livetest', 'LiveTest')
        self.win.map()
        self.d.flush()
        self.addCleanup(lambda: (self.win.destroy(), self.d.flush()))
        self.b = X11Bridge(XlibConn())
        self.mine = self.wait(lambda: next((w for w in self.b.windows() if w.title == 'Atticus live test'), None))

    def wait(self, fn, secs=5):
        end = time.time() + secs
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.1)
        self.fail('timed out')

    def test_window_listed_with_app_id(self):
        self.assertEqual(self.mine.app_id, 'livetest')

    def test_move_resize_reads_back(self):
        self.assertTrue(self.b.move_resize(self.mine.id, 200, 150, 400, 300))
        moved = self.wait(lambda: next((w for w in self.b.windows() if w.id == self.mine.id and (w.width, w.height) == (400, 300)), None))
        self.assertLess(abs(moved.x - 200) + abs(moved.y - 150), 40)   # the WM may add its frame offset

    def test_screenshot_output_size(self):
        out = self.b.outputs()[0]
        path = os.path.join(tempfile.mkdtemp(), 'o.png')
        self.assertTrue(self.b.screenshot_output(out.name, path))
        self.assertEqual(B.png_size(path), (out.width, out.height))

    def test_activate_focuses(self):
        self.assertTrue(self.b.activate(self.mine.id))
        self.wait(lambda: (self.b.focused() or B.Window('', '', 0, '', 0, 0, 0, 0)).id == self.mine.id)

    def test_pointer(self):
        x, y = self.b.pointer()
        self.assertIsInstance(x, float)
