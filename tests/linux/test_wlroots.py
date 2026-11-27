import json
import re
import shlex
import subprocess
import unittest

from tests import linux  # noqa: F401
from atticus_shell.desktop import bridge as B
from atticus_shell.desktop.hyprland import HyprlandBridge
from atticus_shell.desktop.sway import SwayBridge
from tests.linux.test_bridge_contract import BridgeContract


def done(args, out='', code=0):
    return subprocess.CompletedProcess(args, code, out, '')


def grim(args):
    path = args[-1]
    if '-o' in args:
        w, h = (2880, 1620) if args[args.index('-o') + 1] == 'HDMI-1' else (1280, 800)
    else:
        w, h = map(int, args[args.index('-g') + 1].split()[1].split('x'))
    B.write_png(path, w, h, b'\x00' * (w * h * 4))
    return done(args)


class FakeHyprland:
    """hyprctl -j output shapes (Hyprland 0.4x): clients, monitors, cursorpos; dispatches applied to state."""

    def __init__(self):
        self.clients = [
            {'address': '0x1', 'mapped': True, 'hidden': False, 'at': [0, 0], 'size': [1280, 800], 'workspace': {'id': 1, 'name': '1'},
             'floating': False, 'fullscreen': 0, 'monitor': 0, 'class': 'firefox', 'title': 'Firefox', 'pid': 10, 'focusHistoryID': 0},
            {'address': '0x2', 'mapped': True, 'hidden': False, 'at': [100, 100], 'size': [600, 400], 'workspace': {'id': 1, 'name': '1'},
             'floating': True, 'fullscreen': 0, 'monitor': 0, 'class': 'kitty', 'title': 'Terminal', 'pid': 11, 'focusHistoryID': 1}]
        self.monitors = [
            {'id': 0, 'name': 'eDP-1', 'width': 1280, 'height': 800, 'x': 0, 'y': 0, 'scale': 1.0, 'transform': 0, 'focused': True, 'reserved': [0, 30, 0, 0]},
            {'id': 1, 'name': 'HDMI-1', 'width': 2880, 'height': 1620, 'x': 1280, 'y': 0, 'scale': 1.5, 'transform': 0, 'focused': False, 'reserved': [0, 0, 0, 0]}]
        self.log = []

    def by(self, addr):
        return next((c for c in self.clients if c['address'] == addr), None)

    def __call__(self, args, **kw):
        if args[0] == 'grim':
            return grim(args)
        if args[:2] == ['hyprctl', '-j']:
            what = args[2]
            if what == 'clients':
                return done(args, json.dumps(self.clients))
            if what == 'monitors':
                return done(args, json.dumps(self.monitors))
            if what == 'cursorpos':
                return done(args, json.dumps({'x': 640, 'y': 400}))
        if args[:2] == ['hyprctl', '--batch']:
            outs = []
            for cmd in [c.strip() for c in args[2].split(';') if c.strip()]:
                outs.append(self.dispatch(shlex.split(cmd)))
            return done(args, ''.join(outs))
        return done(args, 'unknown request', 1)

    def dispatch(self, words):
        self.log.append(words)
        assert words[0] == 'dispatch'
        verb, rest = words[1], ' '.join(words[2:])
        addr = re.search(r'address:(0x[0-9a-f]+)', rest)
        c = self.by(addr.group(1)) if addr else next(o for o in self.clients if o['focusHistoryID'] == 0)   # no address: the focused window
        if c is None:
            return 'No such window found'
        if verb == 'setfloating':
            c['floating'] = True
        elif verb == 'movewindowpixel':
            x, y = map(int, re.match(r'exact (-?\d+) (-?\d+),', rest).groups())
            c['at'] = [x, y]
        elif verb == 'resizewindowpixel':
            w, h = map(int, re.match(r'exact (\d+) (\d+),', rest).groups())
            c['size'] = [w, h]
        elif verb == 'focuswindow':
            for o in self.clients:
                o['focusHistoryID'] += 1
            c['focusHistoryID'] = 0
        elif verb == 'fullscreenstate':
            c['fullscreen'] = int(rest.split()[0])
        elif verb == 'alterzorder':
            pass
        return 'ok'


class HyprlandContract(BridgeContract, unittest.TestCase):
    scale = 1.5

    def make(self):
        self.fake = FakeHyprland()
        return HyprlandBridge(run=self.fake)


class HyprlandSpecifics(unittest.TestCase):
    def test_logical_monitor_size_and_reserved_work_area(self):
        outs = {o.name: o for o in HyprlandBridge(run=FakeHyprland()).outputs()}
        self.assertEqual((outs['HDMI-1'].width, outs['HDMI-1'].height), (1920, 1080))
        self.assertEqual(outs['eDP-1'].work_area, (0, 30, 1280, 770))

    def test_tiled_window_floats_before_moving(self):
        fake = FakeHyprland()
        HyprlandBridge(run=fake).move_resize('0x1', 10, 20, 300, 200)
        self.assertEqual(fake.log[0][1], 'setfloating')

    def test_window_picture_only_visible_part(self):
        self.assertFalse(HyprlandBridge(run=FakeHyprland()).covers_hidden)


