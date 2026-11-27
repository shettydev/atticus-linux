"""X11 backend: EWMH for windows, RandR for outputs, XComposite for covered-window pictures. Works under any EWMH WM."""
from atticus_shell.desktop.bridge import BUILTIN, Output, Window, bgra_to_rgba, output_at, write_png

FULL = '_NET_WM_STATE_FULLSCREEN'
HIDDEN = '_NET_WM_STATE_HIDDEN'
MAX_H, MAX_V, ABOVE = '_NET_WM_STATE_MAXIMIZED_HORZ', '_NET_WM_STATE_MAXIMIZED_VERT', '_NET_WM_STATE_ABOVE'
ADD, REMOVE = 1, 0
SOURCE_PAGER = 2   # EWMH source indication: a tool acting for the user, so WMs honor it


class X11Bridge:
    available = True
    unavailable_reason = ''

    def __init__(self, conn=None):
        self.conn = conn or XlibConn()

    @property
    def covers_hidden(self):
        return bool(self.conn.composite)

    def outputs(self):
        work = self.conn.workarea()
        outs = []
        for name, x, y, w, h in self.conn.outputs():
            area = (x, y, w, h)
            if work:
                wx, wy, ww, wh = work
                left, top = max(x, wx), max(y, wy)
                right, bottom = min(x + w, wx + ww), min(y + h, wy + wh)
                if right > left and bottom > top:
                    area = (left, top, right - left, bottom - top)
            outs.append(Output(name, x, y, w, h, 1.0, bool(BUILTIN.match(name)), area))
        return outs

    def windows(self):
        outs, active = self.outputs(), self.conn.active_window()
        wins = []
        for xid in reversed(self.conn.stacking()):
            i = self.conn.info(xid)
            if i is None:
                continue
            cls = i['wm_class'] or ('', '')
            wins.append(Window(hex(xid), (cls[1] or cls[0]).lower(), i['pid'] or 0, i['title'] or '', i['x'], i['y'], i['width'], i['height'],
                               output_at(outs, i['x'] + i['width'] / 2, i['y'] + i['height'] / 2), str(i['desktop']),
                               HIDDEN in i['state'], FULL in i['state'], xid == active))
        return wins

    def focused(self):
        return next((w for w in self.windows() if w.focused), None)

    def pointer(self):
        x, y = self.conn.pointer()
        return float(x), float(y)

    def _xid(self, win_id):
        try:
            xid = int(win_id, 16)
        except (TypeError, ValueError):
            return None
        return xid if xid in self.conn.stacking() else None

    def move_resize(self, win_id, x, y, w, h):
        xid = self._xid(win_id)
        if xid is None:
            return False
        self.conn.client_message(xid, '_NET_WM_STATE', [REMOVE, MAX_H, MAX_V, SOURCE_PAGER, 0])   # a maximized window ignores moves
        flags = 1 | (0b1111 << 8) | (SOURCE_PAGER << 12)   # NorthWest gravity, x/y/w/h present
        self.conn.client_message(xid, '_NET_MOVERESIZE_WINDOW', [flags, x, y, w, h])
        return True

    def set_fullscreen(self, win_id, on):
        xid = self._xid(win_id)
        if xid is None:
            return False
        self.conn.client_message(xid, '_NET_WM_STATE', [ADD if on else REMOVE, FULL, 0, SOURCE_PAGER, 0])
        return True

    def activate(self, win_id):
        xid = self._xid(win_id)
        if xid is None:
            return False
        self.conn.client_message(xid, '_NET_ACTIVE_WINDOW', [SOURCE_PAGER, 0, 0, 0, 0])
        return True

    def lower(self, win_id):
        xid = self._xid(win_id)
        if xid is None:
            return False
        self.conn.lower(xid)
        return True

    def keep_above(self, win_id, on):
        xid = self._xid(win_id)
        if xid is None:
            return False
        self.conn.client_message(xid, '_NET_WM_STATE', [ADD if on else REMOVE, ABOVE, 0, SOURCE_PAGER, 0])
        return True

    def screenshot_output(self, name, path):
        o = next((o for o in self.outputs() if o.name == name), None)
        if not o:
            return False
        data = self.conn.capture(0, o.x, o.y, o.width, o.height)
        if data is None:
            return False
        write_png(path, o.width, o.height, bgra_to_rgba(data))
        return True

    def screenshot_window(self, win_id, path):
        xid = self._xid(win_id)
        if xid is None:
            return False
        i = self.conn.info(xid)
        data = self.conn.capture(xid, 0, 0, i['width'], i['height'])
        if data is None:
            return False
        write_png(path, i['width'], i['height'], bgra_to_rgba(data))
        return True


