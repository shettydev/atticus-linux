import unittest

from tests import linux  # noqa: F401
from atticus_shell import onboarding as O


class Probe:
    def __init__(self, **kw):
        self.values = {'a11y': False, 'bridge': True, 'bridge_reason': '', 'input': True, 'mics': True, 'speech': False,
                       'extension': False, 'calendar': False}
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
        self.assertEqual(O.detail(p)['screen'], 'Log out and back in once to finish setup.')


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.js, self.prefs, self.actions = [], {'hotkeyLabel': 'Ctrl+Alt+Space', 'routing': 'balanced'}, []
        self.c = O.OnboardingController(lambda fn, arg: self.js.append((fn, arg)), self.prefs,
                                        lambda k, v: self.prefs.__setitem__(k, v) or True, Probe(),
                                        fix=lambda key: self.actions.append(key), run=lambda args: '{"ok": true, "client": "claude"}',
                                        close=lambda: self.actions.append('closed'))

    def test_push_sends_set_prefs_and_server(self):
        self.c.push()
        self.assertEqual([fn for fn, _ in self.js], ['ob.set', 'ob.detail', 'ob.prefs', 'ob.server'])

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


if __name__ == '__main__':
    unittest.main()
