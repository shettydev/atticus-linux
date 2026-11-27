import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell.desktop import bridge as B
from atticus_shell.desktop import kwin as K
from tests.linux.test_bridge_contract import BridgeContract

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / 'linux/atticus_shell/desktop/kwin-script/atticus.js'


class FakeKwinBus:
    """Answers generated scripts by reading their OP/ARGS header, the way atticus.js would on KWin 6."""

    def __init__(self, present=True, authorized=True):
        self.present, self.authorized = present, authorized
        self.wins = [{'id': '{a}', 'app_id': 'firefox', 'pid': 10, 'title': 'Firefox', 'x': 0, 'y': 0, 'width': 1280, 'height': 800,
                      'workspace': '1', 'minimized': False, 'fullscreen': False, 'focused': True},
                     {'id': '{b}', 'app_id': 'org.kde.konsole', 'pid': 11, 'title': 'Terminal', 'x': 100, 'y': 100, 'width': 600,
                      'height': 400, 'workspace': '1', 'minimized': False, 'fullscreen': False, 'focused': False}]
        self.scripts = []

    def kwin_present(self):
        return self.present

    def run_script(self, src):
        self.scripts.append(src)
        op = re.search(r'const OP = (".*?");', src).group(1)
        args = json.loads(re.search(r'const ARGS = (.*?);\n', src).group(1))
        op = json.loads(op)
        w = next((w for w in self.wins if w['id'] == args.get('id')), None)
        if op == 'outputs':
            return [{'name': 'eDP-1', 'x': 0, 'y': 0, 'width': 1280, 'height': 800, 'scale': 1.0, 'area': [0, 0, 1280, 756]},
                    {'name': 'HDMI-1', 'x': 1280, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.5, 'area': [1280, 0, 1920, 1036]}]
        if op == 'windows':
            return [dict(w) for w in self.wins]
        if op == 'pointer':
            return {'x': 5, 'y': 6}
        if not w:
            return {'ok': False}
        if op == 'move_resize':
            w.update(x=args['x'], y=args['y'], width=args['width'], height=args['height'])
        elif op == 'set_fullscreen':
            w['fullscreen'] = args['on']
        elif op == 'activate':
            for o in self.wins:
                o['focused'] = o is w
            self.wins.sort(key=lambda o: not o['focused'])
        return {'ok': True}

    def capture(self, method, target, path):
        if not self.authorized:
            raise K.NotAuthorized('org.kde.KWin.ScreenShot2.Error.NoAuthorized')
        if method == 'CaptureScreen':
            w, h = (2880, 1620) if target == 'HDMI-1' else (1280, 800)
        else:
            w, h = 600, 400
        B.write_png(path, w, h, b'\x00' * (w * h * 4))
        return True


class KwinContract(BridgeContract, unittest.TestCase):
    scale = 1.5

    def make(self):
        return K.KwinBridge(FakeKwinBus())


class KwinSpecifics(unittest.TestCase):
    def test_script_carries_op_and_json_args(self):
        src = K.script_for('move_resize', {'id': '{a}', 'x': 1, 'y': 2, 'width': 3, 'height': 4}, 'org.x', '/p')
        self.assertIn('const OP = "move_resize";', src)
        self.assertIn('const REPLY = ["org.x", "/p"];', src)

    def test_quotes_in_args_cannot_break_out(self):
        src = K.script_for('activate', {'id': '"); evil(); ("'}, 'org.x', '/p')
        args = json.loads(re.search(r'const ARGS = (.*?);\n', src).group(1))
        self.assertEqual(args['id'], '"); evil(); ("')

    def test_screenshot2_unauthorized_reports_desktop_file(self):
        b = K.KwinBridge(FakeKwinBus(authorized=False))
        self.assertFalse(b.screenshot_output('eDP-1', os.path.join(tempfile.mkdtemp(), 'x.png')))
        self.assertIn('X-KDE-DBUS-Restricted-Interfaces', b.screenshot_reason)
        self.assertTrue(b.available)    # windows still work

    def test_no_kwin_is_unavailable(self):
        b = K.KwinBridge(FakeKwinBus(present=False))
        self.assertFalse(b.available)
        self.assertEqual(b.windows(), [])

    def test_convert_bgra_with_stride(self):
        raw = b'\x01\x02\x03\x04' * 2 + b'\x00' * 8 + b'\x05\x06\x07\x08' * 2 + b'\x00' * 8   # 2x2, stride 16
        self.assertEqual(K.to_rgba(raw, 2, 2, 16, 6), b'\x03\x02\x01\xff' * 2 + b'\x07\x06\x05\xff' * 2)

    def test_convert_rgba(self):
        self.assertEqual(K.to_rgba(b'\x01\x02\x03\x04', 1, 1, 4, 17), b'\x01\x02\x03\xff')