def sway_node(nid, name, app_id, rect, focused=False, floating=False, pid=0):
    return {'id': nid, 'type': 'floating_con' if floating else 'con', 'name': name, 'app_id': app_id, 'pid': pid, 'rect': rect,
            'focused': focused, 'fullscreen_mode': 0, 'visible': True, 'nodes': [], 'floating_nodes': []}


class FakeSway:
    """swaymsg -r -t get_tree / get_outputs / get_workspaces shapes; commands applied to state."""

    def __init__(self):
        self.wins = [sway_node(11, 'Firefox', 'firefox', {'x': 0, 'y': 0, 'width': 1280, 'height': 800}, focused=True, pid=10),
                     sway_node(12, 'Terminal', None, {'x': 100, 'y': 100, 'width': 600, 'height': 400}, floating=True, pid=11)]
        self.wins[1]['window_properties'] = {'class': 'XTerm', 'title': 'Terminal'}
        self.log = []

    def tree(self):
        ws = {'id': 3, 'type': 'workspace', 'name': '1', 'rect': {'x': 0, 'y': 30, 'width': 1280, 'height': 770},
              'nodes': [w for w in self.wins if w['type'] == 'con'], 'floating_nodes': [w for w in self.wins if w['type'] == 'floating_con']}
        out = {'id': 2, 'type': 'output', 'name': 'eDP-1', 'rect': {'x': 0, 'y': 0, 'width': 1280, 'height': 800}, 'nodes': [ws], 'floating_nodes': []}
        return {'id': 1, 'type': 'root', 'name': 'root', 'rect': {}, 'nodes': [out], 'floating_nodes': []}

    def __call__(self, args, **kw):
        if args[0] == 'grim':
            return grim(args)
        assert args[0] == 'swaymsg'
        if '-t' in args:
            what = args[args.index('-t') + 1]
            if what == 'get_tree':
                return done(args, json.dumps(self.tree()))
            if what == 'get_outputs':
                return done(args, json.dumps([
                    {'name': 'eDP-1', 'active': True, 'rect': {'x': 0, 'y': 0, 'width': 1280, 'height': 800}, 'scale': 1.0},
                    {'name': 'HDMI-1', 'active': True, 'rect': {'x': 1280, 'y': 0, 'width': 1920, 'height': 1080}, 'scale': 1.5},
                    {'name': 'DP-9', 'active': False, 'rect': {'x': 0, 'y': 0, 'width': 0, 'height': 0}, 'scale': 1.0}]))
            if what == 'get_workspaces':
                return done(args, json.dumps([{'name': '1', 'output': 'eDP-1', 'visible': True, 'rect': {'x': 0, 'y': 30, 'width': 1280, 'height': 770}},
                                              {'name': '2', 'output': 'HDMI-1', 'visible': True, 'rect': {'x': 1280, 'y': 0, 'width': 1920, 'height': 1080}}]))
        cmd = args[-1]
        self.log.append(cmd)
        m = re.match(r'\[con_id=(\d+)\] (.*)', cmd)
        w = next((w for w in self.wins if m and w['id'] == int(m.group(1))), None)
        if not w:
            return done(args, json.dumps([{'success': False, 'error': 'No matching node'}]), 2)
        for part in [p.strip() for p in m.group(2).split(',')]:
            if part == 'floating enable':
                w['type'] = 'floating_con'
            elif part.startswith('move absolute position'):
                x, y = map(int, part.split()[3:5])
                w['rect'].update(x=x, y=y)
            elif part.startswith('resize set'):
                ww, hh = map(int, part.split()[2:4])
                w['rect'].update(width=ww, height=hh)
            elif part == 'focus':
                for o in self.wins:
                    o['focused'] = o is w
            elif part.startswith('fullscreen'):
                w['fullscreen_mode'] = 1 if part.endswith('enable') else 0
        return done(args, json.dumps([{'success': True}]))


class SwayContract(BridgeContract, unittest.TestCase):
    scale = 1.5

    def make(self):
        self.fake = FakeSway()
        return SwayBridge(run=self.fake)


class SwaySpecifics(unittest.TestCase):
    def test_sway_pointer_unavailable(self):
        with self.assertRaises(B.PointerUnavailable):
            SwayBridge(run=FakeSway()).pointer()

    def test_inactive_outputs_skipped_and_work_area_from_workspace(self):
        outs = {o.name: o for o in SwayBridge(run=FakeSway()).outputs()}
        self.assertNotIn('DP-9', outs)
        self.assertEqual(outs['eDP-1'].work_area, (0, 30, 1280, 770))

    def test_xwayland_app_id_from_class(self):
        self.assertEqual(SwayBridge(run=FakeSway()).windows()[1].app_id, 'xterm')

    def test_lower_unsupported(self):
        self.assertFalse(SwayBridge(run=FakeSway()).lower('11'))

    def test_con_id_must_be_numeric(self):
        fake = FakeSway()
        self.assertFalse(SwayBridge(run=fake).activate('11] exec rm -rf ~ ['))
        self.assertEqual(fake.log, [])


if __name__ == '__main__':
    unittest.main()
