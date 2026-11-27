import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import linux  # noqa: F401
from atticus_shell import onboarding as O


class Probe:
    def __init__(self, **kw):
        self.values = {'a11y': False, 'bridge': True, 'bridge_reason': '', 'input': True, 'mics': True, 'speech': False,
                       'speech_model': False, 'speech_engine': True, 'extension': False, 'calendar': False}
        self.values.update(kw)

    def __getattr__(self, name):
        return lambda: self.values[name]


class StatusTests(unittest.TestCase):
    def test_keys_match_the_macos_setup_page(self):
        self.assertEqual(set(O.status(Probe(), {'agentUserTools': True})), {'mic', 'speech', 'ax', 'screen', 'auto', 'calendar', 'tools', 'chrome'})

    def test_values(self):
        s = O.status(Probe(a11y=True, extension=True), {'agentUserTools': False})
        self.assertEqual((s['ax'], s['chrome'], s['tools'], s['speech']), (True, True, False, False))

    def test_status_needs_relogin_after_extension_install(self):
        p = Probe(bridge=False, bridge_reason='Log out and back in once to finish setup.')
        self.assertEqual(O.status(p, {})['screen'], False)
        self.assertEqual(O.hints(p, rule_installed=False)['screen'], 'Log out and back in once to finish setup.')


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.js, self.prefs, self.actions = [], {'hotkeyLabel': 'Ctrl+Alt+Space', 'routing': 'balanced'}, []
        self.c = O.OnboardingController(lambda fn, arg: self.js.append((fn, arg)), self.prefs,
                                        lambda k, v: self.prefs.__setitem__(k, v) or True, Probe(),
                                        fix=lambda key: self.actions.append(key), run=lambda args: '{"ok": true, "client": "claude"}',
                                        close=lambda: self.actions.append('closed'))

    def test_push_sends_set_prefs_and_server(self):
        self.c.push()
        self.assertEqual([fn for fn, _ in self.js], ['ob.platform', 'ob.set', 'ob.prefs', 'ob.server'])
        self.assertEqual(self.js[0][1], {'os': 'linux'})

    def test_every_ob_message_handled(self):
        for msg in ({'do': 'page', 'id': 'ai'}, {'do': 'kofi'}, {'do': 'feedback'}, {'do': 'set', 'k': 'model', 'v': 'opus'},
                    {'do': 'provider', 'client': 'pi'}, {'do': 'dictationFocus', 'v': 'true'}, {'do': 'permissionsReady'},
                    {'do': 'grant', 'id': 'ax'}, {'id': 'speech'}, {'do': 'done'}):
            self.c.handle(msg)
        self.assertEqual(self.prefs['model'], 'opus')
        self.assertEqual(self.prefs['client'], 'pi')
        self.assertTrue(self.prefs['onboarded'])
        self.assertEqual(self.actions[:2], ['ax', 'speech'])
        self.assertEqual(self.actions[-1], 'closed')

    def test_bad_provider_ignored(self):
        self.c.handle({'do': 'provider', 'client': 'evil'})
        self.assertNotIn('client', self.prefs)

    def test_plan_check(self):
        self.c.handle({'do': 'provider', 'client': 'claude'})
        self.assertIn(('ob.plan', {'ok': True, 'client': 'claude'}), self.js)


ROOT = Path(__file__).resolve().parents[2]


class PageContractTests(unittest.TestCase):
    """Every ob.* the Linux app calls must exist on the page (ob.notice and ob.detail didn't, so setup showed nothing)."""

    def test_page_has_every_function_the_linux_app_calls(self):
        page = (ROOT / 'ui/onboarding.html').read_text()
        body = page[page.index('  window.ob = {'):page.index('\n  };', page.index('  window.ob = {'))]
        defined = set(re.findall(r'^    (\w+)\(', body, re.M))
        called = set()
        for f in ('linux/atticus_shell/onboarding.py', 'linux/atticus_shell/app.py'):
            called |= set(re.findall(r"""['"]ob\.(\w+)['"]""", (ROOT / f).read_text()))
        self.assertIn('set', called)
        self.assertEqual(sorted(called - defined), [])


class HintTests(unittest.TestCase):
    def test_hints_for_rows_one_click_cannot_finish(self):
        p = Probe(bridge=False, bridge_reason='Log out and back in once to finish setup.', input=False, speech=False, speech_model=True, speech_engine=False)
        hints = O.hints(p, rule_installed=True)
        self.assertEqual(hints['screen'], 'Log out and back in once to finish setup.')
        self.assertIn('build-linux.sh', hints['speech'])
        self.assertIn('Log out and back in', hints['auto'])

    def test_no_hint_when_a_click_fixes_it(self):
        p = Probe(input=False, speech=False, speech_model=False, speech_engine=True)
        self.assertEqual(O.hints(p, rule_installed=False), {})

    def test_push_sends_hints_but_not_over_a_busy_row(self):
        js = []
        p = Probe(bridge=False, bridge_reason='Log out and back in once to finish setup.', speech_model=True, speech_engine=False)
        c = O.OnboardingController(lambda fn, arg: js.append((fn, arg)), {}, lambda k, v: True, p, fix=lambda k: None, run=lambda a: '{}',
                                   close=lambda: None, hints=lambda: O.hints(p, rule_installed=False), busy=lambda: {'speech'})
        c.push()
        rows = [arg for fn, arg in js if fn == 'ob.row']
        self.assertEqual([r['id'] for r in rows], ['screen'])
        self.assertFalse(rows[0]['busy'])


