"""Pictures of the screen for the brain (--look, --step-screen, --draw-target) and screen-share detection.

--look <screen 1..n> <out.jpg> [windowID] [mark JSON] (Atticus.swift:6344): one display, Atticus's own windows hidden
for the frame, 1280 px wide, JPEG 70. A windowID paints that window's own picture where it sits (covered or not) and
prints "window x y w h", so picture spots stay where clicks land; marks print "pointed". Exit 0 when the file exists.
"""
import json
import math
import os
import tempfile
import time

from atticus_shell.desktop import ids
from atticus_shell.desktop.bridge import output_at

WIDTH = 1280
YELLOW = (1, 214 / 255, 10 / 255)


def picture_size(out):
    return WIDTH, round(WIDTH * out.height / out.width)


def picture_rect(rect, out, pw, ph):
    x, y, w, h = rect
    sx, sy = pw / out.width, ph / out.height
    return round((x - out.x) * sx), round((y - out.y) * sy), round(w * sx), round(h * sy)


def desktop_crop(out, outs, img_w, img_h):
    """Where one output sits in a picture of the whole layout (the portal's screenshot)."""
    left, top = min(o.x for o in outs), min(o.y for o in outs)
    right, bottom = max(o.x + o.width for o in outs), max(o.y + o.height for o in outs)
    sx, sy = img_w / (right - left), img_h / (bottom - top)
    return round((out.x - left) * sx), round((out.y - top) * sy), round(out.width * sx), round(out.height * sy)


def sharing_active(pw_dump):
    """True while a portal screen cast runs (a meeting app sharing the screen): Atticus's box then stays out of it."""
    try:
        nodes = json.loads(pw_dump)
    except ValueError:
        return False
    for n in nodes if isinstance(nodes, list) else []:
        props = ((n.get('info') or {}).get('props') or {}) if isinstance(n, dict) else {}
        owner = ' '.join(str(props.get(k, '')) for k in ('application.name', 'node.name', 'application.process.binary'))
        if props.get('media.class') == 'Video/Source' and ('portal' in owner or 'mutter' in owner or 'kwin' in owner.lower()):
            return True
    return False


def is_atticus(w):
    return w.app_id == 'atticus'


def screens_rows(bridge, visible=False):
    """The brain's screens(): [name, x, y, w, h, built_in] in layout coordinates."""
    rows = []
    for o in bridge.outputs():
        x, y, w, h = o.work_area if visible else (o.x, o.y, o.width, o.height)
        rows.append([o.name, x, y, w, h, o.builtin])
    return rows


def window_target(w):
    return {'pid': w.pid, 'windowID': ids.public_id(w.id), 'windowFrame': [w.x, w.y, w.width, w.height],
            'window': w.title or w.app_id or 'Window', 'app': w.app_id or 'App', 'minimized': w.minimized}


def draw_target(bridge, screen=''):
    """The user's front window on the ask's screen (Atticus's own windows never count), as --draw-target prints it."""
    outs = bridge.outputs()
    wins = [w for w in bridge.windows() if not is_atticus(w) and not w.minimized]
    on = [w for w in wins if not screen or output_at(outs, w.x + w.width / 2, w.y + w.height / 2) == screen]
    pick = next((w for w in on if w.focused), on[0] if on else None)
    return window_target(pick) if pick else None


def hide_overlays():
    from atticus_shell import ipc
    try:
        if ipc.call('HideOverlays', (180,)) is not None:
            time.sleep(0.08)   # one composited frame without the box, its trail or drawings
    except Exception:
        pass


# --- pixels (pycairo + GdkPixbuf: no Pillow) ----------------------------------------------------------------------------
def _cairo():
    import cairo
    return cairo


def _capture(bridge, out, hide):
    """The output as a cairo surface in physical pixels, or None."""
    cairo = _cairo()
    hide()
    fd, path = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    try:
        if not bridge.screenshot_output(out.name, path):
            return None
        img = cairo.ImageSurface.create_from_png(path)
        if bridge.__class__.__name__ == 'PortalBridge':
            outs = bridge.outputs()
            if outs:
                x, y, w, h = desktop_crop(out, outs, img.get_width(), img.get_height())
                crop = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
                c = cairo.Context(crop)
                c.set_source_surface(img, -x, -y)
                c.paint()
                img = crop
        return img
    finally:
        os.unlink(path)


def _scaled(img, w, h, fmt=None):
    cairo = _cairo()
    out = cairo.ImageSurface(fmt if fmt is not None else cairo.FORMAT_RGB24, w, h)
    c = cairo.Context(out)
    c.scale(w / img.get_width(), h / img.get_height())
    c.set_source_surface(img, 0, 0)
    c.get_source().set_filter(cairo.FILTER_GOOD)
    c.paint()
    return out


