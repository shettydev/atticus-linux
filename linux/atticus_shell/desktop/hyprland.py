"""Hyprland backend: hyprctl's JSON IPC for windows and monitors, grim for pictures."""
from atticus_shell.desktop.bridge import BUILTIN, Output, Window, output_at
from atticus_shell.desktop.wlroots import ADDR, CliBackend, run_cli


class HyprlandBridge(CliBackend):
    def __init__(self, run=run_cli):
        super().__init__(run)

    def outputs(self):
        outs = []
        for m in self._json(['hyprctl', '-j', 'monitors']) or []:
            scale = float(m.get('scale') or 1)
            pw, ph = (m['height'], m['width']) if m.get('transform', 0) % 2 else (m['width'], m['height'])
            w, h = round(pw / scale), round(ph / scale)
            left, top, right, bottom = (m.get('reserved') or [0, 0, 0, 0])[:4]
            outs.append(Output(m['name'], m['x'], m['y'], w, h, scale, bool(BUILTIN.match(m['name'])),
                               (m['x'] + left, m['y'] + top, w - left - right, h - top - bottom)))
        return outs

    def windows(self):
        outs = self.outputs()
        rows = [c for c in self._json(['hyprctl', '-j', 'clients']) or [] if c.get('mapped', True) and not c.get('hidden')]
        rows.sort(key=lambda c: c.get('focusHistoryID', 99))   # Hyprland has no z-order query; most recently focused first
        return [Window(c['address'], (c.get('class') or c.get('initialClass') or '').lower(), c.get('pid') or 0, c.get('title') or '',
                       c['at'][0], c['at'][1], c['size'][0], c['size'][1],
                       output_at(outs, c['at'][0] + c['size'][0] / 2, c['at'][1] + c['size'][1] / 2),
                       str((c.get('workspace') or {}).get('name', '')), False, bool(c.get('fullscreen')),
                       c.get('focusHistoryID') == 0) for c in rows]

    def pointer(self):
        p = self._json(['hyprctl', '-j', 'cursorpos']) or {}
        return float(p.get('x', 0)), float(p.get('y', 0))

    def _batch(self, win_id, *cmds):
        if not ADDR.match(str(win_id)) or not any(w.id == win_id for w in self.windows()):
            return False
        r = self.run(['hyprctl', '--batch', ' ; '.join(f'dispatch {c}' for c in cmds)])
        return r.returncode == 0 and 'No such window' not in (r.stdout or '')

    def move_resize(self, win_id, x, y, w, h):   # tiled windows ignore pixel moves until they float
        a = f'address:{win_id}'
        return self._batch(win_id, f'setfloating {a}', f'resizewindowpixel exact {w} {h},{a}', f'movewindowpixel exact {x} {y},{a}')

    def set_fullscreen(self, win_id, on):
        return self._batch(win_id, f'focuswindow address:{win_id}', f'fullscreenstate {2 if on else 0} -1')

    def activate(self, win_id):
        return self._batch(win_id, f'focuswindow address:{win_id}')

    def lower(self, win_id):
        return self._batch(win_id, f'alterzorder bottom,address:{win_id}')

    def keep_above(self, win_id, on):
        a = f'address:{win_id}'
        return self._batch(win_id, f'setfloating {a}', f'alterzorder {"top" if on else "bottom"},{a}')
