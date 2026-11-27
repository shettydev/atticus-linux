import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import box as X
from atticus_shell.desktop.bridge import Output

ROOT = Path(__file__).resolve().parents[2]


def swift_box_keys():
    src = (ROOT / 'Atticus.swift').read_text()
    start = src.index('if m.name == "c", let b = m.body as? [String: Any] {')
    end = src.index('let size = ((m.body as? [NSNumber])', start)
    return set(re.findall(r'b\["([a-zA-Z]+)"\]', src[start:end]))


def page_keys():
    html = (ROOT / 'ui/ui.html').read_text()
    return set(re.findall(r'post\(\s*\{\s*([a-zA-Z]+)\s*[:,}]', html)) | set(re.findall(r'\?\s*\{\s*([a-zA-Z]+):', html))


class ContractTests(unittest.TestCase):
    def test_every_ui_message_kind_handled(self):
        swift, page = swift_box_keys(), page_keys()
        self.assertGreater(len(swift), 40)
        self.assertIn('fold', page)
        self.assertEqual(sorted((swift | page) - set(X.HANDLED)), [])


class RouteTests(unittest.TestCase):
    def test_answer_and_u2028(self):
        self.assertEqual(X.route_line('✓ Two lines'), ('answer', 'Two\nlines'))
        self.assertEqual(X.route_line('♬ Say this'), ('speech', 'Say\nthis'))

    def test_steps_and_notes_go_to_the_page(self):
        self.assertEqual(X.route_line('▸ Opening Spotify'), ('line', '▸ Opening Spotify'))
        self.assertEqual(X.route_line('· thinking'), ('line', '· thinking'))
        self.assertEqual(X.route_line('! Signed out'), ('msg', 'Signed out'))

    def test_control_lines(self):
        self.assertEqual(X.route_line('! setup'), ('setup', None))
        self.assertEqual(X.route_line('! feedback'), ('feedback', None))
        self.assertEqual(X.route_line('! improve'), ('improve', None))
        self.assertEqual(X.route_line('♬!'), ('shush', None))
        self.assertEqual(X.route_line('♧ retry'), ('upgrade_retry', None))
        self.assertEqual(X.route_line('▤ a1'), ('show_agent', 'a1'))
        self.assertEqual(X.route_line('▥ a1'), ('open_agents', 'a1'))
        self.assertEqual(X.route_line('@ 10,20 the button answer'), ('point', '@ 10,20 the button answer'))

    def test_json_lines(self):
        self.assertEqual(X.route_line('◇ {"q": "Which?", "a": "/tmp/x"}'), ('pick', {'q': 'Which?', 'a': '/tmp/x'}))
        self.assertEqual(X.route_line('↺ {"t": "new", "mode": "replace"}'), ('rewrite', {'t': 'new', 'mode': 'replace'}))
        self.assertEqual(X.route_line('▣ {"app": "Firefox"}'), ('mini', {'app': 'Firefox'}))
        self.assertEqual(X.route_line('☷ {"phase": "waiting"}'), ('walkthrough', {'phase': 'waiting'}))
        self.assertEqual(X.route_line('⤴ {"task": "x"}'), ('handoff', {'task': 'x'}))
        self.assertEqual(X.route_line('⇢ {"a": "/tmp/m"}'), ('movable', {'a': '/tmp/m'}))
        self.assertEqual(X.route_line('♧ {"id": "/tmp/u"}'), ('upgrade', {'id': '/tmp/u'}))
        self.assertEqual(X.route_line('✎ {"steps": []}'), ('draw', '{"steps": []}'))

    def test_bad_json_falls_through_to_the_page(self):
        self.assertEqual(X.route_line('◇ {nope'), ('line', '◇ {nope'))
        self.assertEqual(X.route_line('↺ {"mode": "x"}'), ('drop', None))   # a rewrite needs its text

    def test_ask_card(self):
        self.assertEqual(X.route_line('? Delete it?\trm x\t/tmp/a'), ('ask', ['Delete it?', 'rm x', '/tmp/a']))
        self.assertEqual(X.route_line('? only\ttwo'), ('drop', None))

    def test_setting_line(self):
        self.assertEqual(X.route_line('⚙ speak false'), ('setting', ('speak', 'false')))
        self.assertEqual(X.route_line('⚙ broken'), ('drop', None))