def _valid_mark(m):
    def finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    try:
        return (m['kind'] in ('circle', 'line', 'area', 'point') and finite(m['scale']) and 0 < m['scale'] < 100
                and len(m['path']) <= 2400 and len(m['spots']) <= 5
                and all(len(p) == 2 and all(map(finite, p)) for p in m['path'])
                and all(len(s) >= 3 and 1 <= s[0] <= 5 and all(map(finite, s)) for s in m['spots'])
                and (m['box'] == [] or (len(m['box']) == 4 and all(map(finite, m['box'])))))
    except (KeyError, TypeError):
        return False


def _draw_marks(surface, m):
    """Plip's gesture annotation (via Atticus.swift): soft yellow strokes, area and spot rings with a dark halo."""
    cairo = _cairo()
    c = cairo.Context(surface)
    scale, width = m['scale'], max(2, round(3 * m['scale']))
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.set_line_cap(cairo.LINE_CAP_ROUND)

    def stroke(alpha, w, color=YELLOW):
        c.set_source_rgba(*color, alpha)
        c.set_line_width(w)
    if m['kind'] in ('circle', 'line') and m['path']:
        for halo in (True, False):
            c.move_to(*m['path'][0])
            for p in m['path'][1:]:
                c.line_to(*p)
            if m['kind'] == 'circle':
                c.close_path()
            stroke(80 / 255, width + 2, (0, 0, 0)) if halo else stroke(200 / 255, width)
            c.stroke()
    if m['kind'] == 'area' and len(m['box']) == 4:
        x0, y0, x1, y1 = m['box']
        r = 10 * scale
        c.new_sub_path()
        c.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
        c.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
        c.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
        c.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
        c.close_path()
        c.set_source_rgba(*YELLOW, 28 / 255)
        c.fill_preserve()
        stroke(190 / 255, width)
        c.stroke()
    for spot in m['spots']:
        r = max(10, 16 * scale)
        c.arc(spot[1], spot[2], r + 1, 0, 2 * math.pi)
        stroke(80 / 255, width + 2, (0, 0, 0))
        c.stroke()
        c.arc(spot[1], spot[2], r, 0, 2 * math.pi)
        stroke(235 / 255, width)
        c.stroke()
        if m.get('numbered'):
            c.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            c.set_font_size(max(10, 12 * scale))
            c.move_to(spot[1] + r * 0.75, spot[2] - r * 0.75)
            c.set_source_rgb(0, 0, 0)
            c.show_text(str(int(spot[0])))


def _save_jpeg(surface, path):
    import gi
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, surface.get_width(), surface.get_height())
    pixbuf.savev(path, 'jpeg', ['quality'], ['70'])


def look(args, bridge, hide=hide_overlays):
    if len(args) < 2:
        return 2
    outs = bridge.outputs()
    if not outs:
        return 1
    try:
        index = int(args[0])
    except ValueError:
        index = 1
    out = outs[max(0, min(index - 1, len(outs) - 1))]
    path, window_id = args[1], (args[2] if len(args) > 2 else '')
    img = _capture(bridge, out, hide)
    if img is None:
        return 1
    pw, ph = picture_size(out)
    pic = _scaled(img, pw, ph)
    if window_id:
        win = next((w for w in bridge.windows() if ids.public_id(w.id) == window_id), None)
        if win and bridge.covers_hidden:
            fd, wpath = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            try:
                if bridge.screenshot_window(win.id, wpath):
                    cairo = _cairo()
                    shot = cairo.ImageSurface.create_from_png(wpath)
                    x, y, w, h = picture_rect((win.x, win.y, win.width, win.height), out, pw, ph)
                    c = cairo.Context(pic)
                    c.translate(x, y)
                    c.scale(w / shot.get_width(), h / shot.get_height())
                    c.set_source_surface(shot, 0, 0)
                    c.paint()
                    print(f'window {win.x} {win.y} {win.width} {win.height}')
            finally:
                os.unlink(wpath)
    if len(args) > 3:
        try:
            mark = json.loads(args[3])
        except ValueError:
            mark = None
        if isinstance(mark, dict) and _valid_mark(mark):
            _draw_marks(pic, mark)
            print('pointed')
    _save_jpeg(pic, path)
    return 0 if os.path.exists(path) else 1


def step_screen(bridge, hide=hide_overlays):
    """64x40 grayscale of every output: whether a guided step changed the screen, no image kept."""
    pixels = []
    for out in bridge.outputs():
        img = _capture(bridge, out, hide)
        if img is None:
            return []
        small = _scaled(img, 64, 40)
        small.flush()
        data, stride = bytes(small.get_data()), small.get_stride()
        for y in range(40):
            row = data[y * stride:y * stride + 64 * 4]
            pixels += [round(0.114 * row[i] + 0.587 * row[i + 1] + 0.299 * row[i + 2]) for i in range(0, 256, 4)]
    return pixels
