import unittest

from tests import linux  # noqa: F401
from atticus_shell import agents  # noqa: F401  (import order: app pulls box)
from atticus_shell.app import MiniCard
from atticus_shell.desktop.bridge import Output, Window
from tests.linux.fakes import FakeBridge

TARGET = {'pid': 10, 'windowID': '26', 'windowFrame': [0, 0, 800, 600], 'window': 'Docs', 'app': 'firefox', 'minimized': False}


class MiniCardTests(unittest.TestCase):
    def setUp(self):
        self.ui = []
        self.bridge = FakeBridge([Output('eDP-1', 0, 0, 1280, 800)], [Window('0x1a', 'firefox', 10, 'Docs', 0, 0, 800, 600, 'eDP-1', '0')])
        self.prefs = {'miniScreen': True}
        self.card = MiniCard(lambda fn, *a: self.ui.append((fn, a[0] if a else None)), self.bridge, self.prefs)

    def test_fallback_icon_title_when_unavailable(self):
        self.card.receive(dict(TARGET, action='click'))
        self.assertEqual(self.ui, [('mini', {**TARGET, 'action': 'click', 'shown': False, 'live': False})])

    def test_setting_off_shows_nothing(self):
        self.prefs['miniScreen'] = False
        self.card.receive(TARGET)
        self.assertEqual(self.ui, [])

    def test_click_opens_that_exact_window(self):
        self.card.receive(TARGET)
        self.card.open()
        self.assertIn(('activate', '0x1a'), self.bridge.calls)

    def test_stops_on_task_end(self):
        self.card.receive(TARGET)
        self.card.finish()
        self.assertIsNone(self.card.target)


if __name__ == '__main__':
    unittest.main()
