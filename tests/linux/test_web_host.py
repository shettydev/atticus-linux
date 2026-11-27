import io
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import linux
from atticus_shell import web_host as W

ROOT = Path(__file__).resolve().parents[2]


class FrameTests(unittest.TestCase):
    def test_native_messaging_framing(self):
        buf = io.BytesIO()
        W.write_frame(buf, {'op': 'tabs', 'é': 1})
        raw = buf.getvalue()
        self.assertEqual(struct.unpack('<I', raw[:4])[0], len(raw) - 4)
        self.assertEqual(W.read_frame(io.BytesIO(raw)), {'op': 'tabs', 'é': 1})
        self.assertIsNone(W.read_frame(io.BytesIO(b'')))


class FakeExtension(threading.Thread):
    """Reads frames the host sends and answers each with {'id', 'ok', 'value'}."""

    def __init__(self, inbound, outbound):
        super().__init__(daemon=True)
        self.inbound, self.outbound, self.seen = inbound, outbound, []

    def run(self):
        W.write_frame(self.outbound, {'hello': {'browser': 'Chromium', 'version': '1'}})
        while True:
            msg = W.read_frame(self.inbound)
            if msg is None:
                return
            self.seen.append(msg)
            value = [{'id': 7, 'windowId': 3, 'active': True, 'url': 'https://example.com', 'title': 'Example'}] if msg['op'] == 'tabs' else msg.get('args')
            W.write_frame(self.outbound, {'id': msg['id'], 'ok': True, 'value': value})


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sock = os.path.join(self.dir, 'web.sock')
        to_ext_r, to_ext_w = os.pipe()
        from_ext_r, from_ext_w = os.pipe()
        self.ext = FakeExtension(os.fdopen(to_ext_r, 'rb'), os.fdopen(from_ext_w, 'wb', buffering=0))
        self.ext.start()
        self.relay = W.Relay(self.sock, os.fdopen(from_ext_r, 'rb'), os.fdopen(to_ext_w, 'wb', buffering=0))
        threading.Thread(target=self.relay.serve, daemon=True).start()
        end = time.time() + 5
        while not os.path.exists(self.sock) and time.time() < end:
            time.sleep(0.01)

    def test_hello_answered_by_the_host(self):
        self.assertEqual(W.call('hello', path=self.sock)['browser'], 'Chromium')

    def test_ops_reach_the_extension_and_come_back(self):
        tabs = W.call('tabs', path=self.sock)
        self.assertEqual(tabs[0]['id'], 7)
        self.assertEqual(W.call('run', path=self.sock, tab=7, args=['read', '', '']), ['read', '', ''])
        self.assertEqual(self.ext.seen[-1]['op'], 'run')

    def test_socket_is_private(self):
        self.assertEqual(os.stat(self.sock).st_mode & 0o777, 0o600)

    def test_host_rejects_non_owner_socket(self):
        os.chmod(self.sock, 0o666)
        with self.assertRaises(W.HostError):
            W.call('hello', path=self.sock)


class NoHostTests(unittest.TestCase):
    def test_no_extension_says_install_once(self):
        a = linux.linux_brain(self)
        import brain_linux
        with mock.patch.object(brain_linux, '_host', side_effect=W.HostError('no host')):
            reply = a.web('read')
        self.assertIn('Settings → Browser', reply)


class FakeHost:
    def __init__(self):
        self.calls, self.windows = [], {3: {'tabs': {7: 'https://example.com/'}}}
        self.next_window = 40

    def __call__(self, op, **args):
        self.calls.append((op, args))
        if op == 'hello':
            return {'browser': 'Chromium'}
        if op == 'tabs':
            return [{'id': t, 'windowId': w, 'active': True, 'url': u, 'title': 'Example', 'focused': True}
                    for w, d in self.windows.items() for t, u in d['tabs'].items()]
        if op == 'open':
            w = self.next_window
            self.next_window += 1
            self.windows[w] = {'tabs': {w * 10: args['url']}}
            return {'window': w, 'tab': w * 10}
        if op == 'run':
            c = args['args'][0]
            if c == 'ready':
                return json.dumps({'ready': True, 'signature': 'https://example.com/|x'})
            if c == 'read':
                return 'Page: Example\n[1] link "More"'
            return 'Clicked "More"' if c == 'click' else ''
        if op == 'document':
            return ['https://example.com/', '1700000000000']
        if op == 'window':
            return {'left': 0, 'top': 0, 'width': 800, 'height': 600, 'title': 'Example'}
        if op == 'close_window':
            self.windows.pop(args['window'], None)
            return True
        raise AssertionError(op)


class BrainWebTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        import brain_linux
        self.host = FakeHost()
        for p in (mock.patch.object(brain_linux, '_host', self.host), mock.patch.object(self.a, 'preview_app', return_value=None),
                  mock.patch.object(self.a, 'hands_map', return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.ask = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {'ATTICUS_ASK': self.ask})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop('ATTICUS_AGENT', None)
        os.environ['ATTICUS_FRONT'] = 'Chromium'

    def test_run_passes_web_js_args(self):
        out = self.a.web('read')
        self.assertIn('[1] link "More"', out)
        runs = [args for op, args in self.host.calls if op == 'run']
        self.assertEqual(runs[-1], {'tab': 7, 'args': ['read', '', '']})
        saved = json.loads(Path(self.ask, 'web.json').read_text())
        self.assertEqual((saved['adapter'], saved['window'], saved['tab']), ('extension', 3, 7))

    def test_click_reports_changed_state(self):
        self.a.web('read')
        out = self.a.web('click', '1')
        self.assertTrue(out.startswith('Clicked "More"'), out)

    def test_agent_window_closed_on_task_end(self):
        os.environ['ATTICUS_AGENT'] = '1'
        self.addCleanup(os.environ.pop, 'ATTICUS_AGENT', None)
        with mock.patch.object(self.a, 'agent_gate_dir', return_value=self.ask), mock.patch.object(self.a, 'keep_behind'):
            out = self.a.web('read', 'https://example.com/')
        self.assertIn('[1] link "More"', out)
        opened = [args for op, args in self.host.calls if op == 'open']
        self.assertEqual((opened[0]['window'], opened[0]['active']), ('agent', False))
        win = json.loads(Path(self.ask, 'browser-window.json').read_text())['window']
        with mock.patch.object(self.a, 'agent_gate_dir', return_value=self.ask):
            self.a.close_agent_window(self.ask)
        self.assertIn(('close_window', {'window': win}), self.host.calls)
        self.assertNotIn(win, self.host.windows)

    def test_adapter_is_extension(self):
        self.assertEqual(self.a.browser_adapter('Chromium'), 'extension')


NODE = r'''
const fs = require('fs');
const calls = [], posted = [];
let listener = null;
global.chrome = {
  runtime: { connectNative: name => ({ name, postMessage: m => posted.push(m), onMessage: { addListener: f => { listener = f; } },
                                       onDisconnect: { addListener: () => {} } }), getManifest: () => ({ version: '9' }) },
  tabs: { query: async q => [{ id: 7, windowId: 3, active: true, url: 'https://x.org', title: 'X' }], create: async o => { calls.push(['tab', o]); return { id: 11, windowId: o.windowId }; },
          update: async (id, o) => { calls.push(['update', id, o]); return { id }; }, get: async id => ({ id, url: 'https://x.org', title: 'X', windowId: 3 }) },
  windows: { create: async o => { calls.push(['window', o]); return { id: 40, tabs: [{ id: 400 }] }; }, remove: async id => calls.push(['remove', id]),
             get: async id => ({ left: 1, top: 2, width: 3, height: 4 }), update: async (id, o) => calls.push(['focus', id, o]), getLastFocused: async () => ({ id: 3 }) },
  scripting: { executeScript: async o => { calls.push(['script', o.files || o.args]); return [{ result: o.files ? undefined : 'ran:' + JSON.stringify(o.args) }]; } },
};
Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'Mozilla/5.0 Chrome/131.0' }, configurable: true });   // Node 21+ has its own
eval(fs.readFileSync(process.argv[2], 'utf8'));
(async () => {
  for (const m of [{ id: 1, op: 'tabs' }, { id: 2, op: 'run', tab: 7, args: ['read', '', ''] }, { id: 3, op: 'open', url: 'https://y.org', window: 'agent', active: false },
                   { id: 4, op: 'close_window', window: 40 }, { id: 5, op: 'nope' }]) await listener(m);
  await new Promise(r => setTimeout(r, 50));
  console.log(JSON.stringify({ posted, calls }));
})();
'''


@unittest.skipUnless(shutil.which('node'), 'needs node')
class ExtensionLogicTests(unittest.TestCase):
    def test_background_script_against_mock_chrome(self):
        harness = os.path.join(tempfile.mkdtemp(), 'h.js')
        Path(harness).write_text(NODE)
        r = subprocess.run(['node', harness, str(ROOT / 'linux/browser/extension/background.js')], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        posted = {p.get('id', 'hello'): p for p in out['posted']}
        self.assertEqual(posted['hello']['hello']['browser'], 'Chrome')
        self.assertEqual(posted[1]['value'][0]['id'], 7)
        self.assertEqual(posted[2]['value'], 'ran:["read","",""]')
        self.assertIn(['script', ['web-inject.js']], out['calls'])            # web.js is injected as a packaged file
        self.assertEqual(posted[3]['value'], {'window': 40, 'tab': 400})
        self.assertIn(['window', {'url': 'https://y.org', 'focused': False, 'state': 'normal'}], out['calls'])
        self.assertIn(['remove', 40], out['calls'])
        self.assertFalse(posted[5]['ok'])


class ManifestTests(unittest.TestCase):
    def test_manifest_permissions(self):
        m = json.loads((ROOT / 'linux/browser/extension/manifest.json').read_text())
        self.assertEqual(m['manifest_version'], 3)
        self.assertTrue({'nativeMessaging', 'scripting', 'tabs'} <= set(m['permissions']))

    def test_extension_id_from_key(self):
        # Chrome: the first 32 hex digits of sha256(public key DER), each mapped 0-f -> a-p
        self.assertEqual(W.extension_id(b'\x00' * 10), W.extension_id(b'\x00' * 10))
        self.assertRegex(W.extension_id(b'abc'), r'^[a-p]{32}$')


if __name__ == '__main__':
    unittest.main()