class SettingRuleTests(unittest.TestCase):
    prefs = {'speak': True, 'paused': False, 'safe': True, 'workIn': 'back', 'hotkeyLabel': 'x', 'boxW': 500}

    def test_bool_switch(self):
        self.assertEqual(X.setting_change('speak', 'false', self.prefs), ('speak', False))

    def test_never_pause_and_safe_only_on(self):
        self.assertIsNone(X.setting_change('paused', 'true', self.prefs))
        self.assertIsNone(X.setting_change('safe', 'false', self.prefs))
        self.assertEqual(X.setting_change('safe', 'true', self.prefs), ('safe', True))

    def test_choice_but_not_hotkeys_or_numbers(self):
        self.assertEqual(X.setting_change('workIn', 'front', self.prefs), ('workIn', 'front'))
        self.assertIsNone(X.setting_change('hotkeyLabel', 'F1', self.prefs))
        self.assertIsNone(X.setting_change('boxW', '9', self.prefs))
        self.assertIsNone(X.setting_change('unknown', 'x', self.prefs))


class GeometryTests(unittest.TestCase):
    out = Output('eDP-1', 0, 0, 1280, 800, work_area=(0, 32, 1280, 768))

    def test_box_width(self):
        self.assertEqual(X.box_width(500, 1280), 500)
        self.assertEqual(X.box_width(10, 1280), 340)
        self.assertEqual(X.box_width(5000, 1280), 900)
        self.assertEqual(X.box_width(float('nan'), 1280), 500)
        self.assertEqual(X.box_width(800, 600), 584)

    def test_initial_position_corners(self):
        self.assertEqual(X.initial_position(self.out, 500, 'top-left'), (14, 42))
        self.assertEqual(X.initial_position(self.out, 500, 'top-right'), (766, 42))

    def test_box_stays_inside_work_area_on_resize(self):
        self.assertEqual(X.clamp_frame((1200, 700, 500, 400), self.out.work_area), (772, 392, 500, 400))
        self.assertEqual(X.clamp_frame((10, 40, 500, 2000), self.out.work_area), (10, 40, 500, 752))   # never taller than the display
        self.assertEqual(X.clamp_frame((-50, 0, 500, 300), self.out.work_area), (8, 40, 500, 300))


FAKE_BRAIN = r'''
import json, os, sys, time
assert sys.argv[1:] == ['--wait'], sys.argv
commit = os.environ['ATTICUS_COMMIT']
request = json.loads(sys.stdin.readline())
for _ in range(100):
    if os.path.exists(commit): break
    time.sleep(0.02)
print('▸ ' + request['task'], flush=True)
print('✓ 4 done', flush=True)
print(json.dumps({'env': {k: os.environ.get(k) for k in ('ATTICUS_UI', 'ATTICUS_FRONT', 'ATTICUS_SCREEN', 'ATTICUS_APP')}, 'request': request}), flush=True)
'''


