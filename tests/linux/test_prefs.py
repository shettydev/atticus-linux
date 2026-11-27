import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import prefs as P

ROOT = Path(__file__).resolve().parents[2]


def swift_default_keys():
    """Top-level keys of `Prefs.defaults` in Atticus.swift (nested meetingStyles entries excluded)."""
    src = (ROOT / 'Atticus.swift').read_text()
    start = src.index('static let defaults: [String: Any] = [') + len('static let defaults: [String: Any] = ')
    depth, keys, i = 0, [], start
    while True:
        ch = src[i]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return keys
        elif ch == '"' and depth == 1:
            m = re.match(r'"(\w+)":', src[i:])
            end = src.index('"', i + 1)
            if m:
                keys.append(m.group(1))
            i = end
        i += 1


class PrefsTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path = os.path.join(self.root.name, 'settings.json')

    def load(self, saved=None):
        if saved is not None:
            Path(self.path).write_text(json.dumps(saved))
        return P.Prefs(self.path)

    def test_defaults_cover_swift_keys(self):
        keys = swift_default_keys()
        self.assertIn('voiceRate', keys)       # the scan reached the end of the literal
        self.assertNotIn('name', keys)         # and skipped nested meetingStyles entries
        self.assertEqual(set(keys) - set(P.DEFAULTS), set())

    def test_swift_values_mirrored(self):
        p = self.load()
        self.assertEqual((p.num('boxW'), p.num('boxH'), p.num('voiceRate'), p.num('volume')), (500, 420, 196, 0.16))
        self.assertEqual(p.str('talkMode'), 'hold')

    def test_routing_migration(self):
        self.assertEqual(self.load({'model': 'sonnet'}).str('routing'), 'capable')
        self.assertEqual(self.load({'model': 'haiku'}).str('routing'), 'balanced')
        self.assertEqual(self.load({'model': 'haiku', 'escalate': False}).str('routing'), 'cheapest')
        self.assertEqual(self.load({'model': 'haiku', 'escalateAuto': False}).str('routing'), 'cheapest')
        self.assertEqual(self.load({'routing': 'cheapest', 'model': 'sonnet'}).str('routing'), 'cheapest')

    def test_old_box_size_reset(self):
        p = self.load({'boxW': 460, 'boxH': 360})
        self.assertEqual((p.num('boxW'), p.num('boxH')), (500, 420))
        self.assertEqual(self.load({'boxW': 470}).num('boxW'), 470)

    def test_linux_keys_present(self):
        p = self.load()
        self.assertEqual(p.str('linuxHotkey'), 'CTRL+ALT+space')
        self.assertEqual(p.str('linuxDictateHotkey'), 'CTRL+LOGO+space')
        self.assertEqual(p.str('hotkeyLabel'), 'Ctrl+Alt+Space')

    def test_empty_dictation_shortcut_gets_linux_default(self):
        self.assertEqual(self.load({'dictateHotkeyLabel': ''}).str('dictateHotkeyLabel'), 'Ctrl+Super+Space')

    def test_dictation_shortcut_cleared_on_purpose_stays_off(self):
        self.assertEqual(self.load({'dictateHotkeyLabel': '', 'dictateHotkeyOff': True}).str('dictateHotkeyLabel'), '')

    def test_saved_values_win(self):
        self.assertFalse(self.load({'speak': False}).bool('speak'))

    def test_corrupt_file_falls_back_to_defaults(self):
        Path(self.path).write_text('{not json')
        self.assertTrue(P.Prefs(self.path).bool('speak'))

    def test_set_persists_with_0600(self):
        p = self.load()
        self.assertTrue(p.set('speak', False))
        self.assertFalse(json.loads(Path(self.path).read_text())['speak'])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertFalse(P.Prefs(self.path).bool('speak'))

    def test_set_rolls_back_on_error(self):
        blocker = os.path.join(self.root.name, 'file')
        Path(blocker).write_text('x')
        p = P.Prefs(os.path.join(blocker, 'settings.json'))   # parent is a file: cannot be written
        self.assertFalse(p.set('speak', False))
        self.assertTrue(p.bool('speak'))

    def test_ui_language(self):
        self.assertEqual(self.load({'uiLanguage': 'de'}).ui_language, 'de')
        self.assertEqual(self.load({'uiLanguage': 'pt'}).ui_language, 'en')
        self.assertEqual(P.resolve_ui_language('auto', ['fr_FR.UTF-8']), 'fr')

    def test_default_path_uses_atticus_dir(self):
        self.assertEqual(P.default_path({'ATTICUS_DIR': '/x'}), '/x/settings.json')
        self.assertEqual(P.default_path({'HOME': '/h'}), '/h/.config/atticus/settings.json')


if __name__ == '__main__':
    unittest.main()
