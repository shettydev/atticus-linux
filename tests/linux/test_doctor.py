"""atticus-shell --doctor: one screen that says what works and what to do, for shortcut, desktop, microphone and speech."""
import io
import json
import os
import struct
import tempfile
import unittest
from types import SimpleNamespace

from tests import linux  # noqa: F401
from atticus_shell import doctor as D


def facts(**kw):
    base = dict(env=SimpleNamespace(session='wayland', compositor='hyprland', portals=frozenset({'GlobalShortcuts'})),
                running=True, shortcuts={'route': 'hyprland', 'bound': {'talk': True, 'dictate': True}, 'error': ''},
                bridge=(True, ''), mics=[SimpleNamespace(name='Built-in Audio')], default_mic='Built-in Audio',
                engine='2.0.3', model='/home/u/.config/atticus/models/zipformer', client='/usr/bin/claude',
                talk_label='Ctrl+Alt+Space')
    base.update(kw)
    return base


class ReportTests(unittest.TestCase):
    def test_all_good(self):
        lines, ok = D.report(**facts())
        text = '\n'.join(lines)
        self.assertTrue(ok)
        self.assertIn('Hyprland on Wayland', text)
        self.assertIn('Ctrl+Alt+Space works (hyprland)', text)
        self.assertNotIn('✗', text)

    def test_each_problem_says_what_to_do(self):
        cases = [
            (dict(running=False), 'systemctl --user restart atticus'),
            (dict(shortcuts=None), 'atticus-shell --trigger talk press'),
            (dict(shortcuts={'route': 'hyprland', 'bound': {'talk': False}, 'error': 'Ctrl+Alt+Space is already used in Hyprland (rofi)'}), 'rofi'),
            (dict(bridge=(False, 'Log out and back in once to finish setup.')), 'Log out and back in'),
            (dict(mics=[]), 'No microphone'),
            (dict(engine=None), 'build-linux.sh'),
            (dict(model=None), 'Speech model'),
            (dict(client=None), 'Claude Code'),
        ]
        for change, words in cases:
            with self.subTest(change):
                lines, ok = D.report(**facts(**change))
                self.assertFalse(ok)
                self.assertIn(words, '\n'.join(lines))
                self.assertIn('✗', '\n'.join(lines))


class LevelTests(unittest.TestCase):
    def test_peak_of_s16_audio(self):
        quiet = struct.pack('<4h', 0, 10, -10, 0)
        loud = struct.pack('<4h', 0, 16384, -32768, 0)
        self.assertLess(D.peak(quiet), 0.01)
        self.assertEqual(D.peak(loud), 1.0)

    def test_listen_verdicts(self):
        self.assertIn('no sound', D.listen_verdict(0.0, ''))
        self.assertIn('quiet', D.listen_verdict(0.02, ''))
        self.assertIn('heard sound but no words', D.listen_verdict(0.4, ''))
        self.assertIn('“hello there”', D.listen_verdict(0.4, 'hello there'))


class StatusFileTests(unittest.TestCase):
    def test_reads_what_the_app_saved(self):
        path = os.path.join(tempfile.mkdtemp(), 's.json')
        with open(path, 'w') as f:
            json.dump({'route': 'gnome', 'bound': {'talk': True}, 'error': ''}, f)
        self.assertEqual(D.saved_shortcuts(path)['route'], 'gnome')
        self.assertIsNone(D.saved_shortcuts(path + '.missing'))


if __name__ == '__main__':
    unittest.main()