class InputProbeTests(unittest.TestCase):
    def test_wayland_needs_uinput(self):
        p = O.Probe(None, '/tmp', env=SimpleNamespace(session='wayland', compositor='gnome'), uinput=lambda: False)
        self.assertFalse(p.input())   # XWayland's DISPLAY does not count: XTest can't reach Wayland apps

    def test_x11_session_or_uinput(self):
        self.assertTrue(O.Probe(None, '/tmp', env=SimpleNamespace(session='x11', compositor='x11'), uinput=lambda: False).input())
        self.assertTrue(O.Probe(None, '/tmp', env=SimpleNamespace(session='wayland', compositor='kde'), uinput=lambda: True).input())


class SlowStore:
    """ModelStore.ensure that reports progress until released."""

    def __init__(self, fail=None):
        self.calls, self.release, self.fail = 0, threading.Event(), fail

    def ensure(self, model_id, progress=None):
        self.calls += 1
        for f in (0.0, 0.004, 0.42, 0.42, 1.0):
            progress(f)
        self.release.wait(5)
        if self.fail:
            raise self.fail
        return '/models/x'


class FixerTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.pushes, self.ran = [], [], []
        self.rule = os.path.join(tempfile.mkdtemp(), '60-atticus-uinput.rules')

    def fixer(self, store=None, engine=True, compositor='gnome', bridge_ok=False, a11y_after=True):
        state = {'a11y': False}
        probe = Probe(bridge=bridge_ok, bridge_reason='', mics=True, input=False)
        probe.values['a11y'] = False
        def turn_on():
            state['a11y'] = a11y_after
            probe.values['a11y'] = a11y_after
        return O.Fixer(SimpleNamespace(session='wayland', compositor=compositor), '/tmp/atticus-test', probe,
                       row=lambda id, text='', busy=False: self.rows.append((id, text, busy)), push=lambda: self.pushes.append(1),
                       post=lambda fn: fn(), store=store or SlowStore(), engine_ok=lambda: engine, ensure_a11y=turn_on,
                       run=lambda args, **k: self.ran.append(args), rule_path=self.rule)

    def wait_for(self, check):
        end = time.time() + 5
        while time.time() < end and not check():
            time.sleep(0.01)
        self.assertTrue(check())

    def test_speech_download_shows_progress_then_rechecks(self):
        store = SlowStore()
        f = self.fixer(store)
        f.fix('speech')
        self.wait_for(lambda: ('speech', 'Downloading 42%', True) in self.rows)
        self.assertEqual([r for r in self.rows if r[1] == 'Downloading 42%'], [('speech', 'Downloading 42%', True)])   # once per percent
        self.assertIn(('speech', 'Unpacking…', True), self.rows)
        self.assertEqual(f.busy(), {'speech'})
        store.release.set()
        self.wait_for(lambda: self.pushes)
        self.assertEqual(self.rows[-1], ('speech', '', False))
        self.assertEqual(f.busy(), set())

    def test_second_click_does_not_start_a_second_download(self):
        store = SlowStore()
        f = self.fixer(store)
        f.fix('speech')
        f.fix('speech')
        store.release.set()
        self.wait_for(lambda: self.pushes)
        self.assertEqual(store.calls, 1)

    def test_download_error_is_shown_on_the_row(self):
        from atticus_shell.speech.stt import ModelError
        store = SlowStore(fail=ModelError('Download failed. Check your connection and try again.'))
        store.release.set()
        f = self.fixer(store)
        f.fix('speech')
        self.wait_for(lambda: self.rows and not self.rows[-1][2] and self.rows[-1][1])
        self.assertEqual(self.rows[-1], ('speech', 'Download failed. Check your connection and try again.', False))
        self.assertEqual(f.busy(), set())

    def test_missing_engine_says_so_and_downloads_nothing(self):
        store = SlowStore()
        f = self.fixer(store, engine=False)
        f.fix('speech')
        self.assertEqual(store.calls, 0)
        self.assertIn('build-linux.sh', self.rows[-1][1])

    def test_accessibility_turns_on_or_explains(self):
        self.fixer().fix('ax')
        self.assertEqual(self.rows, [])
        self.fixer(a11y_after=False).fix('ax')
        self.assertIn('toolkit-accessibility', self.rows[-1][1])

    def test_input_explains_rule_or_relogin(self):
        f = self.fixer()
        f.fix('auto')
        self.assertIn('build-linux.sh', self.rows[-1][1])
        Path(self.rule).write_text('x')
        f.fix('auto')
        self.assertIn('Log out and back in', self.rows[-1][1])

    def test_gnome_desktop_access_turns_extension_on(self):
        self.fixer().fix('screen')
        self.assertIn(['gnome-extensions', 'enable', 'atticus@atticus.app'], self.ran)
        self.assertIn('Log out and back in', self.rows[-1][1])


if __name__ == '__main__':
    unittest.main()
