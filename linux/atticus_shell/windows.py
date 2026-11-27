"""Atticus's floating windows: above everything, never taking focus unless typing, clicks passing through where asked.

X11 (and XWayland on GNOME): keep-above, no focus, skip taskbar, wm_class "atticus" (the GNOME extension keeps those above).
wlroots/KDE Wayland: gtk-layer-shell on the OVERLAY layer, keyboard only on demand.
"""
import os

LAYER_SHELL_COMPOSITORS = ('kde', 'hyprland', 'sway', 'other')
FULL_SCREEN_KINDS = ('drawing', 'pen')


def choose_gdk_backend(env, environ=os.environ):
    """Before Gtk loads: GNOME's Mutter has no layer-shell, so Atticus's windows live on XWayland there."""
    if env.session == 'wayland' and env.compositor == 'gnome':
        environ['GDK_BACKEND'] = 'x11'


def layer_shell_supported():
    try:
        import gi
        gi.require_version('GtkLayerShell', '0.1')
        from gi.repository import GtkLayerShell
        return bool(GtkLayerShell.is_supported())
    except (ImportError, ValueError):
        return False


def backend_for(env, layer_shell=None):
    if env.session != 'wayland' or env.compositor not in LAYER_SHELL_COMPOSITORS:
        return 'x11'
    if layer_shell is None:
        layer_shell = layer_shell_supported()
    return 'layer-shell' if layer_shell else 'x11'


def _gtk():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk, Gtk
    return Gdk, Gtk


def _monitor(output):
    Gdk, _ = _gtk()
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        m = display.get_monitor(i)
        if m.get_model() == output.name or (m.get_geometry().x, m.get_geometry().y) == (output.x, output.y):
            return m
    return display.get_primary_monitor() or display.get_monitor(0)


def overlay(kind, output, focusable, env, layer_shell=None):
    Gdk, Gtk = _gtk()
    w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    w.set_title(f'Atticus {kind}')
    w.set_wmclass('atticus', 'atticus')
    w.set_decorated(False)
    w.set_app_paintable(True)
    visual = w.get_screen().get_rgba_visual()
    if visual is not None:
        w.set_visual(visual)   # transparent corners and click-through drawings
    w.set_skip_taskbar_hint(True)
    w.set_skip_pager_hint(True)
    w.set_accept_focus(focusable)
    w.set_focus_on_map(focusable)
    w.atticus_kind, w.atticus_output = kind, output
    if backend_for(env, layer_shell) == 'layer-shell':
        import gi
        gi.require_version('GtkLayerShell', '0.1')
        from gi.repository import GtkLayerShell as L
        L.init_for_window(w)
        L.set_layer(w, L.Layer.OVERLAY)
        L.set_namespace(w, f'atticus-{kind}')
        L.set_keyboard_mode(w, L.KeyboardMode.ON_DEMAND if focusable else L.KeyboardMode.NONE)
        L.set_monitor(w, _monitor(output))
        edges = (L.Edge.TOP, L.Edge.LEFT, L.Edge.BOTTOM, L.Edge.RIGHT) if kind in FULL_SCREEN_KINDS else (L.Edge.TOP, L.Edge.LEFT)
        for e in edges:
            L.set_anchor(w, e, True)
        w.atticus_backend = 'layer-shell'
    else:
        w.set_keep_above(True)
        w.set_type_hint(Gdk.WindowTypeHint.UTILITY if focusable else Gdk.WindowTypeHint.NOTIFICATION)
        w.stick()
        if kind in FULL_SCREEN_KINDS:
            w.move(output.x, output.y)
            w.set_default_size(output.width, output.height)
        w.atticus_backend = 'x11'
    return w


def move(w, x, y):
    """Place a window's top-left at layout coordinates (x, y)."""
    if getattr(w, 'atticus_backend', 'x11') == 'layer-shell':
        from gi.repository import GtkLayerShell as L
        o = w.atticus_output
        L.set_margin(w, L.Edge.LEFT, int(x - o.x))
        L.set_margin(w, L.Edge.TOP, int(y - o.y))
    else:
        w.move(int(x), int(y))


def set_input_region(w, rects):
    """Clicks reach the window only inside rects (x, y, w, h); an empty list passes every click through."""
    import cairo
    region = cairo.Region([cairo.RectangleInt(*map(int, r)) for r in rects]) if rects else cairo.Region()
    w.input_shape_combine_region(region)


def input_rects(w):
    """The X server's input shape of w (tests and diagnostics; X11 only)."""
    import gi
    gi.require_version('GdkX11', '3.0')
    from gi.repository import GdkX11  # noqa: F401
    from Xlib import display
    from Xlib.ext import shape
    d = display.Display()
    try:
        xwin = d.create_resource_object('window', w.get_window().get_xid())
        reply = xwin.shape_get_rectangles(shape.SK.Input)
        return [(r.x, r.y, r.width, r.height) for r in reply.rectangles]
    finally:
        d.close()
