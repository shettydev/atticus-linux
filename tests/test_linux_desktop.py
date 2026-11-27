"""Placement target selection and D-Bus receipts, including stale or changed targets."""
import unittest
from unittest.mock import Mock, patch

import linux_desktop as desktop


class LinuxPlacementTests(unittest.TestCase):
    def setUp(self):
        self.monitor = {'index': 0, 'name': 'eDP-1', 'builtin': True, 'primary': True}
        self.external = {'index': 1, 'name': 'DP-1', 'builtin': False, 'primary': False}
        self.window = dict(id=8, title='Study-Guide.pdf', app='Viewer', desktop='org.example.Viewer.desktop',
                           wmclass='viewer', monitor=0, fullscreen=False, minimized=False, frame=[50, 20, 600, 400])
        self.copy = dict(self.window, id=9, title='Study Guide copy.pdf')
        self.snapshot = dict(monitors=[self.monitor, self.external], current=1, windows=[self.copy, self.window])

    def test_display_selection(self):
        for name in ('monitor', 'current', 'DP-1', 'display 2'):
            self.assertEqual(desktop.monitor_for(self.snapshot, name), self.external)
        for name in ('laptop', 'primary', 'edp-1', 'display 1'):
            self.assertEqual(desktop.monitor_for(self.snapshot, name), self.monitor)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            desktop.monitor_for(self.snapshot, 'HDMI-1')

    def test_target_matching_preserves_the_copy(self):
        rows = self.snapshot['windows']
        self.assertEqual(desktop.window_for(rows, 'org.example.Viewer', 'study guide')['id'], 8)
        self.assertEqual(desktop.window_for(rows, 'VIEWER', 'copy')['id'], 9)
        with self.assertRaisesRegex(ValueError, 'Multiple'):
            desktop.window_for(rows, 'viewer', 'guide')

    def test_lost_target_never_returns_a_verified_move(self):
        bridge = Mock()
        bridge.call.side_effect = [self.snapshot, [0, 20, 500, 700], dict(self.snapshot, windows=[self.copy])]
        with patch.object(desktop, 'GLib', Mock(Error=RuntimeError)), patch.object(desktop, 'Desktop', return_value=bridge):
            result = desktop.place('Viewer', 'left', 'primary', 'study guide')
        self.assertIn('Could not confirm', result)
        self.assertEqual(bridge.call.call_args_list[1].args, ('Place', '(uis)', (8, 0, 'left')))
        self.assertEqual(bridge.call.call_count, 3)
