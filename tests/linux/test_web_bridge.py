import os
import tempfile
import time
import unittest
from pathlib import Path

from tests import linux
from atticus_shell import web_bridge as W

ROOT = Path(__file__).resolve().parents[2]
PAGES = sorted(p.name for p in (ROOT / 'ui').glob('*.html'))
LIVE = bool(os.environ.get('DISPLAY')) and linux.HAVE_GI


class ComposeTests(unittest.TestCase):
    def test_compose_inlines_markers_and_language(self):
        html = W.compose_page('ui.html', str(ROOT), 'de', 'en')
        for marker in ('/*TOKENS*/', '/*ORBS*/', '/*I18N*/'):
            self.assertNotIn(marker, html)
        self.assertIn("window.atticusLanguage = 'de'; window.atticusAutoLanguage = 'en';", html)
        self.assertIn("window.atticusPlatform = 'linux';", html)
        self.assertIn((ROOT / 'ui/tokens.css').read_text()[:80], html)

    def test_missing_page_says_so(self):
        self.assertEqual(W.compose_page('nope.html', str(ROOT), 'en', 'en'), 'missing nope.html')

    def test_js_call_spreads_json_args(self):
        self.assertEqual(W.js_call('ui.show', [1, {'a': '</script>'}]), 'ui.show(...[1, {"a": "<\\/script>"}])')

    def test_error_row_from_exception_message(self):
        self.assertEqual(W.eval_error_row('ui.show(...[])', "TypeError: undefined is not a function", 3, 9),
                         ['ui', 'TypeError', 'eval', '3', '9'])
        self.assertEqual(W.eval_error_row('st.x', 'boom', 0, 0)[1], 'thrown')
        self.assertEqual(W.eval_error_row('ui.line(...[])', "about:blank:1:3: ReferenceError: Can't find variable: ui", 0, 0),
                         ['ui', 'ReferenceError', 'eval', '1', '3'])   # WebKitGTK's own location prefix


def spin(until, secs=10):
    from gi.repository import Gtk
    end = time.time() + secs
    while time.time() < end and not until():
        Gtk.main_iteration_do(False)
        time.sleep(0.002)
    return until()


@unittest.skipUnless(LIVE, 'needs PyGObject and a display (xvfb-run in the test image)')
class LiveViewTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        Path(self.root, 'ui').mkdir()
        for f in ('tokens.css', 'orbs.js', 'i18n.js'):
            Path(self.root, 'ui', f).write_text((ROOT / 'ui' / f).read_text())

    def page(self, body):
        Path(self.root, 'ui', 't.html').write_text(f'<html><body><script>{body}</script></body></html>')

    def test_message_handler_round_trip(self):
        got = []
        self.page("window.webkit.messageHandlers.c.postMessage({x: 1, s: 'é'})")
        W.make_view('t.html', {'c': got.append}, resources=self.root)
        self.assertTrue(spin(lambda: got))
        self.assertEqual(got[0], {'x': 1, 's': 'é'})

    def test_call_js_reaches_page(self):
        got = []
        self.page("window.echo = (...a) => window.webkit.messageHandlers.c.postMessage(a)")
        view = W.make_view('t.html', {'c': got.append}, resources=self.root)
        spin(lambda: view.loaded)
        W.call_js(view, 'echo', [1, 'two', {'three': 3}])
        self.assertTrue(spin(lambda: got))
        self.assertEqual(got[0], [1, 'two', {'three': 3}])

    def test_page_errors_reported_as_code_locations(self):
        rows = []
        self.page("throw new RangeError('my secret words')")
        W.make_view('t.html', {}, resources=self.root, on_error=rows.append)
        self.assertTrue(spin(lambda: rows))
        self.assertEqual(rows[0][:2], ['t', 'muted'])   # no base URL: an opaque origin, so WebKit mutes details (as on macOS)
        self.assertNotIn('secret', ' '.join(map(str, rows[0])))

    def test_every_ui_page_loads_without_page_errors(self):
        for name in PAGES:
            with self.subTest(name):
                rows, view = [], None
                handlers = {h: (lambda m: None) for h in ('c', 'h', 'st', 'ob', 'meeting', 'agents', 'drawingMath')}
                view = W.make_view(name, handlers, resources=str(ROOT), on_error=rows.append)
                self.assertTrue(spin(lambda: view.loaded), name)
                spin(lambda: False, 0.5)
                self.assertEqual(rows, [], name)


@unittest.skipUnless(LIVE, 'needs PyGObject and a display (xvfb-run in the test image)')
class OverlayTests(unittest.TestCase):
    def test_overlay_not_focusable(self):
        from atticus_shell import windows
        from atticus_shell.desktop.bridge import Output
        from atticus_shell.desktop.env import DesktopEnv
        w = windows.overlay('box', Output('screen', 0, 0, 1280, 800), focusable=False, env=DesktopEnv('x11', 'x11'))
        self.assertFalse(w.get_accept_focus())
        self.assertEqual(w.get_wmclass_class() if hasattr(w, 'get_wmclass_class') else 'atticus', 'atticus')
        self.assertTrue(w.get_skip_taskbar_hint())
        self.assertEqual(windows.backend_for(DesktopEnv('x11', 'x11')), 'x11')

    def test_focusable_box_for_typing(self):
        from atticus_shell import windows
        from atticus_shell.desktop.bridge import Output
        from atticus_shell.desktop.env import DesktopEnv
        w = windows.overlay('box', Output('screen', 0, 0, 1280, 800), focusable=True, env=DesktopEnv('x11', 'x11'))
        self.assertTrue(w.get_accept_focus())

    def test_input_region_empty_passes_clicks(self):
        from atticus_shell import windows
        from atticus_shell.desktop.bridge import Output
        from atticus_shell.desktop.env import DesktopEnv
        from gi.repository import GdkX11  # noqa: F401
        w = windows.overlay('drawing', Output('screen', 0, 0, 640, 480), focusable=False, env=DesktopEnv('x11', 'x11'))
        w.set_default_size(640, 480)
        w.show_all()
        spin(lambda: w.get_window() is not None and w.get_mapped())
        windows.set_input_region(w, [])
        self.assertTrue(spin(lambda: windows.input_rects(w) == [], 3), windows.input_rects(w))
        windows.set_input_region(w, [(10, 20, 30, 40)])
        self.assertTrue(spin(lambda: windows.input_rects(w) == [(10, 20, 30, 40)], 3), windows.input_rects(w))

    def test_backend_choice(self):
        from atticus_shell import windows
        from atticus_shell.desktop.env import DesktopEnv
        self.assertEqual(windows.backend_for(DesktopEnv('wayland', 'gnome'), layer_shell=True), 'x11')    # Mutter has no layer-shell: XWayland
        self.assertEqual(windows.backend_for(DesktopEnv('wayland', 'kde'), layer_shell=True), 'layer-shell')
        self.assertEqual(windows.backend_for(DesktopEnv('wayland', 'sway'), layer_shell=False), 'x11')


class GdkBackendTests(unittest.TestCase):
    def test_gnome_wayland_forces_xwayland(self):
        from atticus_shell import windows
        from atticus_shell.desktop.env import DesktopEnv
        env = {}
        windows.choose_gdk_backend(DesktopEnv('wayland', 'gnome'), env)
        self.assertEqual(env['GDK_BACKEND'], 'x11')
        env = {}
        windows.choose_gdk_backend(DesktopEnv('wayland', 'kde'), env)
        self.assertNotIn('GDK_BACKEND', env)


if __name__ == '__main__':
    unittest.main()
