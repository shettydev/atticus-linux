import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests import linux
from tests.test_spoken import FIXTURES
from atticus_shell.speech import tts as T

ROOT = Path(__file__).resolve().parents[2]


def words(s):
    return ' '.join(s.split())


class SpokenTests(unittest.TestCase):
    def test_spoken_matches_swift_cases(self):
        for text, expected in FIXTURES:
            with self.subTest(text):
                self.assertEqual(words(T.read_aloud_text(text)), expected)

    def test_sentences_stream_like_swift(self):
        for text, expected in FIXTURES:
            with self.subTest(text):
                q, heard = T.SpeechSentences(), []
                for end in range(1, len(text)):
                    heard += q.take(T.read_aloud_text(text[:end]))
                heard += q.take(T.read_aloud_text(text), final=True)
                self.assertEqual(' '.join(heard), expected)

    def test_line_breaks_end_items(self):
        q = T.SpeechSentences()
        self.assertEqual(q.take(T.read_aloud_text('First line\n')), ['First line'])
        self.assertEqual(q.take(T.read_aloud_text('First line\nSecond line'), final=True), ['Second line'])

    def test_long_answers_cap_at_900_chars(self):
        q = T.SpeechSentences()
        said = q.take(('This is a sentence of words. ' * 60), final=True)
        self.assertEqual(said[-1], 'The rest is on screen.')
        self.assertLessEqual(sum(len(s) for s in said[:-1]), 900)
        self.assertEqual(q.take('More. Words.', final=True), [])


class SpeakerTests(unittest.TestCase):
    def test_rate_mapping_196_wpm(self):
        self.assertEqual(T.spd_rate(196), 12)
        self.assertEqual(T.spd_rate(175), 0)
        self.assertEqual(T.spd_rate(9999), 100)
        self.assertAlmostEqual(T.piper_length_scale(196), 0.893, places=3)

    def test_piper_preferred_when_voice_installed(self):
        root = tempfile.mkdtemp()
        Path(root, 'en_US-amy-medium.onnx').write_text('x')
        cmd = T.speak_command('Hello there.', 196, voices_dir=root, which=lambda n: f'/usr/bin/{n}')
        self.assertEqual(cmd[0], 'sh')
        self.assertIn('piper', cmd[2])
        self.assertTrue(cmd[4].endswith('en_US-amy-medium.onnx'))   # a positional argument, never spliced into the shell line

    def test_spd_say_without_piper_voice(self):
        cmd = T.speak_command('Hello there.', 196, voices_dir=tempfile.mkdtemp(), which=lambda n: f'/usr/bin/{n}' if n == 'spd-say' else None)
        self.assertEqual(cmd, ['spd-say', '-w', '-r', '12', '--', 'Hello there.'])

    def test_nothing_installed_is_silent(self):
        self.assertIsNone(T.speak_command('Hi.', 196, voices_dir=tempfile.mkdtemp(), which=lambda n: None))

    def test_words_never_reach_the_shell(self):
        root = tempfile.mkdtemp()
        Path(root, 'v.onnx').write_text('x')
        cmd = T.speak_command('$(rm -rf ~); `x`', 196, voices_dir=root, which=lambda n: f'/usr/bin/{n}')
        self.assertNotIn('rm -rf', cmd[2])          # the text goes on stdin, not into the pipeline
        self.assertEqual(cmd[-1], '$(rm -rf ~); `x`')

    def test_stop_kills_playback_process(self):
        s = T.Speaker(command=lambda text, rate: ['sleep', '30'])
        s.say('hello', 196)
        proc = s.proc
        s.stop()
        self.assertIsNotNone(proc.wait(5))
        self.assertIsNone(s.proc)

    def test_sound_files_for_mac_names(self):
        for name in ('Pop', 'Bottle', 'Glass', 'Tink'):
            path = T.sound_path(name, str(ROOT))
            self.assertTrue(path and os.path.exists(path), name)
        self.assertIsNone(T.sound_path('None', str(ROOT)))


@linux.needs_gi
class SoundPlaybackTests(unittest.TestCase):
    def test_sound_decodes(self):
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        p = Gst.parse_launch(f'filesrc location="{T.sound_path("Glass", str(ROOT))}" ! decodebin ! audioconvert ! fakesink')
        p.set_state(Gst.State.PLAYING)
        msg = p.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        p.set_state(Gst.State.NULL)
        self.assertEqual(msg.type, Gst.MessageType.EOS)


if __name__ == '__main__':
    unittest.main()
