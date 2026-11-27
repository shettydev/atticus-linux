import os
import re
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import settings as S
from atticus_shell.dictation import DictationData

ROOT = Path(__file__).resolve().parents[2]


class Rig:
    def __init__(self, writable=True):
        self.js, self.ran, self.opened, self.copied, self.events = [], [], [], [], []
        self.prefs = {'login': True, 'safe': True, 'dictateWords': [], 'dictateFixes': {}, 'dictateHistory': True}
        self.writable = writable

        def set_pref(k, v):
            if not self.writable:
                return False
            self.prefs[k] = v
            return True
        d = tempfile.mkdtemp()
        self.c = S.SettingsController(js=lambda fn, arg: self.js.append((fn, arg)), prefs=self.prefs, set_pref=set_pref,
                                      data=DictationData(os.path.join(d, 'd.json'), self.prefs, set_pref, known=lambda w: True),
                                      run=lambda args: self.ran.append(args) or '', open_uri=self.opened.append, copy=self.copied.append,
                                      routines_path=os.path.join(d, 'routines.json'), on_change=lambda k, v: self.events.append((k, v)),
                                      version='2.0.2', mics=lambda: [{'uid': 'm', 'name': 'Mic'}], in_test=True)


class SettingsTests(unittest.TestCase):
    def test_push_payload(self):
        r = Rig()
        r.c.push()
        load = next(arg for fn, arg in r.js if fn == 'st.load')
        self.assertTrue({'prefs', 'mics', 'version', 'routines', 'hotkeyTaken'} <= set(load))
        self.assertEqual(load['version'], '2.0.2')
        self.assertIn('st.dictation', [fn for fn, _ in r.js])

    def test_set_saves_and_tells_the_app(self):
        r = Rig()
        r.c.handle({'do': 'set', 'k': 'speak', 'v': False})
        self.assertFalse(r.prefs['speak'])
        self.assertEqual(r.events, [('speak', False)])

    def test_failed_save_puts_the_old_value_back(self):
        r = Rig(writable=False)
        r.c.handle({'do': 'set', 'k': 'speak', 'v': False})
        self.assertIn(('st.notice', {'text': 'Couldn’t save that change, so the old setting is back.'}), r.js)
        self.assertIn('st.load', [fn for fn, _ in r.js])

    def test_login_switch_uses_the_user_service(self):
        r = Rig()
        r.c.in_test = False
        r.c.handle({'do': 'set', 'k': 'login', 'v': False})
        self.assertIn(['systemctl', '--user', 'disable', 'atticus.service'], r.ran)

    def test_dictionary_word(self):
        r = Rig()
        r.c.handle({'do': 'dictationWord', 'word': 'Kubernetes'})
        self.assertEqual(r.prefs['dictateWords'], ['Kubernetes'])
        r.c.handle({'do': 'dictationWord', 'word': 'x' * 81})
        self.assertTrue(any(fn == 'st.notice' for fn, _ in r.js))

    def test_routine_remove_goes_through_the_brain(self):
        r = Rig()
        r.c.handle({'do': 'routineRemove', 'id': 'abcdef123456'})
        self.assertEqual(r.ran[0][-3:], ['--routine', 'remove', 'abcdef123456'])

    def test_links_copy_and_tip(self):
        r = Rig()
        r.c.handle({'do': 'github'})
        r.c.handle({'do': 'kofi'})
        r.c.handle({'do': 'copy', 'text': 'abc'})
        self.assertEqual(r.copied, ['abc'])
        self.assertTrue(all(u.startswith('https://') for u in r.opened))

    def test_every_swift_action_has_an_answer(self):
        src = (ROOT / 'Atticus.swift').read_text()
        start = src.index('    func handle(_ b: [String: Any]) {\n        guard let what = b["do"]')
        end = src.index('\n    }\n', src.index('switch what', start))
        actions = set(re.findall(r'case "([a-zA-Z]+)"', src[start:end]))
        self.assertIn('set', actions)
        r = Rig()
        for a in sorted(actions - set(S.HANDLED)):
            with self.subTest(a):
                r.js.clear()
                r.c.handle({'do': a})
                self.assertTrue(r.js, a)   # unported actions still say so instead of doing nothing


if __name__ == '__main__':
    unittest.main()
