import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import dictation as D

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Later:
    def __init__(self):
        self.jobs = []

    def __call__(self, secs, fn):
        self.jobs.append(fn)

    def run(self):
        jobs, self.jobs = self.jobs, []
        for fn in jobs:
            fn()


class Engine:
    def __init__(self):
        self.final, self.running = '', False

    def start(self, lang, on_partial, on_final):
        self.running, self.on_final = True, on_final

    def feed(self, pcm):
        pass

    def finish(self):
        self.running = False
        self.on_final(self.final)

    def cancel(self):
        self.running = False


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prefs = {'dictateHistory': True, 'dictateWords': [], 'dictateFixes': {}}
        self.data = D.DictationData(os.path.join(self.dir, 'dictation.json'), self.prefs, lambda k, v: self.prefs.__setitem__(k, v) or True,
                                    known=lambda w: w in {'cash', 'start'}, today=lambda: '2026-11-27', now=lambda: 1_800_000_000.0)

    def test_archive_json_compatible_with_swift(self):
        src = (ROOT / 'Atticus.swift').read_text()
        entry = re.search(r'struct Entry: Codable \{(.*?)\}', src, re.S).group(1)
        archive = re.search(r'struct Archive: Codable \{(.*?)\}', src, re.S).group(1)
        self.assertTrue(self.data.record('hello there world', 'um hello there world', 'Notes', 2.0))
        saved = json.loads(Path(self.dir, 'dictation.json').read_text())
        self.assertEqual(set(saved), set(re.findall(r'(\w+)\s*(?:=|:)', archive)) - {'Entry'})
        row = saved['history'][0]
        self.assertEqual(set(row), set(re.findall(r'(\w+): ', entry)) | {'raw'})
        self.assertEqual((saved['words'], saved['todayWords'], saved['sessions'], saved['day']), (3, 3, 1, '2026-11-27'))
        self.assertEqual(os.stat(Path(self.dir, 'dictation.json')).st_mode & 0o777, 0o600)

    def test_state_shape(self):
        self.data.record('two words', 'two words', 'Notes', 0)
        st = self.data.state()
        self.assertEqual(set(st), {'words', 'history', 'stats'})
        self.assertNotIn('raw', st['history'][0])   # raw kept only when cleanup changed it
        self.assertEqual(st['stats']['today'], 2)

    def test_history_off_still_counts(self):
        self.prefs['dictateHistory'] = False
        self.data.record('a b c', 'a b c', 'X', 0)
        self.assertEqual(self.data.state()['history'], [])
        self.assertEqual(self.data.state()['stats']['words'], 3)

    def test_fixes_swap_only_non_words(self):
        self.prefs['dictateWords'] = ['Kubernetes', 'Cache']
        self.data.fix('kubernettes', 'Kubernetes')
        self.data.fix('cash', 'Cache')
        self.assertEqual(self.data.fixed('deploy kubernettes and cash it'), 'deploy Kubernetes and cash it')
        self.assertEqual(self.data.fixed('Kubernettes first'), 'Kubernetes first')


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.later, self.engine = Clock(), Later(), Engine()
        self.ui, self.cues, self.inserted, self.cleaned = [], [], [], []
        self.prefs = {'dictateMode': 'cleanup', 'dictateHistory': True, 'dictateWords': [], 'dictateFixes': {}}
        self.target = object()
        self.result = 'typed'
        self.dir = tempfile.mkdtemp()
        data = D.DictationData(os.path.join(self.dir, 'd.json'), self.prefs, lambda k, v: self.prefs.__setitem__(k, v) or True, known=lambda w: True)

        def insert(text, target):
            self.inserted.append((text, target))
            return self.result

        def cleanup(before, text):
            self.cleaned.append(text)
            return text.replace('um ', '')
        self.d = D.DictationController(lambda fn, arg=None: self.ui.append((fn, arg)), lambda: self.engine, None, self.prefs, data,
                                       capture=lambda: self.target, insert=insert, cleanup=cleanup, later=self.later,
                                       cue=self.cues.append, now=self.clock)

    def hold(self, words, secs=1.0):
        self.d.edge('down')
        self.clock.t += secs
        self.engine.final = words
        self.d.edge('up')

    def test_hold_cleans_up_and_inserts(self):
        self.hold('um hello there')
        self.assertEqual(self.inserted, [('hello there', self.target)])
        self.assertIn(('dictated', {'text': '', 'ok': True}), self.ui)
        self.assertEqual(self.cues[-1], 'soundDone')

    def test_verbatim_skips_ai(self):
        self.prefs['dictateMode'] = 'verbatim'
        self.hold('um hello there')
        self.assertEqual((self.cleaned, self.inserted[0][0]), ([], 'um hello there'))

    def test_double_tap_locks_then_press_inserts(self):
        self.d.edge('down')
        self.clock.t += 0.1
        self.d.edge('up')
        self.clock.t += 0.1
        self.d.edge('down')               # second tap: hands-free
        self.assertIn(('locked', True), self.ui)
        self.d.edge('up')
        self.later.run()                  # the first tap's pending finish sees the lock and waits
        self.assertEqual(self.inserted, [])
        self.clock.t += 5
        self.engine.final = 'long hands free words'
        self.d.edge('down')               # one more press inserts
        self.assertEqual(self.inserted, [('long hands free words', self.target)])

    def test_single_tap_finishes_after_the_double_tap_window(self):
        self.d.edge('down')
        self.clock.t += 0.1
        self.engine.final = 'quick'
        self.d.edge('up')
        self.assertEqual(self.inserted, [])
        self.later.run()
        self.assertEqual(self.inserted[0][0], 'quick')

    def test_no_text_field_saves_to_history(self):
        self.target = None
        self.hold('save these words')
        self.assertIn(('dictated', {'text': 'No text field. Saved to History.', 'ok': False, 'history': True}), self.ui)

    def test_history_off_keeps_words_in_pill(self):
        self.prefs['dictateHistory'] = False
        self.result = 'saved'
        self.hold('keep these words')
        self.assertIn(('dictated', {'text': 'Couldn’t insert. Your words are still here.', 'ok': False, 'words': 'keep these words'}), self.ui)

    def test_nothing_heard_ends_quietly(self):
        self.hold('   ')
        self.assertEqual(self.inserted, [])
        self.assertEqual(self.cues[-1], 'soundStop')

    def test_esc_cancels(self):
        self.d.edge('down')
        self.d.cancel()
        self.assertFalse(self.engine.running)
        self.assertIn(('dictate', False), self.ui)
        self.d.edge('up')
        self.assertEqual(self.inserted, [])


if __name__ == '__main__':
    unittest.main()
