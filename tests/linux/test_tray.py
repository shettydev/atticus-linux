import os
import unittest

from tests import linux
from atticus_shell import tray as T


def titles(model):
    return [m if m == '-' else m[1] for m in model]


class MenuModelTests(unittest.TestCase):
    def test_menu_matches_mac_items(self):
        self.assertEqual(titles(T.menu_model({})), [
            'Ask Atticus', '-', 'Agents…', 'Show Agent Shelf', 'Dictation History…', 'Paste Last Dictation', 'Meeting Copilot…', '-',
            'Pause Atticus', 'Settings…', 'Report Feedback…', 'Leave a Tip…', '-', 'Quit Atticus'])

    def test_paused(self):
        t = titles(T.menu_model({'paused': True}))
        self.assertEqual(t[0], 'Atticus is paused')
        self.assertIn('Resume Atticus', t)
        self.assertNotIn('Pause Atticus', t)

    def test_meeting_and_shelf_and_tips(self):
        t = titles(T.menu_model({'meeting_running': True, 'shelf_shown': True, 'tips': False}))
        self.assertIn('Stop Meeting Copilot', t)
        self.assertIn('Hide Agent Shelf', t)
        self.assertIn('Show Tips Again', t)

    def test_dictation_off_hides_its_items(self):
        t = titles(T.menu_model({'dictate': False}))
        self.assertNotIn('Paste Last Dictation', t)

    def test_ids_are_unique(self):
        ids = [m[0] for m in T.menu_model({'meeting_running': True, 'tips': False}) if m != '-']
        self.assertEqual(len(ids), len(set(ids)))

    def test_icon_state(self):
        self.assertEqual(T.icon_state({'paused': True}), 'paused')
        self.assertEqual(T.icon_state({'listening': True}), 'listening')
        self.assertEqual(T.icon_state({}), 'idle')


class AvailabilityTests(unittest.TestCase):
    def test_no_watcher_reports_unavailable(self):
        t = T.Tray(lambda item: None, lambda: {}, watcher=lambda: False)
        self.assertFalse(t.available)
        self.assertIn('AppIndicator', t.unavailable_reason)


@unittest.skipUnless(linux.HAVE_GI and os.environ.get('DISPLAY'), 'needs PyGObject and a display')
class GtkMenuTests(unittest.TestCase):
    def test_menu_items_activate(self):
        chosen = []
        t = T.Tray(chosen.append, lambda: {}, watcher=lambda: True, indicator=False)
        menu = t.build_menu()
        labels = [i.get_label() for i in menu.get_children() if i.get_label()]
        self.assertEqual(labels[0], 'Ask Atticus')
        menu.get_children()[0].activate()
        self.assertEqual(chosen, ['ask'])


if __name__ == '__main__':
    unittest.main()