class BrainRunTests(unittest.TestCase):
    def test_runs_request_and_streams_lines(self):
        root = tempfile.mkdtemp()
        brain = Path(root, 'brain.py')
        brain.write_text(FAKE_BRAIN)
        lines, exited = [], threading.Event()
        codes = []
        run = X.BrainRun([sys.executable, str(brain), '--wait'], {'ATTICUS_UI': '1', 'ATTICUS_FRONT': 'Firefox', 'ATTICUS_SCREEN': 'eDP-1', 'ATTICUS_APP': '/x/shell'},
                         on_line=lines.append, on_exit=lambda c: (codes.append(c), exited.set()), post=lambda fn: fn())
        run.start()
        run.send({'front': 'Firefox', 'task': 'what is 2+2'})
        self.assertTrue(exited.wait(10))
        self.assertEqual(codes, [0])
        self.assertEqual(lines[:2], ['▸ what is 2+2', '✓ 4 done'])
        echo = json.loads(lines[2])
        self.assertEqual(echo['env'], {'ATTICUS_UI': '1', 'ATTICUS_FRONT': 'Firefox', 'ATTICUS_SCREEN': 'eDP-1', 'ATTICUS_APP': '/x/shell'})
        self.assertEqual(echo['request']['task'], 'what is 2+2')
        self.assertFalse(os.path.exists(run.commit))   # cleaned up after the run

    def test_cancelled_run_reports_nothing_late(self):
        root = tempfile.mkdtemp()
        brain = Path(root, 'slow.py')
        brain.write_text('import sys, time\nsys.stdin.readline()\ntime.sleep(5)\nprint("✓ late", flush=True)\n')
        lines, codes = [], []
        run = X.BrainRun([sys.executable, str(brain)], {}, on_line=lines.append, on_exit=codes.append, post=lambda fn: fn())
        run.start()
        run.send({'task': 'x'})
        run.cancel()
        run.proc.wait(5)
        self.assertEqual((lines, codes), ([], []))

    def test_py_env_puts_user_bins_first(self):
        env = X.py_env({'PATH': '/usr/bin', 'HOME': '/h'}, '/p/atticus-shell', {'ATTICUS_UI': '1'})
        self.assertTrue(env['PATH'].startswith('/h/.local/bin:'))
        self.assertEqual((env['ATTICUS_APP'], env['ATTICUS_UI']), ('/p/atticus-shell', '1'))


class FakeUI:
    def __init__(self):
        self.calls = []

    def __call__(self, fn, arg=X.NOARG):
        self.calls.append((fn,) if arg is X.NOARG else (fn, arg))

    def names(self):
        return [c[0] for c in self.calls]


class FakeWindow:
    def __init__(self):
        self.visible, self.events = False, []

    def show(self):
        self.visible = True
        self.events.append('show')

    def hide(self):
        self.visible = False
        self.events.append('hide')

    def take_keys(self):
        self.events.append('keys')

    def release_keys(self):
        self.events.append('release')

    def set_height(self, h):
        self.events.append(('height', h))


class FakeEngine:
    def __init__(self):
        self.running, self.final = False, ''

    def start(self, lang, on_partial, on_final):
        self.running, self.on_partial, self.on_final = True, on_partial, on_final

    def feed(self, pcm):
        pass

    def finish(self):
        self.running = False
        self.on_final(self.final)

    def cancel(self):
        self.running = False


class FakeBrain:
    made = []

    def __init__(self, on_line, on_exit):
        self.on_line, self.on_exit, self.sent, self.cancelled = on_line, on_exit, [], False
        FakeBrain.made.append(self)

    def start(self):
        pass

    def send(self, request):
        self.sent.append(request)

    def cancel(self):
        self.cancelled = True

    @property
    def running(self):
        return not self.cancelled


