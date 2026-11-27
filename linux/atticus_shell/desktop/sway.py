"""Sway backend: swaymsg's JSON IPC for windows and outputs, grim for pictures. Sway exposes no pointer position."""
from atticus_shell.desktop.bridge import BUILTIN, Output, PointerUnavailable, Window, output_at
from atticus_shell.desktop.wlroots import CliBackend, run_cli

NO_POINTER = 'Sway does not share the pointer position; Atticus follows it with its own layer while you hold the shortcut.'


def _leaves(node, workspace=''):
    if node.get('type') == 'workspace':
        workspace = node.get('name', '')
    kids = node.get('nodes', []) + node.get('floating_nodes', [])
    if node.get('type') in ('con', 'floating_con') and not kids and (node.get('app_id') is not None or node.get('window_properties')):
        yield node, workspace
    for k in kids:
        yield from _leaves(k, workspace)


class SwayBridge(CliBackend):
    def __init__(self, run=run_cli):
        super().__init__(run)

    def outputs(self):
        areas = {ws['output']: ws['rect'] for ws in self._json(['swaymsg', '-r', '-t', 'get_workspaces']) or [] if ws.get('visible')}
        outs = []
        for o in self._json(['swaymsg', '-r', '-t', 'get_outputs']) or []:
            if not o.get('active'):
                continue
            r, a = o['rect'], areas.get(o['name'], o['rect'])
            outs.append(Output(o['name'], r['x'], r['y'], r['width'], r['height'], float(o.get('scale') or 1),
                               bool(BUILTIN.match(o['name'])), (a['x'], a['y'], a['width'], a['height'])))
        return outs

    def windows(self):
        tree = self._json(['swaymsg', '-r', '-t', 'get_tree'])
        if not tree:
            return []
        outs = self.outputs()
        rows = list(_leaves(tree))
        rows.sort(key=lambda nw: (not nw[0].get('focused'), nw[0]['type'] != 'floating_con'))   # focused, then floating, then tiled
        wins = []
        for n, ws in rows:
            r = n['rect']
            app = n.get('app_id') or (n.get('window_properties') or {}).get('class') or ''
            wins.append(Window(str(n['id']), app.lower(), n.get('pid') or 0, n.get('name') or '', r['x'], r['y'], r['width'], r['height'],
                               output_at(outs, r['x'] + r['width'] / 2, r['y'] + r['height'] / 2), ws, False,
                               bool(n.get('fullscreen_mode')), bool(n.get('focused'))))
        return wins

    def pointer(self):
        raise PointerUnavailable(NO_POINTER)

    def _cmd(self, win_id, command):
        if not str(win_id).isdigit():
            return False
        r = self.run(['swaymsg', f'[con_id={win_id}] {command}'])
        return r.returncode == 0

    def move_resize(self, win_id, x, y, w, h):
        return self._cmd(win_id, f'floating enable, resize set {w} {h}, move absolute position {x} {y}')

    def set_fullscreen(self, win_id, on):
        return self._cmd(win_id, f'fullscreen {"enable" if on else "disable"}')

    def activate(self, win_id):
        return self._cmd(win_id, 'focus')

    def lower(self, win_id):
        return False   # Sway has no z-order command

    def keep_above(self, win_id, on):
        return self._cmd(win_id, 'floating enable' if on else 'floating disable')