NODE_HARNESS = r'''
const fs = require('fs');
const [kind, op, args] = [process.argv[2], process.argv[3], process.argv[4]];
const geo = (x, y, w, h) => ({x, y, width: w, height: h});
const win = (id, cls, cap, g, extra) => Object.assign({internalId: id, resourceClass: cls, desktopFileName: '', pid: 7, caption: cap,
  frameGeometry: g, minimized: false, fullScreen: false, normalWindow: true, keepAbove: false, keepBelow: false, desktops: [{id: 'd1'}]}, extra);
const a = win('{a}', 'firefox', 'Firefox', geo(0, 0, 800, 600), {desktopFileName: 'firefox'});
const b = win('{b}', 'konsole', 'Terminal', geo(10, 10, 300, 200));
const out = {}; let replied = null;
global.callDBus = (s, p, i, m, payload) => { replied = JSON.parse(payload); };
global.KWin = {MaximizeArea: 2};
const screens = [{name: 'eDP-1', geometry: geo(0, 0, 1280, 800), devicePixelRatio: 1.25}];
global.workspace = kind === '6' ? {
  windowList: () => [b, a], activeWindow: a, cursorPos: {x: 3, y: 4}, screens, currentDesktop: {id: 'd1'},
  clientArea: (k, o, d) => geo(0, 0, 1280, 760), raiseWindow: w => {},
} : {
  clientList: () => [b, a], activeClient: a, cursorPos: {x: 3, y: 4}, numScreens: 1, currentDesktop: 1,
  clientArea: (k, s, d) => geo(0, 0, 1280, 760),
};
const src = fs.readFileSync(process.argv[5], 'utf8').replace('__OP__', JSON.stringify(op)).replace('__ARGS__', args)
  .replace('__SERVICE__', '"s"').replace('__PATH__', '"/p"');
eval(src);
console.log(JSON.stringify({replied, a: {g: a.frameGeometry, fs: a.fullScreen, above: a.keepAbove}, active: (workspace.activeWindow || workspace.activeClient).internalId}));
'''


@unittest.skipUnless(shutil.which('node'), 'needs node to run the KWin script against a mock workspace')
class KwinScriptLogic(unittest.TestCase):
    def run_js(self, kind, op, args='{}'):
        harness = os.path.join(tempfile.mkdtemp(), 'h.js')
        Path(harness).write_text(NODE_HARNESS)
        r = subprocess.run(['node', harness, kind, op, args, str(JS)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_windows_top_first_kwin6(self):
        rows = self.run_js('6', 'windows')['replied']
        self.assertEqual([r['title'] for r in rows], ['Firefox', 'Terminal'])
        self.assertTrue(rows[0]['focused'])
        self.assertEqual(rows[0]['app_id'], 'firefox')

    def test_windows_kwin5_api(self):
        rows = self.run_js('5', 'windows')['replied']
        self.assertEqual([r['id'] for r in rows], ['{a}', '{b}'])

    def test_move_resize_sets_frame_geometry(self):
        r = self.run_js('6', 'move_resize', '{"id": "{a}", "x": 5, "y": 6, "width": 700, "height": 500}')
        self.assertEqual(r['replied'], {'ok': True})
        self.assertEqual(r['a']['g'], {'x': 5, 'y': 6, 'width': 700, 'height': 500})

    def test_unknown_window_is_not_ok(self):
        self.assertEqual(self.run_js('6', 'activate', '{"id": "{zz}"}')['replied'], {'ok': False})

    def test_outputs_kwin6(self):
        rows = self.run_js('6', 'outputs')['replied']
        self.assertEqual(rows, [{'name': 'eDP-1', 'x': 0, 'y': 0, 'width': 1280, 'height': 800, 'scale': 1.25, 'area': [0, 0, 1280, 760]}])

    def test_activate_and_keep_above(self):
        self.assertEqual(self.run_js('6', 'activate', '{"id": "{b}"}')['active'], '{b}')
        self.assertTrue(self.run_js('6', 'keep_above', '{"id": "{a}", "on": true}')['a']['above'])

    def test_pointer(self):
        self.assertEqual(self.run_js('5', 'pointer')['replied'], {'x': 3, 'y': 4})


if __name__ == '__main__':
    unittest.main()