class Later:
    def __init__(self):
        self.jobs = []

    def __call__(self, secs, fn):
        self.jobs.append((secs, fn))

    def run(self):
        jobs, self.jobs = self.jobs, []
        for _, fn in jobs:
            fn()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        FakeBrain.made = []
        self.ui, self.win, self.engine, self.later = FakeUI(), FakeWindow(), FakeEngine(), Later()
        self.prefs = {'paused': False, 'talkMode': 'hold', 'speak': False, 'typeRight': False}
        self.cues = []
        self.box = X.BoxController(self.ui, self.win, brain=lambda on_line, on_exit: FakeBrain(on_line, on_exit),
                                   engine=lambda: self.engine, prefs=self.prefs, later=self.later, cue=self.cues.append,
                                   mic=None, speak=None)

    def talk(self, words):
        self.box.shortcut(True)
        self.box.heard(words)
        self.engine.final = words
        self.box.shortcut(False)

    def test_hold_talk_release_runs_the_brain(self):
        self.talk('what is two plus two')
        brain = FakeBrain.made[-1]
        self.assertEqual(brain.sent[-1]['task'], 'what is two plus two')
        self.assertIn(('words', 'what is two plus two'), self.ui.calls)
        brain.on_line('✓ 4')
        brain.on_exit(0)
        self.assertIn(('line', '✓ 4'), self.ui.calls)
        self.assertIn('finished', self.ui.names())
        self.assertEqual(self.cues[-1], 'soundDone')
        self.assertFalse(self.box.busy)

    def test_nothing_heard_ends_quietly(self):
        self.talk('')
        self.assertEqual(FakeBrain.made[-1].sent, [])
        self.assertIn(('msg', ''), self.ui.calls)

    def test_hotkey_during_answer_stops_it(self):
        self.talk('find my notes')
        brain = FakeBrain.made[-1]
        self.box.shortcut(True)
        self.assertTrue(brain.cancelled)
        self.assertIn(('stopped', 'Answer stopped'), self.ui.calls)
        self.assertEqual(self.cues[-1], 'soundStop')
        self.assertFalse(self.box.busy)

    def test_esc_cancels_without_send(self):
        self.box.shortcut(True)
        self.box.heard('delete everything')
        self.box.escape()
        self.assertFalse(self.engine.running)
        self.assertEqual([b.sent for b in FakeBrain.made], [[]] * len(FakeBrain.made))
        self.assertIn(('stopped', 'Stopped listening'), self.ui.calls)

    def test_paused_says_so(self):
        self.prefs['paused'] = True
        self.box.shortcut(True)
        self.assertFalse(self.engine.running)
        self.assertIn('paused', self.ui.names())

    def test_typed_ask_runs(self):
        self.box.handle('c', {'send': 'open spotify'})
        self.assertEqual(FakeBrain.made[-1].sent[-1]['task'], 'open spotify')

    def test_report_feedback_opens_settings(self):
        opened = []
        self.box.open_settings = opened.append
        self.box.typed('Report feedback')
        self.assertEqual(opened, ['feedback'])
        self.assertEqual(FakeBrain.made, [])

    def test_ask_card_answer_written(self):
        self.talk('delete the file')
        f = os.path.join(tempfile.mkdtemp(), 'answer')
        FakeBrain.made[-1].on_line(f'? Delete it?\trm x\t{f}')
        self.assertIn(('ask', ['Delete it?', 'rm x', '', '']), self.ui.calls)
        self.box.handle('c', {'allow': True})
        self.assertEqual(Path(f).read_text(), 'y')

    def test_setting_line_applies(self):
        self.prefs['speak'] = True
        self.talk('stop reading answers')
        FakeBrain.made[-1].on_line('⚙ speak false')
        self.assertFalse(self.prefs['speak'])

    def test_height_message_resizes(self):
        self.box.handle('h', 240)
        self.box.handle('h', [180, 60])
        self.assertEqual([e for e in self.win.events if isinstance(e, tuple)], [('height', 240), ('height', 60)])

    def test_copy_and_link(self):
        copied, opened = [], []
        self.box.copy_text, self.box.open_uri = copied.append, opened.append
        self.box.handle('c', {'copy': 'answer text'})
        self.box.handle('c', {'link': 'https://example.com'})
        self.box.handle('c', {'link': 'file:///etc/passwd'})
        self.assertEqual((copied, opened), (['answer text'], ['https://example.com']))
        self.assertIn('copied', self.ui.names())

    def test_no_speech_model_says_where_to_get_it(self):
        box = X.BoxController(self.ui, self.win, brain=lambda on_line, on_exit: FakeBrain(on_line, on_exit),
                              engine=lambda: None, prefs=self.prefs, later=self.later, cue=self.cues.append)
        box.shortcut(True)
        self.assertFalse(box.listening)
        self.assertIn(('msg', X.NO_SPEECH), self.ui.calls)
        box.typed('still works typed')
        self.assertEqual(FakeBrain.made[-1].sent[-1]['task'], 'still works typed')

    def test_answer_lingers_by_length(self):
        self.assertEqual(X.linger_after('x' * 12), 6)
        self.assertEqual(X.linger_after('x' * 240), 20)
        self.assertEqual(X.linger_after('x' * 9000), 30)


if __name__ == '__main__':
    unittest.main()
