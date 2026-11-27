"""In-memory stand-ins for the desktop: a Bridge, an X11 connection, a D-Bus proxy, subprocess runs."""
import copy

from atticus_shell.desktop.bridge import Output, Window, output_at, write_png


class FakeBridge:
    """A desktop with these outputs and windows (top of the stack first). Records every action it receives."""
    available = True
    unavailable_reason = ''
    covers_hidden = True

    def __init__(self, outputs=(), windows=(), pointer=(0.0, 0.0)):
        self._outputs = list(outputs)
        self._windows = [copy.copy(w) for w in windows]
        self._pointer = pointer
        self.calls = []

    def outputs(self):
        return list(self._outputs)

    def windows(self):
        return [copy.copy(w) for w in self._windows]

    def focused(self):
        return next((copy.copy(w) for w in self._windows if w.focused), None)

    def pointer(self):
        return self._pointer

    def _win(self, win_id):
        return next((w for w in self._windows if w.id == win_id), None)

    def move_resize(self, win_id, x, y, w, h):
        self.calls.append(('move_resize', win_id, x, y, w, h))
        win = self._win(win_id)
        if not win:
            return False
        win.x, win.y, win.width, win.height = x, y, w, h
        win.output = output_at(self._outputs, x + w / 2, y + h / 2)
        return True

    def set_fullscreen(self, win_id, on):
        self.calls.append(('set_fullscreen', win_id, on))
        win = self._win(win_id)
        if win:
            win.fullscreen = on
        return bool(win)

    def activate(self, win_id):
        self.calls.append(('activate', win_id))
        if not self._win(win_id):
            return False
        for w in self._windows:
            w.focused = w.id == win_id
        self._windows.sort(key=lambda w: not w.focused)
        return True

    def lower(self, win_id):
        self.calls.append(('lower', win_id))
        win = self._win(win_id)
        if win:
            self._windows.remove(win)
            self._windows.append(win)
        return bool(win)

    def keep_above(self, win_id, on):
        self.calls.append(('keep_above', win_id, on))
        return bool(self._win(win_id))

    def screenshot_output(self, name, path):
        o = next((o for o in self._outputs if o.name == name), None)
        if not o:
            return False
        w, h = round(o.width * o.scale), round(o.height * o.scale)
        write_png(path, w, h, b'\x10\x20\x30\xff' * (w * h))
        return True

    def screenshot_window(self, win_id, path):
        win = self._win(win_id)
        if not win:
            return False
        write_png(path, win.width, win.height, b'\x01\x02\x03\xff' * (win.width * win.height))
        return True


class FakeX11Conn:
    """The subset of X11 that X11Bridge uses, over plain dicts. windows: dicts with xid, title, wm_class, pid, x, y, w, h."""

    def __init__(self, outputs, windows, active=0, pointer=(0, 0), workarea=None, composite=True):
        self._outputs = outputs            # [(name, x, y, w, h)]
        self._windows = {w['xid']: dict({'desktop': 0, 'state': set()}, **w) for w in windows}
        self.order = [w['xid'] for w in windows][::-1]   # X stacking: bottom first
        self.active = active
        self._pointer = pointer
        self._workarea = workarea
        self.composite = composite
        self.messages = []

    def outputs(self):
        return list(self._outputs)

    def workarea(self):
        return self._workarea

    def stacking(self):
        return list(self.order)

    def active_window(self):
        return self.active

    def info(self, xid):
        w = self._windows[xid]
        return {'title': w['title'], 'wm_class': w['wm_class'], 'pid': w['pid'], 'x': w['x'], 'y': w['y'],
                'width': w['w'], 'height': w['h'], 'desktop': w['desktop'], 'state': set(w['state'])}

    def pointer(self):
        return self._pointer

    def client_message(self, xid, kind, data):
        self.messages.append((xid, kind, list(data)))
        w = self._windows.get(xid)
        if kind == '_NET_MOVERESIZE_WINDOW' and w:
            w['x'], w['y'], w['w'], w['h'] = data[1:5]
        elif kind == '_NET_WM_STATE' and w:
            atom = data[1]
            (w['state'].add if data[0] == 1 else w['state'].discard)(atom)
        elif kind == '_NET_ACTIVE_WINDOW' and w:
            self.active = xid
            self.order.remove(xid)
            self.order.append(xid)

    def lower(self, xid):
        self.messages.append((xid, 'lower', []))
        self.order.remove(xid)
        self.order.insert(0, xid)

    def capture(self, xid, x, y, w, h):
        """BGRA pixels of the root (xid 0) or one window (needs composite for covered windows)."""
        if xid and not self.composite:
            return None
        return b'\x30\x20\x10\x00' * (w * h)


__all__ = ['FakeBridge', 'FakeX11Conn', 'Output', 'Window']
