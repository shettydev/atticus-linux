import unittest

from tests import linux  # noqa: F401  (puts linux/ on sys.path)
from atticus_shell.desktop.env import DesktopEnv, detect


def none():
    return set()


class DetectTests(unittest.TestCase):
    def test_gnome_wayland(self):
        env = detect({'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'ubuntu:GNOME'}, none)
        self.assertEqual((env.session, env.compositor), ('wayland', 'gnome'))

    def test_hyprland_by_signature(self):
        self.assertEqual(detect({'XDG_SESSION_TYPE': 'wayland', 'HYPRLAND_INSTANCE_SIGNATURE': 'x'}, none).compositor, 'hyprland')

    def test_sway_by_socket(self):
        self.assertEqual(detect({'XDG_SESSION_TYPE': 'wayland', 'SWAYSOCK': '/run/s'}, none).compositor, 'sway')

    def test_kde(self):
        self.assertEqual(detect({'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE'}, none).compositor, 'kde')

    def test_gnome_on_x11_uses_x11(self):
        env = detect({'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': 'GNOME'}, none)
        self.assertEqual((env.session, env.compositor), ('x11', 'x11'))

    def test_unknown_wayland_is_other(self):
        self.assertEqual(detect({'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'niri'}, none).compositor, 'other')

    def test_wayland_inferred_from_display_when_session_type_unset(self):
        env = detect({'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'KDE'}, none)
        self.assertEqual((env.session, env.compositor), ('wayland', 'kde'))

    def test_x11_inferred_from_display(self):
        self.assertEqual(detect({'DISPLAY': ':0'}, none).session, 'x11')

    def test_no_display_is_tty(self):
        self.assertEqual(detect({}, none).session, 'tty')

    def test_portals_recorded(self):
        env = detect({'XDG_SESSION_TYPE': 'wayland'}, lambda: {'GlobalShortcuts', 'Screenshot'})
        self.assertEqual(env.portals, frozenset({'GlobalShortcuts', 'Screenshot'}))
        self.assertIsInstance(env, DesktopEnv)

    def test_probe_failure_means_no_portals(self):
        def broken():
            raise OSError('no session bus')
        self.assertEqual(detect({'XDG_SESSION_TYPE': 'wayland'}, broken).portals, frozenset())


if __name__ == '__main__':
    unittest.main()