class XlibConn:
    """The real X server through python-xlib."""

    def __init__(self, name=None):
        from Xlib import X, display
        from Xlib.protocol import event
        self.X, self.event = X, event
        self.d = display.Display(name)
        self.root = self.d.screen().root
        self.composite = False
        if self.d.has_extension('Composite'):
            from Xlib.ext import composite
            composite.redirect_subwindows(self.root, composite.RedirectAutomatic)
            self.composite = True

    def _atom(self, name):
        return self.d.intern_atom(name)

    def _prop(self, win, name):
        p = win.get_full_property(self._atom(name), self.X.AnyPropertyType)
        return p.value if p else None

    def _win(self, xid):
        return self.d.create_resource_object('window', xid)

    def outputs(self):
        from Xlib.ext import randr
        mons = randr.get_monitors(self.root, True).monitors
        return [(self.d.get_atom_name(m.name), m.x, m.y, m.width_in_pixels, m.height_in_pixels) for m in mons]

    def workarea(self):
        v = self._prop(self.root, '_NET_WORKAREA')
        return tuple(v[:4]) if v is not None and len(v) >= 4 else None

    def stacking(self):
        v = self._prop(self.root, '_NET_CLIENT_LIST_STACKING')
        if v is None:
            v = self._prop(self.root, '_NET_CLIENT_LIST')
        return list(v) if v is not None else []

    def active_window(self):
        v = self._prop(self.root, '_NET_ACTIVE_WINDOW')
        return v[0] if v is not None and len(v) else 0

    def info(self, xid):
        from Xlib import error
        try:
            w = self._win(xid)
            g = w.get_geometry()
            pos = w.translate_coords(self.root, 0, 0)
            name = self._prop(w, '_NET_WM_NAME')
            title = name.decode('utf-8', 'replace') if isinstance(name, bytes) else (w.get_wm_name() or '')
            pid = self._prop(w, '_NET_WM_PID')
            desk = self._prop(w, '_NET_WM_DESKTOP')
            state = self._prop(w, '_NET_WM_STATE')
            return {'title': title, 'wm_class': w.get_wm_class(), 'pid': int(pid[0]) if pid is not None and len(pid) else 0,
                    'x': -pos.x, 'y': -pos.y, 'width': g.width, 'height': g.height,
                    'desktop': int(desk[0]) if desk is not None and len(desk) else 0,
                    'state': {self.d.get_atom_name(a) for a in (state if state is not None else [])}}
        except error.XError:
            return None

    def pointer(self):
        q = self.root.query_pointer()
        return q.root_x, q.root_y

    def client_message(self, xid, kind, data):
        values = [self._atom(v) if isinstance(v, str) else v for v in data]
        ev = self.event.ClientMessage(window=self._win(xid), client_type=self._atom(kind), data=(32, values))
        self.root.send_event(ev, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)
        self.d.flush()

    def lower(self, xid):
        self._win(xid).configure(stack_mode=self.X.Below)
        self.d.flush()

    def capture(self, xid, x, y, w, h):
        from Xlib import error
        try:
            if xid:
                if not self.composite:
                    return None
                from Xlib.ext import composite
                pixmap = composite.name_window_pixmap(self._win(xid))
                img = pixmap.get_image(x, y, w, h, self.X.ZPixmap, 0xffffffff)
                pixmap.free()
            else:
                img = self.root.get_image(x, y, w, h, self.X.ZPixmap, 0xffffffff)
            return img.data
        except error.XError:
            return None
