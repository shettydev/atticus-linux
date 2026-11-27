import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell.desktop import bridge as B
from atticus_shell.desktop import gnome as G
from tests.linux.test_bridge_contract import BridgeContract

ROOT = Path(__file__).resolve().parents[2]

# org.gnome.Mutter.DisplayConfig.GetCurrentState, trimmed to what parse_display_state reads (logical layout mode).
STATE = (1,
         [(('eDP-1', 'BOE', '0x1', '0'), [('1280x800@60', 1280, 800, 60.0, 1.0, [1.0], {'is-current': True})], {'is-builtin': True}),
          (('HDMI-1', 'DEL', 'U27', 'x'), [('2880x1620@60', 2880, 1620, 60.0, 1.5, [1.5], {'is-current': True}),
                                            ('1920x1080@60', 1920, 1080, 60.0, 1.0, [1.0], {})], {})],
         [(0, 0, 1.0, 0, True, [('eDP-1', 'BOE', '0x1', '0')], {}),
          (1280, 0, 1.5, 0, False, [('HDMI-1', 'DEL', 'U27', 'x')], {})],
         {'layout-mode': 1})


class FakeGnomeBus:
    """What the extension answers over app.atticus.ShellBridge.Call, plus Mutter's DisplayConfig state."""

    def __init__(self, state=STATE, owned=True):
        self.state, self.owned = state, owned
        self.wins = [{'id': '11', 'app_id': 'firefox', 'pid': 10, 'title': 'Firefox', 'x': 0, 'y': 0, 'width': 1280, 'height': 800,
                      'workspace': 0, 'minimized': False, 'fullscreen': False, 'focused': True},
                     {'id': '12', 'app_id': 'org.gnome.Terminal', 'pid': 11, 'title': 'Terminal', 'x': 100, 'y': 100, 'width': 600,
                      'height': 400, 'workspace': 0, 'minimized': False, 'fullscreen': False, 'focused': False}]
        self.calls = []

    def name_owned(self):
        return self.owned

    def display_state(self):
        return self.state

    def call(self, method, args):
        self.calls.append((method, args))
        w = next((w for w in self.wins if w['id'] == str(args.get('id'))), None)
        if method == 'windows':
            return [dict(w) for w in self.wins]
        if method == 'pointer':
            return {'x': 640.5, 'y': 400.0}
        if method == 'work_areas':
            return [{'x': 0, 'y': 0, 'width': 1280, 'height': 800, 'area': [0, 32, 1280, 768]},
                    {'x': 1280, 'y': 0, 'width': 1920, 'height': 1080, 'area': [1280, 32, 1920, 1048]}]
        if method in ('move_resize', 'set_fullscreen', 'activate', 'lower', 'keep_above', 'screenshot_window') and not w:
            return {'ok': False}
        if method == 'move_resize':
            w.update(x=args['x'], y=args['y'], width=args['width'], height=args['height'])
        elif method == 'set_fullscreen':
            w['fullscreen'] = args['on']
        elif method == 'activate':
            for o in self.wins:
                o['focused'] = o is w
            self.wins.sort(key=lambda o: not o['focused'])
        elif method == 'screenshot_area':
            scale = 1.5 if args['x'] >= 1280 else 1.0
            pw, ph = round(args['width'] * scale), round(args['height'] * scale)
            B.write_png(args['path'], pw, ph, b'\x00' * (pw * ph * 4))
        elif method == 'screenshot_window':
            B.write_png(args['path'], w['width'], w['height'], b'\x00' * (w['width'] * w['height'] * 4))
        return {'ok': True}


class GnomeContract(BridgeContract, unittest.TestCase):
    scale = 1.5

    def make(self):
        self.bus = FakeGnomeBus()
        return G.GnomeBridge(self.bus)


class GnomeSpecifics(unittest.TestCase):
    def test_parse_display_state_logical(self):
        outs = {o.name: o for o in G.parse_display_state(STATE)}
        self.assertEqual((outs['HDMI-1'].x, outs['HDMI-1'].width, outs['HDMI-1'].height, outs['HDMI-1'].scale), (1280, 1920, 1080, 1.5))
        self.assertTrue(outs['eDP-1'].builtin)

    def test_parse_display_state_physical_layout(self):
        state = STATE[:3] + ({'layout-mode': 2},)
        self.assertEqual(G.parse_display_state(state)[1].width, 2880)

    def test_work_area_from_extension(self):
        b = G.GnomeBridge(FakeGnomeBus())
        self.assertEqual({o.name: o.work_area for o in b.outputs()}['HDMI-1'], (1280, 32, 1920, 1048))

    def test_gnome_backend_unloaded_reports_relogin(self):
        b = G.GnomeBridge(FakeGnomeBus(owned=False), installed=lambda: True)
        self.assertFalse(b.available)
        self.assertEqual(b.unavailable_reason, B.RELOGIN)
        self.assertEqual(b.windows(), [])
        self.assertFalse(b.move_resize('11', 0, 0, 1, 1))
        self.assertEqual(len(b.outputs()), 2)          # outputs still come from Mutter

    def test_not_installed_says_turn_on(self):
        b = G.GnomeBridge(FakeGnomeBus(owned=False), installed=lambda: False)
        self.assertIn('GNOME extension', b.unavailable_reason)

    def test_pointer_is_logical_float(self):
        self.assertEqual(G.GnomeBridge(FakeGnomeBus()).pointer(), (640.5, 400.0))

    def test_extension_keeps_atticus_windows_above(self):
        js = (ROOT / 'linux/atticus_shell/desktop/gnome-extension/atticus@atticus.app/extension.js').read_text()
        meta = json.loads((ROOT / 'linux/atticus_shell/desktop/gnome-extension/atticus@atticus.app/metadata.json').read_text())
        self.assertEqual(meta['uuid'], 'atticus@atticus.app')
        self.assertIn('46', meta['shell-version'])     # Ubuntu 24.04 LTS
        for method in ('windows', 'pointer', 'work_areas', 'move_resize', 'set_fullscreen', 'activate', 'lower',
                       'keep_above', 'screenshot_area', 'screenshot_window'):
            self.assertIn(f"{method}:", js, method)     # every method the bridge calls has a handler


@unittest.skipUnless(os.environ.get('ATTICUS_GNOME_LIVE'), 'live GNOME: run linux/packaging/gnome-live.sh')
class GnomeLive(unittest.TestCase):
    def test_extension_answers(self):
        b = G.GnomeBridge()
        self.assertTrue(b.available, b.unavailable_reason)
        self.assertGreaterEqual(len(b.outputs()), 1)
        self.assertIsInstance(b.windows(), list)
        x, y = b.pointer()
        self.assertIsInstance(x, float)
        out = b.outputs()[0]
        path = os.path.join(tempfile.mkdtemp(), 'g.png')
        self.assertTrue(b.screenshot_output(out.name, path))
        self.assertGreater(B.png_size(path)[0], 0)


if __name__ == '__main__':
    unittest.main()
