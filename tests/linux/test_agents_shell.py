import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import agents as G

ROOT = Path(__file__).resolve().parents[2]


def swift_actions():
    src = (ROOT / 'Atticus.swift').read_text()
    start = src.index('let surface = message.webView === viewWeb ? "view" : "shelf"')
    end = src.index('default: send(body)', start)
    return set(re.findall(r'case ((?:"[a-zA-Z]+",?\s*)+):', src[start:end]).__iter__() and
               re.findall(r'"([a-zA-Z]+)"', ' '.join(re.findall(r'case ((?:"[a-zA-Z]+",?\s*)+):', src[start:end]))))


class Rig:
    def __init__(self):
        self.sent, self.js, self.opened, self.notes = [], [], [], []
        self.c = G.AgentsController(send=self.sent.append, js=lambda surface, call, value=None: self.js.append((surface, call, value)),
                                    open_path=lambda path, reveal: self.opened.append((path, reveal)), notify=self.notes.append,
                                    set_pref=lambda k, v: None, prefs={})


class ControllerTests(unittest.TestCase):
    def test_every_swift_page_action_handled(self):
        actions = swift_actions()
        self.assertIn('dismiss', actions)
        self.assertEqual(sorted(actions - set(G.HANDLED)), [])

    def test_ready_surface_gets_mode_prefs_and_list(self):
        r = Rig()
        r.c.receive({'event': 'list', 'agents': [{'id': 'a', 'status': 'running', 'created': 1}], 'named': []})
        r.c.page('shelf', {'action': 'ready'})
        calls = [c[1] for c in r.js if c[0] == 'shelf']
        self.assertEqual(calls[:3], ['mode', 'prefs', 'list'])
        self.assertEqual(r.js[-1][2][0]['id'], 'a') if r.js[-1][1] == 'list' else None

    def test_list_reaches_ready_surfaces_only(self):
        r = Rig()
        r.c.page('view', {'action': 'ready'})
        r.js.clear()
        r.c.receive({'event': 'list', 'agents': [{'id': 'b', 'status': 'done', 'created': 2}, {'id': 'a', 'status': 'running', 'created': 1}], 'named': [{'name': 'Researcher'}]})
        self.assertEqual([c[:2] for c in r.js], [('view', 'list'), ('view', 'named')])
        self.assertEqual([row['id'] for row in r.js[0][2]], ['b', 'a'])   # newest first
        self.assertEqual(r.c.badge, {'count': 1, 'attention': False})

    def test_dismissing_a_done_chip_marks_it_seen(self):
        r = Rig()
        r.c.receive({'event': 'list', 'agents': [{'id': 'd', 'status': 'done', 'created': 1}]})
        r.c.page('shelf', {'action': 'dismiss', 'id': 'd'})
        self.assertEqual(r.sent, [{'action': 'seen', 'id': 'd'}])
        self.assertIn('d', r.c.dismissed)

    def test_unknown_actions_go_to_the_supervisor(self):
        r = Rig()
        r.c.page('view', {'action': 'stop', 'id': 'x'})
        self.assertEqual(r.sent, [{'action': 'stop', 'id': 'x'}])

    def test_open_event_opens_the_file(self):
        r = Rig()
        r.c.receive({'event': 'open', 'path': '/tmp/report.pdf', 'reveal': False})
        self.assertEqual(r.opened, [('/tmp/report.pdf', False)])

    def test_finished_agent_notifies_once(self):
        r = Rig()
        r.c.receive({'event': 'list', 'agents': [{'id': 'a', 'status': 'running', 'created': 1, 'title': 'USB hubs'}]})
        r.c.receive({'event': 'list', 'agents': [{'id': 'a', 'status': 'done', 'created': 1, 'title': 'USB hubs', 'summary': 'Found three'}]})
        r.c.receive({'event': 'list', 'agents': [{'id': 'a', 'status': 'done', 'created': 1, 'title': 'USB hubs', 'summary': 'Found three'}]})
        self.assertEqual(r.notes, ['Found three'])


FAKE = r'''
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    print(json.dumps({'event': 'list', 'agents': [{'id': 'z', 'status': 'running', 'created': 1}], 'echo': req}), flush=True)
'''


class ServiceTests(unittest.TestCase):
    def test_supervisor_round_trip(self):
        script = Path(tempfile.mkdtemp(), 'agents.py')
        script.write_text(FAKE)
        got, done = [], threading.Event()
        s = G.AgentService([sys.executable, str(script)], lambda e: (got.append(e), done.set()), post=lambda fn: fn())
        s.start()
        self.addCleanup(s.stop)
        self.assertTrue(done.wait(10))
        self.assertEqual(got[0]['echo'], {'action': 'list'})   # a fresh supervisor is asked for its saved rows

    def test_restart_backoff(self):
        self.assertEqual([G.backoff(n) for n in (1, 2, 3, 7)], [2, 4, 8, 60])


if __name__ == '__main__':
    unittest.main()
