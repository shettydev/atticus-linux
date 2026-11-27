"""Pointing while you talk (the trail) and "Point at your screen" (a crosshair layer), ported from TalkPen/PointerView.

Samples are [t, x, y] in layout points, t from the press (the brain's read_gesture reads them). Bridges that can't
read the pointer (Sway) get a full-screen capture surface while the shortcut is held, fed through motion().
"""
import base64
import os
import re
import tempfile
import time
import uuid

from atticus_shell.desktop.bridge import PointerUnavailable

MAX_SAMPLES = 2400        # 40 s at 60 Hz (atticus.py MAX_SAMPLES)
STILL = 18.0              # within this radius smoothing can't reach Plip's 40-point movement threshold
POINTING = re.compile(r'\b(this|that|these|those|here)\b', re.I)


class PointerSampler:
    def __init__(self, read, now=time.monotonic):
        self.read, self.now = read, now
        self.samples, self.t0, self.needs_capture, self.running = [], 0.0, False, False

    def start(self):
        self.samples, self.t0, self.running, self.needs_capture = [], self.now(), True, False

    def _add(self, x, y):
        if self.running and len(self.samples) < MAX_SAMPLES:
            self.samples.append([round(self.now() - self.t0, 4), float(x), float(y)])

    def tick(self):
        if not self.running or self.needs_capture:
            return
        try:
            x, y = self.read()
        except PointerUnavailable:
            self.needs_capture = True
            return
        self._add(x, y)

    def motion(self, x, y):
        self._add(x, y)

    def stop(self):
        self.running = False
        return list(self.samples)


def settle(samples, text, classify):
    """Swift's settleTalkPointer: a pointer that never moved counts only when the words point ("this", "here")."""
    if not samples:
        return [], text
    x0, y0 = samples[0][1], samples[0][2]
    if all(((s[1] - x0) ** 2 + (s[2] - y0) ** 2) ** 0.5 <= STILL for s in samples) and not POINTING.search(text):
        return [], text
    result = classify(samples, text)
    if not isinstance(result, dict) or result.get('meaningful') is not True:
        return [], text
    if not text.strip():
        text = result.get('silent') or ''
    return samples, text


def brain_classify(samples, text):
    """atticus.py --gesture: {"samples", "text"} in, {"meaningful", "silent"} out."""
    import json
    import subprocess
    from atticus_shell import box
    try:
        r = subprocess.run(box.py_command(['--gesture']), input=json.dumps({'samples': samples, 'text': text}),
                           capture_output=True, text=True, timeout=5)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def point_region(points, outputs):
    if not points:
        return None
    first = points[0]
    out = next((o for o in outputs if o.contains(*first)), None)
    if out is None:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = min(xs) - 24, min(ys) - 24
    x1, y1 = min(xs) + max(1, max(xs) - min(xs)) + 24, min(ys) + max(1, max(ys) - min(ys)) + 24
    x0, y0, x1, y1 = max(x0, out.x), max(y0, out.y), min(x1, out.x + out.width), min(y1, out.y + out.height)
    return (round(x0), round(y0), round(x1 - x0), round(y1 - y0)) if x1 > x0 and y1 > y0 else None


def point_context(points, outputs, capture, element, mark=''):
    """The ask's "point" context (Swift finishPoint): bounds, path, the control there, a crop of the spot."""
    region = point_region(points, outputs)
    if region is None:
        return None
    path = os.path.join(tempfile.gettempdir(), f'atticus-point-{uuid.uuid4()}.jpg')
    captured = capture(region, path)
    x, y, w, h = region
    ctx = {'bounds': list(region), 'path': [[p[0], p[1]] for p in points], 'element': element((x + w / 2, y + h / 2))}
    if mark:
        ctx['mark'] = mark[:1000]
    if captured:
        ctx['image'] = path
    if 'image' not in ctx and not mark and not ctx['element']:
        return None
    return ctx


def thumb(path):
    """The box chip's picture: a data URL (the page can't read temporary files), at most 480x300."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        return ''
    scale = min(1, 480 / pb.get_width(), 300 / pb.get_height())
    small = pb.scale_simple(max(1, int(pb.get_width() * scale)), max(1, int(pb.get_height() * scale)), GdkPixbuf.InterpType.BILINEAR)
    ok, data = small.save_to_bufferv('jpeg', ['quality'], ['70'])
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode() if ok else ''


def capture_region(bridge, region, path):
    from atticus_shell import screen
    out = next((o for o in bridge.outputs() if o.contains(region[0], region[1])), None)
    if out is None:
        return False
    img = screen._capture(bridge, out, screen.hide_overlays)
    if img is None:
        return False
    import cairo
    sx, sy = img.get_width() / out.width, img.get_height() / out.height
    x, y, w, h = region
    crop = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, round(w * sx)), max(1, round(h * sy)))
    c = cairo.Context(crop)
    c.set_source_surface(img, -(x - out.x) * sx, -(y - out.y) * sy)
    c.paint()
    screen._save_jpeg(crop, path)
    return os.path.exists(path)


def element_at(bridge, point, apps):
    """'<App> <role>: <label>' for the control under a point (masked), or ''."""
    from atticus_shell import a11y
    wins = [w for w in bridge.windows() if w.app_id != 'atticus' and w.x <= point[0] < w.x + w.width and w.y <= point[1] < w.y + w.height]
    if not wins:
        return ''
    app = next((a for a in apps if a.pid == wins[0].pid), None)
    if app is None:
        return ''
    hit = [n for n in a11y.capture_app(app).nodes if n.frame[0] <= point[0] < n.frame[0] + n.frame[2] and n.frame[1] <= point[1] < n.frame[1] + n.frame[3]]
    if not hit:
        return ''
    n = min(hit, key=lambda n: n.frame[2] * n.frame[3])
    return f'{app.name} {n.role}' + (f': {n.label}' if n.label else '')[:500]


# --- GTK overlays ------------------------------------------------------------------------------------------------------
class TrailOverlay:
    """The light trail that follows the cursor while you hold the shortcut. Never takes a click."""

    def __init__(self, output, env, capture=False, on_motion=None):
        from atticus_shell import windows
        self.output, self.points = output, []
        self.window = windows.overlay('pen', output, focusable=False, env=env)
        self.window.connect('draw', self._draw)
        self.window.connect('realize', lambda w: windows.set_input_region(w, [(0, 0, output.width, output.height)] if capture else []))
        self.window.connect('map', lambda w: windows.set_input_region(w, [(0, 0, output.width, output.height)] if capture else []))
        if capture and on_motion:
            from gi.repository import Gdk
            self.window.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
            self.window.connect('motion-notify-event', lambda _w, e: on_motion(output.x + e.x, output.y + e.y))

    def add(self, x, y):
        self.points = (self.points + [(x - self.output.x, y - self.output.y)])[-90:]
        self.window.queue_draw()

    def show(self):
        self.window.show_all()

    def hide(self):
        self.points = []
        self.window.hide()

    def _draw(self, _w, c):
        import cairo
        c.set_operator(cairo.OPERATOR_SOURCE)
        c.set_source_rgba(0, 0, 0, 0)
        c.paint()
        c.set_operator(cairo.OPERATOR_OVER)
        n = len(self.points)
        for i in range(1, n):
            c.set_source_rgba(1, 214 / 255, 10 / 255, 0.15 + 0.6 * i / n)
            c.set_line_width(4)
            c.set_line_cap(cairo.LINE_CAP_ROUND)
            c.move_to(*self.points[i - 1])
            c.line_to(*self.points[i])
            c.stroke()
        return False


class PointerView:
    """Point at your screen: a crosshair layer over the output; the box and cards keep their clicks."""

    def __init__(self, output, env, box, done):
        from atticus_shell import windows
        self.output, self.box, self.done = output, box, done
        self.stroke = []
        self.window = windows.overlay('drawing', output, focusable=False, env=env)
        self.window.connect('map', lambda w: windows.set_input_region(w, self.input_rects()))
        try:
            from gi.repository import Gdk
            self.window.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
            ox, oy = output.x, output.y
            self.window.connect('button-press-event', lambda _w, e: self.press(ox + e.x, oy + e.y))
            self.window.connect('motion-notify-event', lambda _w, e: self.move(ox + e.x, oy + e.y) if self.stroke else None)
            self.window.connect('button-release-event', lambda _w, e: self.release(ox + e.x, oy + e.y))
            self.window.connect('realize', lambda w: w.get_window().set_cursor(Gdk.Cursor.new_from_name(w.get_display(), 'crosshair')))
        except ImportError:
            pass

    def input_rects(self):
        """Everything on the output except the box: four bands around it (layout -> window coordinates)."""
        o = self.output
        bx, by, bw, bh = self.box[0] - o.x, self.box[1] - o.y, self.box[2], self.box[3]
        bands = [(0, 0, o.width, by), (0, by + bh, o.width, o.height - by - bh), (0, by, bx, bh), (bx + bw, by, o.width - bx - bw, bh)]
        return [b for b in bands if b[2] > 0 and b[3] > 0]

    def press(self, x, y):
        self.stroke = [(x, y)]

    def move(self, x, y):
        self.stroke.append((x, y))

    def release(self, x, y):
        if not self.stroke:
            return
        self.stroke.append((x, y))
        points, self.stroke = self.stroke, []
        self.done(points)

    def show(self):
        self.window.show_all()

    def hide(self):
        self.window.hide()
